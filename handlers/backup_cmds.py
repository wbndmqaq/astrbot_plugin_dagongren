"""备份管理指令业务函数（管理员）。"""

import asyncio
import re

from ..core.career_common import make_title, tt
from ..core.result import R
from .base import Route

_title = make_title("backup")


def _t(key: str, variables: dict | None = None) -> str:
    """取 backup.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("backup", key, variables)


def _key_hint(example: str) -> str:
    """「恢复/删除备份」共用的「要带序号」提示，示例由调用方给。"""
    return _t("key_hint", {"example": example})


async def backup_create(ctx, event):
    m = re.search(r"备份\s+(\S+)", event.message_str or "")
    label = m.group(1) if m else ""
    info = await asyncio.to_thread(ctx.backups.create, label)
    if info is None:
        return R(err=_t("err_create_fail"))
    size_kb = info["size"] // 1024
    return R(
        tmpl="panel",
        data={
            "icon": "💾",
            "title": _title("title_backup_create", "备份创建成功"),
            "accent": "#6fe08c",
            "blocks": [
                {"label": _t("lbl_name"), "value": info["name"]},
                {"label": _t("lbl_size"), "value": f"{size_kb} KB"},
                {
                    "label": _t("lbl_location"),
                    "value": _t("val_backup_location"),
                },
                {
                    "label": _t("lbl_restore_way"),
                    "value": _t("restore_way_val", {"name": info["name"]}),
                },
            ],
            "foot": _t("foot_create", {"max_keep": ctx.backups.max_keep}),
        },
        text=_t("text_create", {"name": info["name"], "size": size_kb}),
    )


async def backup_list(ctx, event):
    items = await asyncio.to_thread(ctx.backups.list)
    if not items:
        return R(err=_t("err_empty"))
    rows = [
        {
            "cells": [str(i), it["name"], f"{it['size'] // 1024} KB", it["time"]],
            "fail": False,
        }
        for i, it in enumerate(items, 1)
    ]
    return R(
        tmpl="table",
        data={
            "icon": "🗄️",
            "title": f"{_title('title_backup_list', '备份列表')}（{len(items)}）",
            "accent": "#7fd1ff",
            "summary": [
                {"label": _t("lbl_count"), "value": _t("val_count", {"count": len(items)})}
            ],
            "cols": [_t("col_seq"), _t("col_name"), _t("col_size"), _t("col_time")],
            "rows": rows,
            "note": _t("note_list"),
        },
        text=_t("text_list")
        + "："
        + "；".join(f"{i}. {it['name']}" for i, it in enumerate(items, 1)),
    )


def _key(message: str, verb: str) -> str:
    m = re.search(rf"{verb}\s+(\S+)", message or "")
    return m.group(1) if m else ""


async def backup_restore(ctx, event):
    key = _key(event.message_str, "恢复备份")
    if not key:
        return R(err=_key_hint(_t("key_example_restore")))
    item = await asyncio.to_thread(ctx.backups.restore, key)
    if item is None:
        return R(err=_t("err_not_found", {"key": key}))
    if item.get("error"):
        return R(err=_t("err_aborted", {"error": item["error"]}))
    # 恢复后的库可能来自同版本的另一份快照，重跑一次建表保证索引/表齐全
    await asyncio.to_thread(ctx.db.init)
    return R(
        tmpl="panel",
        data={
            "icon": "♻️",
            "title": _title("title_backup_restore", "备份恢复完成"),
            "accent": "#6fe08c",
            "blocks": [
                {"label": _t("lbl_from"), "value": item["name"]},
                {"label": _t("lbl_effect"), "value": _t("effect_val")},
                {"label": _t("lbl_remind"), "value": _t("remind_val")},
            ],
        },
        text=_t("text_restore", {"name": item["name"]}),
    )


async def backup_delete(ctx, event):
    key = _key(event.message_str, "删除备份")
    if not key:
        return R(err=_key_hint(_t("key_example_delete")))
    item = await asyncio.to_thread(ctx.backups.delete, key)
    if item is None:
        return R(err=_t("err_not_found_del", {"key": key}))
    remain = await asyncio.to_thread(ctx.backups.list)
    return R(
        tmpl="panel",
        data={
            "icon": "🗑️",
            "title": _title("title_backup_delete", "备份已删除"),
            "accent": "#ffd86f",
            "blocks": [
                {"label": _t("lbl_deleted"), "value": item["name"]},
                {
                    "label": _t("lbl_remain"),
                    "value": _t("val_count", {"count": len(remain)}),
                },
            ],
        },
        text=_t("text_delete", {"name": item["name"]}),
    )


ROUTES = [
    # 备份是全库维护操作，四条都与群无关：group_only=False 才能在私聊里用
    Route(
        r"^#(创建备份|数据备份|备份)(?:\s+\S+)?$",
        "cmd_backup_create",
        "管理员：创建全量数据备份",
        backup_create,
        admin=True,
        group_only=False,
    ),
    Route(
        r"^#备份列表$",
        "cmd_backup_list",
        "管理员：查看所有备份",
        backup_list,
        admin=True,
        group_only=False,
    ),
    Route(
        r"^#恢复备份(?:\s*\S+)?$",
        "cmd_backup_restore",
        "管理员：恢复指定备份（序号或完整名称）",
        backup_restore,
        admin=True,
        group_only=False,
    ),
    Route(
        r"^#删除备份(?:\s*\S+)?$",
        "cmd_backup_delete",
        "管理员：删除指定备份（序号或完整名称）",
        backup_delete,
        admin=True,
        group_only=False,
    ),
]
