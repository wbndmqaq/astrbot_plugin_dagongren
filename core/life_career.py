"""resume 等功能（由 life.py 拆分）。"""

import asyncio
import random
import time

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("life_career", key, variables)


_title = make_title("life_career")


async def resume(db, gid, uid, nickname, app_id: str = ""):
    # 简历是纯查看命令：既不能 get_player 顺手给陌生 ID 建号，也必须用严格
    # (gid, uid) 查询——find_player_any 的模糊匹配会在自己无档案时命中别人
    p = await asyncio.to_thread(db.get_player_row, gid, uid)
    if not p:
        return R(err=logic.no_record(_t("no_record_resume")))
    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    pos = gd.position(int(p["lvl"]))
    pos_list = gd.positions()
    nxt = pos_list[int(p["lvl"]) + 1] if int(p["lvl"]) + 1 < len(pos_list) else None
    hs = gd.house(int(p["house"]))

    total_assets = round(float(p["cash"]) + float(p["deposit"]) + float(p["fund"]), 2)
    progress = 1.0
    if nxt and nxt["need"] > 0:
        progress = min(1.0, int(p["exp"]) / max(1, nxt["need"]))

    return R(
        tmpl="resume",
        data={
            "me": {
                "name": p["nickname"] or f"用户{uid}",
                "id": uid,
                "avatar": logic.avatar_of(uid, app_id),
            },
            "company": comp,
            "position": pos["title"],
            "salary": logic.fmt_money(p["salary"]),
            "exp": int(p["exp"]),
            "next_title": nxt["title"] if nxt else None,
            "next_need": nxt["need"] if nxt else 0,
            "progress": round(progress * 100),
            "health": round(float(p["health"]), 1),
            "mind": round(float(p["mind"]), 1),
            "house": {"name": hs["name"], "rent": hs["rent"], "recover": hs["recover"]},
            "cash": logic.fmt_money(p["cash"]),
            "deposit": logic.fmt_money(p["deposit"]),
            "fund": logic.fmt_money(p["fund"]),
            "total": logic.fmt_money(total_assets),
            "streak": p["attend_streak"],
            "commute": gd.commute_name(p.get("commute") or ""),
            "fund_savings": logic.fmt_money(p.get("fund_savings") or 0),
            "comp_leave": int(p.get("comp_leave") or 0),
            "value": logic.fmt_money(p["value"]),
            "duel": _t(
                "val_duel_record",
                {"wins": p["duel_wins"], "losses": p["duel_losses"]},
            ),
            "rank": _t(
                "val_rank_score",
                {"tier": p["rank_tier"], "score": p["rank_score"]},
            ),
        },
        text=_t(
            "text_resume",
            {
                "company": comp["name"] if comp else _t("val_jobless"),
                "position": pos["title"],
                "salary": logic.fmt_money(p["salary"]),
                "exp": p["exp"],
                "health": p["health"],
                "mind": p["mind"],
                "house": hs["name"],
                "cash": logic.fmt_money(p["cash"]),
                "deposit": logic.fmt_money(p["deposit"]),
                "fund": logic.fmt_money(p["fund"]),
                "total": logic.fmt_money(total_assets),
                "value": logic.fmt_money(p["value"]),
                "commute": gd.commute_name(str(p.get("commute") or "")),
                "fund_savings": logic.fmt_money(p.get("fund_savings") or 0),
                "wins": p["duel_wins"],
                "losses": p["duel_losses"],
                "tier": p["rank_tier"],
            },
        ),
    )


async def payslip(db, gid, uid, nickname):
    # 纯查看命令不建号；同 resume，必须严格按 uid 查，否则会显示别人的账目
    p = await asyncio.to_thread(db.get_player_row, gid, uid)
    if not p:
        return R(err=logic.no_record(_t("no_record_payslip")))
    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    txs = await asyncio.to_thread(db.recent_transactions, gid, uid, 12)
    rows = []
    # recent_transactions 已是「最新在前」，这里直接按序渲染。
    # 此前写成 reversed(txs) 收集、payload 再补一次 [::-1]，两次反转正好抵消
    # （且 rows 最多 12 条，rows[-12:] 是空操作），读代码时极易误判真实展示顺序。
    for t in txs:
        amount = float(t["amount"])
        rows.append(
            {
                "cells": [
                    time.strftime("%m-%d %H:%M", time.localtime(t["created_at"])),
                    t["kind"],
                    _t(
                        "val_yuan",
                        {"amount": ("+" if amount >= 0 else "") + logic.fmt_money(amount)},
                    ),
                    t.get("note") or "",
                ],
                "fail": amount < 0,
            }
        )
    if not rows:
        rows.append(
            {
                "cells": ["-", _t("cell_no_tx"), "-", _t("cell_no_tx_hint")],
                "fail": True,
            }
        )
    return R(
        tmpl="table",
        data={
            "icon": "🧾",
            "title": _title("title_payslip", "我的工资条 · 收支流水"),
            "accent": "#7fd1ff",
            "summary": [
                {
                    "label": _t("lbl_total_earned"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["total_earned"])}),
                },
                {
                    "label": _t("lbl_fund_balance"),
                    "value": _t("val_yuan", {"amount": logic.fmt_money(p["fund_savings"])}),
                },
                {
                    "label": _t("lbl_cash_deposit"),
                    "value": _t(
                        "val_cash_deposit",
                        {
                            "cash": logic.fmt_money(p["cash"]),
                            "deposit": logic.fmt_money(p["deposit"]),
                        },
                    ),
                },
                {
                    "label": _t("lbl_salary_std"),
                    "value": (
                        _t("val_yuan", {"amount": logic.fmt_money(p["salary"])})
                        if comp
                        else _t("val_no_job")
                    ),
                },
            ],
            "cols": [_t("col_time"), _t("col_item"), _t("col_amount"), _t("col_note")],
            "rows": rows,
            "note": _t(
                "foot_payslip",
                {
                    "comp_leave": p.get("comp_leave", 0),
                    "commute": gd.commute_name(str(p.get("commute") or "")),
                },
            ),
        },
        text=_t(
            "text_payslip",
            {
                "total": logic.fmt_money(p["total_earned"]),
                "fund": logic.fmt_money(p["fund_savings"]),
            },
        ),
    )


async def train_self(db, gid, uid, nickname, cfg):
    """自费进修班：花现金提升身价与经验。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    cost = max(
        float(logic.cfg_get(cfg, "train_cost_min", 200)),
        round(
            float(p["value"])
            * float(logic.cfg_get(cfg, "train_cost_rate", 0.1))
            * float(logic.cfg_get(cfg, "train_cost_multiplier", 5))
        ),
    )
    if float(p["cash"]) < cost:
        return R(err=_t("err_train_cost", {"cost": logic.fmt_money(cost)}))
    cd = int(float(logic.cfg_get(cfg, "train_cooldown_hours", 2.0)) * 3600)
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "jinxiu") > 0:
        return R(
            err=_t("err_train_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "jinxiu"))})
        )
    logic.cd_set(p, "jinxiu", cd)
    p["cash"] = round(float(p["cash"]) - cost, 2)
    exp_gain = logic.ri(3, 8)

    if random.random() < float(logic.cfg_get(cfg, "train_success_rate", 0.7)):
        increase = round(
            float(p["value"]) * float(logic.cfg_get(cfg, "train_value_increase_rate", 0.2)),
            2,
        )
        p["value"] = round(float(p["value"]) + increase, 2)
        p["exp"] = int(p["exp"]) + exp_gain
        line = logic.pick(gd.t("company", "jinxiu_ok"))
        await asyncio.to_thread(db.save_player, p)
        await asyncio.to_thread(
            db.add_transaction, gid, uid, _t("kind_training_fee"), -cost, _t("train_success_note")
        )
        return R(
            tmpl="panel",
            data={
                "icon": "🎓",
                "title": _title("title_train_ok", "进修结业 · 能力提升"),
                "accent": "#6fe08c",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_tuition"),
                        "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                    },
                    {
                        "label": _t("lbl_value"),
                        "value": _t(
                            "val_value_up",
                            {
                                "increase": logic.fmt_money(increase),
                                "value": logic.fmt_money(p["value"]),
                            },
                        ),
                    },
                    {
                        "label": _t("lbl_exp"),
                        "value": _t("val_gain_cur", {"gain": exp_gain, "cur": p["exp"]}),
                    },
                ],
            },
            text=_t(
                "text_train_ok",
                {
                    "increase": logic.fmt_money(increase),
                    "value": logic.fmt_money(p["value"]),
                    "cost": logic.fmt_money(cost),
                },
            ),
        )

    p["exp"] = int(p["exp"]) + logic.ri(1, 3)
    line = logic.pick(gd.t("company", "jinxiu_fail"))
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_training_fee"), -cost, _t("train_fail_note")
    )
    return R(
        tmpl="panel",
        data={
            "icon": "📉",
            "title": _title("title_train_fail", "进修未果"),
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_tuition"),
                    "value": _t("val_tuition_wasted", {"cost": logic.fmt_money(cost)}),
                },
                {"label": _t("lbl_exp"), "value": _t("val_exp_little")},
            ],
        },
        text=_t("text_train_fail", {"line": line, "cost": logic.fmt_money(cost)}),
    )
