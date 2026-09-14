"""养宠物：领养与每日互动（条目表 resources/data/pets.json）。

由 core/life2.py 按业务域拆分而来（“2” 是开发批次编号，不是业务域）。
文案表仍沿用历史命名 resources/texts/life2.json，宠物条目仍在 data/pets.json，
表名与字段名都不动 —— 改文案只改 JSON。
"""

import asyncio

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R

# 与 logic.today_str 同义，直接复用，避免同一件事两份实现
_today = logic.today_str


def _t(key: str, variables: dict | None = None) -> str:
    """取 life2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life2", key, variables)


_title = make_title("life2")


async def adopt_pet(ctx_db, gid, uid, nickname, pet_type, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    if p.get("pet"):
        return R(err=_t("adopt_have", {"pet": p["pet"]}))
    pets = gd.pets()
    pet = next((x for x in pets if x["type"] == pet_type), None)
    if not pet:
        return R(err=_t("adopt_list", {"list": " / ".join(x["type"] for x in pets)}))
    cost = float(pet["cost"])
    if float(p["cash"]) < cost:
        return R(err=_t("adopt_short", {"pet": pet_type, "cost": logic.fmt_money(cost)}))
    p["cash"] = round(float(p["cash"]) - cost, 2)
    p["pet"] = pet_type
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": str(pet.get("icon") or "🐾"),
            "title": f"{_title('title_adopt_pet', '领养了')}{pet_type}！",
            "accent": "#6fe08c",
            "lines": [pet["desc"]],
            "blocks": [
                {
                    "label": _t("lbl_fee"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                },
                {
                    "label": _t("lbl_mind_bonus"),
                    "value": _t("val_plus_num", {"num": pet.get("mind_bonus", 0)}),
                },
                {
                    "label": _t(
                        "lbl_send_action",
                        {"action": pet.get("action") or _t("val_default_action")},
                    ),
                    "value": _t("val_daily_interact"),
                },
            ],
        },
        text=_t(
            "text_adopt_pet",
            {"pet": pet_type, "cost": logic.fmt_money(cost), "desc": pet["desc"]},
        ),
    )


async def pet_interact(ctx_db, gid, uid, nickname):
    p = await asyncio.to_thread(ctx_db.get_player, gid, uid, nickname)
    pet = p.get("pet")
    if not pet:
        return R(err=_t("interact_nopet"))
    today = _today()
    if p.get("pet_day") == today:
        return R(err=_t("interact_duplicate"))
    p["pet_day"] = today
    ev = logic.pick(gd.t("extra3", "pet_interact"))
    if not isinstance(ev, dict):
        ev = {"text": _t("interact_fallback"), "mind": 5, "health": 0}
    ev["text"] = str(ev.get("text") or "")
    # num_of 兜住 null/字符串，且保持「值为 0 / 缺失时不写健康」的原有语义
    mind_delta = logic.num_of(ev, "mind", 5)
    health_delta = logic.num_of(ev, "health", 0)
    p["mind"] = round(float(p["mind"]) + mind_delta, 1)
    if health_delta:
        p["health"] = round(float(p["health"]) + health_delta, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": gd.pet_icon(pet),
            "title": f"{_title('title_pet_interact', '和')}{pet}{_title('title_pet_interact_tail', '互动')}",
            "accent": "#6fe08c",
            "lines": [ev["text"]],
            "blocks": [
                {
                    "label": _t("lbl_mind"),
                    "value": _t(
                        "val_delta_cur",
                        # 展示值与实际生效值同源（num_of 归一后的 mind_delta）：
                        # 用 ev.get('mind', 5) 时，运维把条目写成 null/字符串会让
                        # :+g 格式化抛 TypeError —— 且崩溃点在 save_player 之后，
                        # 状态已落库玩家却只收到异常提示。
                        {"delta": f"{mind_delta:+g}", "cur": p["mind"]},
                    ),
                }
            ],
        },
        text=_t("text_pet_interact", {"pet": pet, "text": ev["text"]}),
    )
