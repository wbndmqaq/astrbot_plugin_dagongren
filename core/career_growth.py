"""promote 等功能（由 career.py 拆分）。"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("career_growth", key, variables)


_title = make_title("career_growth")


async def promote(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_unemployed_promote"))
    pos_list = gd.positions()
    if int(p["lvl"]) >= len(pos_list) - 1:
        top_title = pos_list[-1]["title"] if pos_list else _t("fallback_top_position")
        return R(err=_t("err_top", {"top_title": top_title}))
    nxt = pos_list[int(p["lvl"]) + 1]
    if int(p["exp"]) < nxt["need"]:
        return R(
            err=_t("err_need_exp", {"title": nxt["title"], "need": nxt["need"], "exp": p["exp"]})
        )
    cost = float(nxt["cost"])
    if float(p["cash"]) < cost:
        return R(err=_t("err_pay_short", {"cost": logic.fmt_money(cost)}))

    rate = logic.promote_rate(
        int(p["lvl"]),
        float(logic.cfg_get(cfg, "promote_base_rate", 0.85)),
        float(logic.cfg_get(cfg, "promote_decay", 0.06)),
    )
    # 技能加成：每掌握一门技能，晋升成功率 +3%（上限 95%）
    rate = min(
        float(logic.cfg_get(cfg, "promote_max_rate", 0.95)),
        rate
        + float(logic.cfg_get(cfg, "skill_promote_bonus_rate", 0.03))
        * len(p.get("_skills", []) or []),
    )
    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    if random.random() < rate:
        p["lvl"] = int(p["lvl"]) + 1
        new_pos = gd.position(int(p["lvl"]))
        p["salary"] = logic.base_salary_of(p, comp["salary"], new_pos["mult"])
        p["cash"] = round(float(p["cash"]) - cost, 2)
        await asyncio.to_thread(db.save_player, p)
        name = logic.name_of(p, uid)
        await asyncio.to_thread(
            db.add_event,
            gid,
            uid,
            _t("kind_promote"),
            _t(
                "event_promote",
                {"name": name, "title": new_pos["title"], "salary": logic.fmt_money(p["salary"])},
            ),
        )
        return R(
            tmpl="panel",
            data={
                "icon": "🚀",
                "title": f"{_title('title_promote_ok', '恭喜晋升')}「{new_pos['title']}」",
                "accent": "#ffd86f",
                "lines": [logic.pick(gd.t("work", "promote_ok"))],
                "blocks": [
                    {
                        "label": _t("lbl_bribe_cost"),
                        "value": _t("val_bribe_cost", {"cost": logic.fmt_money(cost)}),
                    },
                    {
                        "label": _t("lbl_new_salary"),
                        "value": _t("val_new_salary", {"salary": logic.fmt_money(p["salary"])}),
                    },
                    {
                        "label": _t("lbl_next_promote"),
                        "value": (
                            _t(
                                "val_next_promote_need",
                                {"need": pos_list[p["lvl"] + 1]["need"]},
                            )
                            if p["lvl"] + 1 < len(pos_list)
                            else _t("val_next_promote_peak")
                        ),
                    },
                ],
            },
            text=_t(
                "text_promote_ok",
                {"title": new_pos["title"], "salary": logic.fmt_money(p["salary"])},
            ),
        )
    refund_rate = float(logic.cfg_get(cfg, "promote_fail_refund_rate", 0.5))
    lost = round(cost * (1 - refund_rate), 2)
    p["cash"] = round(max(0.0, float(p["cash"]) - lost), 2)
    p["mind"] = float(p["mind"]) - 8
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    fail_line = logic.pick(gd.t("work", "promote_fail"))
    return R(
        tmpl="panel",
        data={
            "icon": "😞",
            "title": _title("title_promote_fail", "晋升失败"),
            "accent": "#fc6262",
            "lines": [fail_line],
            "blocks": [
                {
                    "label": _t("lbl_success_rate"),
                    "value": _t("val_success_rate", {"rate": f"{rate * 100:.0f}"}),
                },
                {
                    "label": _t("lbl_bribe_refund", {"rate": f"{refund_rate * 100:g}"}),
                    "value": _t("val_bribe_cost", {"cost": logic.fmt_money(lost)}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_mind_100", {"mind": f"{p['mind']}"}),
                },
            ],
        },
        text=_t("text_promote_fail", {"rate": f"{rate * 100:.0f}", "line": fail_line}),
    )


async def negotiate_salary(db, gid, uid, nickname, cfg):
    """和公司谈加薪：成功率与经验、职级挂钩。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_unemployed_negotiate"))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "negotiate") > 0:
        return R(
            err=_t(
                "err_negotiate_cd",
                {"remaining": logic.fmt_remaining(logic.cd_left(p, "negotiate"))},
            )
        )
    logic.cd_set(p, "negotiate", float(logic.cfg_get(cfg, "negotiate_cooldown_days", 7)) * 86400)

    pos_list = gd.positions()
    nxt = pos_list[int(p["lvl"]) + 1] if int(p["lvl"]) + 1 < len(pos_list) else None
    benchmark = max(100, nxt["need"] if nxt else 500)
    rate = min(
        float(logic.cfg_get(cfg, "negotiate_max_rate", 0.75)),
        float(logic.cfg_get(cfg, "negotiate_base_rate", 0.25)) + int(p["exp"]) / (benchmark * 1.5),
    )
    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)

    if random.random() < rate:
        pct = logic.rf(
            float(logic.cfg_get(cfg, "negotiate_raise_min_rate", 0.05)),
            float(logic.cfg_get(cfg, "negotiate_raise_max_rate", 0.15)),
        )
        old_salary = float(p["salary"])
        # 走 apply_raise：同时累进 salary_bonus，晋升/跳槽重算时才不会抹掉这次涨薪
        logic.apply_raise(p, pct)
        p["mind"] = round(float(p["mind"]) + 6, 1)
        gain = round(p["salary"] - old_salary, 0)
        logic.clamp_status(p)
        await asyncio.to_thread(db.save_player, p)
        name = logic.name_of(p, uid)
        await asyncio.to_thread(
            db.add_event,
            gid,
            uid,
            _t("kind_raise"),
            _t(
                "event_negotiate_ok",
                {
                    "name": name,
                    "old": logic.fmt_money(old_salary),
                    "new": logic.fmt_money(p["salary"]),
                },
            ),
        )
        line = logic.pick(gd.t("company", "negotiation_ok"))
        return R(
            tmpl="panel",
            data={
                "icon": "📈",
                "title": _title("title_negotiate_ok", "加薪谈判成功！"),
                "accent": "#6fe08c",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_success_rate"),
                        "value": _t("val_success_rate", {"rate": f"{rate * 100:.0f}"}),
                    },
                    {
                        "label": _t("lbl_salary_change"),
                        "value": _t(
                            "val_salary_change",
                            {
                                "old": logic.fmt_money(old_salary),
                                "new": logic.fmt_money(p["salary"]),
                                "gain": logic.fmt_money(gain),
                            },
                        ),
                    },
                    {
                        "label": _t("lbl_mind"),
                        "value": _t("val_mind_up6", {"mind": f"{p['mind']}"}),
                    },
                ],
                "foot": _t("foot_negotiate_ok", {"comp": comp["name"]}),
            },
            text=_t(
                "text_negotiate_ok",
                {"old": logic.fmt_money(old_salary), "new": logic.fmt_money(p["salary"])},
            ),
        )

    p["mind"] = round(float(p["mind"]) - 8, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("company", "negotiation_fail"))
    return R(
        tmpl="panel",
        data={
            "icon": "🥾",
            "title": _title("title_negotiate_fail", "谈判失败"),
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_success_rate"),
                    "value": _t("val_success_rate", {"rate": f"{rate * 100:.0f}"}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_mind_down8", {"mind": f"{p['mind']}"}),
                },
                {"label": _t("lbl_advice"), "value": _t("val_negotiate_advice")},
            ],
        },
        text=_t("text_negotiate_fail", {"rate": f"{rate * 100:.0f}", "line": line}),
    )


async def my_company(db, gid, uid, nickname, cfg):
    """查看当前雇主公司详情。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    n_comp = len(gd.companies())
    if int(p["company"]) == -1:
        return R(
            tmpl="panel",
            data={
                "icon": "🧳",
                "title": _title("title_unemployed", "你目前处于失业状态"),
                "accent": "#fc6262",
                "lines": [
                    _t("unemployed_line1", {"n_comp": n_comp}),
                    _t("unemployed_line2"),
                ],
                "foot": _t("foot_unemployed"),
            },
            text=_t("unemployed_text", {"n_comp": n_comp}),
        )

    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    pos = gd.position(int(p["lvl"]))
    pos_list = gd.positions()
    nxt = pos_list[int(p["lvl"]) + 1] if int(p["lvl"]) + 1 < len(pos_list) else None
    today = logic.today_str()
    perks = comp.get("perks") or []
    today_perk = random.Random(f"{today}-{comp['id']}").choice(perks) if perks else _t("perk_none")
    checked = p["work_day"] == today
    commute = gd.commute_mode(str(p.get("commute") or ""))
    mode, c_cost, late_r = (
        str(commute["name"]),
        float(commute["cost"]),
        float(commute["late_rate"]),
    )

    lines = [
        _t("comp_desc", {"desc": comp["desc"]}),
        _t("comp_perk", {"perk": today_perk}),
        (
            _t("today_checked")
            if checked
            else _t(
                "today_not_checked",
                {
                    "pay": logic.fmt_money(
                        logic.daily_pay(float(p["salary"]), 1.0, int(p["attend_streak"]), cfg=cfg)
                    )
                },
            )
        ),
    ]
    blocks = [
        {
            "label": _t("lbl_company"),
            "value": _t("val_company", {"name": comp["name"], "tag": comp["tag"]}),
        },
        {
            "label": _t("lbl_your_position"),
            "value": _t(
                "val_your_position",
                {"title": pos["title"], "salary": logic.fmt_money(p["salary"])},
            ),
        },
        {
            "label": _t("lbl_intensity"),
            "value": _t(
                "val_intensity",
                {
                    "hp": f"{round(comp['intensity'] * float(logic.cfg_get(cfg, 'work_intensity_health_factor', 0.4)), 1)}"
                },
            ),
        },
        {
            "label": _t("lbl_layoff_risk"),
            "value": _t("val_layoff_risk", {"risk": f"{comp['risk'] * 100:.1f}"}),
        },
        {
            "label": _t("lbl_attend_streak"),
            "value": _t("val_attend_streak_bonus", {"days": p["attend_streak"]}),
        },
        {
            "label": _t("lbl_commute"),
            "value": _t(
                "val_commute_detail",
                {
                    "mode": mode,
                    "cost": logic.fmt_money(c_cost),
                    "late": int(late_r * 100),
                },
            ),
        },
    ]
    if nxt:
        blocks.append(
            {
                "label": _t("lbl_promote_target"),
                "value": _t(
                    "val_promote_target",
                    {
                        "title": nxt["title"],
                        "need": nxt["need"],
                        "cost": logic.fmt_money(nxt["cost"]),
                    },
                ),
            }
        )
    return R(
        tmpl="panel",
        data={
            "icon": "🏢",
            "title": f"{_title('title_my_company', '我的公司')} · {comp['name']}",
            "accent": "#7fd1ff",
            "lines": lines,
            "blocks": blocks,
            "foot": _t("foot_my_company"),
        },
        text=_t(
            "text_employed",
            {
                "comp": comp["name"],
                "pos": pos["title"],
                "salary": logic.fmt_money(p["salary"]),
                "check_status": _t("checked_mark") if checked else _t("not_checked_mark"),
            },
        ),
    )
