"""social_network 等功能（由 extra.py 拆分）。"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_social.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra_social", key, variables)


_title = make_title("extra_social")


async def social_network(db, gid, me, target, nickname, cfg, target_name=""):
    if str(target) == str(me):
        return R(err=_t("self_social"))
    p = await logic.load_player(db, gid, me, nickname, cfg)
    # 先确认对方已入档：load_player 会给陌生 ID 顺手建号，
    # 于是一次失败的社交也会在库里留下一条幽灵玩家
    td = await asyncio.to_thread(db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("not_in_game_social")))
    target = td["uid"]
    if str(target) == str(me):
        return R(err=_t("self_social"))
    if not logic.is_exempt(cfg, me) and logic.cd_left(p, "social") > 0:
        return R(err=_t("cooldown", {"remaining": logic.fmt_remaining(logic.cd_left(p, "social"))}))
    cost = float(logic.cfg_get(cfg, "social_cost", 20.0))
    if float(p["cash"]) < cost:
        return R(err=_t("cost_short", {"cost": logic.fmt_money(cost)}))
    # 校验通过后才设冷却：否则余额不足也会白白烧掉 4 小时冷却
    logic.cd_set(p, "social", float(logic.cfg_get(cfg, "social_cooldown_hours", 4)) * 3600)
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    tname = logic.name_of(td, target_name)
    myname = logic.name_of(p, me)
    if random.random() < float(logic.cfg_get(cfg, "social_success_rate", 0.65)):
        gain = logic.ri(
            int(logic.cfg_get(cfg, "social_pts_min", 3)),
            int(logic.cfg_get(cfg, "social_pts_max", 8)),
        )
        p["social_pts"] = int(p.get("social_pts") or 0) + gain
        p["mind"] = round(float(p["mind"]) + 5, 1)
        # 文案里有带 {a}/{b}（自己/对方）占位的条目，必须 pick_filled
        line = logic.pick_filled(gd.t("extra", "social_ok"), {"a": myname, "b": tname})
        await asyncio.to_thread(db.save_player, p)
        # 对方现金走原子列更新，防跨用户快照覆盖（add_cash_atomic 已同步他的
        # 累计总收入）；流水也要补一条，否则 #工资条 的两个口径对不上。
        reward = float(logic.cfg_get(cfg, "social_target_reward", 10.0))
        if reward > 0:
            await asyncio.to_thread(db.add_cash_atomic, gid, str(target), reward)
            await asyncio.to_thread(
                db.add_transaction,
                gid,
                str(target),
                _t("kind_social_reward"),
                reward,
                _t("tx_social_reward_note", {"name": myname}),
            )
        await asyncio.to_thread(
            db.add_event,
            gid,
            me,
            _t("kind_social"),
            _t("event", {"nickname": p["nickname"] or me, "target": tname, "gain": gain}),
        )
        return R(
            tmpl="panel",
            data={
                "icon": "🤝",
                "title": _title("title_social_ok", "社交成功"),
                "accent": "#6fe08c",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_tea_money"),
                        "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)})
                        + (
                            _t(
                                "val_target_reward",
                                {"amount": logic.fmt_money(reward)},
                            )
                            if reward > 0
                            else ""
                        ),
                    },
                    {
                        "label": _t("lbl_social_pts"),
                        "value": _t(
                            "val_gain_cur",
                            {"gain": gain, "cur": p["social_pts"]},
                        ),
                    },
                    {
                        "label": _t("lbl_mind"),
                        "value": _t("val_gain_cur", {"gain": 5, "cur": p["mind"]}),
                    },
                ],
                "foot": _t("foot_social"),
            },
            text=_t("text_social_ok", {"name": tname, "gain": gain}),
        )
    p["mind"] = round(max(0, float(p["mind"]) - 3), 1)
    line = logic.pick_filled(gd.t("extra", "social_fail"), {"a": myname, "b": tname})
    await asyncio.to_thread(db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "😅",
            "title": _title("title_social_fail", "社交翻车"),
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_mind_down", {"cur": p["mind"]}),
                }
            ],
        },
        text=_t("text_social_fail", {"line": line}),
    )


async def side_hustle_upgrade(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    lvl = int(p.get("side_lvl") or 1)
    max_lvl = int(logic.cfg_get(cfg, "side_hustle_max_level", 5))
    if lvl >= max_lvl:
        return R(err=_t("side_maxed"))
    cost = lvl * float(logic.cfg_get(cfg, "side_hustle_upgrade_cost_base", 2000.0))
    if float(p["cash"]) < cost:
        return R(
            err=_t(
                "side_upgrade_short", {"cost": logic.fmt_money(cost), "lvl": lvl, "next": lvl + 1}
            )
        )
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    p["side_lvl"] = lvl + 1
    await asyncio.to_thread(db.save_player, p)
    # 称号文案走 data JSON（workstations.side_hustle_titles），运营可改
    titles = gd.side_hustle_titles()
    name = (
        titles[lvl] if 0 <= lvl < len(titles) else _t("val_side_title_fallback", {"lvl": lvl + 1})
    )
    line = logic.pick(gd.t("extra", "side_hustle_up"))
    # 摆摊收益加成百分比与 stall() 的 side_mult 同源，避免硬编码 20%
    per_level = float(logic.cfg_get(cfg, "side_hustle_bonus_per_level", 0.2))
    return R(
        tmpl="panel",
        data={
            "icon": "💼",
            "title": f"{_title('title_side_up', '副业升级')} → Lv.{lvl + 1} {name}",
            "accent": "#ffd86f",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_upgrade_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_cur_level"),
                    "value": _t("val_level_name", {"lvl": lvl + 1, "name": name}),
                },
                {
                    "label": _t("lbl_stall_bonus"),
                    "value": _t("val_pct_plus", {"pct": f"{lvl * per_level * 100:g}"}),
                },
            ],
        },
        text=_t("text_side_up", {"lvl": lvl + 1}),
    )


async def annual_leave(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("annual_unemployed"))
    # 跨年自动重置年假额度
    year = time.strftime("%Y")
    if p.get("annual_year") != year:
        p["annual_year"] = year
        p["annual_leave"] = int(logic.cfg_get(cfg, "annual_leave_days", 3))
    days = int(p.get("annual_leave") or 0)
    if days <= 0:
        return R(err=_t("annual_used"))
    p["annual_leave"] = days - 1
    mind_gain = float(logic.cfg_get(cfg, "annual_leave_mind_gain", 25))
    health_gain = float(logic.cfg_get(cfg, "annual_leave_health_gain", 15))
    p["mind"] = round(min(100, float(p["mind"]) + mind_gain), 1)
    p["health"] = round(min(100, float(p["health"]) + health_gain), 1)
    p["attend_streak"] = 0
    # 年假必须占掉今天的出勤位（与 #请假、#带薪调休 一致）：不写 work_day 的话，
    # 年假拿完 +25 精神/+15 健康（比请假的 +15/+8 还多）后再「#上班」照样能领
    # 全额工资，年假在每个维度上都严格优于请假与调休。
    p["work_day"] = logic.today_str()
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("extra", "annual_leave"))
    return R(
        tmpl="panel",
        data={
            "icon": "🏖️",
            "title": _title("title_annual_leave", "年假开始！"),
            "accent": "#6fe08c",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_annual_left"),
                    "value": _t("val_days", {"days": days - 1}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_gain_cur", {"gain": f"{mind_gain:g}", "cur": p["mind"]}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t(
                        "val_gain_cur",
                        {"gain": f"{health_gain:g}", "cur": p["health"]},
                    ),
                },
                {"label": _t("lbl_note"), "value": _t("val_annual_note")},
            ],
        },
        text=_t("text_annual", {"days": days - 1, "line": line}),
    )


async def gossip(db, gid, uid, nickname):
    # 只需要两个人，不必把整群读进内存（all_players 还会带 3N 条子表查询）
    picked = await asyncio.to_thread(db.random_players, gid, 2)
    if len(picked) < 2:
        return R(err=_t("gossip_few"))
    a, b = picked
    an = logic.name_of(a)
    bn = logic.name_of(b)
    text = logic.pick(gd.t("extra", "gossip_texts"))
    if not text:
        text = _t("gossip_empty")
    gossip = logic.fill(text, {"a": an, "b": bn})
    return R(
        tmpl="panel",
        data={
            "icon": "☕",
            "title": _title("title_gossip", "职场八卦速递"),
            "accent": "#ffd86f",
            "lines": [gossip],
            "foot": _t("foot_gossip"),
        },
        text=_t("text_gossip", {"gossip": gossip}),
    )
