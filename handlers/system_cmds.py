"""系统指令路由：帮助、职场早报、四大排行榜。"""

import asyncio
import random
import re

from ..core import gamedata as gd
from ..core import logic, social
from ..core.career_common import make_title, tt
from ..core.result import R
from .base import Route, gid_of

_title = make_title("system")


def _t(key: str, variables: dict | None = None) -> str:
    """取 system.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("system", key, variables)


def _rank_aliases() -> dict[str, str]:
    """{触发词: 榜单 key}，单一来源 resources/data/rankings.json。

    榜单定义集中在 rankings.json，别名与路由正则都由这里生成，
    加一个榜只需改 rankings.json，无需再动 social.py 或本文件的任何硬编码。
    """
    out: dict[str, str] = {}
    for r in gd.rankings():
        key = str(r.get("key") or "")
        for alias in r.get("aliases") or []:
            if key and alias:
                out[str(alias)] = key
    return out


def _rank_pattern() -> str:
    """排行榜路由正则。模块导入时求值一次（gamedata 已在 initialize 预热前可读盘）。"""
    aliases = sorted(_rank_aliases(), key=len, reverse=True)
    if not aliases:
        aliases = ["富豪榜"]
    return r"^#(" + "|".join(re.escape(a) for a in aliases) + r")$"


def _help_fallback_text(help_data) -> str:
    """纯文本兜底帮助：由 help.json（单一来源）动态生成，避免与文案库漂移。"""
    lines = [_t("text_help_header"), _t("text_help_prefix_note")]
    for s in help_data:
        title = f"{s.get('icon', '')} {s.get('title', '')}"
        lines.append(title)
        for c in s.get("commands", []):
            usage = str(c.get("usage", "")).strip()
            desc = str(c.get("desc", "")).strip()
            lines.append(f"  {usage} — {desc}" if usage else f"  {desc}")
    return "\n".join(lines)


async def help_cmd(ctx, event):
    help_data = gd.t("help", "sections")
    section_cmds = sum(len(s.get("commands", [])) for s in help_data)
    # 与 README/CHANGELOG 一致：用插件实例上的 cmd_* handler 方法统计全量指令数
    star = getattr(ctx, "star", None)
    total_cmds = sum(1 for a in dir(star) if a.startswith("cmd_")) if star is not None else 0
    webui_port = int(ctx.c("webui_port", 17817))

    img = await ctx.render(
        "help",
        {
            "sections": help_data,
            "total_cmds": total_cmds,
            "section_cmds": section_cmds,
            "company_count": len(gd.companies()),
            "webui_port": webui_port,
        },
    )
    if img:
        return R(img=img)
    return R(text=_help_fallback_text(help_data))


async def news(ctx, event):
    headline = gd.news_of_day()
    return R(
        tmpl="panel",
        data={
            "icon": "📰",
            "title": _title("title_news", "职场早报 · 今日份的离谱"),
            "accent": "#ffd86f",
            "lines": [headline],
            "foot": _t("foot_news"),
        },
        text=_t("text_news", {"headline": headline}),
    )


async def rank_board(ctx, event):
    gid = gid_of(event)
    aliases = _rank_aliases()
    msg = event.message_str or ""
    # 长别名优先：避免「富豪榜单」被「富豪榜」抢先命中后残留后缀
    word = next((w for w in sorted(aliases, key=len, reverse=True) if w in msg), None)
    if not word:
        return R(err=_t("err_unknown_rank"))
    return await social.rank_data(ctx.db, gid, aliases[word], ctx.app_id, ctx.config)


async def today_event(ctx, event):
    today = logic.today_str()
    events_pool = gd.atmosphere_events()
    if not events_pool:
        # 兜底必须与 atmosphere.json 的事件同构（title/desc/accent 的 dict），
        # 写成 tuple 会让下面的 ev.get 直接 AttributeError
        events_pool = [
            {
                "title": _t("val_atmosphere_fallback_title"),
                "desc": _t("val_atmosphere_fallback_desc"),
                "accent": "#7fd1ff",
            }
        ]
    seed_val = int(today.replace("-", ""))
    rng = random.Random(seed_val)
    ev = rng.choice(events_pool)
    title = str(ev.get("title", "📢"))
    desc = str(ev.get("desc", ""))
    accent = str(ev.get("accent", "#7fd1ff"))

    return R(
        tmpl="panel",
        data={
            "icon": "📢",
            "title": f"{_title('title_today_event', '今日职场氛围')} · {today}",
            "accent": accent,
            "lines": [_t("line_today_title", {"title": title}), desc],
            "foot": _t("foot_today_event"),
        },
        text=_t("text_today_event", {"title": title, "desc": desc}),
    )


async def last_week_board(ctx, event):
    """上周财富榜快照：读 archives 表里 _weekly_archive 归档的那一份。"""
    gid = gid_of(event)
    prev_y, prev_w = logic.prev_iso_week(*logic.iso_week())
    payload = await asyncio.to_thread(ctx.db.last_review_payload, gid, prev_y, prev_w)
    top = (payload or {}).get("top") or []
    if not top:
        return R(err=_t("err_no_week_archive"))
    week = str((payload or {}).get("week") or f"{prev_y}-W{prev_w:02d}")
    rows = [
        {
            "rank": i,
            "name": str(item.get("name") or "?"),
            "id": "",
            "score": logic.fmt_money(item.get("total", 0)),
            "avatar": "",
        }
        for i, item in enumerate(top, start=1)
    ]
    return R(
        tmpl="ranking",
        data={
            "title": f"{week} {_title('title_last_week', '周榜快照（总资产）')}",
            "unit": _t("val_unit_yuan"),
            "rows": rows,
        },
        text=_t("text_last_week", {"week": week})
        + "\n".join(
            _t(
                "val_rank_row",
                {"rank": r["rank"], "name": r["name"], "score": r["score"]},
            )
            for r in rows
        ),
    )


ROUTES = [
    # 帮助 / 早报 / 今日氛围都是全服共享内容，不读任何群数据 → 私聊也能用
    #
    # priority 保持默认 0（不再用 5）：抬高优先级会让本插件抢在所有默认优先级的
    # 处理器之前执行，而本插件的 handler 在 finally 里一定会 stop_event()
    # （见 handlers/base.py），于是宿主上其它插件的帮助菜单会被整体吞掉。
    # 注意 priority=0 只是「不再抢跑」，并不保证别的插件能收到：同优先级按注册
    # 顺序执行，本插件先注册就仍然先消费掉这条消息。真要彻底不越界，#帮助 这类
    # 高频撞名指令应改用更不易冲突的别名。
    Route(
        r"^#(打工人|上班族|上班)?(帮助|菜单|功能)$",
        "cmd_help",
        "查看打工人·上班族物语帮助",
        help_cmd,
        group_only=False,
    ),
    Route(
        r"^#(职场早报|早报|新闻)$",
        "cmd_news",
        "查看今日职场早报",
        news,
        group_only=False,
    ),
    Route(
        r"^#(今日事件|群事件|突发事件)$",
        "cmd_today_event",
        "查看今日全群职场氛围播报",
        today_event,
        group_only=False,
    ),
    Route(
        _rank_pattern(),
        "cmd_rank_board",
        "查看职场排行榜（榜单定义见 resources/data/rankings.json）",
        rank_board,
    ),
    Route(
        r"^#(上周榜|周榜|上周榜单)$",
        "cmd_last_week_board",
        "查看上周归档的财富榜快照",
        last_week_board,
    ),
]
