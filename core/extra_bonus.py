"""skill_list 等功能（由 extra.py 拆分）。"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra_bonus.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra_bonus", key, variables)


_title = make_title("extra_bonus")


def skill_list() -> list[str]:
    """可学技能列表（resources/data/skills.json）。每次读取，WebUI 改 JSON 后热生效。"""
    return list(gd.skills().keys())


def _skills(p):
    return p.get("_skills", [])


async def year_bonus(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("year_bonus_unemployed"))
    year = time.strftime("%Y")
    if p.get("year_bonus_year") == year:
        return R(err=_t("year_bonus_already"))
    lvl = int(p["lvl"])
    streak = int(p["attend_streak"])
    lvl_factor = float(logic.cfg_get(cfg, "year_bonus_level_factor", 0.3))
    streak_factor = float(logic.cfg_get(cfg, "year_bonus_streak_factor", 0.02))
    streak_cap = int(logic.cfg_get(cfg, "attend_streak_bonus_days", 20))
    base = float(p["salary"]) * (1 + lvl * lvl_factor + min(streak, streak_cap) * streak_factor)
    # 档位（名称/配色/图标 + 概率与倍数的配置键）走 resources/data/yearbonus.json，
    # 与 review.json 同一范式：改文案不动代码，调数值不动 JSON。
    tiers = gd.year_bonus_tiers()
    if not tiers:
        # 档位表读不到时直接拒绝，绝不能一边发 0 元一边把 year_bonus_year 写掉
        # ——那等于白烧玩家一整年的领取额度
        return R(err=_t("year_bonus_tiers_unavailable"))
    roll = random.random()
    accum = 0.0
    tier = tiers[-1] if tiers else {}
    for t in tiers:
        rate_key = t.get("rate_key")
        if not rate_key:
            tier = t  # 兜底档（rate_key 为 null）
            break
        accum += float(logic.cfg_get(cfg, rate_key, t.get("rate_default") or 0.0))
        if roll < accum:
            tier = t
            break
    multi = float(
        logic.cfg_get(cfg, tier.get("multi_key") or "", tier.get("multi_default") or 0.0)
        if tier.get("multi_key")
        else (tier.get("multi_default") or 0.0)
    )
    bonus = round(base * multi, 2)
    line = logic.pick(gd.t("extra", str(tier.get("text_key") or "yearbonus_ok")))
    title = str(tier.get("title") or _title("title_year_bonus", "年终奖到账"))
    accent = str(tier.get("color") or "#6fe08c")
    icon = str(tier.get("icon") or "🧧")
    p["cash"] = round(float(p["cash"]) + bonus, 2)
    p["total_earned"] = round(float(p.get("total_earned") or 0) + bonus, 2)
    p["year_bonus_year"] = year
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        uid,
        _t("kind_year_bonus"),
        bonus,
        _t("year_bonus_tx_note", {"lvl": lvl, "streak": streak}),
    )
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_year_bonus"),
        _t(
            "year_bonus_event", {"nickname": p["nickname"] or uid, "amount": logic.fmt_money(bonus)}
        ),
    )
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": title,
            "accent": accent,
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_year_bonus"),
                    "value": _t("val_plus_yuan", {"amount": logic.fmt_money(bonus)}),
                },
                {"label": _t("lbl_level"), "value": _t("val_level", {"lvl": lvl})},
                {
                    "label": _t("lbl_streak"),
                    "value": _t("val_days", {"days": streak}),
                },
                {
                    "label": _t("lbl_cash_balance"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["cash"])}),
                },
            ],
            "foot": _t("foot_year_bonus"),
        },
        text=_t(
            "text_year_bonus",
            {"title": title, "amount": logic.fmt_money(bonus), "line": line},
        ),
    )


async def learn_skill(db, gid, uid, nickname, cfg, skill):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    skill = (skill or "").strip()
    skills_list = skill_list()
    if not skills_list:
        return R(err=_t("skills_not_configured"))
    if skill not in skills_list:
        return R(err=_t("skills_available", {"skills": " / ".join(skills_list)}))
    skills = _skills(p)
    if skill in skills:
        return R(err=_t("skill_already_learned", {"skill": skill}))
    cost = float(logic.cfg_get(cfg, "skill_cost", 800.0))
    exp_gain = int(logic.cfg_get(cfg, "skill_exp_gain", 15))
    if float(p["cash"]) < cost:
        return R(err=_t("skill_cost_short", {"cost": logic.fmt_money(cost)}))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "skill") > 0:
        return R(
            err=_t("skill_cooldown", {"remaining": logic.fmt_remaining(logic.cd_left(p, "skill"))})
        )
    cd_days = float(logic.cfg_get(cfg, "skill_cooldown_days", 7))
    logic.cd_set(p, "skill", cd_days * 86400)
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    # 经验只在这两个分支里各给一次：原先成功档的 exp_gain 写在 if 之外，
    # 失败时还会再叠一份安慰经验，于是「学失败」比「学成功」拿的经验更多
    if random.random() < float(logic.cfg_get(cfg, "skill_success_rate", 0.75)):
        p["exp"] = int(p["exp"]) + exp_gain + logic.ri(0, 5)
        skills.append(skill)
        p["_skills"] = skills
        line = logic.pick(gd.t("extra", "skill_learn_ok"))
        await asyncio.to_thread(db.save_player, p)
        await asyncio.to_thread(
            db.add_transaction, gid, uid, _t("kind_skill_tuition"), -cost, skill
        )
        return R(
            tmpl="panel",
            data={
                "icon": "🎓",
                "title": f"{_title('title_skill_ok', '学会')}「{skill}」！",
                "accent": "#6fe08c",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_tuition"),
                        "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                    },
                    {
                        "label": _t("lbl_skills_owned"),
                        "value": "、".join(skills) or _t("val_none"),
                    },
                    {
                        "label": _t("lbl_exp"),
                        "value": _t("val_exp_gain", {"gain": exp_gain, "cur": p["exp"]}),
                    },
                ],
                "foot": _t("foot_skill"),
            },
            text=_t("text_skill_ok", {"skill": skill, "line": line}),
        )
    p["exp"] = int(p["exp"]) + logic.ri(1, 3)
    line = logic.pick(gd.t("extra", "skill_learn_fail"))
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(db.add_transaction, gid, uid, _t("kind_skill_tuition"), -cost, skill)
    return R(
        tmpl="panel",
        data={
            "icon": "📉",
            "title": _title("title_skill_fail", "学习失败"),
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_tuition"),
                    "value": _t("val_tuition_wasted", {"amount": logic.fmt_money(cost)}),
                }
            ],
        },
        text=_t("text_skill_fail", {"line": line}),
    )


async def my_skills(db, gid, uid, nickname, cfg):
    # 纯查看命令，不能 get_player 给陌生 ID 顺手建号
    p = await asyncio.to_thread(db.get_player_row, gid, uid)
    if not p:
        return R(err=logic.no_record(_t("no_record_skill")))
    skills = _skills(p)
    all_sk = "、".join(skill_list()) or _t("val_empty")
    owned = "、".join(skills) if skills else _t("val_empty")
    return R(
        tmpl="panel",
        data={
            "icon": "🎯",
            "title": _title("title_my_skills", "我的技能"),
            "accent": "#7fd1ff",
            "blocks": [
                {"label": _t("lbl_owned"), "value": owned},
                {"label": _t("lbl_learnable"), "value": all_sk},
                {
                    "label": _t("lbl_tuition"),
                    "value": _t(
                        "val_tuition_each",
                        {"cost": logic.fmt_money(logic.cfg_get(cfg, "skill_cost", 800.0))},
                    ),
                },
                {
                    "label": _t("lbl_cooldown"),
                    "value": _t(
                        "val_days",
                        {"days": logic.cfg_get(cfg, "skill_cooldown_days", 7)},
                    ),
                },
            ],
            "foot": _t("foot_skill"),
        },
        text=_t("text_my_skills", {"owned": owned, "learnable": all_sk}),
    )
