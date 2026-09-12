"""彩票系统：游戏化双色球（累积奖池）。

规则：
- 每注号码 = 红球 16 选 3（不计顺序）+ 蓝球 8 选 1；
- 票价与每期每人限购由配置决定，购票款 100% 进入当期奖池（资金零和，不通胀）；
- 每日 lottery_draw_hour 点自动开奖（由 main._push_loop 触发），开奖号同构；
- 奖级（红球命中数 + 蓝球是否命中）：
    一等奖  3红+蓝  → 奖池 60%（同注平分）
    二等奖  3红     → 奖池 25%（同注平分）
    三等奖  2红+蓝  → 奖池 15%（同注平分）
  无人命中的份额自动滚存下一期，头奖越滚越大。
- 购票支持自选（`买彩票 3 7 12 5`，前 3 位红球 + 末 1 位蓝球）与机选（`买彩票 [张数]`）。
"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 lottery.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("lottery", key, variables)


_title = make_title("lottery")

# 玩法数值与奖级结构都在 resources/data/lottery.json 里；这里的常量只是 JSON
# 缺失/损坏时的兜底（gamedata 逐文件容错，坏掉的那份会退回上一次的值）。
# 名称与规则这两项【展示文案】不在这里硬编码第二份：它们走 texts/lottery.json
# （WebUI 可改），见 _fallback_tier_text。这里只留结构性字段。
FALLBACK_RULES = {
    "red_max": 16,
    "blue_max": 8,
    "red_pick": 3,
    "tiers": [
        {
            "id": "jackpot",
            "red_hit": 3,
            "blue_hit": True,
            "pct_key": "lottery_jackpot_pct",
            "pct_default": 0.6,
        },
        {
            "id": "second",
            "red_hit": 3,
            "blue_hit": False,
            "pct_key": "lottery_second_pct",
            "pct_default": 0.25,
        },
        {
            "id": "third",
            "red_hit": 2,
            "blue_hit": True,
            "pct_key": "lottery_third_pct",
            "pct_default": 0.15,
        },
    ],
}


def _fallback_tier_text(tid: str) -> dict:
    """兜底奖级的展示文案 {name, rule}：取自 texts/lottery.json。

    为什么不写成模块级常量：那会在【导入期】就调用 gd.s → 触发 gd.load_all()
    同步读盘（40+ 个 JSON），把「导入模块」变成磁盘 IO，也破坏了 main 里
    「首次在线程里预热」的设计。这里只在真的需要兜底（data/lottery.json 缺
    tiers）时才算一次。

    三个奖级的键/默认值写成显式字面量而不是 f-string 拼键：tests/test_audit_round4
    的静态漂移测试按 (表名, 键, 默认值) 三元组扫描，拼出来的键它看不见，
    漂移就会静默漏过。默认值本身是 gd.s 的崩溃安全网（连 texts 都读不到时用）。
    """
    if tid == "jackpot":
        return {
            "name": gd.s("lottery", "fallback_tier_name_jackpot", "一等奖"),
            "rule": gd.s("lottery", "fallback_tier_rule_jackpot", "3红+蓝"),
        }
    if tid == "second":
        return {
            "name": gd.s("lottery", "fallback_tier_name_second", "二等奖"),
            "rule": gd.s("lottery", "fallback_tier_rule_second", "3红"),
        }
    if tid == "third":
        return {
            "name": gd.s("lottery", "fallback_tier_name_third", "三等奖"),
            "rule": gd.s("lottery", "fallback_tier_rule_third", "2红+蓝"),
        }
    return {"name": str(tid), "rule": ""}


def _fallback_tiers() -> list[dict]:
    """兜底奖级表：结构来自 FALLBACK_RULES，展示文案来自 texts/lottery.json。"""
    out = []
    for tier in FALLBACK_RULES["tiers"]:
        t = dict(tier)
        t.update(_fallback_tier_text(str(t.get("id") or "")))
        out.append(t)
    return out


def rules() -> dict:
    """当前玩法配置（红蓝球池、每注红球个数、奖级表）。"""
    r = gd.lottery_rules()
    return r if r.get("tiers") else {**FALLBACK_RULES, "tiers": _fallback_tiers()}


def _pool() -> tuple[int, int, int]:
    r = rules()
    return (
        max(2, int(r.get("red_max") or 16)),
        max(1, int(r.get("blue_max") or 8)),
        max(1, int(r.get("red_pick") or 3)),
    )


def tiers() -> list[dict]:
    return list(rules().get("tiers") or _fallback_tiers())


def tier_names() -> dict[str, str]:
    return {str(t["id"]): str(t.get("name") or t["id"]) for t in tiers()}


def _tier_rule_template() -> str:
    """奖级说明模板「{name} {rule}」，分隔符 rule_sep，均在 lottery.json 里。"""
    return gd.s("lottery", "tier_rule", "{name} {rule}")


def tier_rule_text() -> str:
    """「一等奖 3红+蓝 · 二等奖 3红 · 三等奖 2红+蓝」这类奖级说明。

    文案（分隔符、单级模板）走 lottery.json，缺键回退默认值。
    """
    tpl = _tier_rule_template()
    sep = gd.s("lottery", "rule_sep", " · ")
    return sep.join(
        logic.fill(tpl, {"name": t.get("name") or t["id"], "rule": t.get("rule") or ""}).strip()
        for t in tiers()
    )


def _tier_pcts(cfg) -> dict[str, float]:
    """各奖级的奖池占比：JSON 只存配置键 + 默认值，实际数值由插件配置决定。"""
    out = {}
    for t in tiers():
        key = t.get("pct_key")
        default = float(t.get("pct_default") or 0.0)
        out[str(t["id"])] = float(logic.cfg_get(cfg, key, default)) if key and cfg else default
    return out


def fmt_number(red: list, blue: int) -> str:
    """规范化存储格式：'03,07,12|05'（红球升序 | 蓝球）。"""
    return ",".join(f"{n:02d}" for n in sorted(red)) + f"|{blue:02d}"


def parse_number(s: str):
    """'03,07,12|05' -> (red_set, blue)；非法返回 None。"""
    red_max, blue_max, red_pick = _pool()
    try:
        red_part, blue_part = str(s).split("|")
        red = {int(x) for x in red_part.split(",")}
        blue = int(blue_part)
        if len(red) != red_pick or not (1 <= blue <= blue_max):
            return None
        if any(not (1 <= r <= red_max) for r in red):
            return None
        return red, blue
    except (ValueError, AttributeError):
        return None


def random_number() -> str:
    red_max, blue_max, red_pick = _pool()
    return fmt_number(random.sample(range(1, red_max + 1), red_pick), random.randint(1, blue_max))


def make_judge(draw_number: str, cfg=None):
    """生成绑定开奖号的判奖函数，供 db.lottery_settle 调用。"""
    parsed = parse_number(draw_number)
    if parsed is None:
        raise ValueError(f"bad draw number {draw_number}")
    draw_red, draw_blue = parsed
    pcts = _tier_pcts(cfg)
    _, _, red_pick = _pool()

    # 判奖规则来自 JSON：red_hit 为负表示「red_pick + red_hit」个（-1 → 少命中一个）
    def _need_red(t) -> int:
        # `or red_pick` 而不是 dict.get 的默认值：JSON 里把 red_hit 写成 null 时
        # 默认值不生效，int(None) 会在开奖途中抛 TypeError 打断整场开奖
        v = int(t.get("red_hit") or red_pick)
        return v if v >= 0 else red_pick + v

    rules_list = [(str(t["id"]), _need_red(t), bool(t.get("blue_hit"))) for t in tiers()]

    def judge(tickets: list, pool: float):
        winners = []
        paid = 0.0
        buckets: dict[str, list] = {}
        for t in tickets:
            tp = parse_number(t["number"])
            if tp is None:
                continue
            t_red, t_blue = tp
            red_hit = len(t_red & draw_red)
            blue_hit = t_blue == draw_blue
            for tid, need_red, need_blue in rules_list:
                if red_hit == need_red and blue_hit == need_blue:
                    buckets.setdefault(tid, []).append(t)
                    break
        for tid, ratio in pcts.items():
            hits = buckets.get(tid) or []
            if not hits:
                continue
            # 向下取整到分：round() 会让同档多人时的合计比 pool*ratio 多出几分钱
            share = int(pool * ratio / len(hits) * 100) / 100
            if share <= 0:
                continue
            for t in hits:
                winners.append(
                    {
                        "tier": tid,
                        "gid": t["gid"],
                        "uid": t["uid"],
                        "name": t["name"],
                        "number": t["number"],
                        "amount": share,
                    }
                )
                paid = round(paid + share, 2)
        return winners, paid

    return judge


def fmt_draw(number: str) -> str:
    """'03,07,12|05' -> '红球 03 07 12 + 蓝球 05'。模板走 lottery.json。"""
    red_part, blue_part = str(number).split("|")
    red = " ".join(red_part.split(","))
    blue = blue_part
    return logic.fill(
        gd.s("lottery", "fmt_draw", "红球 {red} + 蓝球 {blue}"), {"red": red, "blue": blue}
    )


def _label(key: str, default: str) -> str:
    """面板 block 标签（结构文案）从 lottery.json 的 labels 子对象读，缺键回退默认。"""
    labels = gd.load_all().get("texts", {}).get("lottery", {}).get("labels") or {}
    return str(labels.get(key, default))


def _pick_note(tokens: list) -> str:
    """机选/自选标记：文案走 lottery.json。"""
    auto = not tokens or (len(tokens) == 1 and logic.is_num(tokens[0]))
    return gd.s("lottery", "pick_auto", "机选") if auto else gd.s("lottery", "pick_manual", "自选")


def _line_num(n: str) -> str:
    """'03,07,12|05' -> '红 03 07 12 + 蓝 05'（出票/我的彩票列表行）。模板走 lottery.json。"""
    red_part, blue_part = n.split("|")
    return logic.fill(
        gd.s("lottery", "line_num", "红 {red} + 蓝 {blue}"),
        {"red": red_part.replace(",", " "), "blue": blue_part},
    )


async def buy_ticket(db, gid, uid, args_str, cfg, nickname=""):
    """购票：`买彩票` / `买彩票 3`（机选 N 张）/ `买彩票 3 7 12 5`（自选一注）。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    # 票价必须为正：schema 对 lottery_ticket_price 没有下界，填 0 就是免费票
    # （还能中滚存的奖池），填负数会让「扣款」变成 cash - (-100) 的入账。
    price = max(0.01, float(logic.cfg_get(cfg, "lottery_ticket_price", 100)))
    limit = max(1, int(logic.cfg_get(cfg, "lottery_max_tickets", 5)))
    today = logic.today_str()

    # 开奖后禁购（P1 修复）：本期已开奖则拒收，否则票落空、钱白扣、永远无法结算。
    # 用「最近一期开奖日期 == 今天」判断，不依赖服务器时钟，最准确。
    last = await asyncio.to_thread(db.lottery_last_draw)
    if last and last.get("date") == today:
        return R(
            err=_t(
                "already_drawn",
                {"today": today, "number": fmt_draw(last["number"])},
            )
        )

    bought = await asyncio.to_thread(db.lottery_today_count, gid, uid, today)
    remain = limit - bought
    if remain <= 0:
        return R(err=_t("limit_reached", {"limit": limit}))

    tokens = [t for t in str(args_str or "").replace(",", " ").replace("，", " ").split() if t]
    red_max, blue_max, red_pick = _pool()
    numbers: list[str] = []
    if not tokens:
        count = 1
        numbers = [random_number() for _ in range(count)]
    elif len(tokens) == 1 and logic.is_num(tokens[0]):
        # 单数字 = 机选张数（按剩余额度截断，保证每期限购）
        count = max(1, min(int(tokens[0]), remain))
        numbers = [random_number() for _ in range(count)]
    elif len(tokens) == red_pick + 1 and all(logic.is_num(t) for t in tokens):
        # red_pick+1 个数字 = 自选一注（前 red_pick 位红球 + 末 1 位蓝球）
        red = [int(x) for x in tokens[:red_pick]]
        blue = int(tokens[red_pick])
        if len(set(red)) != red_pick or any(not (1 <= r <= red_max) for r in red):
            return R(err=_t("red_invalid", {"red_max": red_max, "red_pick": red_pick}))
        if not (1 <= blue <= blue_max):
            return R(err=_t("blue_invalid", {"blue_max": blue_max}))
        numbers = [fmt_number(red, blue)]
    else:
        return R(
            err=_t(
                "format_error",
                {
                    "limit": limit,
                    "red_pick": red_pick,
                    "red_max": red_max,
                    "blue_max": blue_max,
                },
            )
        )

    count = len(numbers)
    total = round(price * count, 2)
    if float(p["cash"]) < total:
        return R(
            err=_t(
                "cash_short",
                {
                    "count": count,
                    "total": logic.fmt_money(total),
                    "price": logic.fmt_money(price),
                },
            )
        )

    # 限购裁剪 + 扣款 + 出票 + 奖池入金 + 记流水，全在一个事务里完成
    name = logic.name_of(p, uid)
    res = await asyncio.to_thread(
        db.lottery_purchase,
        gid,
        uid,
        name,
        numbers,
        today,
        price,
        limit,
        # 实际出票数要到事务内限购裁剪后才确定，传模板由存储层替换 {count}/{date}
        _t("tx_buy_note"),
        _t("kind_buy_lottery_tx"),
    )
    written = int(res["written"])
    if written <= 0:
        if res["reason"] == "cash":
            return R(
                err=_t(
                    "cash_short",
                    {
                        "count": count,
                        "total": logic.fmt_money(total),
                        "price": logic.fmt_money(price),
                    },
                )
            )
        return R(err=_t("sold_out", {"limit": limit}))
    total = float(res["charged"])
    count = written
    numbers = numbers[:written]
    bought = int(res.get("bought_before", bought))
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_buy_lottery"),
        _t("event_bought", {"name": name, "total": logic.fmt_money(total), "count": count}),
    )
    pool = await asyncio.to_thread(db.lottery_current_pool, today)

    pick_note = _pick_note(tokens)
    return R(
        tmpl="panel",
        data={
            "icon": "🎰",
            "title": _title("title_buy_ticket", "双色球出票成功")
            + logic.fill(
                _title("title_buy_ticket_count", " · {count} 注（{pick_note}）"),
                {"count": count, "pick_note": pick_note},
            ),
            "accent": "#ffd86f",
            "lines": [_line_num(n) for n in numbers],
            "blocks": [
                {
                    "label": _label("cost", "花费"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(total)}),
                },
                {
                    "label": _label("held", "本期持有"),
                    "value": _t("val_held", {"held": bought + count, "limit": limit}),
                },
                {
                    "label": _label("pool", "当前奖池"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(pool)}),
                },
            ],
            "foot": logic.fill(
                gd.s("lottery", "foot_buy_ticket", "每晚自动开奖：{rules}，无人中滚存下期"),
                {"rules": tier_rule_text()},
            ),
        },
        text=_t(
            "buy_text",
            {
                "pick_note": pick_note,
                "count": count,
                "lines": "\n".join(f"{i + 1}. {_line_num(n)}" for i, n in enumerate(numbers)),
                "total": logic.fmt_money(total),
                "pool": logic.fmt_money(pool),
            },
        ),
    )


async def my_tickets(db, gid, uid, cfg):
    today = logic.today_str()
    tickets = await asyncio.to_thread(db.lottery_my_tickets, gid, uid, today)
    if not tickets:
        return R(err=_t("no_tickets"))
    pool = await asyncio.to_thread(db.lottery_current_pool, today)
    limit = max(1, int(logic.cfg_get(cfg, "lottery_max_tickets", 5)))
    return R(
        tmpl="panel",
        data={
            "icon": "🎟️",
            "title": _title("title_my_tickets", "我的双色球 · 本期"),
            "accent": "#7fd1ff",
            "lines": [f"{i + 1}. {_line_num(t['number'])}" for i, t in enumerate(tickets)],
            "blocks": [
                {
                    "label": _label("hold", "持有"),
                    "value": _t("val_held", {"held": len(tickets), "limit": limit}),
                },
                {
                    "label": _label("pool", "当前奖池"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(pool)}),
                },
                {"label": _label("prize_tier", "奖级"), "value": tier_rule_text()},
            ],
        },
        text=_t("my_tickets_text", {"numbers": "；".join(t["number"] for t in tickets)}),
    )


def _tier_lines(
    winners: list,
    names_map: dict,
    *,
    hit_tpl: str,
    miss_tpl: str,
    winner_tpl: str,
    more_tpl: str,
    cap: int,
) -> list[str]:
    """逐奖级中奖名单行：开奖面板（lottery_result）与开奖播报（broadcast_lines）共用。

    两处此前各写了一份几乎相同的循环，且文案分别硬编码——括号全角/半角、
    展示人数上限、「等 N 人」有无前导空格全都不一样，改一处必漏另一处。
    现在差异只剩调用方传入的模板与上限，两边渲染结果与各自原样一致。
    """
    out = []
    for tier, tier_name in names_map.items():
        ws = [w for w in winners if w["tier"] == tier]
        if not ws:
            out.append(logic.fill(miss_tpl, {"tier": tier_name}))
            continue
        names = "、".join(
            logic.fill(winner_tpl, {"name": w["name"], "amount": logic.fmt_money(w["amount"])})
            for w in ws[:cap]
        )
        more = logic.fill(more_tpl, {"n": len(ws)}) if len(ws) > cap else ""
        out.append(logic.fill(hit_tpl, {"tier": tier_name, "names": names, "more": more}))
    return out


async def lottery_result(db):
    last = await asyncio.to_thread(db.lottery_last_draw)
    if not last:
        return R(err=_t("no_draw"))
    winners = last.get("winners") or []
    names_map = tier_names()
    lines = _tier_lines(
        winners,
        names_map,
        hit_tpl=gd.s("lottery", "tier_hit", "{tier}：{names}{more}"),
        miss_tpl=gd.s("lottery", "tier_none", "{tier}：无人命中，滚存奖池"),
        winner_tpl=gd.s("lottery", "tier_winner", "{name}（+{amount}）"),
        more_tpl=gd.s("lottery", "tier_more", "等 {n} 人"),
        cap=5,
    )
    draw_line = logic.fill(
        gd.s("lottery", "draw_line", "开奖号码：{number}（共 {count} 注参与）"),
        {"number": fmt_draw(last["number"]), "count": last.get("ticket_count", 0)},
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🎊",
            "title": f"{_title('title_lottery_result', '双色球开奖')} · {last['date']}",
            "accent": "#ffd86f",
            "lines": [draw_line, *lines],
            "blocks": [
                {
                    "label": _label("pool_period", "本期奖池"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(last["pool"])}),
                },
                {
                    "label": _label("paid_total", "派奖合计"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(last["paid"])}),
                },
                {
                    "label": _label("carry_over", "滚存下期"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(last["carry"])}),
                },
            ],
        },
        text=_t(
            "result_text",
            {
                "date": last["date"],
                "number": fmt_draw(last["number"]),
                "count": last.get("ticket_count", 0),
                "lines": "\n".join(lines),
                "paid": logic.fmt_money(last["paid"]),
                "carry": logic.fmt_money(last["carry"]),
            },
        ),
    )


async def lottery_pool_view(db, cfg):
    today = logic.today_str()
    pool = await asyncio.to_thread(db.lottery_current_pool, today)
    tickets = await asyncio.to_thread(db.lottery_today_all_count, today)
    draw_hour = int(logic.cfg_get(cfg, "lottery_draw_hour", 21))
    price = float(logic.cfg_get(cfg, "lottery_ticket_price", 100))
    red_max, blue_max, red_pick = _pool()
    pcts = _tier_pcts(cfg)
    names_map = tier_names()
    play_text = logic.fill(
        gd.s("lottery", "play_text", "红球 1~{red_max} 选 {red_pick} + 蓝球 1~{blue_max} 选 1"),
        {"red_max": red_max, "red_pick": red_pick, "blue_max": blue_max},
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🏦",
            "title": _title("title_lottery_pool", "一夜暴富梦 · 双色球奖池"),
            "accent": "#ffd86f",
            "blocks": [
                {
                    "label": _label("pool", "当前奖池"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(pool)}),
                },
                {"label": _label("sold", "本期售出"), "value": _t("val_tickets", {"n": tickets})},
                {
                    "label": _label("draw_time", "开奖时间"),
                    "value": logic.fill(
                        gd.s("lottery", "draw_time_tpl", "每天 {hour}:00 自动开奖"),
                        {"hour": draw_hour},
                    ),
                },
                {
                    "label": _label("price", "票价"),
                    "value": _t("val_price_per_ticket", {"amount": logic.fmt_money(price)}),
                },
                {"label": _label("play", "玩法"), "value": play_text},
                {
                    "label": _label("prize_tier", "奖级"),
                    "value": " · ".join(
                        _t(
                            "val_tier_pct",
                            {"name": names_map.get(tid, tid), "pct": f"{pct * 100:.0f}"},
                        )
                        for tid, pct in pcts.items()
                    ),
                },
            ],
            "foot": gd.s(
                "lottery",
                "foot_lottery_pool",
                "彩票收入全部进入奖池，无人中奖自动滚存，头奖越滚越大",
            ),
        },
        text=logic.fill(
            gd.s(
                "lottery",
                "pool_text",
                "🏦 双色球本期奖池 {pool} 元（{tickets} 注），每天 {hour}:00 开奖。{play}",
            ),
            {
                "pool": logic.fmt_money(pool),
                "tickets": tickets,
                "hour": draw_hour,
                "play": play_text,
            },
        ),
    )


def broadcast_lines(result: dict, late: bool = False) -> list[str]:
    """把开奖结算结果格式化为播报文本行（main.py 推送循环用）。

    文案走 resources/texts/extra3.json 的 lottery_broadcast，占位符由 logic.fill 注入。
    """
    tx = gd.t("extra3", "lottery_broadcast")
    tpl = tx[0] if isinstance(tx, list) and tx and isinstance(tx[0], dict) else {}
    names_map = tier_names()
    v = {
        "date": str(result.get("date") or ""),
        "number": fmt_draw(result["number"]),
        "count": str(result.get("ticket_count") or 0),
        "paid": logic.fmt_money(result.get("paid") or 0),
        "carry": logic.fmt_money(result.get("carry") or 0),
    }
    head_key = "header_late" if late else "header"
    # 两条分支的兜底文案必须各给一条：以前共用「（补）开奖」那一句，
    # extra3.json 一旦缺 header 键，正常开奖也会被标成补开奖。
    head_default = (
        gd.s("lottery", "bc_header_late", "🎰 双色球补开奖（{date} 期，机器人当时不在线）")
        if late
        else gd.s("lottery", "bc_header", "🎰 一夜暴富梦双色球开奖啦！")
    )
    lines = [
        logic.fill(tpl.get(head_key) or head_default, v),
        logic.fill(
            tpl.get("number_line")
            or gd.s("lottery", "draw_line", "开奖号码：{number}（共 {count} 注参与）"),
            v,
        ),
    ]
    winners = result.get("winners") or []
    # 名单行与面板共用 _tier_lines：播报侧的括号/上限/「等 N 人」写法与面板不同，
    # 差异只体现在这里传入的模板与 cap 上（半角括号是播报的既有样式）。
    lines.extend(
        _tier_lines(
            winners,
            names_map,
            hit_tpl=tpl.get("tier_hit")
            or gd.s("lottery", "bc_tier_hit", "🎉 {tier}：{names}{more}"),
            miss_tpl=tpl.get("tier_miss")
            or gd.s("lottery", "bc_tier_miss", "{tier}：无人命中，滚存下期"),
            winner_tpl=gd.s("lottery", "bc_tier_winner", "{name}(+{amount})"),
            more_tpl=gd.s("lottery", "bc_tier_more", " 等 {n} 人"),
            cap=6,
        )
    )
    lines.append(
        logic.fill(
            tpl.get("footer")
            or gd.s(
                "lottery", "bc_footer", "本期派奖 {paid} 元，滚存 {carry} 元 —— 头奖越滚越大！"
            ),
            v,
        )
    )
    cta = tpl.get("cta")
    if cta:
        lines.append(logic.fill(cta, v))
    return [x for x in lines if x]
