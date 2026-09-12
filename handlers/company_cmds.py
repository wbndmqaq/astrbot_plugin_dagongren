"""公司与成长指令路由：加薪谈判、进修、同事录。"""

from ..core import career, life, social
from .base import Route, gid_of


async def negotiate(ctx, event):
    gid = gid_of(event)
    return await career.negotiate_salary(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def train_self(ctx, event):
    gid = gid_of(event)
    return await life.train_self(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def colleagues(ctx, event):
    gid = gid_of(event)
    return await social.market_list(ctx.db, gid, ctx.app_id, ctx.config)


ROUTES = [
    Route(
        r"^#(要求加薪|谈薪|加薪)$", "cmd_negotiate", "和公司谈加薪，成功率看经验与职级", negotiate
    ),
    Route(r"^#(进修|深造)$", "cmd_train_self", "自费进修班，提升职场身价与经验", train_self),
    Route(
        r"^#(人才市场|同事录|同事)$", "cmd_colleagues", "查看全群同事的在职状态与月薪", colleagues
    ),
]
