"""生活线指令路由：吃饭、健身、租房、通勤、午休、团建、购物、买房、工资条。"""

import re

from ..core import life
from .base import Route, gid_of


async def eat(ctx, event):
    gid = gid_of(event)
    # 不在这里枚举吃法名：合法值来自 meals.json，由 life.eat 校验并回列表提示，
    # 否则加一种吃法要同时改 JSON、这里的正则、和 ROUTES 的正则三处
    m = re.search(r"^[#＃](?:吃饭|干饭)\s*(\S+)?", event.message_str or "")
    return await life.eat(
        ctx.db,
        gid,
        event.get_sender_id(),
        await ctx.anick(event),
        (m.group(1) if m and m.group(1) else ""),
        ctx.config,
    )


async def gym(ctx, event):
    gid = gid_of(event)
    return await life.gym(ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config)


async def house_move(ctx, event):
    gid = gid_of(event)
    m = re.search(r"租房\s*(\S+)?", event.message_str or "")
    kw = (m.group(1) if m and m.group(1) else "").replace("#", "").replace("/", "").strip()
    return await life.move_house(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), kw, ctx.config
    )


async def set_commute(ctx, event):
    gid = gid_of(event)
    # 合法通勤方式来自 commute.json，由 life.set_commute 校验并回列表提示
    m = re.search(r"^[#＃]通勤\s*(\S+)?", event.message_str or "")
    return await life.set_commute(
        ctx.db, gid, str(event.get_sender_id()), m.group(1) if m and m.group(1) else ""
    )


async def nap(ctx, event):
    gid = gid_of(event)
    return await life.nap(ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config)


async def stall(ctx, event):
    gid = gid_of(event)
    return await life.stall(ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config)


async def team_building(ctx, event):
    gid = gid_of(event)
    return await life.team_building(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def shopping(ctx, event):
    gid = gid_of(event)
    return await life.shopping(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def buy_house(ctx, event):
    gid = gid_of(event)
    return await life.buy_house(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config
    )


async def payslip(ctx, event):
    gid = gid_of(event)
    return await life.payslip(ctx.db, gid, str(event.get_sender_id()), await ctx.anick(event))


async def shop_page(ctx, event):
    return await life.shop_page()


async def shop_buy(ctx, event):
    gid = gid_of(event)
    m = re.search(r"^[#＃](?:购买|买)\s*(\S+)", event.message_str or "")
    kw = (m.group(1) if m else "").replace("#", "").replace("/", "").strip()
    return await life.shop_buy(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), kw, ctx.config
    )


async def resume(ctx, event):
    gid = gid_of(event)
    return await life.resume(ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.app_id)


async def my_bag(ctx, event):
    gid = gid_of(event)
    return await life.my_bag(ctx.db, gid, event.get_sender_id(), await ctx.anick(event), ctx.config)


async def use_item(ctx, event):
    gid = gid_of(event)
    m = re.search(r"^[#＃]使用\s*(\S+)", event.message_str or "")
    item_name = (m.group(1) if m else "").strip()
    return await life.use_item(
        ctx.db, gid, event.get_sender_id(), await ctx.anick(event), item_name, ctx.config
    )


ROUTES = [
    # 吃法/通勤方式的合法值来自 meals.json / commute.json，路由只负责捕获一个词，
    # 由服务层校验并回可选项提示——正则里再枚举一遍就成了第二份数据源
    Route(r"^#(吃饭|干饭)(\s*\S+)?\s*$", "cmd_eat", "吃饭恢复健康与精神", eat),
    Route(r"^#午休$", "cmd_nap", "工位趴睡20分钟，一天一次", nap),
    Route(r"^#健身$", "cmd_gym", "花钱健身回复健康", gym),
    Route(r"^#租房(\s*\S+)?\s*$", "cmd_house", "查看房源或搬入新住处", house_move),
    Route(
        r"^#通勤(\s*\S+)?\s*$",
        "cmd_commute",
        "查看或切换通勤方式，影响每日打卡",
        set_commute,
    ),
    Route(r"^#团建$", "cmd_teambuild", "参加公司团建，酸甜苦辣随机", team_building),
    Route(r"^#(摆摊|副业)$", "cmd_stall", "出摊赚外快", stall),
    Route(r"^#(购物|剁手)$", "cmd_shopping", "网购剁手，可能薅到羊毛也可能吃土", shopping),
    # 「购买」别名 +「房子」后缀：cmd_buy 的负向前瞻只看到「房」就让路，
    # 这里不接的话「#买房子」「#购买 房」「#买 房」全是零反馈的死输入。
    Route(
        r"^#(?:购买|买)\s*房(?:子)?$",
        "cmd_buy_house",
        "用现金+公积金全款拿下自购小窝",
        buy_house,
    ),
    Route(r"^#(工资条|账单|流水)$", "cmd_payslip", "查看最近收支流水与累计收入", payslip),
    Route(r"^#(商店|便利店|商城)$", "cmd_shop", "查看便利店货架", shop_page),
    # 负向前瞻排除 基金/房/彩票/股票/买入：这些是其它指令的关键词，
    # 否则残缺的「#买入 茅台」（缺金额的股票买单）会被当作便利店购物处理
    # 负向前瞻里必须写「入」而不是「买入」：前瞻位置在 (购买|买) 之后，
    # 「#买入茅台」到这里剩下的是「入茅台」，永远匹配不到「买入」这个分支，
    # 于是残缺的股票买单会被当成便利店购物（正是注释声称要挡的场景）。
    # \s* 让带空格的「#买 基金」「#买 房」也一并让给对应指令。
    Route(
        r"^#(购买|买)(?!\s*(?:基金|房|彩票|股票|入))(?:\s*\S+)?$",
        "cmd_buy",
        "从便利店购买物品",
        shop_buy,
    ),
    Route(r"^#(我的背包|背包|道具包)$", "cmd_bag", "查看自己拥有的职场道具卡", my_bag),
    Route(
        r"^#使用(?:\s*\S+)?$",
        "cmd_use_item",
        "使用背包中的职场道具卡，如：使用 咖啡续命包",
        use_item,
    ),
    Route(r"^#(我的简历|简历|我的状态|状态)$", "cmd_resume", "查看个人简历档案", resume),
]
