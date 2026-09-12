"""职场成就：解锁检测与称号佩戴（由 extra.py 拆分）。"""

import asyncio
import json

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_achievement.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra_achievement", key, variables)


_title = make_title("extra_achievement")


def _metric(p: dict, name: str) -> float:
    """把成就的判定口径映射成一个可比较的数值。

    口径名与阈值都在 resources/texts/achievements.json 里；此前阈值写死在
    本模块的 lambda 表里，而 JSON 的 desc 又写着「10 天」「10 万元」——
    同一个数字两份来源，改 JSON 描述完全不影响行为。
    """
    if name == "net_worth":
        return float(p.get("cash") or 0) + float(p.get("deposit") or 0) + float(p.get("fund") or 0)
    if name == "has_pet":
        return 1.0 if p.get("pet") else 0.0
    try:
        return float(p.get(name) or 0)
    except (TypeError, ValueError):
        return 0.0


def achievements_def() -> list[dict]:
    """成就定义：{id,name,desc,metric,threshold}，全部来自 JSON。"""
    out = []
    for a in gd.achievements():
        metric = str(a.get("metric") or "")
        if not metric:
            continue  # 没写判定口径的条目无法检测，跳过而不是静默永不解锁
        try:
            threshold = float(a.get("threshold") or 0)
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "id": str(a.get("id") or ""),
                "name": str(a.get("name") or a.get("id") or ""),
                "desc": str(a.get("desc") or ""),
                "metric": metric,
                "threshold": threshold,
            }
        )
    return out


def _reached(p: dict, ach: dict) -> bool:
    return _metric(p, ach["metric"]) >= ach["threshold"]


async def my_achievements(db, gid, uid, nickname, cfg):
    """查看个人成就并自动检测解锁。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    defs = achievements_def()
    unlocked = logic.achievements_of(p)
    new_unlocked = []

    for ach in defs:
        if ach["id"] not in unlocked and _reached(p, ach):
            unlocked.add(ach["id"])
            new_unlocked.append(ach["name"])

    if new_unlocked:
        p["achievements"] = json.dumps(sorted(unlocked))
        await asyncio.to_thread(db.save_player, p)

    rows = []
    for ach in defs:
        status = _t("val_unlocked") if ach["id"] in unlocked else _t("val_locked")
        equipped = _t("val_equipped") if p.get("title") == ach["name"] else ""
        rows.append(
            _t(
                "line_achievement",
                {
                    "status": status,
                    "name": ach["name"],
                    "equipped": equipped,
                    "desc": ach["desc"],
                },
            )
        )

    cur_title = p.get("title") or _t("val_no_title")
    return R(
        tmpl="panel",
        data={
            "icon": "🎖️",
            "title": f"{p['nickname'] or uid} {_title('title_achievements', '的职场成就')}",
            "accent": "#ffd86f",
            "lines": rows,
            "blocks": [
                {"label": _t("lbl_cur_title"), "value": cur_title},
                {
                    "label": _t("lbl_progress"),
                    "value": _t(
                        "val_progress",
                        {"unlocked": len(unlocked), "total": len(defs)},
                    ),
                },
            ],
            "foot": _t("foot_achievements"),
        },
        text=_t(
            "text_achievements",
            {"unlocked": len(unlocked), "total": len(defs), "title": cur_title},
        )
        + "\n".join(rows),
    )


async def set_title(db, gid, uid, nickname, title_name, cfg):
    """佩戴已解锁的成就称号。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    unlocked = logic.achievements_of(p)
    title_name = (title_name or "").strip()
    if not title_name:
        return R(err=_t("set_title_usage"))

    # 匹配称号
    target = next(
        (
            ach
            for ach in achievements_def()
            if ach["name"] == title_name or title_name in ach["name"]
        ),
        None,
    )
    if not target:
        return R(err=_t("title_not_found", {"name": title_name}))
    if target["id"] not in unlocked:
        return R(err=_t("title_locked", {"name": target["name"], "desc": target["desc"]}))

    p["title"] = target["name"]
    await asyncio.to_thread(db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "✨",
            "title": _title("title_title_set", "称号佩戴成功"),
            "accent": "#6fe08c",
            "lines": [_t("line_title_set", {"name": target["name"]})],
            "blocks": [
                {
                    "label": _t("lbl_cur_headline"),
                    "value": _t("val_cur_headline", {"name": target["name"]}),
                },
                {"label": _t("lbl_ach_desc"), "value": target["desc"]},
            ],
        },
        text=_t("text_title_set", {"name": target["name"]}),
    )


async def unset_title(db, gid, uid, nickname, cfg):
    """卸下当前佩戴的头衔。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    p["title"] = ""
    await asyncio.to_thread(db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "🍃",
            "title": _title("title_title_unset", "已卸下称号"),
            "accent": "#7fd1ff",
            "lines": [_t("line_title_unset")],
        },
        text=_t("text_title_unset"),
    )
