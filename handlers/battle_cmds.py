"""对抗线指令路由：对线与卷王大赛（均为玩家亲自出战）。"""

from ..core import social
from ..core.career_common import tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 social.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("social", key, variables)


async def duel(ctx, event):
    gid = gid_of(event)
    me = str(event.get_sender_id())
    # @ 目标优先于裸数字，避免「#对线 12345 @B」时数字 12345 抢占目标
    ats = ctx.ats(event)
    candidates = list(ats) + [c for c in ctx.nums(event, ("对线",)) if c not in ats]
    target = next((c for c in candidates if c != me), "")
    if not target:
        return R(err=_t("err_duel_usage"))
    return await social.duel(
        ctx.db, gid, me, target, ctx.config, await ctx.anick(event, target), ctx.app_id
    )


async def rank_show(ctx, event):
    gid = gid_of(event)
    return await social.rank_show(ctx.db, gid, str(event.get_sender_id()))


async def rank_join(ctx, event):
    gid = gid_of(event)
    return await social.rank_join(
        ctx.db, gid, str(event.get_sender_id()), ctx.config, await ctx.anick(event)
    )


ROUTES = [
    Route(
        # {0,2}：handler 里「@ 目标优先于裸数字」的分支需要两个参数才可达，
        # 单参数正则会让「#对线 123 @群友」直接落空、零反馈。
        r"^#对线(?:\s+@?\S+){0,2}$",
        "cmd_duel",
        "与群友来一场职场对线，赢奖金涨身价",
        duel,
    ),
    Route(r"^#(卷王大赛|排位赛)$", "cmd_rank_show", "查看自己的卷王大赛段位与积分", rank_show),
    Route(
        r"^#参加(卷王大赛|排位赛)$", "cmd_rank_join", "亲自出战卷王大赛，冲击传奇卷王", rank_join
    ),
]
