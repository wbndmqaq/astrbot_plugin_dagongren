"""对抗与信息线：职场对线、卷王大赛（亲自出战）、同事录。"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 social.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("social", key, variables)


_title = make_title("social")


async def market_list(db, gid, app_id: str = "", cfg=None):
    cap = int(logic.cfg_get(cfg, "market_list_max", 60))
    # 排序与截断都下沉到 SQL：以前是 all_players() 拉全群（含 3N 条子表查询）
    # 再在事件循环上 sort()，500 人的群每次「#同事录」都要全量转换一遍
    players = await asyncio.to_thread(db.market_players, gid, cap)
    if not players:
        return R(err=_t("no_players"))
    total = await asyncio.to_thread(db.count_players, gid)
    names = gd.company_names(await asyncio.to_thread(db.custom_companies_of_group, gid))
    rows = []
    for i, pl in enumerate(players, 1):
        rows.append(
            {
                "rank": i,
                "name": logic.name_of(pl),
                "id": pl["uid"],
                "value": logic.fmt_money(pl["value"]),
                "position": gd.position(int(pl["lvl"]))["title"],
                "company": gd.display_company(pl["company"], names),
                "boss": _t("val_salary_month", {"amount": logic.fmt_money(pl["salary"])}),
                "avatar": logic.avatar_of(pl["uid"], app_id),
            }
        )
    return R(
        tmpl="market",
        data={"rows": rows, "count": total},
        text=_t("text_market")
        + "；".join(f"{r['name']}({r['company']}·{r['position']})" for r in rows[:15]),
    )


async def duel(db, gid, me, target, cfg, target_name="", app_id: str = ""):
    if str(target) == str(me):
        return R(err=_t("self_duel"))
    # 对手必须是已入档玩家：否则可以对着随手编的 ID 建号刷胜场和奖金
    td = await asyncio.to_thread(db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("no_target")))
    target = td["uid"]
    # find_player_any 可能解析到自己的别名（如昵称匹配命中自己），再防一次
    if str(target) == str(me):
        return R(err=_t("self_duel"))
    p = await logic.load_player(db, gid, me, "", cfg)
    fmt = logic.fmt_money
    my_name = logic.name_of(p, me)
    t_name = logic.name_of(td, target_name)

    cd = float(logic.cfg_get(cfg, "duel_cooldown_hours", 2)) * 3600
    if not logic.is_exempt(cfg, me) and logic.cd_left(p, "duel") > 0:
        return R(err=_t("duel_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "duel"))}))
    fee = float(logic.cfg_get(cfg, "duel_entry_fee", 50))
    if float(p["cash"]) < fee:
        return R(err=_t("duel_fee_short", {"fee": logic.fmt_money(fee)}))
    logic.cd_set(p, "duel", cd)

    # 探测【防甩锅护盾】（仅失利时消耗，胜利保留）
    cds = p.setdefault("_cds", {})
    shield = cds.get("shield_active")

    v1, v2 = float(p["value"]), float(td["value"])
    diff = v1 - v2
    weight = float(logic.cfg_get(cfg, "duel_value_weight", 0.5))
    max_adv = float(logic.cfg_get(cfg, "duel_max_advantage", 0.3))
    win_rate = float(logic.cfg_get(cfg, "duel_base_win_rate", 0.5)) + min(
        max_adv, abs(diff) / max(v1, v2, 1) * weight
    ) * (1 if diff > 0 else -1)
    win = random.random() < win_rate

    # 用 logic.fill 而不是 str.format：duel.json 由运维手改，一个多余的 `{`
    # 或写错的占位名会让 str.format 抛 KeyError/ValueError，整条对线指令报异常。
    actions_pool = logic.fill_all(gd.t("duel", "actions"), {"a": my_name, "b": t_name})
    process = random.sample(actions_pool, k=min(len(actions_pool), random.randint(3, 4)))

    # 护盾生效分支：对线失败但持有护盾，免除场地费扣除与身价下跌
    if not win and shield:
        cds.pop("shield_active", None)
        await asyncio.to_thread(db.save_player, p)
        shield_line = logic.pick_filled(gd.t("duel", "shield_duel"), {"target": t_name})
        return R(
            tmpl="panel",
            data={
                "icon": "🛡️",
                "title": _title("title_duel_shield", "对线失利 · 护盾生效！"),
                "accent": "#ffd86f",
                "lines": [shield_line],
                "blocks": [
                    {"label": _t("lbl_shield_status"), "value": _t("val_shield_used")},
                    {"label": _t("lbl_fee_value"), "value": _t("val_fee_exempt")},
                    {"label": _t("lbl_record_note"), "value": _t("val_record_skip")},
                ],
            },
            text=_t("text_duel_shield", {"line": shield_line}),
        )

    # 这批文案里有 6 条带 {a}/{b} 占位（对手名），必须 pick_filled 而不是 pick，
    # 否则用户看到的是裸的「{b}」
    result_line = logic.pick_filled(
        gd.t("duel", "win_lines" if win else "lose_lines"),
        {"a": my_name, "b": t_name},
    )

    reward_rate = float(logic.cfg_get(cfg, "duel_reward_rate", 0.2))
    bonus_rate = float(logic.cfg_get(cfg, "duel_value_bonus_rate", 0.1))
    penalty_rate = float(logic.cfg_get(cfg, "duel_value_penalty_rate", 0.05))
    # 获胜：退还报名费并按报名费比例发放奖金，确保赢家净收益为正
    reward = round(fee * (1 + reward_rate), 2) if win else 0.0
    net = round(reward - fee, 2)
    p["cash"] = round(float(p["cash"]) + net, 2)
    # 净收益为正时才计入累计总收入：获胜拿回的是自己的场地费 + 奖金，只有奖金
    # 部分（= net）是收入；落败的 -fee 是支出，不该动「只增不减」的累计列。
    if net > 0:
        p["total_earned"] = round(float(p.get("total_earned") or 0) + net, 2)
    # 身价是「转移」而不是「凭空产生」：两项都以【败方身价】为基数，且胜方所得
    # 被钳在败方所失之内。原实现胜方按【自己】身价 +10%、败方按自己 -5%，
    # 两人身价相当时每场净注入 5% 身价，胜者还按 ×1.1 复利——而身价是开公司
    # （create_company_min_value）与对线胜率的门槛，通胀会直接击穿这两个设计。
    stake_base = float(td["value"]) if win else float(p["value"])
    v_down = int(stake_base * penalty_rate)
    v_up = min(int(stake_base * bonus_rate), v_down)
    # 我方在 per-user 锁内走 save_player；对方走原子列更新，
    # 防止"读对方快照→全列覆盖"把对方并发改动（存款/打卡等）冲掉
    if win:
        p["value"] = round(float(p["value"]) + v_up, 2)
        p["duel_wins"] = int(p["duel_wins"]) + 1
        await asyncio.to_thread(db.save_player, p)
        await asyncio.to_thread(db.bump_duel_loss, gid, str(target), float(v_down))
    else:
        p["value"] = round(max(20.0, float(p["value"]) - v_down), 2)
        p["duel_losses"] = int(p["duel_losses"]) + 1
        await asyncio.to_thread(db.save_player, p)
        await asyncio.to_thread(db.bump_duel_win, gid, str(target), float(v_up))
    # 场地费与奖金一并进流水（net 为负 = 输掉场地费）。放在 save_player 之后：
    # 写回被资金完整性检查拒绝时不该留下一条对不上的账面流水。
    if net:
        await asyncio.to_thread(
            db.add_transaction,
            gid,
            me,
            _t("kind_duel"),
            net,
            _t("duel_tx_note", {"name": t_name}),
        )
    # 展示值：我方取内存，对方取落库后的真实值（含 MAX(20) 下限）
    td_after = await asyncio.to_thread(db.get_player, gid, str(target))
    winner_value = p["value"] if win else td_after["value"]
    loser_value = td_after["value"] if win else p["value"]
    w_name = my_name if win else t_name
    l_name = t_name if win else my_name
    net_str = ("+" if net >= 0 else "") + fmt(net)
    await asyncio.to_thread(
        db.add_event,
        gid,
        me,
        _t("kind_duel"),
        _t("ev_duel", {"winner": w_name, "loser": l_name, "net": net_str}),
    )
    return R(
        tmpl="duel",
        data={
            "a": {"name": my_name, "avatar": logic.avatar_of(me, app_id)},
            "b": {"name": t_name, "avatar": logic.avatar_of(target, app_id)},
            "process": process,
            "win": win,
            "winner": w_name,
            "result_line": result_line,
            "blocks": [
                {"label": _t("lbl_fee"), "value": _t("val_minus_yuan", {"amount": fmt(fee)})},
                {
                    "label": _t("lbl_bonus"),
                    "value": _t("val_bonus_win", {"amount": fmt(reward)})
                    if win
                    else _t("val_bonus_lose"),
                },
                {
                    "label": _t("lbl_net_income"),
                    "value": _t("val_yuan", {"amount": net_str}),
                },
                {
                    "label": _t("lbl_value_of", {"name": w_name}),
                    "value": _t("val_value_up", {"delta": v_up, "after": fmt(winner_value)}),
                },
                {
                    "label": _t("lbl_value_of", {"name": l_name}),
                    "value": _t("val_value_down", {"delta": v_down, "after": fmt(loser_value)}),
                },
                {
                    "label": _t("lbl_record"),
                    "value": _t(
                        "val_record",
                        {"wins": p["duel_wins"], "losses": p["duel_losses"]},
                    ),
                },
            ],
        },
        text=_t("text_duel", {"winner": w_name, "line": result_line, "net": net_str}),
    )


async def rank_show(db, gid, me):
    # 纯查看命令，不能 get_player 给陌生 ID 顺手建号
    p = await asyncio.to_thread(db.get_player_row, gid, str(me))
    if not p:
        return R(err=logic.no_record(_t("no_record_rank")))
    lines = [
        _t(
            "line_rank_stats",
            {
                "tier": p["rank_tier"],
                "score": p["rank_score"],
                "matches": p["rank_matches"],
            },
        ),
        _t("line_rank_tip"),
    ]
    return R(
        tmpl="panel",
        data={
            "icon": "🏁",
            "title": _title("title_rank_show", "卷王大赛 · 我的战绩"),
            "accent": "#ffd86f",
            "lines": lines,
            "foot": _t("foot_rank_show", {"tiers": logic.tier_desc()}),
        },
        text=lines[0],
    )


async def rank_join(db, gid, me, cfg, nickname=""):
    p = await logic.load_player(db, gid, me, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("rank_no_job"))
    cooldown = int(float(logic.cfg_get(cfg, "rank_cooldown_minutes", 60.0)) * 60)
    if not logic.is_exempt(cfg, me) and logic.cd_left(p, "rank") > 0:
        return R(err=_t("rank_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "rank"))}))

    ev = logic.pick(gd.rank_events())
    # 三个字段一律先归一再用：rankevents.json 由运维手改，条目缺 desc/effect 时
    # 直接下标会在【积分与奖金都已落库之后】抛 KeyError（desc 只用于最后的展示），
    # 玩家状态已经变了却只看到「指令执行异常」。事件名/描述取文案表兜底，
    # 倍率非法时按 1.0（不改变胜负概率）。归一化统一走 logic.num_of。
    if not isinstance(ev, dict):
        ev = {}
    ev_effect = logic.num_of(ev, "effect", 1.0)
    ev_name = str(ev.get("name") or _t("val_rank_event_fallback"))
    ev_desc = str(ev.get("desc") or _t("val_rank_event_fallback_desc"))
    opponent = gd.match_opponent(int(p["rank_score"]))
    base = float(logic.cfg_get(cfg, "rank_base_win_rate", 0.5))
    win = random.random() < base * ev_effect
    diff = logic.elo_change(
        int(p["rank_score"]),
        int(opponent["score"]),
        win,
        k=int(logic.cfg_get(cfg, "rank_elo_k", 32)),
    )
    p["rank_score"] = max(0, int(p["rank_score"]) + diff)
    p["rank_matches"] = int(p["rank_matches"]) + 1
    p["rank_tier"] = logic.tier_of(p["rank_score"])
    # 只对胜利发奖金：此前输了也按积分差发钱，等于人人都有稳定出场费
    reward = int(abs(diff) * float(logic.cfg_get(cfg, "rank_reward_rate", 0.1))) if win else 0
    p["cash"] = round(float(p["cash"]) + reward, 2)
    if reward > 0:
        # 奖金是收入：累计总收入与流水成对写入（#工资条 会并排显示两者）
        p["total_earned"] = round(float(p.get("total_earned") or 0) + reward, 2)
    logic.cd_set(p, "rank", cooldown)
    await asyncio.to_thread(db.save_player, p)
    if reward > 0:
        await asyncio.to_thread(
            db.add_transaction,
            gid,
            me,
            _t("kind_rank_match"),
            reward,
            _t("rank_tx_note", {"opponent": opponent["name"]}),
        )
    name = logic.name_of(p, me)
    diff_str = f"{diff:+d}"
    await asyncio.to_thread(
        db.add_event,
        gid,
        me,
        _t("kind_rank_match"),
        _t(
            "ev_rank",
            {
                "name": name,
                "opponent": opponent["name"],
                "result": _t("ev_rank_win") if win else _t("ev_rank_lose"),
                "diff": diff_str,
                "score": p["rank_score"],
            },
        ),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🏆",
            "title": f"{_title('title_rank_join', '卷王大赛')} · {_title('title_rank_win', '胜利！') if win else _title('title_rank_lose', '惜败...')}",
            "accent": "#ffd86f" if win else "#fc6262",
            "lines": [
                _t("line_rank_event", {"name": ev_name, "desc": ev_desc}),
                _t(
                    "line_rank_vs",
                    {"name": opponent["name"], "effect": opponent["effect"]},
                ),
            ],
            "blocks": [
                {
                    "label": _t("lbl_result"),
                    "value": _t("val_result_win") if win else _t("val_result_lose"),
                },
                {
                    "label": _t("lbl_score_change"),
                    "value": _t(
                        "val_score_change",
                        {"diff": diff_str, "score": p["rank_score"]},
                    ),
                },
                {"label": _t("lbl_tier"), "value": p["rank_tier"]},
                {
                    "label": _t("lbl_appearance_fee"),
                    "value": _t("val_plus_yuan", {"amount": logic.fmt_money(reward)}),
                },
            ],
        },
        text=_t(
            "text_rank_join",
            {
                "opponent": opponent["name"],
                "result": _t("val_win_short") if win else _t("val_lose_short"),
                "diff": diff_str,
                "score": p["rank_score"],
                "reward": logic.fmt_money(reward),
            },
        ),
    )


async def rank_data(db, gid, kind, app_id: str = "", cfg=None):
    # 榜单名/单位/别名的单一来源是 resources/data/rankings.json
    # （handlers/system_cmds.py 的路由正则也由它拼装）
    defs = {str(r["key"]): r for r in gd.rankings()}
    if kind not in defs:
        kind = next(iter(defs)) if defs else "wealth"
    meta = defs.get(kind) or {
        # 只有 rankings.json 整表缺失时才会命中（defs 非空时 kind 已被钳进 defs）：
        # 榜名/单位取文案表，不再逐字复制 rankings.json 的首条定义
        "name": _t("val_rank_fallback_name"),
        "unit": _t("val_unit_yuan"),
    }
    top_n = int(logic.cfg_get(cfg, "ranking_top_n", 10))
    if kind == "level":
        players = await asyncio.to_thread(db.top_level, gid, top_n)
        col = "lvl"
    elif kind == "wealth":
        players = await asyncio.to_thread(db.top_wealth, gid, top_n)
        col = "total"
    else:
        # key 就是 players 表的列名（rankevents/rankings 的约定）：直接按它排序，
        # top_by_column 内部对列名做白名单校验，越界会抛 ValueError。
        # 旧实现把非 level/wealth/exp 一律当成 value，于是往 rankings.json 加一个
        # 「存款榜」时标题换了、排序与数值却还是身价 —— "加一个榜只改这一处"
        # 的承诺在社交层的这条通用路径上是失效的。列名非法（运维写错）才退回 value。
        col = kind
        try:
            players = await asyncio.to_thread(db.top_by_column, gid, col, top_n)
        except ValueError:
            col = "value"
            players = await asyncio.to_thread(db.top_by_column, gid, col, top_n)
    rows = []
    names = (
        gd.company_names(await asyncio.to_thread(db.custom_companies_of_group, gid))
        if kind == "level"
        else {}
    )
    for pl in players:
        if kind == "wealth":
            score_val = logic.fmt_money(pl.get("total", 0))
        elif kind == "level":
            comp_name = gd.display_company(pl["company"], names, jobless="")
            score_val = f"L{pl['lvl']} · {gd.position(int(pl['lvl']))['title']}" + (
                f" @ {comp_name}" if comp_name else ""
            )
        elif kind == "exp":
            score_val = _t("val_exp_points", {"exp": pl["exp"]})
        else:
            score_val = logic.fmt_money(pl.get(col, 0))
        rows.append(
            {
                "rank": int(pl.get("rank", 0)),
                "name": logic.display(pl),
                "id": pl["uid"],
                "score": score_val,
                "avatar": logic.avatar_of(pl["uid"], app_id),
            }
        )
    title = str(meta.get("name") or kind)
    return R(
        tmpl="ranking",
        data={"title": title, "unit": str(meta.get("unit") or ""), "rows": rows},
        text=title
        + "\n"
        + "\n".join(
            _t(
                "val_rank_row",
                {"rank": r["rank"], "name": r["name"], "score": r["score"]},
            )
            for r in rows
        ),
    )
