"""红包：send_redpacket / claim_redpacket（刮刮乐已拆到 core/extra_scratch.py）。

红包与刮刮乐原本同装在本文件里，但两者是互不相干的玩法（一个社交向、一个博彩向），
除了一张历史命名的文案表之外没有任何共享状态。拆开后本文件保留红包实体，并转出
core/extra_scratch.py 的 scratch_lottery —— 老的
`from core import extra_redpacket; extra_redpacket.scratch_lottery(...)` 调用点
（tests/test_services_smoke.py、core/extra.py）不受影响。

文案表仍是 resources/texts/extra_redpacket.json：这张表是【按域命名】的（红包就是这么
命名的），刮刮乐沿用同一张表而不新建 extra_scratch.json —— 换表名 = 换文案 = 静态漂移
扫描（tests/test_audit_round4.py 的 TestTextDefaultsDoNotDrift）会红。
"""

import asyncio

from . import logic
from .career_common import make_title, tt
from .extra_scratch import scratch_lottery
from .result import R

__all__ = ["claim_redpacket", "scratch_lottery", "send_redpacket"]


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_redpacket.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra_redpacket", key, variables)


_title = make_title("extra_redpacket")


async def send_redpacket(db, gid, uid, nickname, amount_str, count_str, cfg):
    """在群内发送拼手气红包。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    amt = logic.parse_amount(amount_str, lo=0.01)
    max_cnt = int(logic.cfg_get(cfg, "redpacket_max_count", 50))
    cnt = logic.parse_int(count_str, lo=1, hi=max_cnt)
    if amt is None or cnt is None:
        return R(err=_t("format_error"))

    min_packet = float(logic.cfg_get(cfg, "redpacket_min_amount", 10.0))
    if amt < min_packet or amt < cnt:
        # amount 必须过 fmt_money：parse_amount 返回的是 round(float, 2)，
        # 直接塞进文案会让用户看到「当前金额 5.0 元」这种带小数的原始 float
        return R(
            err=_t(
                "min_amount",
                {
                    "min_packet": logic.fmt_money(min_packet),
                    "max_cnt": max_cnt,
                    "amount": logic.fmt_money(amt),
                    "count": cnt,
                },
            )
        )

    if float(p["cash"]) < amt:
        return R(err=_t("cash_short", {"amount": logic.fmt_money(amt)}))

    # 原子条件扣款 + 建包：单事务，杜绝「钱扣了但红包没建」孤儿资金。
    ok, reason, packet_id = await asyncio.to_thread(
        db.create_redpacket_atomic,
        str(gid),
        str(uid),
        p["nickname"] or uid,
        float(amt),
        int(cnt),
        _t("tx_send_note", {"count": int(cnt)}),
        _t("kind_send_redpacket_tx"),
    )
    if not ok:
        if reason == "insufficient":
            msg = _t("cash_short", {"amount": logic.fmt_money(amt)})
        elif reason == "no_player":
            # load_player 已建号，正常走不到这里；档案刚被管理员删掉时可能命中
            msg = _t("no_player")
        else:
            msg = _t("failed")
        return R(err=msg)
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_send_redpacket"),
        _t(
            "event",
            {"nickname": p["nickname"] or uid, "amount": logic.fmt_money(amt), "count": cnt},
        ),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🧧",
            "title": _title("title_redpacket_send", "🎉 拼手气红包已发出！"),
            "accent": "#fc6262",
            "lines": [
                _t(
                    "line_packet_sent",
                    {
                        "nickname": p["nickname"] or uid,
                        "amount": logic.fmt_money(amt),
                    },
                ),
                _t("line_packet_id", {"id": packet_id, "count": cnt}),
            ],
            "blocks": [
                {
                    "label": _t("lbl_packet_total"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(amt)}),
                },
                {
                    "label": _t("lbl_packet_count"),
                    "value": _t("val_count", {"count": cnt}),
                },
                {"label": _t("lbl_claim_cmd"), "value": _t("val_claim_cmd")},
            ],
            "foot": _t("foot_packet_send"),
        },
        text=_t(
            "text_packet_send",
            {
                "nickname": p["nickname"] or uid,
                "amount": logic.fmt_money(amt),
                "count": cnt,
            },
        ),
    )


async def claim_redpacket(db, gid, uid, nickname, cfg):
    """群友抢最近的可用红包。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)

    status, res = await asyncio.to_thread(
        db.claim_redpacket,
        str(gid),
        str(uid),
        p["nickname"] or uid,
        # 发包人昵称要到事务内才知道，所以传模板由存储层替换 {sender}
        _t("tx_claim_note"),
        _t("kind_claim_redpacket"),
    )
    if status == "empty":
        return R(err=_t("empty"))
    if status == "already":
        return R(err=_t("already"))
    if status == "no_player":
        return R(err=_t("no_player"))
    if not res:
        return R(err=_t("claimed_all"))

    # 拆包、入账、记流水都在 db.claim_redpacket 的同一个事务里完成，这里只读回展示
    packet, get_amt, new_remain_amt, new_remain_cnt = res
    p = await asyncio.to_thread(db.get_player, gid, uid)

    return R(
        tmpl="panel",
        data={
            "icon": "🧧",
            "title": _title("title_redpacket_claim", "🎉 抢到红包！"),
            "accent": "#ffd86f",
            "lines": [_t("line_packet_claim", {"sender": packet["sender_name"]})],
            "blocks": [
                {
                    "label": _t("lbl_got_amount"),
                    "value": _t("val_plus_yuan", {"amount": logic.fmt_money(get_amt)}),
                },
                {
                    "label": _t("lbl_my_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
                {
                    "label": _t("lbl_packet_left"),
                    "value": _t(
                        "val_packet_left",
                        {
                            "count": new_remain_cnt,
                            "amount": logic.fmt_money(new_remain_amt),
                        },
                    ),
                },
            ],
            "foot": _t("foot_packet_claim"),
        },
        text=_t(
            "text_packet_claim",
            {
                "sender": packet["sender_name"],
                "amount": logic.fmt_money(get_amt),
                "cash": logic.fmt_money(p["cash"]),
            },
        ),
    )
