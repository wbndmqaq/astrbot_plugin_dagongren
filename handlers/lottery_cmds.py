"""双色球彩票指令路由：买彩票（机选/自选）、我的彩票、开奖结果、奖池。"""

import re

from ..core import lottery
from .base import Route, gid_of


async def buy_lottery(ctx, event):
    gid = gid_of(event)
    # 「#买彩票」= 机选 1 注；「#买彩票 3」= 机选 3 注；「#买彩票 3 7 12 5」= 自选一注

    # 字符类必须与路由里的 [\d.\s,，] 一致（含小数点）：路由放行了「#买彩票 3.5」
    # 就是为了让这里能拿到完整的 token 再回一句格式提示；少了 . 会把它截成 "3"
    # 静默买 3 注扣费 —— 用户意图被无声改写，比报错更糟。
    m = re.search(r"(?:购买|买)\s*彩票\s*([\d.\s，,]+)", event.message_str or "")
    args = m.group(1).strip() if m and m.group(1) else ""
    return await lottery.buy_ticket(
        ctx.db, gid, event.get_sender_id(), args, ctx.config, await ctx.anick(event)
    )


async def my_lottery(ctx, event):
    gid = gid_of(event)
    return await lottery.my_tickets(ctx.db, gid, str(event.get_sender_id()), ctx.config)


async def lottery_draw_result(ctx, event):
    return await lottery.lottery_result(ctx.db)


async def lottery_pool(ctx, event):
    return await lottery.lottery_pool_view(ctx.db, ctx.config)


ROUTES = [
    Route(
        # \s* 而不是 \s+：解析器允许「#买彩票3」这种不带空格的写法，
        # 路由要求必须有空格会让它直接落空（无任何回复）
        r"^#(?:购买|买)\s*彩票(?:\s*[\d.\s,，]*)?$",
        "cmd_buy_lottery",
        "双色球购票：机选「#买彩票 3」/ 自选「#买彩票 3 7 12 5」"
        "（号码池见 resources/data/lottery.json）",
        buy_lottery,
    ),
    Route(r"^#(我的彩票|彩票号码)$", "cmd_my_lottery", "查看本期持有的彩票号码", my_lottery),
    Route(
        r"^#(彩票结果|开奖结果)$",
        "cmd_lottery_result",
        "查看最近一期双色球开奖结果与中奖名单",
        lottery_draw_result,
    ),
    Route(
        r"^#(彩票|彩票奖池|奖池)$",
        "cmd_lottery_pool",
        "查看本期双色球累积奖池与开奖时间",
        lottery_pool,
    ),
]
