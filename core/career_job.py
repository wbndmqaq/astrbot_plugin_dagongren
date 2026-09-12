"""find_job 等功能（由 career.py 拆分）。"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import hiring_pool, make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("career_job", key, variables)


_title = make_title("career_job")


async def find_job(db, gid, uid, nickname, cfg, want: str = ""):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) != -1:
        return R(err=_t("err_has_job"))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "job") > 0:
        return R(err=_t("err_job_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "job"))}))

    pool = await hiring_pool(db, gid)
    eligible = [c for c in pool if c["min_exp"] <= int(p["exp"])]
    want = (want or "").strip()

    if want == "" and not eligible:
        return R(err=_t("err_no_eligible"))

    if want:
        target = None
        for c in pool:
            if want == c["name"] or want in c["name"] or c["tag"] == want:
                target = c
                break
        if target is None:
            names = "、".join(c["name"] for c in pool)
            return R(err=_t("err_no_company", {"name": want, "names": names}))
        if target not in eligible:
            need = target["min_exp"]
            return R(
                err=_t(
                    "err_low_exp",
                    {"name": target["name"], "need": need, "exp": p["exp"]},
                )
            )
        offer = target
        success_rate = float(logic.cfg_get(cfg, "job_apply_targeted_rate", 0.85))
    else:
        best = max(eligible, key=lambda c: c["salary"])
        best_rate = float(logic.cfg_get(cfg, "job_best_offer_rate", 0.7))
        offer = best if random.random() < best_rate else random.choice(eligible)
        success_rate = float(logic.cfg_get(cfg, "job_apply_auto_rate", 0.92))

    if random.random() > success_rate:
        logic.cd_set(p, "job", float(logic.cfg_get(cfg, "job_cooldown_minutes", 10)) * 60)
        await asyncio.to_thread(db.save_player, p)
        fail_line = logic.pick(gd.t("work", "job_fail"))
        return R(
            tmpl="panel",
            data={
                "icon": "📄",
                "title": _title("title_interview_fail", "面试失败"),
                "accent": "#fc6262",
                "lines": [fail_line],
                "foot": _t(
                    "foot_interview_fail",
                    {
                        "exp": p["exp"],
                        "cd": f"{logic.cfg_get(cfg, 'job_cooldown_minutes', 10):g}",
                    },
                ),
            },
            text=_t("text_interview_fail", {"line": fail_line}),
        )

    p["company"] = offer["id"]
    pos = gd.position(int(p["lvl"]))
    p["salary"] = logic.base_salary_of(p, offer["salary"], pos["mult"])
    logic.cd_set(p, "job", float(logic.cfg_get(cfg, "job_cooldown_minutes", 10)) * 60)
    await asyncio.to_thread(db.save_player, p)
    name = logic.name_of(p, uid)
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_onboard"),
        _t(
            "ev_onboard",
            {"name": name, "company": offer["name"], "title": pos["title"]},
        ),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🎉",
            "title": _title("title_offer_ok", "Offer 到手！"),
            "accent": "#6fe08c",
            "lines": [logic.pick(gd.t("work", "job_offer"))],
            "blocks": [
                {
                    "label": _t("lbl_company"),
                    "value": _t("val_company", {"name": offer["name"], "tag": offer["tag"]}),
                },
                {"label": _t("lbl_position"), "value": pos["title"]},
                {
                    "label": _t("lbl_salary"),
                    "value": _t("val_salary", {"salary": logic.fmt_money(p["salary"])}),
                },
                {"label": _t("lbl_company_desc"), "value": offer["desc"]},
                {
                    "label": _t("lbl_perks"),
                    "value": "、".join(offer.get("perks") or []) or _t("val_perks_none"),
                },
            ],
            "foot": _t("foot_offer_ok"),
        },
        text=_t(
            "text_offer_ok",
            {
                "name": offer["name"],
                "title": pos["title"],
                "salary": logic.fmt_money(p["salary"]),
            },
        ),
    )


async def job_hop(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_hop_unemployed"))
    cd_key = "hop"
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, cd_key) > 0:
        return R(err=_t("err_hop_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, cd_key))}))
    cur_id = int(p["company"])
    pool = await hiring_pool(db, gid)
    eligible = [c for c in pool if c["min_exp"] <= int(p["exp"]) and c["id"] != cur_id]
    if not eligible:
        return R(err=_t("err_hop_peak"))
    best = max(eligible, key=lambda c: c["salary"])
    target = (
        best
        if random.random() < float(logic.cfg_get(cfg, "job_best_offer_rate", 0.7))
        else random.choice(eligible)
    )

    hop_cd = float(logic.cfg_get(cfg, "hop_cooldown_hours", 1)) * 3600
    if random.random() > float(logic.cfg_get(cfg, "job_hop_success_rate", 0.75)):
        logic.cd_set(p, cd_key, hop_cd)
        await asyncio.to_thread(db.save_player, p)
        fail_line = logic.pick(gd.t("work", "hop_fail"))
        return R(
            tmpl="panel",
            data={
                "icon": "🙃",
                "title": _title("title_hop_fail", "跳槽失败"),
                "accent": "#fc6262",
                "lines": [fail_line],
                "foot": _t(
                    "foot_hop_fail",
                    {"cd": f"{logic.cfg_get(cfg, 'hop_cooldown_hours', 1):g}"},
                ),
            },
            text=_t("text_hop_fail", {"line": fail_line}),
        )

    old_comp_obj = await asyncio.to_thread(gd.resolve_company, cur_id, db)
    old_comp = old_comp_obj["name"] if old_comp_obj else _t("fallback_old_company")
    p["company"] = target["id"]
    pos = gd.position(int(p["lvl"]))
    p["salary"] = logic.base_salary_of(p, target["salary"], pos["mult"])
    logic.cd_set(p, cd_key, hop_cd)
    await asyncio.to_thread(db.save_player, p)
    name = logic.name_of(p, uid)
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_hop"),
        _t("ev_hop", {"name": name, "old": old_comp, "new": target["name"]}),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "🐎",
            "title": _title("title_hop_ok", "跳槽成功！"),
            "accent": "#ffd86f",
            "lines": [logic.pick(gd.t("work", "hop_ok"))],
            "blocks": [
                {"label": _t("lbl_old_company"), "value": old_comp},
                {
                    "label": _t("lbl_new_company"),
                    "value": _t("val_company", {"name": target["name"], "tag": target["tag"]}),
                },
                {
                    "label": _t("lbl_new_salary"),
                    "value": _t(
                        "val_new_salary_same_level",
                        {
                            "salary": logic.fmt_money(p["salary"]),
                            "title": pos["title"],
                        },
                    ),
                },
            ],
        },
        text=_t(
            "text_hop_ok",
            {
                "old": old_comp,
                "new": target["name"],
                "salary": logic.fmt_money(p["salary"]),
            },
        ),
    )


async def resign_job(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_resign_none"))
    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    comp_name = comp["name"] if comp else _t("fallback_unknown_company")
    p["company"] = -1
    p["salary"] = 0.0
    p["attend_streak"] = 0
    # 「裸辞解脱感」+10 精神带冷却：辞职本身零成本，而 #找工作 只有 10 分钟冷却，
    # 于是「辞职 → 找工作 → 辞职」能把精神刷成无限资源，把摸鱼（60 分钟冷却）与
    # 请假（每周 2 次）这两个设计好的回血入口整体架空。加冷却后每次离职最多回一次。
    mind_gain = 0.0
    if logic.is_exempt(cfg, uid) or logic.cd_left(p, "resign") <= 0:
        mind_gain = 10.0
        logic.cd_set(
            p,
            "resign",
            float(logic.cfg_get(cfg, "resign_mind_cooldown_hours", 24.0)) * 3600,
        )
    p["mind"] = round(float(p["mind"]) + mind_gain, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    name = logic.name_of(p, uid)
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_resign"),
        _t("ev_resign", {"name": name, "company": comp_name}),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "✈️",
            "title": _title("title_resign_ok", "裸辞快乐！"),
            "accent": "#6fe08c",
            "lines": [logic.pick(gd.t("work", "resign_texts"))],
            "blocks": [
                {"label": _t("lbl_former_company"), "value": comp_name},
                {
                    "label": _t("lbl_mind"),
                    "value": _t(
                        "val_mind_up10" if mind_gain else "val_mind_unchanged",
                        {"mind": f"{p['mind']}"},
                    ),
                },
                {"label": _t("lbl_notice"), "value": _t("val_resign_notice")},
            ],
        },
        text=_t("text_resign_ok", {"company": comp_name}),
    )
