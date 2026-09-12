"""理财线指令路由：银行、利息、转账、基金。"""

from ..core import finance
from ..core.career_common import tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    """取 finance.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("finance", key, variables)


async def deposit(ctx, event):
    gid = gid_of(event)
    # arg_token 而不是 nums：nums 在整句里扫数字，「#存款 1,000」会被扫成
    # ['1','000']，取第一个就是静默存入 1 元。整个 token 交给 parse_int 判合法。
    return await finance.deposit(
        ctx.db,
        gid,
        event.get_sender_id(),
        ctx.arg_token(event, ("存款",)) or "0",
        ctx.config,
        await ctx.anick(event),
    )


async def deposit_all(ctx, event):
    gid = gid_of(event)
    return await finance.deposit_all(
        ctx.db, gid, event.get_sender_id(), ctx.config, await ctx.anick(event)
    )


async def withdraw(ctx, event):
    gid = gid_of(event)
    return await finance.withdraw(
        ctx.db,
        gid,
        event.get_sender_id(),
        ctx.arg_token(event, ("取款",)) or "0",
        ctx.config,
        await ctx.anick(event),
    )


async def upgrade_credit(ctx, event):
    gid = gid_of(event)
    max_mode = "一键" in (event.message_str or "")
    return await finance.upgrade_credit(
        ctx.db, gid, event.get_sender_id(), max_mode, ctx.config, await ctx.anick(event)
    )


async def bank_info(ctx, event):
    gid = gid_of(event)
    return await finance.bank_info(
        ctx.db, gid, event.get_sender_id(), ctx.config, await ctx.anick(event)
    )


async def collect_interest(ctx, event):
    gid = gid_of(event)
    return await finance.collect_interest(
        ctx.db, gid, event.get_sender_id(), ctx.config, await ctx.anick(event)
    )


async def transfer(ctx, event):
    gid = gid_of(event)
    ats = ctx.ats(event)
    if not ats:
        return R(err=_t("transfer_need_target"))
    # 有 @ 时先剔除所有收款人 QQ，避免适配器把 @ 渲染成数字文本后金额误取为 QQ 号
    # （多 @ 场景下第二个 @ 的 QQ 也会被当数字扫进 nums，必须一并剔除）
    amount = ctx.amount_after(event, ("转账",), ats)
    return await finance.transfer(
        ctx.db,
        gid,
        str(event.get_sender_id()),
        ats[0],
        amount,
        ctx.config,
        await ctx.anick(event, ats[0]),
    )


async def fund_buy(ctx, event):
    gid = gid_of(event)
    # 指令词有「买 基金 / 购买基金」等写法，逐个剔掉后再取第一个完整 token
    return await finance.fund_buy(
        ctx.db,
        gid,
        event.get_sender_id(),
        ctx.arg_token(event, ("购买", "买", "基金")) or "0",
        ctx.config,
        await ctx.anick(event),
    )


async def fund_sell(ctx, event):
    gid = gid_of(event)
    # 「#卖出基金」= 全部赎回；「#卖出基金 50」= 赎回 50%；
    # 「#卖出基金 50%」= 同上。
    ratio = ""
    rest = (event.message_str or "").split("基金", 1)
    if len(rest) == 2:
        head = rest[1].strip()
        ratio = head.split()[0] if head else ""
    return await finance.fund_sell(
        ctx.db, gid, event.get_sender_id(), ratio, ctx.config, await ctx.anick(event)
    )


ROUTES = [
    # 金额一律写 \S+ 而不是 \d+：「#存款 500.5」「#存款 -500」以前一条路由都不匹配，
    # 用户拿到零反馈；进来后由 logic.parse_int 统一回一句格式提示。
    Route(r"^#存款(?:\s*\S+)?$", "cmd_deposit", "把钱存进银行吃利息", deposit),
    Route(r"^#(一键存款|全部存款)$", "cmd_deposit_all", "全部现金存入银行", deposit_all),
    Route(r"^#取款(?:\s*\S+)?$", "cmd_withdraw", "从银行取款", withdraw),
    Route(
        r"^#(一键升级信用|升级信用)$",
        "cmd_upgrade_credit",
        "升级信用等级提高存款上限",
        upgrade_credit,
    ),
    Route(r"^#(银行信息|我的银行|账户信息)$", "cmd_bank_info", "查看银行账户与基金持仓", bank_info),
    Route(r"^#领取利息$", "cmd_interest", "领取存款利息", collect_interest),
    # 两个参数（@目标、金额）顺序不限：handler 用 ats() 取目标、amount_after() 取金额，
    # 本就与顺序无关。旧写法 `(?:\s+@?\S+)?(?:\s+\d+)?` 要求 @ 必须在前，而适配器把 @
    # 渲染成文本时「#转账 500 @群友」——正是报错提示里给的例子——完全匹配不上，
    # 用户发出去没有任何反应。
    Route(
        r"^#转账(?:\s+\S+){0,2}\s*$",
        "cmd_transfer",
        "向群友转账（收手续费）",
        transfer,
    ),
    # \s* 容忍「#买 基金 500」这种带空格的写法，否则它落不到任何路由
    Route(
        # 「购买」别名与 \s* 都不能省：cmd_buy 的负向前瞻是 (?!\s*(?:基金|房|…))，
        # 它把「#买 基金」「#购买 基金」一并让了出来，这里不接就成了死输入。
        # 金额写 \S+ 而不是 \d+：小数「#买 基金 500.5」也要能进来，由
        # logic.parse_int 回一句格式提示，而不是整条指令没反应。
        r"^#(?:购买|买)\s*基金(?:\s*\S+)?$",
        "cmd_fund_buy",
        "申购基金（每日自动结算涨跌）",
        fund_buy,
    ),
    Route(
        # 「卖股票」也要收：cmd_sell_stock 的 (?!\s*基金) 把它让了出来，
        # 这里不接「#卖股票 基金」就没有任何路由响应。
        # 前瞻不会误伤真标的：stocks.json 里没有名字含「基金」的股票。
        r"^#(?:卖出|卖股票|卖)\s*基金(?:\s*\S+)?$",
        "cmd_fund_sell",
        "赎回基金，可带比例如：卖出基金 50%",
        fund_sell,
    ),
]
