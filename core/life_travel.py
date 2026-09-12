"""出去旅游：花一笔钱换精神/健康（目的地表在 texts/extra3.json 的 travel）。

由 core/life2.py 按业务域拆分而来（“2” 是开发批次编号，不是业务域）。
文案表仍沿用历史命名 resources/texts/life2.json。
"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 life2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life2", key, variables)


_title = make_title("life2")


async def travel(ctx_db, gid, uid, nickname, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    dests = gd.t("extra3", "travel")
    if not dests:
        return R(err=_t("travel_empty"))
    dest = random.choice(dests)
    # 目的地字段一律先归一再用：extra3.json 由运维手改，缺 cost/mind/health/text
    # 时直接下标会在【现金已扣、档案已落库之后】抛 KeyError —— 玩家花了钱却只
    # 看到「指令执行异常」。条目不完整时按 0 收益处理，至少钱与状态是对得上的。
    if not isinstance(dest, dict):
        dest = {}
    dest_cost = logic.num_of(dest, "cost", 0)
    dest_mind = logic.num_of(dest, "mind", 0)
    dest_health = logic.num_of(dest, "health", 0)
    dest_text = str(dest.get("text") or "")
    if float(p["cash"]) < dest_cost:
        # 地名取外置的 dest 字段：截 text[:6] 会得到"你去大理旅"，
        # 拼出"去你去大理旅需要约…"这种病句
        where = str(dest.get("dest") or "").strip() or _t("val_this_trip")
        return R(err=_t("travel_short", {"where": where, "cost": logic.fmt_money(dest_cost)}))
    p["cash"] = round(float(p["cash"]) - dest_cost, 2)
    p["mind"] = round(min(100, float(p["mind"]) + dest_mind), 1)
    p["health"] = round(min(100, float(p["health"]) + dest_health), 1)
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "✈️",
            "title": _title("title_travel", "旅行归来"),
            "accent": "#6fe08c",
            "lines": [dest_text],
            "blocks": [
                {
                    "label": _t("lbl_travel_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(dest_cost)}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_gain_cur", {"gain": dest_mind, "cur": p["mind"]}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_gain_cur", {"gain": dest_health, "cur": p["health"]}),
                },
            ],
            "foot": _t("foot_travel"),
        },
        text=_t(
            "text_travel",
            {"text": dest_text, "cost": logic.fmt_money(dest_cost)},
        ),
    )
