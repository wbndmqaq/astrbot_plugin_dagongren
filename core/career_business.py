"""create_company 等功能（由 career.py 拆分）。"""

import asyncio

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("career_business", key, variables)


_title = make_title("career_business")


async def create_company(db, gid, uid, nickname, comp_name, cfg):
    """创业自建公司：身价达标且消耗启动资金。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    comp_name = (comp_name or "").strip()
    if not comp_name:
        return R(err=_t("name_required"))
    max_len = int(logic.cfg_get(cfg, "company_name_max_len", 15))
    if len(comp_name) > max_len:
        return R(err=_t("name_too_long", {"max_len": max_len}))

    # 一人只能开一家公司
    row = await asyncio.to_thread(db.get_custom_company_by_boss, gid, uid)
    if row:
        return R(err=_t("already_boss", {"name": row["name"]}))
    # 在职的人要先离职再创业：直接覆盖 p["company"] 会让人静默消失在原公司
    # （不重置 attend_streak、不写离职事件），和 #辞职 的行为完全不一致。
    if int(p["company"]) != -1:
        comp_now = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
        return R(err=_t("resign_first", {"name": (comp_now or {}).get("name", "")}))

    # 门槛：职级 >= 10 (VP/合伙人) 或 身价 >= min_val，且启动资金 cost 元
    cost = float(logic.cfg_get(cfg, "create_company_cost", 30000.0))
    min_val = float(logic.cfg_get(cfg, "create_company_min_value", 50000.0))
    min_lvl = int(logic.cfg_get(cfg, "create_company_min_level", 10))
    base_salary = float(logic.cfg_get(cfg, "custom_company_base_salary", 6000.0))
    init_balance = float(logic.cfg_get(cfg, "custom_company_init_balance", 10000.0))
    value_bonus = float(logic.cfg_get(cfg, "create_company_value_bonus", 20000.0))
    if int(p["lvl"]) < min_lvl and float(p["value"]) < min_val:
        return R(err=_t("low_qualification", {"min_val": logic.fmt_money(min_val)}))
    if float(p["cash"]) < cost:
        return R(err=_t("cash_short", {"cost": logic.fmt_money(cost)}))

    # 先建公司再扣钱：注册失败就没有钱可退，也不会留下「已扣款但无公司」的中间态。
    # create_custom_company_if_free 自带「一人一司」竞态保护。
    cid = await asyncio.to_thread(
        db.create_custom_company_if_free,
        str(gid),
        str(uid),
        comp_name,
        _t("custom_company_tag"),
        base_salary,
        init_balance,
    )
    if not cid:
        return R(err=_t("already_boss_no_name"))

    # 条件扣款：余额不足时不产生负资产。失败则把刚建的公司撤掉，保持一致。
    ok = await asyncio.to_thread(db.try_debit_cash, gid, uid, cost)
    if not ok:
        await asyncio.to_thread(db.delete_custom_company, cid)
        return R(err=_t("cash_short", {"cost": logic.fmt_money(cost)}))
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        uid,
        _t("kind_register_fund"),
        -cost,
        _t("tx_register", {"name": comp_name}),
    )

    # 老板挂职自建公司：company / salary 必须同时写，
    # 否则 salary 留在 0，daily_pay 永远发 0 元，企业金库也永远进 0。
    # 注意：扣款已由 try_debit_cash 在库内完成，这里绝不能再动 p["cash"]，
    # 否则 save_player 的增量写回会把同一笔钱扣第二次。
    p["value"] = round(float(p["value"]) + value_bonus, 2)
    p["company"] = gd.CUSTOM_BASE + cid
    p["salary"] = logic.base_salary_of(p, base_salary, gd.position(int(p["lvl"]))["mult"])
    await asyncio.to_thread(db.save_player, p)

    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_startup"),
        _t("event_founded", {"name": p["nickname"] or uid, "comp_name": comp_name}),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "👑",
            "title": _title("title_founded", "🎉 创业成功 · 公司成立！"),
            "accent": "#ffd86f",
            "lines": [
                _t("success_line1", {"name": p["nickname"] or uid, "comp_name": comp_name}),
                _t("success_line2"),
            ],
            "blocks": [
                {"label": _t("lbl_company_name"), "value": comp_name},
                {
                    "label": _t("lbl_register_cost"),
                    "value": _t("val_register_cost", {"cost": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_your_salary"),
                    "value": _t("val_salary", {"salary": logic.fmt_money(p["salary"])}),
                },
                {
                    "label": _t("lbl_value_boost"),
                    "value": _t(
                        "val_value_boost",
                        {
                            "bonus": logic.fmt_money(value_bonus),
                            "value": logic.fmt_money(p["value"]),
                        },
                    ),
                },
                {
                    "label": _t("lbl_treasury"),
                    "value": _t("val_treasury", {"balance": logic.fmt_money(init_balance)}),
                },
            ],
            "foot": _t("foot_founded"),
        },
        text=_t("success_text", {"comp_name": comp_name, "value": logic.fmt_money(value_bonus)}),
    )


async def company_dividend(db, gid, uid, nickname, cfg):
    """老板提取公司利润分红。"""
    row_data, dividend, status, _tx_id = await asyncio.to_thread(
        db.withdraw_custom_company_dividend,
        str(gid),
        str(uid),
        # 公司名要到事务内才读到，传模板由存储层替换 {company}
        _t("tx_dividend"),
        _t("kind_dividend"),
    )
    if status == "not_boss":
        return R(err=_t("not_boss"))
    if status == "zero_balance":
        return R(err=_t("zero_balance", {"name": row_data["name"]}))

    # 金库清零、老板入账、记流水已在 withdraw_custom_company_dividend 的
    # 单个事务内原子完成（全部走列级原子更新），不再有跨事务资金丢失窗口。
    p = await asyncio.to_thread(db.get_player, gid, uid)

    return R(
        tmpl="panel",
        data={
            "icon": "💰",
            "title": f"{_title('title_dividend', '企业分红到账')} · {row_data['name']}",
            "accent": "#6fe08c",
            "lines": [_t("dividend_line", {"dividend": logic.fmt_money(dividend)})],
            "blocks": [
                {
                    "label": _t("lbl_dividend_amount"),
                    "value": _t("val_dividend_amount", {"dividend": logic.fmt_money(dividend)}),
                },
                {
                    "label": _t("lbl_current_cash"),
                    "value": _t("val_current_cash", {"cash": logic.fmt_money(p["cash"])}),
                },
                {"label": _t("lbl_treasury_left"), "value": _t("val_treasury_zero")},
            ],
            "foot": _t("foot_dividend"),
        },
        text=_t("dividend_text", {"dividend": logic.fmt_money(dividend)}),
    )
