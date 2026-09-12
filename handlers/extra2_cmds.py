"""扩展玩法第二批指令路由。"""

from ..core import extra2
from ..core.career_common import tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra2", key, variables)


async def party(ctx, event):
    gid = gid_of(event)
    return await extra2.party_lottery(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def lend(ctx, event):
    gid = gid_of(event)
    me = str(event.get_sender_id())
    ats = ctx.ats(event)
    if ats:
        # 有 @ 时先剔除所有收款人 QQ，避免适配器把 @ 渲染成数字文本后把 QQ 号当金额
        # （多 @ 场景下第二个 @ 的 QQ 也会被当数字扫进 nums，必须一并剔除）
        target, amount = ats[0], ctx.amount_after(event, ("借钱", "借"), ats)
    else:
        nums = ctx.nums(event, ("借钱", "借"))
        if len(nums) >= 2:
            # 无 @ 时必须「ID + 金额」两个数字，否则「#借钱 12345」会把同一个
            # token 既当收款人又当金额，变成给用户 12345 借 12345 元
            target, amount = nums[0], nums[1]
        else:
            return R(err=_t("err_lend_usage"))
    return await extra2.lend_money(
        ctx.db, gid, me, target, amount, ctx.config, await ctx.anick(event, target)
    )


async def advice(ctx, event):
    return await extra2.career_advice()


async def workstation(ctx, event):
    gid = gid_of(event)
    return await extra2.upgrade_workstation(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def ot_meal(ctx, event):
    gid = gid_of(event)
    return await extra2.overtime_meal(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def checkup(ctx, event):
    gid = gid_of(event)
    return await extra2.health_checkup(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


ROUTES = [
    Route(r"^#(年会抽奖|年会)$", "cmd_party", "年会抽奖（每年限一次）", party),
    # 与 #转账 同理：@目标与金额顺序不限，旧写法要求 @ 必须在前
    Route(
        r"^#借钱(?:\s+\S+){0,2}\s*$",
        "cmd_lend",
        "借钱给群友（可能收不回来）",
        lend,
    ),
    Route(r"^#(职场建议|建议)$", "cmd_advice", "随机职场生存建议", advice),
    Route(r"^#(工位升级|升级工位)$", "cmd_workstation", "升级工位提高摸鱼舒适度", workstation),
    Route(r"^#(加班餐|加班饭)$", "cmd_ot_meal", "加班餐补贴（一天一次）", ot_meal),
    Route(r"^#(年度体检|体检)$", "cmd_checkup", "年度体检，可能查出问题", checkup),
]
