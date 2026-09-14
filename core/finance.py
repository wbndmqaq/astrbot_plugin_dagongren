"""金融系统服务：银行存取、信用升级、利息、转账、基金。"""

import asyncio
import math
import time

from . import logic
from .career_common import make_title, tt
from .result import R


def _fmt(x):
    return logic.fmt_money(x)


def _t(key: str, variables: dict | None = None) -> str:
    """取 finance.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("finance", key, variables)


_title = make_title("finance")


def _fund_change_note(settle: dict, key: str = "line_fund_change") -> str:
    """基金当日波动提示：涨跌图标 + 百分比格式化只此一处。

    申购/赎回两处用的是同一句（line_fund_change），账户信息面板的副标题措辞不同
    （subtitle_fund_change），所以文案键可传入；此前三处各写了一份
    `emoji = ... if change >= 0 else ...` + f-string，改文案要改三处。
    """
    emoji = "📈" if settle["change"] >= 0 else "📉"
    return _t(key, {"emoji": emoji, "change": f"{settle['change']:+.2f}"})


def _settle_fund(p, cfg=None):
    today = logic.today_str()
    if p["fund_day"] == today or float(p["fund"]) <= 0:
        return None
    change = logic.fund_daily_change(cfg)
    old = float(p["fund"])
    p["fund"] = round(old * (1 + change / 100.0), 2)
    if abs(p["fund"]) < 0.01:
        p["fund"] = 0.0
    p["fund_day"] = today
    return {"change": change, "old": round(old, 2), "new": float(p["fund"])}


def _credit_interest(p, gained: float):
    """利息入账：现金 + 累计总收入 + 推进计息起点。

    #存款/#取款 触发的自动结息与 #收利息 手动领息必须走同一处：此前只有自动结息
    更新 total_earned，手动领息不更新，同一笔利息在富豪榜/成就口径里存在与不存在
    取决于玩家用哪条指令领，两边永远对不上。
    """
    p["cash"] = round(float(p["cash"]) + gained, 2)
    p["total_earned"] = round(float(p.get("total_earned") or 0) + gained, 2)
    p["last_interest"] = logic.now_ts()


async def _record_interest_tx(db, gid, uid, settled: float):
    """自动结息入账写流水（金额 0 时不记）。

    存/取款成功路径上会无条件调用本函数（此时 settled 可能为 0），因此内部
    要自行跳过 0，调用方不必重复判断。文案与 #收利息 手动领息同源，保证
    #工资条 的「累计总收入」与「收支流水」两个口径一致。
    """
    if settled > 0:
        await asyncio.to_thread(
            db.add_transaction, gid, uid, _t("kind_interest"), settled, _t("tx_interest_note")
        )


def _accrue_interest(p, cfg=None) -> float:
    """结清「按旧余额算到现在」的利息并重置计息起点，返回入账金额。

    存/取之前必须先结清：interest_of 是「当前余额 × 距上次领取的小时数」，
    last_interest 只在领息时刷新。不在余额变动时结清的话，把存款空一整天、
    临领息前才存满，就能拿到满 24 小时的利息——资金完全不需要真的锁在银行里。

    不足一小时（gained == 0）时【不】推进起点：否则频繁存取会把不满一小时的
    零头一次次清掉，等于白扔计息时间；这也与 collect_interest 的行为一致。
    """
    last = int(p.get("last_interest") or 0)
    if not last or float(p.get("deposit") or 0) <= 0:
        # 没有计息起点或余额为 0：把起点推到现在（从 0 建仓时开始计时）
        p["last_interest"] = logic.now_ts()
        return 0.0
    rate = float(logic.cfg_get(cfg, "bank_interest_rate_hourly", 0.01))
    max_h = int(logic.cfg_get(cfg, "bank_max_interest_hours", 24))
    gained = logic.interest_of(float(p["deposit"]), last, rate, max_h)
    if gained > 0:
        _credit_interest(p, gained)
    return gained


async def deposit(db, gid, uid, amount, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    amt = logic.parse_int(amount, lo=1)
    if amt is None:
        return R(err=_t("deposit_invalid"))
    # 先结息：利息入的是现金，会影响下面「现金是否够」的判断
    settled = _accrue_interest(p, cfg)
    if amt > float(p["cash"]):
        if settled > 0:  # 结息已发生，即使存不成也要落库
            await asyncio.to_thread(db.save_player, p)
            await _record_interest_tx(db, gid, uid, settled)
        settled_note = _t("settled_note", {"settled": _fmt(settled)}) if settled > 0 else ""
        return R(
            err=_t("deposit_cash_short", {"cash": _fmt(p["cash"]), "settled_note": settled_note})
        )
    space = float(p["bank_limit"]) - float(p["deposit"])
    if amt > space:
        if settled > 0:
            await asyncio.to_thread(db.save_player, p)
            await _record_interest_tx(db, gid, uid, settled)
        return R(err=_t("deposit_limit", {"limit": _fmt(p["bank_limit"]), "space": _fmt(space)}))
    p["cash"] = round(float(p["cash"]) - amt, 2)
    p["deposit"] = round(float(p["deposit"]) + amt, 2)
    await asyncio.to_thread(db.save_player, p)
    await _record_interest_tx(db, gid, uid, settled)
    return R(
        tmpl="panel",
        data={
            "icon": "🏦",
            "title": _title("title_deposit_ok", "存款成功"),
            "accent": "#6fe08c",
            "lines": (
                [_t("line_deposit_settled", {"settled": _fmt(settled)})] if settled > 0 else []
            ),
            "blocks": [
                {"label": _t("lbl_deposit"), "value": _t("val_yuan", {"amount": _fmt(amt)})},
                {
                    "label": _t("lbl_deposit_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
                {
                    "label": _t("lbl_rate_note"),
                    "value": _t(
                        "val_rate_note",
                        {
                            "rate": f"{float(logic.cfg_get(cfg, 'bank_interest_rate_hourly', 0.01)) * 100:.0f}",
                            "hours": int(logic.cfg_get(cfg, "bank_max_interest_hours", 24)),
                        },
                    ),
                },
            ],
            "foot": _t("foot_remind_interest"),
        },
        text=_t("text_deposit_ok", {"amount": _fmt(amt), "deposit": _fmt(p["deposit"])}),
    )


async def deposit_all(db, gid, uid, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    # 先结息再取现金快照：反过来的话，结息加到 cash 上的钱会被后面的
    # p["cash"] = 0 一起抹掉——既没留在现金里也没进存款，直接蒸发
    settled = _accrue_interest(p, cfg)
    amt = round(float(p["cash"]), 2)
    if amt <= 0:
        # 只结了息、没有可存的现金：结息结果仍要落库
        if settled > 0:
            await asyncio.to_thread(db.save_player, p)
            await _record_interest_tx(db, gid, uid, settled)
            return R(
                tmpl="panel",
                data={
                    "icon": "🪙",
                    "title": _title("title_auto_interest", "已自动结息"),
                    "accent": "#7fd1ff",
                    "lines": [_t("line_settled_only", {"settled": _fmt(settled)})],
                    "blocks": [
                        {
                            "label": _t("lbl_deposit_balance"),
                            "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                        },
                        {
                            "label": _t("lbl_cash_balance"),
                            "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                        },
                    ],
                },
                text=_t(
                    "text_auto_interest",
                    {"settled": _fmt(settled), "cash": _fmt(p["cash"])},
                ),
            )
        return R(err=_t("deposit_all_zero"))
    space = float(p["bank_limit"]) - float(p["deposit"])
    if amt > space:
        if settled > 0:  # 结息已发生，必须落库，不能因为存不下就丢掉
            await asyncio.to_thread(db.save_player, p)
            await _record_interest_tx(db, gid, uid, settled)
        return R(
            err=_t(
                "deposit_all_limit",
                {"limit": _fmt(p["bank_limit"]), "space": _fmt(space), "settled": _fmt(settled)},
            )
        )
    p["cash"] = 0.0
    p["deposit"] = round(float(p["deposit"]) + amt, 2)
    await asyncio.to_thread(db.save_player, p)
    await _record_interest_tx(db, gid, uid, settled)
    return R(
        tmpl="panel",
        data={
            "icon": "🏦",
            "title": _title("title_deposit_all", "一键存款成功"),
            "accent": "#6fe08c",
            "lines": (
                [_t("line_deposit_all_settled", {"settled": _fmt(settled)})] if settled > 0 else []
            ),
            "blocks": [
                {"label": _t("lbl_deposit"), "value": _t("val_yuan", {"amount": _fmt(amt)})},
                {
                    "label": _t("lbl_deposit_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                },
                {"label": _t("lbl_cash_balance"), "value": _t("cash_moon")},
            ],
        },
        text=_t("text_deposit_all", {"deposit": _fmt(p["deposit"])}),
    )


async def withdraw(db, gid, uid, amount, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    amt = logic.parse_int(amount, lo=1)
    if amt is None:
        return R(err=_t("withdraw_invalid"))
    if amt > float(p["deposit"]):
        return R(err=_t("withdraw_short", {"deposit": _fmt(p["deposit"])}))
    settled = _accrue_interest(p, cfg)
    p["cash"] = round(float(p["cash"]) + amt, 2)
    p["deposit"] = round(float(p["deposit"]) - amt, 2)
    await asyncio.to_thread(db.save_player, p)
    await _record_interest_tx(db, gid, uid, settled)
    return R(
        tmpl="panel",
        data={
            "icon": "💵",
            "title": _title("title_withdraw_ok", "取款成功"),
            "accent": "#ffd86f",
            "lines": (
                [_t("line_withdraw_settled", {"settled": _fmt(settled)})] if settled > 0 else []
            ),
            "blocks": [
                {"label": _t("lbl_withdraw"), "value": _t("val_yuan", {"amount": _fmt(amt)})},
                {
                    "label": _t("lbl_deposit_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
        },
        text=_t("text_withdraw_ok", {"amount": _fmt(amt)}),
    )


async def upgrade_credit(db, gid, uid, max_mode, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    multi = float(logic.cfg_get(cfg, "bank_upgrade_price_multi", 1.2))
    limit_multi = float(logic.cfg_get(cfg, "bank_limit_increase_multi", 1.25))
    max_times = int(logic.cfg_get(cfg, "credit_upgrade_max_batch", 99)) if max_mode else 1
    spent = 0.0
    upgrades = 0
    # 升级费地板：bank_initial_upgrade_price 的 schema min 是 0.0，而下一级价格是
    # int(price * multi) —— price < 1 时 int() 截断成 0 并永远停在 0
    # （int(0 * 1.2) == 0），循环条件 cash >= price 随即永真：0 元就能连升到
    # credit_upgrade_max_batch（默认 99）级，bank_limit 顺带被乘成天文数字。
    # 入口先把值抬到地板，循环内再保证严格增长，两头都堵住。
    MIN_UPGRADE_PRICE = 1.0
    p["bank_upgrade_price"] = max(MIN_UPGRADE_PRICE, float(p["bank_upgrade_price"]))
    while upgrades < max_times and float(p["cash"]) >= float(p["bank_upgrade_price"]):
        price = float(p["bank_upgrade_price"])
        p["cash"] = round(float(p["cash"]) - price, 2)
        spent = round(spent + price, 2)
        upgrades += 1
        p["bank_level"] = int(p["bank_level"]) + 1
        p["bank_limit"] = round(float(p["bank_limit"]) * limit_multi, 0)
        nxt = float(int(price * multi))
        if nxt <= price:
            # multi <= 1 或价格过小时 int() 截断会让价格原地踏步（价格恒定 =
            # 循环只看现金，一路买到 max_times）。至少 +1 元，保证必然终止。
            nxt = price + 1.0
        p["bank_upgrade_price"] = nxt
    if upgrades == 0:
        return R(
            err=_t(
                "upgrade_short", {"price": _fmt(p["bank_upgrade_price"]), "cash": _fmt(p["cash"])}
            )
        )
    await asyncio.to_thread(db.save_player, p)
    title = (
        _title("title_credit_upgrade_all", "一键升级信用成功")
        if max_mode
        else _title("title_credit_upgrade", "升级信用成功")
    )
    blocks = [
        {"label": _t("lbl_credit_level"), "value": _t("val_level", {"level": p["bank_level"]})},
        {"label": _t("lbl_limit"), "value": _t("val_yuan", {"amount": _fmt(p["bank_limit"])})},
        {
            "label": _t("lbl_upgrade_fee"),
            "value": _t("val_yuan", {"amount": _fmt(p["bank_upgrade_price"])}),
        },
        {"label": _t("lbl_cash_balance"), "value": _t("val_yuan", {"amount": _fmt(p["cash"])})},
    ]
    extra = (
        [
            {
                "label": _t("lbl_this_stat"),
                "value": _t("val_upgrade_stat", {"times": upgrades, "spent": _fmt(spent)}),
            }
        ]
        if max_mode
        else []
    )
    return R(
        tmpl="panel",
        data={
            "icon": "📈",
            "title": title,
            "accent": "#6fe08c",
            "blocks": blocks + extra,
        },
        text=_t(
            "text_upgrade_ok",
            {"title": title, "level": p["bank_level"], "limit": _fmt(p["bank_limit"])},
        ),
    )


async def bank_info(db, gid, uid, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    rate = float(logic.cfg_get(cfg, "bank_interest_rate_hourly", 0.01))
    max_h = int(logic.cfg_get(cfg, "bank_max_interest_hours", 24))
    last = int(p["last_interest"] or 0)
    if last > 0:
        interest = logic.interest_of(float(p["deposit"]), last, rate, max_h)
    else:
        interest = 0.0
    fund_note = ""
    settle = _settle_fund(p, cfg)
    if settle:
        # 无论 fund 是否归零，都必须持久化结算结果，否则 stale fund_day
        # 导致下次访问重新随机结算，玩家可通过反复查询刷正收益
        await asyncio.to_thread(db.save_player, p)
        if float(p["fund"]) > 0:
            fund_note = _fund_change_note(settle, "subtitle_fund_change")
    return R(
        tmpl="panel",
        data={
            "icon": "🏦",
            "title": _title("title_bank_info", "上班族银行 · 账户信息"),
            "accent": "#7fd1ff",
            "subtitle": fund_note,
            "blocks": [
                {"label": _t("lbl_cash"), "value": _t("val_yuan", {"amount": _fmt(p["cash"])})},
                {
                    "label": _t("lbl_deposit_short"),
                    "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                },
                {
                    "label": _t("lbl_fund_hold"),
                    "value": _t("val_yuan", {"amount": _fmt(p["fund"])}),
                },
                {
                    "label": _t("lbl_total_asset"),
                    "value": _t(
                        "val_yuan",
                        {
                            "amount": _fmt(
                                round(float(p["cash"]) + float(p["deposit"]) + float(p["fund"]), 2)
                            )
                        },
                    ),
                },
                {
                    "label": _t("lbl_credit_level"),
                    "value": _t(
                        "val_credit_level",
                        {"level": p["bank_level"], "limit": _fmt(p["bank_limit"])},
                    ),
                },
                {
                    "label": _t("lbl_upgrade_fee"),
                    "value": _t("val_yuan", {"amount": _fmt(p["bank_upgrade_price"])}),
                },
                {
                    "label": _t("lbl_interest_wait"),
                    "value": _t("val_yuan", {"amount": _fmt(interest)}),
                },
            ],
        },
        text=_t(
            "text_bank_info",
            {
                "cash": _fmt(p["cash"]),
                "deposit": _fmt(p["deposit"]),
                "fund": _fmt(p["fund"]),
                "interest": _fmt(interest),
            },
        ),
    )


async def collect_interest(db, gid, uid, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    rate = float(logic.cfg_get(cfg, "bank_interest_rate_hourly", 0.01))
    max_h = int(logic.cfg_get(cfg, "bank_max_interest_hours", 24))
    last = int(p["last_interest"] or 0)
    if last <= 0:
        # 首次领取：先建立计息基准，否则 last_interest 恒为 0 会永远领不到
        if float(p["deposit"]) <= 0:
            return R(err=_t("interest_nodeposit"))
        p["last_interest"] = int(time.time())
        await asyncio.to_thread(db.save_player, p)
        return R(
            tmpl="panel",
            data={
                "icon": "🪙",
                "title": _title("title_interest_start", "利息已开始累计"),
                "accent": "#7fd1ff",
                "blocks": [
                    {
                        "label": _t("lbl_deposit_short"),
                        "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                    },
                    {
                        "label": _t("lbl_rate"),
                        "value": _t("val_rate", {"rate": f"{rate * 100:.0f}", "hours": max_h}),
                    },
                ],
                "foot": _t("foot_interest_first"),
            },
            text=_t("text_interest_start"),
        )
    interest = logic.interest_of(float(p["deposit"]), last, rate, max_h)
    if interest <= 0:
        return R(err=_t("interest_none"))
    _credit_interest(p, interest)
    await asyncio.to_thread(db.save_player, p)
    # 利息也要进流水：#工资条 的汇总行摆着「累计总收入」和「收支流水」，
    # 只记 total_earned 不记流水的话，两个数字永远对不上
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_interest"), interest, _t("tx_interest_note")
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🪙",
            "title": _title("title_interest_got", "利息到账"),
            "accent": "#6fe08c",
            "blocks": [
                {
                    "label": _t("lbl_this_interest"),
                    "value": _t("val_plus_yuan", {"amount": _fmt(interest)}),
                },
                {
                    "label": _t("lbl_deposit_short"),
                    "value": _t("val_yuan", {"amount": _fmt(p["deposit"])}),
                },
                {"label": _t("lbl_cash"), "value": _t("val_yuan", {"amount": _fmt(p["cash"])})},
            ],
        },
        text=_t("text_interest_got", {"interest": _fmt(interest)}),
    )


async def transfer(db, gid, me, target, amount, cfg, target_name=""):
    if str(target) == str(me):
        return R(err=_t("transfer_self"))
    amt = logic.parse_int(amount, lo=1)
    if amt is None:
        return R(err=_t("transfer_invalid"))
    min_amt = int(logic.cfg_get(cfg, "transfer_min_amount", 100))
    if amt < min_amt:
        return R(err=_t("transfer_min", {"min": min_amt}))
    p = await logic.load_player(db, gid, me, "", cfg)
    # 收款人必须是已入档玩家：不能用 get_player 顺手给陌生 ID 建号
    td = await asyncio.to_thread(db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("no_target")))
    target = td["uid"]  # 以库内真实 uid 为准，避免昵称匹配后转错人
    if str(target) == str(me):
        return R(err=_t("transfer_self"))
    fee = math.ceil(amt * float(logic.cfg_get(cfg, "transfer_fee_rate", 0.1)))
    total = amt + fee
    if float(p["cash"]) < total:
        return R(err=_t("transfer_short", {"total": total, "principal": amt, "fee": fee}))
    # 手续费+本金在同一事务内原子扣除，对方入账本金，防并发双花与中途崩溃丢费
    ok, reason = await asyncio.to_thread(db.transfer_cash, gid, me, target, float(amt), float(fee))
    if not ok:
        if reason == "no_target":
            return R(err=logic.not_in_game(_t("no_target")))
        return R(err=_t("transfer_short", {"total": total, "principal": amt, "fee": fee}))
    p = await asyncio.to_thread(db.get_player, gid, me)
    td = await asyncio.to_thread(db.get_player, gid, target)
    tname = logic.name_of(td, target_name)
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        me,
        _t("kind_transfer_out"),
        -(amt + fee),
        _t("tx_transfer_out", {"name": tname, "fee": fee}),
    )
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        target,
        _t("kind_transfer_in"),
        amt,
        _t("tx_transfer_in", {"name": p.get("card") or p.get("nickname") or me}),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "💸",
            "title": _title("title_transfer_ok", "转账成功"),
            "accent": "#6fe08c",
            "blocks": [
                {"label": _t("lbl_receiver"), "value": tname},
                {"label": _t("lbl_amount"), "value": _t("val_plus_yuan", {"amount": _fmt(amt)})},
                {"label": _t("lbl_fee"), "value": _t("val_minus_yuan", {"amount": _fmt(fee)})},
                {
                    "label": _t("lbl_remaining_cash"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
        },
        text=_t(
            "text_transfer_ok",
            {
                "name": tname,
                "amount": _fmt(amt),
                "fee": _fmt(fee),
                "cash": _fmt(p["cash"]),
            },
        ),
    )


async def fund_buy(db, gid, uid, amount, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    settle = _settle_fund(p, cfg)
    if settle:
        # 结算必须先于输入校验持久化，否则玩家可反复发非法金额指令
        # 来丢弃负收益结算、保留正收益结算
        await asyncio.to_thread(db.save_player, p)
    amt = logic.parse_int(amount, lo=1)
    if amt is None:
        return R(err=_t("fund_buy_invalid"))
    # 与卖出侧、以及 stocks.py 的买入/卖出/清仓统一用 round(x, 2)：同一个
    # fund_fee_rate 此前买入向上取整（ceil）、卖出四舍五入，0.5% 的费在两侧
    # 算法不同，玩家来回倒手会凭空多付/少付几分钱。
    fee = round(amt * float(logic.cfg_get(cfg, "fund_fee_rate", 0.005)), 2)
    total = amt + fee
    if float(p["cash"]) < total:
        return R(err=_t("fund_buy_short", {"total": total, "fee": fee}))
    p["cash"] = round(float(p["cash"]) - total, 2)
    p["fund"] = round(float(p["fund"]) + amt, 2)
    # 建仓当天不再参与当日结算：_settle_fund 在 fund<=0 时提前返回、不打日期戳，
    # 于是从 0 建的仓位会在下一条指令里立刻吃到一整天 ±12% 的波动
    # （相当于买入即免费摇一次骰子）。这里补盖今天的戳。
    p["fund_day"] = logic.today_str()
    await asyncio.to_thread(db.save_player, p)
    note_lines = []
    if settle:
        note_lines.append(_fund_change_note(settle))
    return R(
        tmpl="panel",
        data={
            "icon": "📊",
            "title": _title("title_fund_buy_ok", "申购成功 · 祝你好运"),
            "accent": "#ffd86f",
            "lines": note_lines,
            "blocks": [
                {
                    "label": _t("lbl_buy_amount"),
                    "value": _t("val_yuan", {"amount": _fmt(amt)}),
                },
                {"label": _t("lbl_fee"), "value": _t("val_minus_yuan", {"amount": _fmt(fee)})},
                {
                    "label": _t("lbl_current_hold"),
                    "value": _t("val_yuan", {"amount": _fmt(p["fund"])}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": _fmt(p["cash"])}),
                },
            ],
            "foot": _t("foot_fund_risk"),
        },
        text=_t(
            "text_fund_buy",
            {"amount": _fmt(amt), "fee": _fmt(fee), "hold": _fmt(p["fund"])},
        ),
    )


async def fund_sell(db, gid, uid, ratio_str, cfg, nickname=""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if float(p["fund"]) <= 0:
        return R(err=_t("fund_sell_nohold"))
    settle = _settle_fund(p, cfg)
    if settle:
        # 结算必须先于比例校验持久化，防止 stale fund_day 被利用
        await asyncio.to_thread(db.save_player, p)
    rs = (ratio_str or "").strip()
    hold = float(p["fund"])
    # 用 parse_amount 而不是 isdigit + float：isdigit 对 '²' 之类 Unicode 数字
    # 返回 True 但 float() 会抛 ValueError，一句「#卖出基金 ²」就报指令异常
    pct = logic.parse_amount(rs[:-1] if rs.endswith("%") else rs, lo=0.0, hi=100.0)
    if not rs or rs in ("全部", "all", "ALL"):
        ratio = 1.0
    elif pct is not None and pct > 0:
        # 用用户给的真实比例，不再把 <1% 静默抬到 1%：原实现 max(0.01, pct/100)
        # 会让「#卖出基金 0.5%」实际卖掉 1%，而面板还显示「比例 1%」——
        # 小额清仓场景下是 2 倍误差，且玩家无从察觉。
        ratio = min(1.0, pct / 100.0)
    else:
        # 认不出来就报错。以前这里默认 1.0，导致「#卖出基金 abc」直接清仓
        return R(err=_t("fund_sell_badratio"))
    sell_amount = round(hold * ratio, 2)
    if sell_amount <= 0:
        # 比例小到金额四舍五入为 0：明确告知，而不是「赎回成功」却分文未动
        return R(err=_t("fund_sell_too_small", {"ratio": logic.fmt_money(pct)}))
    fee = round(sell_amount * float(logic.cfg_get(cfg, "fund_fee_rate", 0.005)), 2)
    income = round(max(0.0, sell_amount - fee), 2)
    p["fund"] = round(hold - sell_amount, 2)
    if abs(p["fund"]) < 0.01:
        p["fund"] = 0.0
    p["cash"] = round(float(p["cash"]) + income, 2)
    await asyncio.to_thread(db.save_player, p)
    lines = []
    if settle:
        lines.append(_fund_change_note(settle))
    return R(
        tmpl="panel",
        data={
            "icon": "💰",
            "title": _title("title_fund_sell_ok", "基金赎回成功"),
            "accent": "#7fd1ff",
            "lines": lines,
            "blocks": [
                {
                    "label": _t("lbl_sell_shares"),
                    "value": _t(
                        "val_sell_ratio",
                        # fmt_money 而不是 :.0f：0.5% 会显示成「1%」，
                        # 正是这次要修的那个 2 倍误差的另一个出口
                        {"ratio": logic.fmt_money(ratio * 100), "amount": _fmt(sell_amount)},
                    ),
                },
                {"label": _t("lbl_fee"), "value": _t("val_minus_yuan", {"amount": _fmt(fee)})},
                {
                    "label": _t("lbl_actual_income"),
                    "value": _t("val_yuan", {"amount": _fmt(income)}),
                },
                {
                    "label": _t("lbl_remaining_hold"),
                    "value": _t("val_yuan", {"amount": _fmt(p["fund"])}),
                },
            ],
        },
        text=_t(
            "text_fund_sell",
            {"amount": _fmt(sell_amount), "income": _fmt(income), "hold": _fmt(p["fund"])},
        ),
    )
