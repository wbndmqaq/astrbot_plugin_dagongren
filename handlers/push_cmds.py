"""推送开关指令路由。"""

import asyncio

from ..core.career_common import make_title, tt
from ..core.result import R
from .base import Route, gid_of

_title = make_title("push")


def _t(key: str, variables: dict | None = None) -> str:
    """取 push.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("push", key, variables)


async def toggle_push(ctx, event):
    gid = gid_of(event)
    # 单条 SQL 原子翻转：读一次再写回会让同群两人同时发「#推送」时丢掉一次翻转
    on = await asyncio.to_thread(ctx.db.toggle_push, gid)
    state = _t("state_on" if on else "state_off")
    body = _t("body_on" if on else "body_off")
    return R(
        tmpl="panel",
        data={
            "icon": "🔔" if on else "🔕",
            "title": f"{_title('title_push_toggle', '推送')}{state}",
            "accent": "#6fe08c" if on else "#fc6262",
            "lines": [body],
        },
        text=_t("text_toggle", {"state": state, "body": body}),
    )


async def push_status(ctx, event):
    gid = gid_of(event)
    on = await asyncio.to_thread(ctx.db.push_enabled, gid)
    groups = await asyncio.to_thread(ctx.db.push_group_ids)
    state = _t("val_on" if on else "val_off")
    return R(
        tmpl="panel",
        data={
            "icon": "📡",
            "title": _title("title_push_status", "推送状态"),
            "accent": "#7fd1ff",
            "lines": [_t("line_status")],
            "blocks": [
                {"label": _t("lbl_self"), "value": state},
                {"label": _t("lbl_groups"), "value": _t("val_groups", {"n": len(groups)})},
                {"label": _t("lbl_switch"), "value": _t("val_switch")},
            ],
        },
        text=_t("text_status", {"state": state, "n": len(groups)}),
    )


ROUTES = [
    # 推送开关是【群级】共享状态，任何成员都能把全群的每日早报关掉，
    # 因此和备份指令一样标为管理员专用（README 也把它归在「管理」栏）。
    Route(
        r"^#推送$",
        "cmd_push_toggle",
        "切换本群推送开关（管理员）",
        toggle_push,
        admin=True,
    ),
    Route(r"^#(推送状态|推送信息)$", "cmd_push_status", "查看推送状态", push_status),
]
