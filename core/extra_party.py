"""年会抽奖：每年一次，四档概率（阈值与 extra2.json 的 party_prizes[].amount 强耦合）。

由 core/extra2.py 按业务域拆分而来。文件名里的 “2” 是【开发批次编号】而不是业务域
（extra2.py 一个文件装了抽奖／借钱／建议／工位／餐补／体检六块），实体因此改用可
预测的域名，批次命名的 core/extra2.py 只留门面转出。

文案表仍沿用历史命名 resources/texts/extra2.json：表名一改，tt/gd.s 就取不到文案、
全部回退到代码里的中文默认值，静态漂移扫描（tests/test_audit_round4.py 的
TestTextDefaultsDoNotDrift）会红。改文案只动 JSON，不要动这里的表名。
"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra2", key, variables)


_title = make_title("extra2")


async def party_lottery(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("party_unemployed"))
    year = time.strftime("%Y")
    if p.get("party_year") == year:
        return R(err=_t("party_duplicate"))
    p["party_year"] = year

    prizes = [x for x in gd.t("extra2", "party_prizes") if isinstance(x, dict)]
    if not prizes:
        return R(err=_t("party_no_prizes"))
    # 四档中奖概率（累积），其余为「谢谢参与」。分桶阈值随奖品表走，不额外开放。
    rates = [
        float(logic.cfg_get(cfg, "party_grand_rate", 0.03)),
        float(logic.cfg_get(cfg, "party_first_rate", 0.07)),
        float(logic.cfg_get(cfg, "party_second_rate", 0.15)),
        float(logic.cfg_get(cfg, "party_third_rate", 0.25)),
    ]

    def _amt(x) -> float:
        # AttributeError 也要兜：条目在 prizes 构建时已过 isinstance 守卫，
        # 但保留它让本函数对任何输入都安全（返回 0 = 归入「谢谢参与」档）。
        try:
            return float(x.get("amount") or 0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    buckets = [
        # 分桶阈值与 resources/texts/extra2.json 的 party_prizes[].amount 强耦合：
        # 改奖品金额会让奖品在四档之间平移，等于改动各档的实际中奖概率。
        [x for x in prizes if _amt(x) >= 8000],
        [x for x in prizes if 2000 <= _amt(x) < 8000],
        [x for x in prizes if 100 <= _amt(x) < 2000],
        [x for x in prizes if 0 < _amt(x) < 100],
    ]
    empty = [x for x in prizes if _amt(x) == 0]
    roll, accum, prize = random.random(), 0.0, None
    for rate, bucket in zip(rates, buckets, strict=True):
        accum += rate
        if roll < accum:
            # 命中的档位为空时不能 continue 到下一档（那等于把本档概率
            # 白送给更低档），按未中奖处理。
            if bucket:
                prize = random.choice(bucket)
            break
    if prize is None:
        # 未中奖：优先取「谢谢参与」档（amount==0）；没有该档时构造兜底文案。
        # 绝不能从含金额的 prizes 里随机抽，否则「谢谢参与」会白捡大奖。
        prize = (
            random.choice(empty)
            if empty
            else {
                "rank": _t("party_consolation_rank"),
                "text": _t("party_consolation"),
                "amount": 0,
            }
        )

    rank = str(prize.get("rank") or _t("party_consolation_rank"))
    ptext = str(prize.get("text") or "")
    amount = int(_amt(prize))
    if amount > 0:
        p["cash"] = round(float(p["cash"]) + amount, 2)
        p["total_earned"] = round(float(p.get("total_earned") or 0) + amount, 2)
    await asyncio.to_thread(db.save_player, p)
    if amount > 0:
        # 奖金也要进流水：#工资条 把「累计总收入」与收支流水并排显示，只写
        # total_earned 不写流水的话两个数字永远对不上。
        await asyncio.to_thread(
            db.add_transaction,
            gid,
            uid,
            _t("kind_party"),
            amount,
            _t("party_tx_note", {"rank": rank}),
        )
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_party"),
        _t("party_event", {"name": logic.name_of(p, uid), "rank": rank, "text": ptext[:30]}),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🎊",
            "title": f"{_title('title_party', '年会抽奖')} · {rank}",
            "accent": "#ffd86f" if amount > 100 else "#7fd1ff",
            "lines": [ptext],
            "blocks": [
                {
                    "label": _t("lbl_prize_val"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(amount)}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
            "foot": _t("foot_annual"),
        },
        text=_t(
            "party_text",
            {"rank": rank, "text": ptext, "amount": logic.fmt_money(amount)},
        ),
    )
