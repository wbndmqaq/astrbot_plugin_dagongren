"""扩展玩法指令路由。"""

import re

from ..core import extra
from ..core.career_common import tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_social.json 文案并填充占位（缺变量原样保留，见 logic.fill）。

    本文件横跨红包/社交/技能/成就多个域，按消息所属玩法取对应文案表；
    红包与刮刮乐的提示都由 core/extra_redpacket.py 自己返回，这里只剩社交域。
    """
    return tt("extra_social", key, variables)


async def year_bonus(ctx, event):
    gid = gid_of(event)
    return await extra.year_bonus(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def learn_skill(ctx, event):
    gid = gid_of(event)
    # \s* 而不是 \s+：路由已放开不带空格的「#学技能Excel」，解析端要能取到参数
    m = re.search(r"学技能\s*(\S+)", event.message_str or "")
    return await extra.learn_skill(
        ctx.db,
        gid,
        event.get_sender_id(),
        await ctx.anick(event),
        ctx.config,
        m.group(1) if m else "",
    )


async def my_skills(ctx, event):
    gid = gid_of(event)
    return await extra.my_skills(
        ctx.db, gid, str(event.get_sender_id()), await ctx.anick(event), ctx.config
    )


async def social(ctx, event):
    gid = gid_of(event)
    me = str(event.get_sender_id())
    target = next((c for c in ctx.ats(event) if c != me), "")
    if not target:
        return R(err=_t("err_social_usage"))
    return await extra.social_network(
        ctx.db, gid, me, target, await ctx.anick(event), ctx.config, await ctx.anick(event, target)
    )


async def side_up(ctx, event):
    gid = gid_of(event)
    return await extra.side_hustle_upgrade(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def annual_leave(ctx, event):
    gid = gid_of(event)
    return await extra.annual_leave(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def gossip(ctx, event):
    gid = gid_of(event)
    return await extra.gossip(ctx.db, gid, str(event.get_sender_id()), await ctx.anick(event))


async def my_achievements_cmd(ctx, event):
    gid = gid_of(event)
    return await extra.my_achievements(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def set_title_cmd(ctx, event):
    gid = gid_of(event)
    m = re.search(r"^[#＃](?:佩戴称号|佩戴头衔|设置称号)\s*(\S+)", event.message_str or "")
    title_name = (m.group(1) if m else "").strip()
    return await extra.set_title(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), title_name, ctx.config
    )


async def unset_title_cmd(ctx, event):
    gid = gid_of(event)
    return await extra.unset_title(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def send_redpacket_cmd(ctx, event):
    gid = gid_of(event)
    m = re.search(
        r"^[#＃](?:发红包|发群红包|塞红包)\s+(\d+(?:\.\d+)?)\s+(\d+)", event.message_str or ""
    )
    # 不在这里做格式预检：金额/个数解析不出来时 core/extra_redpacket 会返回
    # extra_redpacket.json 的 format_error，此前 handler 里逐字抄了同一句话
    return await extra.send_redpacket(
        ctx.db,
        gid,
        event.get_sender_id(),
        await ctx.anick(event),
        m.group(1) if m else "",
        m.group(2) if m else "",
        ctx.config,
    )


async def claim_redpacket_cmd(ctx, event):
    gid = gid_of(event)
    return await extra.claim_redpacket(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def scratch_lottery_cmd(ctx, event):
    gid = gid_of(event)
    return await extra.scratch_lottery(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


ROUTES = [
    Route(r"^#(年终奖|年度奖金)$", "cmd_year_bonus", "领取年终奖（每年限一次）", year_bonus),
    Route(
        r"^#学技能(?:\s*\S+)?$",
        "cmd_learn_skill",
        "学习技能（发送「#学技能」查看可学列表）",
        learn_skill,
    ),
    Route(r"^#(我的技能|技能列表)$", "cmd_my_skills", "查看已掌握的技能", my_skills),
    Route(r"^#(职场社交|社交)(?:\s+@?\S+)?$", "cmd_social", "请群友喝奶茶建立人脉", social),
    Route(r"^#(副业升级|副业进阶)$", "cmd_side_up", "升级副业等级提高摆摊收益", side_up),
    Route(r"^#(请年假|休年假|年假)$", "cmd_annual_leave", "休年假（不扣钱但断全勤）", annual_leave),
    Route(r"^#(职场八卦|八卦)$", "cmd_gossip", "随机生成一条群内职场八卦", gossip),
    Route(
        r"^#(我的成就|成就|成就列表)$",
        "cmd_my_achievements",
        "查看个人职场成就与解锁进度",
        my_achievements_cmd,
    ),
    Route(
        r"^#(佩戴称号|佩戴头衔|设置称号)(?:\s*\S+)?$",
        "cmd_set_title",
        "佩戴已解锁的成就称号",
        set_title_cmd,
    ),
    Route(
        r"^#(卸下称号|卸下头衔|隐藏称号)$", "cmd_unset_title", "卸下当前佩戴的头衔", unset_title_cmd
    ),
    Route(
        # {0,2} 而不是「要么 0 个要么正好 2 个」：漏写个数的「#发红包 1000」
        # 否则命中不了任何路由，用户看不到 format_error 提示
        r"^#(发红包|发群红包|塞红包)(?:\s+\S+){0,2}$",
        "cmd_send_packet",
        "在群内塞拼手气红包给群友",
        send_redpacket_cmd,
    ),
    Route(
        r"^#(抢红包|领红包|开红包)$",
        "cmd_claim_packet",
        "开抢群内最新拼手气红包",
        claim_redpacket_cmd,
    ),
    Route(
        r"^#(刮刮乐|下班刮刮乐)$", "cmd_scratch", "购买职场刮刮乐（小赌怡情）", scratch_lottery_cmd
    ),
]
