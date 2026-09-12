"""职场福利：工位升级、加班餐补贴、年度体检（都是「花钱/按时段换数值」的日常福利）。

由 core/extra2.py 按业务域拆分而来。文件名里的 “2” 是【开发批次编号】而不是业务域
（extra2.py 一个文件装了抽奖／借钱／建议／工位／餐补／体检六块），实体因此改用可
预测的域名，批次命名的 core/extra2.py 只留门面转出。

文案表仍沿用历史命名 resources/texts/extra2.json：表名一改，tt/gd.s 就取不到文案、
全部回退到代码里的中文默认值，静态漂移扫描（tests/test_audit_round4.py 的
TestTextDefaultsDoNotDrift）会红。改文案只动 JSON，不要动这里的表名。
"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra2", key, variables)


_title = make_title("extra2")


async def upgrade_workstation(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    cur_lv = int(p.get("workstation") or 0)
    # 按条目自带的 lv 定位下一级，而不是列表下标：workstations.json 由运维手改，
    # 重排/插入条目时下标会与玩家档案里的等级脱节。缺 lv 的条目退化为下标。
    levels = {int(w.get("lv", i)): w for i, w in enumerate(gd.workstations())}
    if not levels:
        return R(err=_t("ws_empty"))
    raw = levels.get(cur_lv + 1)
    if raw is None:
        return R(err=_t("ws_max"))
    # 先归一化再动状态：save_player 已经写盘，此刻再因缺字段抛 KeyError 就是
    # 「扣了钱、等级涨了、用户只看到指令异常」，而工位升级不可逆（同 life_items）。
    nxt = {
        "name": str(raw.get("name") or _t("ws_fallback_name")),
        "desc": str(raw.get("desc") or ""),
        "bonus": logic.num_of(raw, "bonus", 0),
        "cost": max(0.0, logic.num_of(raw, "cost", 0)),
    }
    cost = nxt["cost"]
    if float(p["cash"]) < cost:
        return R(err=_t("ws_short", {"name": nxt["name"], "cost": logic.fmt_money(cost)}))
    p["cash"] = round(float(p["cash"]) - cost, 2)
    p["workstation"] = cur_lv + 1
    await asyncio.to_thread(db.save_player, p)
    # 0 元不写流水（本仓库的统一口径：career_work.overtime 的 if ev_cash、
    # cleanup_old_data 的 if amt > 0）。workstations.json 由运维手改，条目缺
    # cost 或写成负数都会被上面的 max(0.0, …) 归一成 0，此时再记一条
    # ('工位升级', 0.0) 只是账本噪声：#工资条 的流水列表会多出一行 0 元支出，
    # 而现金列一分没动。
    if cost > 0:
        await asyncio.to_thread(
            db.add_transaction, gid, uid, _t("kind_workstation"), -cost, nxt["name"]
        )
    return R(
        tmpl="panel",
        data={
            "icon": "🖥️",
            "title": f"{_title('title_ws_up', '工位升级')} → {nxt['name']}",
            "accent": "#7fd1ff",
            "lines": [nxt["desc"]],
            "blocks": [
                {
                    "label": _t("lbl_upgrade_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_comfort"),
                    "value": _t("val_plus", {"bonus": nxt["bonus"]}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
        },
        text=_t("text_ws_up", {"name": nxt["name"], "desc": nxt["desc"]}),
    )


async def overtime_meal(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("ot_unemployed"))
    now = int(time.time())
    hour = time.localtime(now).tm_hour
    start_hour = int(logic.cfg_get(cfg, "overtime_meal_start_hour", 18))
    if hour < start_hour:
        return R(err=_t("ot_too_early", {"hour": start_hour}))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "ot_meal") > 0:
        return R(err=_t("ot_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "ot_meal"))}))
    logic.cd_set(
        p,
        "ot_meal",
        float(logic.cfg_get(cfg, "overtime_meal_cooldown_hours", 24)) * 3600,
    )
    line = logic.pick(gd.t("extra2", "ot_meal"))
    subsidy = min(
        float(logic.cfg_get(cfg, "overtime_meal_subsidy_max", 30.0)),
        float(p["salary"]) / (logic.workdays(cfg) * 10),
    )
    subsidy = round(subsidy, 2)
    p["cash"] = round(float(p["cash"]) + subsidy, 2)
    # 餐补是收入，计入累计总收入（与同文件的 kind_meal_allowance 流水成对出现）
    p["total_earned"] = round(float(p.get("total_earned") or 0) + subsidy, 2)
    p["mind"] = round(float(p["mind"]) + 3, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_meal_allowance"), subsidy, _t("ot_note")
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🍱",
            "title": _title("title_ot_meal", "加班餐补贴"),
            "accent": "#ffd86f",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_meal_subsidy"),
                    "value": _t("val_plus_yuan", {"amount": logic.fmt_money(subsidy)}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_gain_cur", {"gain": 3, "cur": p["mind"]}),
                },
            ],
        },
        text=_t("text_ot_meal", {"amount": logic.fmt_money(subsidy), "line": line}),
    )


async def health_checkup(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    year = time.strftime("%Y")
    if p.get("checkup_year") == year:
        return R(err=_t("checkup_duplicate"))
    cost = float(logic.cfg_get(cfg, "checkup_cost", 200.0))
    if float(p["cash"]) < cost:
        return R(err=_t("checkup_short", {"cost": logic.fmt_money(cost)}))
    # 余额够才标记本年已检：否则钱不够会被白锁一年
    p["checkup_year"] = year
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    if random.random() < float(logic.cfg_get(cfg, "checkup_ok_rate", 0.55)):
        line = logic.pick(gd.t("extra2", "checkup_ok"))
        p["health"] = round(min(100, float(p["health"]) + 5), 1)
        icon, accent, title = "✅", "#6fe08c", _title("title_checkup_ok", "体检结果良好")
    else:
        line = logic.pick(gd.t("extra2", "checkup_bad"))
        # 不能写 max(10, health-8)：那个 10 的意图是「不要跌破 10」，但它对本来就
        # 低于 10 的玩家会变成【抬升】—— health=3 的玩家花 200 元做个「结果堪忧」
        # 的体检就能回血到 10，比「结果良好」的 +5 还多。下限交给 clamp_status(0)。
        p["health"] = round(float(p["health"]) - 8, 1)
        icon, accent, title = "🏥", "#fc6262", _title("title_checkup_bad", "体检结果堪忧")
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(db.add_transaction, gid, uid, _t("kind_checkup"), -cost, title)
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": title,
            "accent": accent,
            "lines": [line],
            "blocks": [
                # 体检费此前写成 f"-{cost} 元"，float 直出会渲染成「-200.0 元」，
                # 与全插件其它金额（fmt_money）不一致
                {
                    "label": _t("lbl_checkup_fee"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_status_100", {"cur": p["health"]}),
                },
            ],
        },
        text=_t("text_checkup", {"title": title, "line": line}),
    )
