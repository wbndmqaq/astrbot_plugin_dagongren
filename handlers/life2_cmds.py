"""扩展生活指令路由：开会、带饭、回消息、抢会议室、吃饭、帮领导、峰会、宠物、考证、旅游。"""

import re

from ..core import gamedata as gd
from ..core import life2
from ..core.career_common import tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 life2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life2", key, variables)


async def meeting(ctx, event):
    gid = gid_of(event)
    return await life2.meeting(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def bring_food(ctx, event):
    gid = gid_of(event)
    me = str(event.get_sender_id())
    target = next((c for c in ctx.ats(event) if c != me), "")
    if not target:
        return R(err=_t("bring_at_target"))
    return await life2.bring_food(
        ctx.db, gid, me, target, await ctx.anick(event), ctx.config, await ctx.anick(event, target)
    )


async def reply_msg(ctx, event):
    gid = gid_of(event)
    return await life2.reply_msg(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def meeting_room(ctx, event):
    gid = gid_of(event)
    return await life2.meeting_room(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def eat_with(ctx, event):
    gid = gid_of(event)
    me = str(event.get_sender_id())
    target = next((c for c in ctx.ats(event) if c != me), "")
    if not target:
        return R(err=_t("eat_at_target"))
    return await life2.eat_with(
        ctx.db, gid, me, target, await ctx.anick(event), ctx.config, await ctx.anick(event, target)
    )


async def boss_task(ctx, event):
    gid = gid_of(event)
    return await life2.boss_task(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def summit(ctx, event):
    gid = gid_of(event)
    return await life2.summit(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def adopt_pet(ctx, event):
    gid = gid_of(event)
    # 宠物种类来自 pets.json，不在这里写死「猫|狗」：加一种宠物只需改 JSON
    # 与本文件的路由别名，不必再改一遍解析正则
    msg = event.message_str or ""
    pet_type = next((x["type"] for x in gd.pets() if x.get("type") and x["type"] in msg), "")
    return await life2.adopt_pet(
        ctx.db,
        gid,
        event.get_sender_id(),
        await ctx.anick(event),
        pet_type,
        ctx.config,
    )


async def pet_interact(ctx, event):
    gid = gid_of(event)
    return await life2.pet_interact(ctx.db, gid, str(event.get_sender_id()), await ctx.anick(event))


async def get_cert(ctx, event):
    gid = gid_of(event)
    # 证书名必须是【可选】捕获组：写成 (\S+) 时正则会为了满足它而让 书? 让位，
    # 于是「#考证书」把自己的后缀「书」当成证书名传下去（本该按"没带参数"处理，
    # 列出可考证书列表）。
    m = re.match(r"^[#＃]考证书?\s*(\S+)?", (event.message_str or "").strip())
    return await life2.get_cert(
        ctx.db,
        gid,
        event.get_sender_id(),
        await ctx.anick(event),
        (m.group(1) or "") if m else "",
        ctx.config,
    )


async def travel(ctx, event):
    gid = gid_of(event)
    return await life2.travel(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


def _pet_adopt_pattern() -> str:
    """领养宠物的路由正则：种类来自 pets.json，加一种宠物只改 JSON。

    注意：本函数与下面的 _pet_action_pattern 在【模块导入期】被 ROUTES 求值一次，
    因此改完 pets.json 需要重载插件才会生效（与加宠物种类同一步骤）。
    """
    types = [str(x["type"]) for x in gd.pets() if x.get("type")]
    alts = "|".join(re.escape(t) for t in types) or "猫|狗"
    return r"^#养(" + alts + r")$"


def _pet_action_pattern() -> str:
    """宠物互动的路由正则：互动指令名（action）与通用别名都来自 pets.json。

    action 来自每条宠物条目，与会话宠物无关的通用别名（如「陪宠物」）来自
    pets.json 的 actions_extra —— 此前那个别名硬编码在本函数里，与本 docstring
    声称的「同样来自 pets.json」不符。
    """
    acts = [str(x["action"]) for x in gd.pets() if x.get("action")]
    acts.extend(gd.pet_action_aliases())
    # 兜底与 _pet_adopt_pattern 一致：alts 为空会编译出 ^#()$，让裸「#」命中
    alts = "|".join(re.escape(a) for a in dict.fromkeys(acts)) or "陪宠物"
    return r"^#(" + alts + r")$"


ROUTES = [
    Route(r"^#开会$", "cmd_meeting", "参加会议，随机事件", meeting),
    Route(r"^#带饭(?:\s+@?\S+)?$", "cmd_bring_food", "帮同事带饭，+人脉", bring_food),
    Route(r"^#(回消息|回工作消息)$", "cmd_reply_msg", "回复工作消息，+经验-精神", reply_msg),
    Route(r"^#抢会议室$", "cmd_meeting_room", "抢会议室，随机结果", meeting_room),
    Route(r"^#和同事吃饭(?:\s+@?\S+)?$", "cmd_eat_with", "和同事吃饭，+双方精神-钱", eat_with),
    Route(r"^#(帮领导做事|帮领导)$", "cmd_boss_task", "帮领导跑腿，有奖励有风险", boss_task),
    Route(r"^#(行业峰会|峰会)$", "cmd_summit", "参加行业峰会，+经验+人脉", summit),
    Route(
        _pet_adopt_pattern(),
        "cmd_adopt_pet",
        "领养宠物（种类见 resources/data/pets.json）",
        adopt_pet,
    ),
    Route(
        _pet_action_pattern(),
        "cmd_pet_interact",
        "和宠物互动恢复精神",
        pet_interact,
    ),
    Route(
        # \s* 而不是 \s+：解析器是 `^[#＃]考证书?\s*(\S+)?`，允许「#考证会计证」这种
        # 不带空格的写法。路由要求必须有空格时这类输入命中零条路由、拿到零反馈
        # ——「发出去毫无反应」比格式错误更难排查。
        r"^#考证(?:书)?(?:\s*\S+)?$",
        "cmd_get_cert",
        "考行业证书（发送「#考证」查看可考列表）",
        get_cert,
    ),
    Route(r"^#(旅游|出去旅游)$", "cmd_travel", "出去旅游，大幅恢复精神", travel),
]
