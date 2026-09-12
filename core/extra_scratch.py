"""下班刮刮乐：按 resources/data/scratch.json 的奖项倍数 + scratch_rtp 反算奖金。

由 core/extra_redpacket.py 拆出。那个文件的文件名说的是红包，但里面装了红包与刮
刮乐两个互不相干的玩法（一个社交向、一个博彩向），玩法之间没有任何共享状态。
core/extra_redpacket.py 保留红包实体并把本模块的 scratch_lottery 转出，老的
`from core import extra_redpacket; extra_redpacket.scratch_lottery` 调用点不受影响。

文案表仍沿用历史命名 resources/texts/extra_redpacket.json：红包与刮刮乐共用同一张
表（表名按域命名而不是按批次），改表名 = 换文案 = 静态漂移扫描会红。
"""

import asyncio
import logging
import random

try:  # 允许脱离 AstrBot 的脚本/测试单独导入本模块
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger("shangbanzu.extra_scratch")

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_redpacket.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra_redpacket", key, variables)


_title = make_title("extra_redpacket")


async def scratch_lottery(db, gid, uid, nickname, cfg):
    """下班刮刮乐：小赌怡情（带冷却，期望返奖率约 92.5%，由 scratch_rtp 配置）。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    cost = float(logic.cfg_get(cfg, "scratch_lottery_cost", 20.0))
    cd = float(logic.cfg_get(cfg, "scratch_cooldown_minutes", 30)) * 60
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "scratch") > 0:
        return R(
            err=_t("cooldown", {"remaining": logic.fmt_remaining(logic.cd_left(p, "scratch"))})
        )
    if float(p["cash"]) < cost:
        return R(err=_t("scratch_cost_short", {"cost": logic.fmt_money(cost)}))

    # 奖项以「相对售价的倍数」定义（resources/data/scratch.json），实际奖金再按
    # 目标返奖率 scratch_rtp 统一缩放。这样改售价不会连带改变返奖率——
    # 老版本把绝对金额写死，售价一调返奖率就失控（20→1 元即变成 1850%）。
    prizes, lose = gd.scratch_table()
    # 先把奖项表归一化：条目由运维手改，缺 multiplier/prob 或值不是数字时，
    # 直接在生成式/累加循环里下标会抛 KeyError（本该给「奖项表未配置」提示），
    # 而且 scale 算错会让返奖率静默失控。坏条目一律丢弃并记一次日志。
    clean_prizes: list[tuple[str, str, float, float]] = []
    dropped = 0
    for x in prizes:
        try:
            clean_prizes.append(
                (
                    str(x["name"]),
                    str(x.get("color") or "#ffd86f"),
                    float(x["multiplier"]),
                    float(x["prob"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            dropped += 1
    if dropped:
        logger.warning(
            f"[上班族物语] scratch.json 有 {dropped} 条奖项缺 name/multiplier/prob，已跳过"
        )
    rtp = float(logic.cfg_get(cfg, "scratch_rtp", 0.925))
    base_rtp = sum(mult * prob for _n, _c, mult, prob in clean_prizes)
    if not clean_prizes or base_rtp <= 0:
        # scratch.json 空/损坏时 scale 会变成 0：那就是「照价扣钱、永远 0 元返奖」。
        # 必须在扣款和写冷却之前拒绝（此时两者都还没落库）。
        return R(err=_t("scratch_not_configured"))
    scale = rtp / base_rtp

    logic.cd_set(p, "scratch", cd)
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)

    roll = random.random()
    accum = 0.0
    prize_name = str(lose.get("name") or _t("val_lose_default"))
    color = str(lose.get("color") or "#fc6262")
    reward = 0.0
    for p_name, p_color, p_mult, p_prob in clean_prizes:
        accum += p_prob
        if roll <= accum:
            prize_name = p_name
            color = p_color
            reward = round(cost * p_mult * scale, 2)
            break
    if reward > 0:
        p["cash"] = round(float(p["cash"]) + reward, 2)
        p["total_earned"] = round(float(p.get("total_earned") or 0) + reward, 2)

    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_scratch"), reward - cost, prize_name
    )

    return R(
        tmpl="panel",
        data={
            "icon": "🎫",
            "title": _title("title_scratch", "🎰 职场刮刮乐开奖！"),
            "accent": color,
            "lines": [_t("line_scratch", {"prize": prize_name})],
            "blocks": [
                {
                    "label": _t("lbl_prize_amount"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(reward)})
                    if reward > 0
                    else _t("val_zero_yuan"),
                },
                {
                    "label": _t("lbl_scratch_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_my_cash"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
            "foot": _t("foot_scratch"),
        },
        text=_t(
            "text_scratch",
            {
                "prize": prize_name,
                "amount": logic.fmt_money(reward),
                "cash": logic.fmt_money(p["cash"]),
            },
        ),
    )
