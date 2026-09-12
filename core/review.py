"""年终考评系统：每年限一次，S/A/B/C/D 五档考评。"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 review.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("review", key, variables)


_title = make_title("review")


async def annual_review(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_unemployed"))

    year = time.strftime("%Y")
    if p.get("review_year") == year:
        return R(err=_t("err_done", {"year": year}))

    lvl = int(p["lvl"])
    exp = int(p["exp"])
    streak = int(p.get("attend_streak") or 0)
    skills = p.get("_skills", [])
    social = int(p.get("social_pts") or 0)

    # 综合评分：五项权重可配（默认与老版本一致）
    def c(key, default):
        return float(logic.cfg_get(cfg, key, default))

    score = (
        exp * c("review_weight_exp", 2)
        + streak * c("review_weight_streak", 5)
        + len(skills) * c("review_weight_skill", 20)
        + social * c("review_weight_social", 3)
        + lvl * c("review_weight_level", 50)
    )
    score = round(
        score
        * (c("review_score_rand_min", 0.8) + random.random() * c("review_score_rand_range", 0.4))
    )

    # 档位阈值 + 年终奖倍数 + 调薪幅度。这是全插件最大的通胀杠杆
    # （S 级一次给数倍月薪现金 **且永久涨薪**），必须让运维能压制：
    # 档位名/配色/图标在 resources/data/review.json，数值仍由插件配置决定。
    grades = gd.review_grades() or [
        # JSON 缺失时的最小兜底：只保留一个不给奖不涨薪的档，绝不静默发钱
        # 档位名取文案表，不再逐字复制 resources/data/review.json 的末档
        {
            "grade": "D",
            "name": _t("val_grade_fallback_name"),
            "color": "#fc6262",
            "icon": "🔴",
            "threshold_key": None,
            "bonus_key": "review_bonus_multi_d",
            "bonus_default": 0.0,
        }
    ]
    tier = grades[-1]
    for g in grades:
        tkey = g.get("threshold_key")
        if not tkey:
            tier = g  # 兜底档：没有阈值，谁都能落到这里
            break
        if score >= c(tkey, g.get("threshold_default") or 0):
            tier = g
            break
    grade = str(tier.get("grade") or "D")
    grade_name = str(tier.get("name") or "")
    bonus_mult = c(tier.get("bonus_key") or "", tier.get("bonus_default") or 0.0)
    raise_pct = (
        c(tier["raise_key"], tier.get("raise_default") or 0.0) if tier.get("raise_key") else 0.0
    )
    color = str(tier.get("color") or "#fc6262")
    icon = str(tier.get("icon") or "🔴")

    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    bonus = round(float(p["salary"]) * bonus_mult, 2)
    if raise_pct > 0:
        # 走 apply_raise：同时累进 salary_bonus，否则这次「永久涨薪」会在下一次
        # 晋升或跳槽重算薪资时被整体覆盖掉
        logic.apply_raise(p, raise_pct)

    p["review_year"] = year
    p["cash"] = round(float(p["cash"]) + bonus, 2)
    p["total_earned"] = round(float(p.get("total_earned") or 0) + bonus, 2)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)

    await asyncio.to_thread(
        db.add_transaction,
        gid,
        uid,
        _t("kind_year_bonus"),
        bonus,
        _t("review_note", {"grade": grade}),
    )
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_annual_review"),
        _t(
            "ev_review",
            {
                "nickname": p["nickname"] or uid,
                "grade": grade,
                "grade_name": grade_name,
                "bonus": logic.fmt_money(bonus),
            },
        ),
    )

    skill_names = "、".join(skills) if skills else _t("val_none")
    grade_level = _t("val_grade_level", {"grade": grade})
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": f"{_title('title_review', '年终考评')} {grade_level} · {grade_name}",
            "accent": color,
            "subtitle": _t("val_company", {"name": comp["name"] if comp else _t("val_none")}),
            "blocks": [
                {"label": _t("lbl_score"), "value": str(score)},
                {
                    "label": _t("lbl_bonus"),
                    "value": _t(
                        "val_bonus",
                        {"amount": logic.fmt_money(bonus), "multi": bonus_mult},
                    ),
                },
                {
                    "label": _t("lbl_raise"),
                    "value": _t("val_raise", {"pct": f"{raise_pct * 100:.0f}"})
                    if raise_pct > 0
                    else _t("val_raise_none"),
                },
                {"label": _t("lbl_skills"), "value": skill_names or _t("val_none")},
                {"label": _t("lbl_social"), "value": str(social)},
                {"label": _t("lbl_streak"), "value": _t("val_days", {"days": streak})},
            ],
            "foot": _t("foot_review"),
        },
        text=_t(
            "text_review",
            {
                "grade": grade,
                "grade_name": grade_name,
                "bonus": logic.fmt_money(bonus),
            },
        )
        + (_t("text_review_raise", {"pct": f"{raise_pct * 100:.0f}"}) if raise_pct > 0 else ""),
    )
