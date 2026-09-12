"""eat 等功能（由 life.py 拆分）。"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import Txs, make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("life_daily", key, variables)


_title = make_title("life_daily")


async def eat(db, gid, uid, nickname, mode, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    meals = gd.meals()
    if not meals:
        return R(err=_t("err_no_meals"))
    mode = (mode or "").strip()
    if not mode:
        # 不带参数时随机挑一个便宜档（前两项），meals.json 只有一项时就用它
        mode = random.choice(list(meals)[:2])
    if mode not in meals:
        return R(
            err=_t(
                "err_eat_mode",
                {"modes": " / ".join(_t("val_eat_mode_item", {"mode": k}) for k in meals)},
            )
        )
    cd = float(logic.cfg_get(cfg, "meal_cooldown_minutes", 30)) * 60
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "meal") > 0:
        return R(err=_t("err_eat_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "meal"))}))
    meal = meals[mode]
    if float(p["cash"]) < meal["cost"]:
        # 先校验余额再扣冷却：钱不够时不能白烧一次冷却
        return R(err=_t("err_eat_cost", {"mode": mode, "cost": meal["cost"]}))
    logic.cd_set(p, "meal", cd)
    p["cash"] = round(max(0.0, float(p["cash"]) - meal["cost"]), 2)
    p["health"] = round(float(p["health"]) + meal["health"], 1)
    p["mind"] = round(float(p["mind"]) + meal["mind"], 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("life", meal.get("key") or "takeout"))
    return R(
        tmpl="panel",
        data={
            "icon": "🍜",
            "title": f"{_title('title_eat', '干饭')} · {mode}",
            "accent": "#ffd86f",
            "lines": [line],
            "blocks": [
                {"label": _t("lbl_cost"), "value": _t("val_minus_yuan", {"amount": meal["cost"]})},
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_gain_cur", {"gain": meal["health"], "cur": p["health"]}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_gain_cur", {"gain": meal["mind"], "cur": p["mind"]}),
                },
                {
                    "label": _t("lbl_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
        },
        text=_t(
            "text_eat",
            {
                "mode": mode,
                "line": line,
                "cost": meal["cost"],
                "health": meal["health"],
                "mind": meal["mind"],
            },
        ),
    )


async def gym(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    cost = float(logic.cfg_get(cfg, "gym_cost", 30.0))
    cd = float(logic.cfg_get(cfg, "gym_cooldown_hours", 2)) * 3600
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "gym") > 0:
        return R(err=_t("err_gym_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "gym"))}))
    if float(p["cash"]) < cost:
        return R(err=_t("err_gym_cost", {"cost": logic.fmt_money(cost)}))
    logic.cd_set(p, "gym", cd)
    lo = int(logic.cfg_get(cfg, "gym_health_min", 8))
    hi = int(logic.cfg_get(cfg, "gym_health_max", 15))
    gain = logic.ri(lo, hi)
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    p["health"] = round(float(p["health"]) + gain, 1)
    p["mind"] = round(float(p["mind"]) + logic.ri(2, 6), 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("life", "gym"))
    return R(
        tmpl="panel",
        data={
            "icon": "🏋️",
            "title": _title("title_gym_ok", "健身完成"),
            "accent": "#6fe08c",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_gain_cur_100", {"gain": gain, "cur": p["health"]}),
                },
                {"label": _t("lbl_mind"), "value": _t("val_status_100", {"cur": p["mind"]})},
            ],
        },
        text=_t("text_gym", {"gain": gain, "cur": p["health"], "line": line}),
    )


async def nap(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_nap_unemployed"))
    now = time.time()
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "nap") > 0:
        return R(err=_t("err_nap_cd"))
    # CD 设置到「下一个 00:00 之后 60s」：过零点意味着新一天自动解锁，
    # 不再出现 23:59 午休 → 30 秒后又合法的小窗口。
    lt = time.localtime(now)
    secs_to_midnight = 86400 - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
    logic.cd_set(p, "nap", secs_to_midnight + 60)
    caught = random.random() < float(logic.cfg_get(cfg, "nap_caught_rate", 0.15))
    if caught:
        p["mind"] = round(float(p["mind"]) - 2, 1)
        line = logic.pick(gd.t("life", "nap_caught"))
        title, accent, icon = _title("title_nap_caught", "午休翻车"), "#fc6262", "😳"
        blocks = [{"label": _t("lbl_mind"), "value": _t("val_nap_mind_down", {"cur": p["mind"]})}]
    else:
        gain = logic.ri(8, 12)
        p["mind"] = round(float(p["mind"]) + gain, 1)
        p["health"] = round(float(p["health"]) + 1, 1)
        line = logic.pick(gd.t("life", "nap_ok"))
        title, accent, icon = _title("title_nap_ok", "午休完毕"), "#6fe08c", "😴"
        blocks = [
            {
                "label": _t("lbl_mind"),
                "value": _t("val_gain_cur", {"gain": gain, "cur": p["mind"]}),
            },
            {"label": _t("lbl_health"), "value": _t("val_nap_health_up")},
        ]
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": title,
            "accent": accent,
            "lines": [line],
            "blocks": blocks,
        },
        text=_t("text_nap", {"title": title, "line": line}),
    )


async def stall(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    cd = float(logic.cfg_get(cfg, "stall_cooldown_hours", 6)) * 3600
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "stall") > 0:
        return R(
            err=_t("err_stall_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "stall"))})
        )
    logic.cd_set(p, "stall", cd)
    if random.random() < float(logic.cfg_get(cfg, "stall_fail_rate", 0.2)):
        p["mind"] = round(float(p["mind"]) - 5, 1)
        logic.clamp_status(p)
        await asyncio.to_thread(db.save_player, p)
        line = logic.pick(gd.t("life", "stall_fail"))
        return R(
            tmpl="panel",
            data={
                "icon": "🌧️",
                "title": _title("title_stall_fail", "出师不利"),
                "accent": "#fc6262",
                "lines": [line],
                "blocks": [{"label": _t("lbl_side_income"), "value": _t("val_zero_yuan")}],
            },
            text=_t("text_stall_fail", {"line": line}),
        )
    per_level = float(logic.cfg_get(cfg, "side_hustle_bonus_per_level", 0.2))
    side_mult = 1 + (int(p.get("side_lvl") or 1) - 1) * per_level
    lo = int(logic.cfg_get(cfg, "stall_income_min", 10))
    hi = int(logic.cfg_get(cfg, "stall_income_max", 60))
    exp_bonus = float(logic.cfg_get(cfg, "stall_exp_bonus_per_100", 5))
    income = round((logic.ri(lo, hi) + int(p["exp"]) // 100 * exp_bonus) * side_mult, 2)
    p["cash"] = round(float(p["cash"]) + income, 2)
    # 摆摊收入是收入：累计总收入与流水必须成对写入（#工资条 会并排显示两者）
    p["total_earned"] = round(float(p.get("total_earned") or 0) + income, 2)
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_stall"), income, _t("stall_tx_note")
    )
    line = logic.pick(gd.t("life", "stall_income"))
    lvl_note = (
        _t(
            "line_side_lvl_note",
            {"lvl": int(p.get("side_lvl") or 1), "mult": f"{side_mult:.1f}"},
        )
        if side_mult > 1
        else ""
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🛒",
            "title": _title("title_stall_ok", "副业创收"),
            "accent": "#ffd86f",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_side_income_got"),
                    "value": _t("val_plus_yuan", {"amount": logic.fmt_money(income)}),
                },
                {
                    "label": _t("lbl_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
        },
        text=_t(
            "text_stall_ok",
            {"income": logic.fmt_money(income), "note": lvl_note, "line": line},
        ),
    )


async def shopping(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "shopping") > 0:
        return R(
            err=_t(
                "err_shopping_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "shopping"))}
            )
        )

    budgets = list(logic.cfg_get(cfg, "shopping_budgets", [99, 299, 999]) or [99, 299, 999])
    weights = list(logic.cfg_get(cfg, "shopping_weights", [60, 30, 10]) or [60, 30, 10])
    # 配置写歪时退回等权，避免 random.choices 抛异常后变成永久兜底。
    # 只校验长度是不够的：schema 对元素没有下界，[0,0,0] 会让 random.choices 抛
    # "Total of weights must be greater than zero"，把 #购物 整条指令打成异常
    # ——而「把某一档权重调到 0」是完全合理的运维意图。
    if (
        len(weights) != len(budgets)
        or not all(
            isinstance(w, (int, float)) and not isinstance(w, bool) and w >= 0 for w in weights
        )
        or sum(float(w) for w in weights) <= 0
    ):
        weights = [1.0] * len(budgets)
    budget = int(random.choices(budgets, weights=weights)[0])

    # 余额校验：现金不足以支付最高预算则拒绝下单，避免 max(0, cash-pay) 免费购物
    if float(p["cash"]) < budget:
        return R(
            err=_t("err_shopping_short", {"budget": budget, "cash": logic.fmt_money(p["cash"])})
        )

    logic.cd_set(p, "shopping", float(logic.cfg_get(cfg, "shopping_cooldown_hours", 6)) * 3600)
    roll = random.random()
    refund_rate = float(logic.cfg_get(cfg, "shopping_refund_rate", 0.15))
    deal_rate = float(logic.cfg_get(cfg, "shopping_deal_rate", 0.30))
    mind_gain = int(logic.cfg_get(cfg, "shopping_deal_mind", 18))
    # 流水全部先登记、save_player 成功后再落库（见 Txs 的类文档）
    txs = Txs()
    if roll < refund_rate:
        shipping = round(budget * float(logic.cfg_get(cfg, "shopping_shipping_rate", 0.1)), 2)
        p["cash"] = round(max(0.0, float(p["cash"]) - shipping), 2)
        p["mind"] = round(
            float(p["mind"]) + float(logic.cfg_get(cfg, "shopping_refund_mind", 5)), 1
        )
        note = logic.pick_filled(
            gd.t("life", "shopping_refund"), {"shipping": logic.fmt_money(shipping)}
        )
        title, accent = _title("title_shop_refund", "退货小能手"), "#7fd1ff"
        txs.add(_t("kind_shopping_refund"), -shipping, note[:40])
        outcome = "refund"
    elif roll < refund_rate + deal_rate:
        # 两个概率各自独立：schema 里 shopping_deal_rate 写的是「薅羊毛概率」，
        # 直接和 roll 比会让实际概率变成 deal_rate - refund_rate，
        # 且 deal_rate <= refund_rate 时这一档永远不可能命中。
        # 预算过低（<20）时 randint(20, ...) 会抛 ValueError，且"省下"会变负数，
        # 直接按原价购买处理，保证任何配置都不致购物崩溃或负向套利。
        if budget < 20:
            pay = budget
            saved = 0
        else:
            saved = random.randint(20, min(200, budget))
            pay = budget - saved
        p["cash"] = round(max(0.0, float(p["cash"]) - pay), 2)
        p["mind"] = round(float(p["mind"]) + mind_gain, 1)
        note = logic.pick_filled(
            gd.t("life", "shopping_deal"),
            {
                "budget": logic.fmt_money(budget),
                "pay": logic.fmt_money(pay),
                "saved": logic.fmt_money(saved),
            },
        )
        title, accent = _title("title_shop_deal", "薅羊毛成功"), "#6fe08c"
        txs.add(
            _t("kind_shopping"),
            -pay,
            _t("shopping_deal_note", {"saved": logic.fmt_money(saved)}),
        )
        outcome = "deal"
    else:
        p["cash"] = round(max(0.0, float(p["cash"]) - budget), 2)
        p["mind"] = round(
            float(p["mind"])
            + logic.ri(
                int(logic.cfg_get(cfg, "shopping_mind_min", 8)),
                int(logic.cfg_get(cfg, "shopping_mind_max", 16)),
            ),
            1,
        )
        note = logic.pick(gd.t("life", "shopping"))
        title, accent = _title("title_shop_normal", "剁手快乐"), "#ffd86f"
        txs.add(_t("kind_shopping"), -budget, note[:40])
        outcome = "normal"
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await txs.flush(db, gid, uid)
    return R(
        tmpl="panel",
        data={
            "icon": "🛍️",
            "title": title,
            "accent": accent,
            "lines": [note],
            "blocks": [
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
                {"label": _t("lbl_mind"), "value": _t("val_status_100", {"cur": p["mind"]})},
                {
                    "label": _t("lbl_remind"),
                    "value": _t("val_remind_spend")
                    if outcome != "refund"
                    else _t("val_remind_refund"),
                },
            ],
        },
        text=_t("text_shopping", {"title": title, "note": note}),
    )


async def team_building(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_teambuild_unemployed"))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "teambuild") > 0:
        return R(
            err=_t(
                "err_teambuild_cd",
                {"remaining": logic.fmt_remaining(logic.cd_left(p, "teambuild"))},
            )
        )
    logic.cd_set(
        p,
        "teambuild",
        float(logic.cfg_get(cfg, "teambuild_cooldown_days", 7)) * 86400,
    )

    ev = logic.pick(gd.t("life", "teambuild"))
    if not isinstance(ev, dict):
        # 抽到的条目不是 dict（life.json 被改坏成 ["...", ...]）：给一份与正常
        # 事件同构的兜底，让面板与文案照常渲染。下面读字段一律走 logic.num_of，
        # 它对缺失键、显式 null、字符串、NaN 全部回默认值 —— 单靠 .get 兜不住
        # JSON 里的 "health": null（.get 返回 None，float(None) 直接 TypeError）。
        ev = {
            "type": _t("teambuild_cancel_type"),
            "text": _t("teambuild_cancel_text"),
            "cash": 0,
            "health": 0,
            "mind": 0,
            "exp": 1,
        }
    ev_type = str(ev.get("type") or _t("teambuild_default_type"))
    # cash 是【带符号的现金变动】，与 work.json 的 checkin_events 同一约定：
    # 负数=自己垫付，正数=团建抽奖等进账。旧实现取 max(0, cash) 当"垫付额"，
    # 于是 -150 的露营变免费、而 +200 的「抽中三等奖」反过来扣了 200 元。
    delta = int(logic.num_of(ev, "cash", 0))
    cash_before = round(float(p["cash"]), 2)
    p["cash"] = round(max(0.0, cash_before + delta), 2)
    # 记账用实际变动额：现金不足时上面的 max(0.0, …) 会截断垫付
    actual = round(float(p["cash"]) - cash_before, 2)
    txs = Txs()
    if actual > 0:
        # 正向进账必须同时计入「累计总收入」，与其余 19 条收入路径同口径。
        # 这里此前是唯一漏网之处：现金 +200、流水 +200、total_earned 不动，
        # 而 #工资条 把两个数字并排显示，玩家一眼就能看出对不上。
        p["total_earned"] = round(float(p.get("total_earned") or 0) + actual, 2)
    p["health"] = float(p["health"]) + logic.num_of(ev, "health", 0)
    p["mind"] = float(p["mind"]) + logic.num_of(ev, "mind", 0)
    exp_gain = logic.int_of(ev, "exp", 1)
    p["exp"] = int(p["exp"]) + exp_gain
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    if actual:
        # 类型标签同样外置（life_daily.json 的 kind_teambuild_*），
        # 不再写死在代码里
        txs.add(
            _t("kind_teambuild_pay") if actual < 0 else _t("kind_teambuild_gain"),
            actual,
            str(ev.get("text", ""))[:40],
        )
    await txs.flush(db, gid, uid)
    if actual < 0:
        cost_value = _t("teambuild_paid", {"paid": logic.fmt_money(-actual)})
    elif actual > 0:
        cost_value = _t("teambuild_gain", {"gain": logic.fmt_money(actual)})
    else:
        cost_value = _t("teambuild_free")
    blocks = [
        {"label": _t("lbl_personal_cost"), "value": cost_value},
        {"label": _t("lbl_health"), "value": _t("val_status_100", {"cur": p["health"]})},
        {"label": _t("lbl_mind"), "value": _t("val_status_100", {"cur": p["mind"]})},
        {
            "label": _t("lbl_exp"),
            "value": _t("val_gain_cur", {"gain": exp_gain, "cur": p["exp"]}),
        },
    ]
    return R(
        tmpl="panel",
        data={
            "icon": "🏕️",
            "title": f"{_title('title_teambuild', '团建')} · {ev_type}",
            "accent": "#7fd1ff",
            "lines": [str(ev.get("text", ""))],
            "blocks": blocks,
            "foot": _t("foot_teambuild"),
        },
        text=_t("text_teambuild", {"type": ev_type, "text": ev.get("text", "")}),
    )
