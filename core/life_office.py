"""办公室日常：开会、帮带饭、回消息、抢会议室、和同事吃饭、帮领导做事、行业峰会。

由 core/life2.py 按业务域拆分而来。文件名里的 “2” 是【开发批次编号】而不是业务域
（life2.py 一个文件装了办公室／宠物／考证／旅游四块），实体因此改用可预测的域名，
批次命名的 core/life2.py 只留门面转出。

文案表仍沿用历史命名 resources/texts/life2.json：表名一改，tt/gd.s 就取不到文案、
全部回退到代码里的中文默认值，静态漂移扫描（tests/test_audit_round4.py 的
TestTextDefaultsDoNotDrift）会红。改文案只动 JSON，不要动这里的表名。
"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R

# 与 logic.today_str 同义，直接复用，避免同一件事两份实现
_today = logic.today_str


def _t(key: str, variables: dict | None = None) -> str:
    """取 life2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life2", key, variables)


_title = make_title("life2")


async def meeting(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("unemployed"))
    today = _today()
    if p.get("meeting_day") == today:
        return R(err=_t("meeting_duplicate"))
    p["meeting_day"] = today
    ev = logic.pick(gd.t("extra3", "meeting"))
    if not isinstance(ev, dict):
        ev = {"text": _t("meeting_cancel"), "mind": 0, "exp": 0}
    # 走 logic.num_of 而不是 ev.get：JSON 里的 "mind": null 会让 .get 返回 None，
    # 默认值不生效，随后的 float(None) 直接 TypeError（见 num_of 的文档）
    mind_delta = logic.num_of(ev, "mind", 0)
    exp_delta = logic.int_of(ev, "exp", 0)
    p["mind"] = float(p["mind"]) + mind_delta
    p["exp"] = int(p["exp"]) + exp_delta
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "📋",
            "title": _title("title_meeting", "开会"),
            "accent": "#7fd1ff",
            "lines": [ev["text"]],
            "blocks": [
                {
                    "label": _t("lbl_mind"),
                    "value": _t(
                        "val_delta_cur",
                        {"delta": f"{mind_delta:+g}", "cur": p["mind"]},
                    ),
                },
                {
                    "label": _t("lbl_exp"),
                    "value": _t("val_gain_cur", {"gain": exp_delta, "cur": p["exp"]}),
                },
            ],
        },
        text=_t("text_meeting", {"text": ev["text"]}),
    )


async def bring_food(ctx_db, gid, me, target, nickname, cfg, target_name=""):
    cost = float(logic.cfg_get(cfg, "bring_food_cost", 15.0))
    p = await logic.load_player(ctx_db, gid, me, nickname, cfg)
    if float(p["cash"]) < cost:
        return R(err=_t("bring_short", {"cost": logic.fmt_money(cost)}))
    # 对方必须是已入档玩家：load_player 会给陌生 ID 顺手建号。
    # 之前对此已修过一次（P1），本次回归由"加 cfg 参数"时没把守卫一起带过来。
    td = await asyncio.to_thread(ctx_db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("bring_no_target")))
    target = td["uid"]
    if str(target) == str(me):
        return R(err=_t("bring_self"))
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    p["social_pts"] = int(p.get("social_pts") or 0) + 2
    await asyncio.to_thread(ctx_db.save_player, p)
    # 对方精神走原子列更新，防跨用户快照覆盖
    await asyncio.to_thread(ctx_db.add_mind_atomic, gid, str(target), 5.0)
    tname = logic.name_of(td, target_name)
    return R(
        tmpl="panel",
        data={
            "icon": "🍱",
            "title": _title("title_bring_food", "帮带饭成功"),
            "accent": "#6fe08c",
            "lines": [logic.pick(gd.t("extra3", "bring_food"))],
            "blocks": [
                {
                    "label": _t("lbl_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_network"),
                    "value": _t("val_gain_cur", {"gain": 2, "cur": p["social_pts"]}),
                },
                {
                    "label": _t("lbl_target_mind", {"name": tname}),
                    "value": _t("val_plus_num", {"num": 5}),
                },
            ],
        },
        text=_t("text_bring_food", {"name": tname}),
    )


async def reply_msg(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("reply_unemployed"))
    today = _today()
    if p.get("reply_day") == today:
        return R(err=_t("reply_duplicate"))
    p["reply_day"] = today
    gain = logic.ri(2, 5)
    p["exp"] = int(p["exp"]) + gain
    p["mind"] = round(max(0, float(p["mind"]) - 3), 1)
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "📱",
            "title": _title("title_reply_msg", "回复工作消息"),
            "accent": "#7fd1ff",
            "lines": [logic.pick(gd.t("extra3", "reply_msg"))],
            "blocks": [
                {
                    "label": _t("lbl_exp"),
                    "value": _t("val_gain_cur", {"gain": gain, "cur": p["exp"]}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_delta_cur", {"delta": "-3", "cur": p["mind"]}),
                },
            ],
        },
        text=_t("text_reply_msg", {"gain": gain}),
    )


async def meeting_room(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("room_unemployed"))
    today = _today()
    if p.get("room_day") == today:
        return R(err=_t("room_duplicate"))
    p["room_day"] = today
    ev = logic.pick(gd.t("extra3", "meeting_room"))
    # 事件字段一律先归一再用：JSON 由运维手改，缺 exp/mind/text 时直接下标会
    # 抛 KeyError，而那时 room_day 已经写进玩家档案 —— 下次进来只会得到
    # 「今天已经抢过了」，等于永久吞掉一次机会。与同文件的 meeting 同口径。
    if not isinstance(ev, dict):
        ev = {}
    ev_text = str(ev.get("text") or _t("room_fallback"))
    # 走 logic.num_of/int_of 而不是手写 try/float + parse_int：同一份 JSON 的
    # 归一化在 8 处各不相同，是"改一处漏三处"的温床
    ev_exp = logic.int_of(ev, "exp", 0)
    ev_mind = logic.num_of(ev, "mind", 0)
    p["exp"] = int(p["exp"]) + ev_exp
    p["mind"] = round(float(p["mind"]) + ev_mind, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "🏢",
            "title": _title("title_meeting_room", "抢会议室"),
            "accent": "#7fd1ff",
            "lines": [ev_text],
            "blocks": [
                {"label": _t("lbl_exp"), "value": _t("val_plus_num", {"num": ev_exp})},
                {"label": _t("lbl_mind"), "value": f"{ev_mind:+g}"},
            ],
        },
        text=_t("text_meeting_room", {"text": ev_text}),
    )


async def eat_with(ctx_db, gid, me, target, nickname, cfg, target_name=""):
    raw_costs = logic.cfg_get(cfg, "eat_with_costs", [50, 80, 120])
    costs = [float(x) for x in (raw_costs or [50, 80, 120])]
    cost = random.choice(costs or [50.0])
    p = await logic.load_player(ctx_db, gid, me, nickname, cfg)
    # 对方必须是已入档玩家，否则一句"和同事吃饭"就会建一条幽灵玩家
    td = await asyncio.to_thread(ctx_db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("eat_no_target")))
    target = td["uid"]
    if str(target) == str(me):
        return R(err=_t("eat_self"))
    if float(p["cash"]) < cost:
        return R(err=_t("eat_short", {"cost": logic.fmt_money(cost)}))
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    p["mind"] = round(float(p["mind"]) + 10, 1)
    p["social_pts"] = int(p.get("social_pts") or 0) + 3
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    # 对方精神走原子列更新，防跨用户快照覆盖
    await asyncio.to_thread(ctx_db.add_mind_atomic, gid, str(target), 8.0)
    tname = logic.name_of(td, target_name)
    return R(
        tmpl="panel",
        data={
            "icon": "🍜",
            "title": _title("title_eat_with", "和同事吃饭"),
            "accent": "#6fe08c",
            "lines": [logic.pick(gd.t("extra3", "eat_with"))],
            "blocks": [
                {
                    "label": _t("lbl_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_your_mind"),
                    "value": _t("val_gain_cur", {"gain": 10, "cur": p["mind"]}),
                },
                {
                    "label": _t("lbl_target_mind", {"name": tname}),
                    "value": _t("val_plus_num", {"num": 8}),
                },
                {
                    "label": _t("lbl_network"),
                    "value": _t("val_gain_cur", {"gain": 3, "cur": p["social_pts"]}),
                },
            ],
        },
        text=_t("text_eat_with", {"name": tname, "cost": logic.fmt_money(cost)}),
    )


async def boss_task(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("boss_unemployed"))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "boss_task") > 0:
        return R(
            err=_t("boss_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "boss_task"))})
        )
    logic.cd_set(p, "boss_task", float(logic.cfg_get(cfg, "boss_task_cooldown_hours", 12)) * 3600)
    if random.random() < float(logic.cfg_get(cfg, "boss_task_success_rate", 0.65)):
        reward = logic.ri(
            int(logic.cfg_get(cfg, "boss_task_reward_min", 50)),
            int(logic.cfg_get(cfg, "boss_task_reward_max", 200)),
        )
        p["cash"] = round(float(p["cash"]) + reward, 2)
        # 奖励是收入：累计总收入与流水必须成对写入（#工资条 会并排显示两者，
        # 只写一边两个数字永远对不上）
        p["total_earned"] = round(float(p.get("total_earned") or 0) + reward, 2)
        p["exp"] = int(p["exp"]) + logic.ri(3, 8)
        await asyncio.to_thread(ctx_db.save_player, p)
        await asyncio.to_thread(
            ctx_db.add_transaction,
            gid,
            uid,
            _t("kind_boss_task"),
            reward,
            _t("boss_tx_ok_note"),
        )
        line = logic.pick(gd.t("extra3", "boss_task_ok"))
        return R(
            tmpl="panel",
            data={
                "icon": "👔",
                "title": _title("title_boss_ok", "帮领导做事"),
                "accent": "#6fe08c",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_reward"),
                        "value": _t("val_plus_yuan", {"amount": logic.fmt_money(reward)}),
                    }
                ],
            },
            text=_t("text_boss_ok", {"reward": logic.fmt_money(reward), "line": line}),
        )
    cost = logic.ri(
        int(logic.cfg_get(cfg, "boss_task_penalty_min", 30)),
        int(logic.cfg_get(cfg, "boss_task_penalty_max", 100)),
    )
    cash_before = float(p["cash"])
    p["cash"] = round(max(0.0, cash_before - cost), 2)
    p["mind"] = round(float(p["mind"]) - 5, 1)
    await asyncio.to_thread(ctx_db.save_player, p)
    # 失败也要记流水，且记【实际】扣款额：余额不足时 max(0.0, …) 会把损失截断，
    # 直接记名义值会让流水显示 -100 而现金只掉了 30（与 _apply_checkin_event 同口径）
    actual = round(float(p["cash"]) - cash_before, 2)
    if actual:
        await asyncio.to_thread(
            ctx_db.add_transaction,
            gid,
            uid,
            _t("kind_boss_task"),
            actual,
            _t("boss_tx_fail_note"),
        )
    line = logic.pick(gd.t("extra3", "boss_task_fail"))
    return R(
        tmpl="panel",
        data={
            "icon": "😅",
            "title": _title("title_boss_fail", "帮领导做事搞砸了"),
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_loss"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                }
            ],
        },
        text=_t("text_boss_fail", {"line": line, "cost": logic.fmt_money(cost)}),
    )


async def summit(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    cost = float(logic.cfg_get(cfg, "summit_cost", 500.0))
    if float(p["cash"]) < cost:
        return R(err=_t("summit_short", {"cost": logic.fmt_money(cost)}))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "summit") > 0:
        return R(
            err=_t("summit_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "summit"))})
        )
    logic.cd_set(p, "summit", float(logic.cfg_get(cfg, "summit_cooldown_days", 14)) * 86400)
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    exp_gain = logic.ri(
        int(logic.cfg_get(cfg, "summit_exp_min", 10)),
        int(logic.cfg_get(cfg, "summit_exp_max", 25)),
    )
    social_gain = logic.ri(3, 8)
    p["exp"] = int(p["exp"]) + exp_gain
    p["social_pts"] = int(p.get("social_pts") or 0) + social_gain
    p["mind"] = round(float(p["mind"]) + 5, 1)
    await asyncio.to_thread(ctx_db.save_player, p)
    line = logic.pick(gd.t("extra3", "summit"))
    return R(
        tmpl="panel",
        data={
            "icon": "🎤",
            "title": _title("title_summit", "行业峰会"),
            "accent": "#b48cff",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_ticket"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_exp"),
                    "value": _t("val_gain_cur", {"gain": exp_gain, "cur": p["exp"]}),
                },
                {
                    "label": _t("lbl_network"),
                    "value": _t("val_gain_cur", {"gain": social_gain, "cur": p["social_pts"]}),
                },
            ],
        },
        text=_t("text_summit", {"exp": exp_gain, "social": social_gain}),
    )
