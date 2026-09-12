"""跳槽市场指令路由。"""

import asyncio

from ..core import career, logic
from ..core.career_common import make_title, tt
from ..core.result import R
from .base import Route, gid_of


def _t(key: str, variables: dict | None = None) -> str:
    return tt("career_job", key, variables)


_title = make_title("career_job")


async def job_market(ctx, event):
    """跳槽市场：查看全部在招公司（含群友自建公司）。"""
    gid = gid_of(event)
    companies = await career.hiring_pool(ctx.db, gid)
    companies.sort(key=lambda c: c["min_exp"])
    # 纯查看命令不建号：未入档玩家按 0 经验展示，避免顺手创建幽灵玩家
    p = await asyncio.to_thread(ctx.db.get_player_row, gid, str(event.get_sender_id()))
    exp = int(p["exp"]) if p else 0
    eligible = [c for c in companies if c["min_exp"] <= exp]
    return R(
        tmpl="table",
        data={
            "icon": "📋",
            "title": _title("title_job_market", "跳槽市场") + _t("title_market_exp", {"exp": exp}),
            "accent": "#7fd1ff",
            "summary": [
                {
                    "label": _t("lbl_hiring_count"),
                    "value": _t("val_company_count", {"count": len(companies)}),
                },
                {
                    "label": _t("lbl_eligible_count"),
                    "value": _t("val_company_count", {"count": len(eligible)}),
                },
            ],
            "cols": [
                _t("col_company"),
                _t("col_salary"),
                _t("col_min_exp"),
                _t("col_risk"),
            ],
            "rows": [
                {
                    "cells": [
                        _t("val_company", {"name": c["name"], "tag": c["tag"]}),
                        _t("val_salary", {"salary": logic.fmt_money(c["salary"])}),
                        _t("val_min_exp", {"exp": c["min_exp"]}),
                        _t("val_risk_pct", {"risk": f"{c['risk'] * 100:.1f}"}),
                    ],
                    "fail": c["risk"] > 0.05,
                }
                for c in companies
            ],
        },
        text=_t("text_market_head")
        + "；".join(
            _t(
                "text_market_item",
                {"name": c["name"], "salary": logic.fmt_money(c["salary"])},
            )
            for c in companies
        ),
    )


ROUTES = [
    Route(r"^#(跳槽市场|招聘市场)$", "cmd_job_market", "查看全部在招公司和薪资范围", job_market),
]
