"""借钱给群友：有借无还的失败分支、转账与双边流水。

由 core/extra2.py 按业务域拆分而来。文件名里的 “2” 是【开发批次编号】而不是业务域
（extra2.py 一个文件装了抽奖／借钱／建议／工位／餐补／体检六块），实体因此改用可
预测的域名，批次命名的 core/extra2.py 只留门面转出。

文案表仍沿用历史命名 resources/texts/extra2.json：表名一改，tt/gd.s 就取不到文案、
全部回退到代码里的中文默认值，静态漂移扫描（tests/test_audit_round4.py 的
TestTextDefaultsDoNotDrift）会红。改文案只动 JSON，不要动这里的表名。
"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra2", key, variables)


_title = make_title("extra2")


async def lend_money(db, gid, me, target, amount, cfg, target_name=""):
    if str(target) == str(me):
        return R(err=_t("lend_self"))
    amt = logic.parse_int(amount, lo=1)
    if amt is None:
        return R(err=_t("lend_invalid"))
    p = await logic.load_player(db, gid, me, "", cfg)
    # 借款对象必须已入档：load_player 会给陌生 ID 顺手建号
    td = await asyncio.to_thread(db.find_player_any, gid, str(target))
    if not td:
        return R(err=logic.not_in_game(_t("lend_no_target")))
    target = td["uid"]
    if str(target) == str(me):
        return R(err=_t("lend_self"))
    tname = logic.name_of(td, target_name)
    if float(p["cash"]) < amt:
        return R(
            err=_t(
                "lend_short", {"cash": logic.fmt_money(p["cash"]), "amount": logic.fmt_money(amt)}
            )
        )
    if random.random() < float(logic.cfg_get(cfg, "lend_fail_rate", 0.3)):
        if not await asyncio.to_thread(db.try_debit_cash, gid, me, float(amt)):
            return R(err=_t("lend_debit_fail"))
        await asyncio.to_thread(
            db.add_transaction,
            gid,
            me,
            _t("kind_loan_unpaid"),
            -amt,
            _t("lend_note_out", {"name": tname}),
        )
        line = logic.pick(gd.t("extra2", "lend_fail"))
        return R(
            tmpl="panel",
            data={
                "icon": "💸",
                "title": _title("title_lend_fail", "借钱有去无回"),
                "accent": "#fc6262",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_loss"),
                        "value": _t("val_minus_yuan", {"amount": logic.fmt_money(amt)}),
                    }
                ],
            },
            text=_t(
                "text_lend_fail",
                {"name": tname, "line": line, "amount": logic.fmt_money(amt)},
            ),
        )
    ok, _reason = await asyncio.to_thread(db.transfer_cash, gid, me, target, float(amt))
    if not ok:
        return R(err=_t("lend_transfer_fail"))
    p = await asyncio.to_thread(db.get_player, gid, me)
    td = await asyncio.to_thread(db.get_player, gid, target)
    await asyncio.to_thread(
        db.add_transaction, gid, me, _t("kind_loan_out"), -amt, _t("lend_note_out", {"name": tname})
    )
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        target,
        _t("kind_loan_in"),
        amt,
        _t("lend_note_in", {"name": p["nickname"] or me}),
    )
    line = logic.pick(gd.t("extra2", "lend_ok"))
    return R(
        tmpl="panel",
        data={
            "icon": "🤝",
            "title": _title("title_lend_ok", "借钱成功"),
            "accent": "#6fe08c",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_lend_amount"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(amt)}),
                },
                {
                    "label": _t("lbl_my_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
                {
                    "label": _t("lbl_their_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(td["cash"])}),
                },
            ],
            "foot": _t("foot_borrow"),
        },
        text=_t("text_lend_ok", {"name": tname, "amount": logic.fmt_money(amt)}),
    )
