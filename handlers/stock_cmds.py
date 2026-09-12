"""股票交易指令路由：行情、买入、卖出、持仓。"""

import asyncio
import re

from ..core import logic
from ..core.career_common import tt
from ..core.logic import fmt_money as _fmt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 stocks.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("stocks", key, variables)


async def market_overview(ctx, event):
    await ctx.market.settle_if_needed()
    stocks = await asyncio.to_thread(ctx.market.list_stocks, 100)

    up = [s for s in stocks if s["chg"] > 0]
    down = [s for s in stocks if s["chg"] < 0]
    top_up = sorted(up, key=lambda s: -s["chg"])[:5]
    top_down = sorted(down, key=lambda s: s["chg"])[:5]

    rows = [
        {
            "cells": [
                "🔴",
                _t("cell_stock", {"name": s["name"], "code": s["code"]}),
                f"{s['price']}",
                _t("cell_chg_up", {"chg": s["chg"]}),
            ],
            "fail": False,
        }
        for s in top_up
    ]
    rows.extend(
        {
            "cells": [
                "🟢",
                _t("cell_stock", {"name": s["name"], "code": s["code"]}),
                f"{s['price']}",
                _t("cell_chg_down", {"chg": s["chg"]}),
            ],
            "fail": True,
        }
        for s in top_down
    )

    summary = [
        {"label": _t("market_sum_up"), "value": _t("val_stock_count", {"n": len(up)})},
        {"label": _t("market_sum_down"), "value": _t("val_stock_count", {"n": len(down)})},
        {
            "label": _t("market_sum_flat"),
            "value": _t("val_stock_count", {"n": len(stocks) - len(up) - len(down)}),
        },
        {
            "label": _t("market_sum_cap"),
            "value": _t("val_yuan", {"amount": _fmt(sum(s["price"] for s in stocks))}),
        },
    ]
    text_lines = [
        _t("market_text_up", {"up": len(up), "down": len(down)}),
        *[_t("market_text_up_stock", {"name": s["name"], "chg": s["chg"]}) for s in top_up[:3]],
        *[_t("market_text_down", {"name": s["name"], "chg": s["chg"]}) for s in top_down[:3]],
    ]
    return R(
        tmpl="table",
        data={
            "icon": "📈",
            "title": _t("market_title"),
            "accent": "#ffd86f",
            "summary": summary,
            "cols": ["", _t("market_col_name"), _t("market_col_price"), _t("market_col_chg")],
            "rows": rows,
        },
        text="\n".join(text_lines),
    )


async def buy_stock(ctx, event):
    gid = gid_of(event)
    # 命令词允许被空格拆开（#买 股票 茅台 500 / #购买股票 …），与路由正则同源
    m = re.search(r"(?:(?:购买|买)\s*股票|买\s*入)\s+(\S+)\s+(\S+)", event.message_str or "")
    if not m:
        return R(err=_t("buy_fmt_err"))
    me = str(event.get_sender_id())
    key = m.group(1)
    min_amt = int(ctx.c("stock_min_buy_amount", 1))
    amt = logic.parse_int(m.group(2), lo=min_amt)
    if amt is None:
        return R(err=_t("buy_amt_err", {"min": min_amt}))

    p = await asyncio.to_thread(ctx.db.get_player, gid, me, await ctx.anick(event))
    fee_rate = float(ctx.c("stock_fee_rate", 0.005))
    if float(p["cash"]) < amt:
        return R(err=_t("cash_short", {"cash": _fmt(p["cash"])}))

    r = await ctx.market.buy(gid, me, key, float(amt), fee_rate)
    if r == "insufficient":
        return R(err=_t("cash_short", {"cash": _fmt(p["cash"])}))
    if r == "too_many":
        return R(err=_t("too_many", {"n": ctx.c("stock_max_positions", 50)}))
    if r is None:
        return R(err=_t("no_stock", {"key": key}))

    # 扣款已在市场事务内完成，这里只读最新余额用于展示。
    # 流水金额必须与实际扣款一致（amount+fee）：旧实现只记 -amt，
    # 手续费在玩家的流水/工资单里凭空消失，且与卖出侧「记净额」不对称。
    cost = round(float(amt) + float(r["fee"]), 2)
    p = await asyncio.to_thread(ctx.db.get_player, gid, me)
    await asyncio.to_thread(
        ctx.db.add_transaction,
        gid,
        me,
        _t("tx_buy"),
        -cost,
        _t("tx_buy_line", {"name": r["stock"]["name"], "fee": _fmt(r["fee"])}),
    )
    st = r["stock"]
    emoji = "📈" if st["chg"] >= 0 else "📉"
    return R(
        tmpl="panel",
        data={
            "icon": emoji,
            "title": _t("buy_title", {"name": st["name"]}),
            "accent": "#6fe08c" if st["chg"] >= 0 else "#fc6262",
            "blocks": [
                {"label": _t("buy_code"), "value": st["code"]},
                {
                    "label": _t("buy_price"),
                    "value": _t("buy_price_val", {"price": _fmt(st["price"]), "chg": st["chg"]}),
                },
                {"label": _t("buy_amount"), "value": _t("val_yuan", {"amount": _fmt(r["amount"])})},
                {"label": _t("buy_fee"), "value": _t("val_minus_yuan", {"amount": _fmt(r["fee"])})},
                {"label": _t("buy_shares"), "value": _t("val_shares", {"shares": r["shares"]})},
                {
                    "label": _t("buy_cash_left"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
            "foot": _t("buy_foot"),
        },
        text=_t("buy_text", {"name": st["name"], "amount": _fmt(amt), "shares": r["shares"]}),
    )


async def sell_stock(ctx, event):
    gid = gid_of(event)
    m = re.search(
        r"(?:卖出|卖股票)\s+(\S+?)(?:\s+(\d+)\s*%?)?\s*$",
        event.message_str or "",
    )
    if not m:
        return R(err=_t("sell_fmt_err"))
    me = str(event.get_sender_id())
    key = m.group(1)
    # 分离出跟在名称后的数字比例（如「茅台50」意图为 50%）。
    # 纯数字的股票代码（如「000001」）由正则保证不会落进 group(2)，
    # 因此不需要再额外判断「是不是数字代码」。
    ratio = None
    if m.group(2):
        ratio = logic.parse_int(m.group(2), default=None, lo=1, hi=100)
        if ratio is None:
            return R(err=_t("sell_ratio_err"))
    if ratio is None:
        ratio = 100

    fee_rate = float(ctx.c("stock_fee_rate", 0.005))
    pos = await ctx.market.position_of(gid, me, key)
    if not pos:
        # 名称尾部粘着数字（如「茅台50」）几乎必然是用户漏了空格、想写「茅台 50」，
        # 直接报「没有持仓」会让他以为仓位没了。但这条判定必须以【该 key 不是
        # 有效标的】为前提：股票代码里 sh600037 同样是「非数字 + 数字」结尾，
        # 一刀切用 \D\d+$ 会把 34/100 支股票判成格式错误 —— 而 sell_fmt_err 的
        # 示例文案恰恰写的就是「卖股票 sh600037」。所以先查持仓，查不到再看形状。
        if re.search(r"\D\d+$", key):
            return R(err=_t("sell_fmt_err"))
        return R(err=_t("sell_no_pos", {"key": key}))

    r = await ctx.market.sell(gid, me, key, ratio / 100.0, fee_rate)
    if r is None:
        return R(err=_t("sell_fail"))

    # 入账已在市场事务内完成，这里只读最新余额用于展示
    p = await asyncio.to_thread(ctx.db.get_player, gid, me)
    await asyncio.to_thread(
        ctx.db.add_transaction,
        gid,
        me,
        _t("tx_sell"),
        r["income"],
        _t("tx_sell_line", {"name": r["name"], "profit": r["profit"]}),
    )
    icon = "🎉" if r["profit"] > 0 else "😢"
    accent = "#6fe08c" if r["profit"] > 0 else "#fc6262"
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": _t(
                "sell_title_profit" if r["profit"] > 0 else "sell_title_loss", {"name": r["name"]}
            ),
            "accent": accent,
            "blocks": [
                {
                    "label": _t("sell_shares"),
                    "value": _t("val_sell_shares", {"shares": r["shares"], "ratio": ratio}),
                },
                {
                    "label": _t("sell_income"),
                    "value": _t(
                        "sell_income_val", {"income": _fmt(r["income"]), "fee": _fmt(r["fee"])}
                    ),
                },
                {
                    "label": _t("sell_profit"),
                    "value": _t(
                        "val_yuan",
                        {"amount": ("+" if r["profit"] >= 0 else "") + _fmt(r["profit"])},
                    ),
                },
                {
                    "label": _t("sell_cash_left"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
        },
        text=_t(
            "sell_text", {"name": r["name"], "income": _fmt(r["income"]), "profit": r["profit"]}
        ),
    )


async def my_portfolio(ctx, event):
    gid = gid_of(event)
    await ctx.market.settle_if_needed()
    positions = await ctx.market.my_positions(gid, str(event.get_sender_id()))
    if not positions:
        return R(err=_t("no_pos"))
    rows = []
    for i, pos in enumerate(positions[:20], 1):
        rows.append(
            {
                "cells": [
                    _t("cell_position", {"i": i, "name": pos["name"], "code": pos["code"]}),
                    _t("val_shares", {"shares": pos["shares"]}),
                    _t("val_yuan", {"amount": _fmt(pos["market_value"])}),
                ],
                "fail": False,
            }
        )
    return R(
        tmpl="table",
        data={
            "icon": "💼",
            "title": _t("my_title"),
            "accent": "#7fd1ff",
            "summary": [
                {"label": _t("my_sum_count"), "value": f"{len(positions)}"},
                {
                    "label": _t("my_sum_value"),
                    "value": _t(
                        "val_yuan",
                        {"amount": _fmt(sum(p["market_value"] for p in positions))},
                    ),
                },
            ],
            "cols": [_t("my_col_stock"), _t("my_col_shares"), _t("my_col_value")],
            "rows": rows,
        },
        text=_t("my_text")
        + "；".join(
            _t("text_my_item", {"name": p["name"], "value": _fmt(p["market_value"])})
            for p in positions
        ),
    )


async def liquidate_all(ctx, event):
    """一键清仓：把当前全部持仓按 100% 卖出。"""
    gid = gid_of(event)
    me = str(event.get_sender_id())
    fee_rate = float(ctx.c("stock_fee_rate", 0.005))
    # 全部持仓在服务层的单个事务里清算并入账：逐笔调 sell() 会变成
    # N 次结算 + N 次抢全局写锁，清仓期间全插件写操作排队
    results = await ctx.market.sell_all(gid, me, fee_rate)
    if not results:
        return R(err=_t("no_pos"))

    total_income = round(sum(float(r["income"]) for r in results), 2)
    total_profit = round(sum(float(r["profit"]) for r in results), 2)
    sold = len(results)
    lines = [
        _t(
            "lines_liquidate_item",
            {
                "name": r["name"],
                "income": _fmt(r["income"]),
                "profit": f"{r['profit']:+}",
            },
        )
        for r in results
    ]
    await asyncio.to_thread(
        ctx.db.add_transaction,
        gid,
        me,
        _t("tx_sell"),
        total_income,
        _t("tx_liquidate", {"n": sold, "profit": total_profit}),
    )
    p = await asyncio.to_thread(ctx.db.get_player, gid, me)
    return R(
        tmpl="panel",
        data={
            "icon": "🧹",
            "title": _t("liquidate_title"),
            "accent": "#ffd86f" if total_profit >= 0 else "#fc6262",
            "lines": lines[:12],
            "blocks": [
                {"label": _t("liquidate_sold"), "value": _t("val_stock_count", {"n": sold})},
                {
                    "label": _t("liquidate_income"),
                    "value": _t("liquidate_income_val", {"income": _fmt(total_income)}),
                },
                {
                    "label": _t("liquidate_profit"),
                    "value": _t(
                        "val_yuan",
                        {"amount": ("+" if total_profit >= 0 else "") + _fmt(total_profit)},
                    ),
                },
                {
                    "label": _t("buy_cash_left"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
            "foot": _t("liquidate_foot"),
        },
        text=_t(
            "liquidate_text", {"sold": sold, "income": _fmt(total_income), "profit": total_profit}
        ),
    )


ROUTES = [
    Route(
        r"^#(股市|大盘|行情|股市行情)$",
        "cmd_market",
        "查看股市涨跌TOP10与市场概况",
        market_overview,
    ),
    Route(r"^#(我的股票|持仓)$", "cmd_my_portfolio", "查看个人股票持仓与市值", my_portfolio),
    Route(
        # 允许 0~2 个参数：路由比解析器更严的话，「#买入 茅台」（漏了金额）
        # 一个路由都命中不了 —— 用户发出去毫无反应，而 buy_fmt_err 这条
        # 早就写好的格式提示永远走不到。校验交给 handler 里的解析器。
        r"^#(?:(?:购买|买)\s*股票|买\s*入)(?:\s*\S+){0,2}$",
        "cmd_buy_stock",
        "按金额买入指定股票（买股票/买入均可）",
        buy_stock,
        priority=1,
    ),
    Route(r"^#清仓$", "cmd_liquidate_all", "一键清仓，卖出全部持仓", liquidate_all, priority=2),
    Route(
        # (?!基金) 负向前瞻：把「#卖出基金…」让给 finance 的 cmd_fund_sell，
        # 避免股票卖出用 priority 更高的 route 截胡，导致用户想卖基金却走股票逻辑
        # (?!\s*基金) 的 \s* 不能省：只写 (?!基金) 时「#卖出 基金」会落到股票卖出，
        # 被当成一支叫「基金」的股票去查持仓，与买入侧的处理不对称。
        r"^#(?:卖出|卖股票)(?!\s*基金)(?:\s+\S+)?(?:\s+\d+%?)?$",
        "cmd_sell_stock",
        "卖出股票，可带比例如「#卖出 xx 50」",
        sell_stock,
        priority=1,
    ),
]
