"""WebUIServer 的 _ProfileMixin：玩家档案的公司名解析与序列化。

从 _core.py 拆出来。_CoreMixin 原来混装了配置读取 / Host-Origin-代理判定 /
限流 / JWT+会话鉴权 / schema 载入 / 玩家档案序列化六件事，这是其中「面板要展示
玩家画像」的那一件：排行榜、搜索、玩家列表、玩家管理都要用它，但和其它五件
没有任何关系。
"""

import asyncio

from ._deps import gd, logic


class _ProfileMixin:
    async def _company_names(self, gid) -> dict:
        """{players.company 值: 公司名} 映射，含本群自建公司。

        排行榜/搜索/档案/玩家管理等多处要展示几十个玩家的公司名，
        一次查出本群自建公司即可，不必逐玩家查库。与群内榜单
        （core/social.py）同源，避免自建公司员工在面板里显示成「失业」。
        """
        rows = await asyncio.to_thread(self.db.custom_companies_of_group, gid)
        return gd.company_names(rows)

    def build_profile(self, p, names: dict | None = None):
        names = names if names is not None else gd.company_names()
        cid = int(p["company"])
        comp_name = gd.display_company(cid, names, jobless="失业中")
        house = gd.house(int(p["house"]))
        disp = logic.name_of(p)
        return {
            "gid": p["gid"],
            "uid": p["uid"],
            "nickname": disp,
            "avatar": logic.avatar_of(p["uid"], self._app_id()),
            "company": comp_name,
            "tag": "自建企业" if cid >= gd.CUSTOM_BASE else "",
            "position": gd.position(int(p["lvl"]))["title"],
            "salary": logic.fmt_money(p["salary"]),
            "exp": int(p["exp"]),
            "health": float(p["health"]),
            "mind": float(p["mind"]),
            "house": {"name": house["name"], "rent": house["rent"]},
            "cash": logic.fmt_money(p["cash"]),
            "deposit": logic.fmt_money(p["deposit"]),
            "fund": logic.fmt_money(p["fund"]),
            "total": logic.fmt_money(
                round(float(p["cash"]) + float(p["deposit"]) + float(p["fund"]), 2)
            ),
            "value": logic.fmt_money(p["value"]),
            "streak": int(p["attend_streak"]),
            "duel": f"{p['duel_wins']}胜{p['duel_losses']}负",
            "tier": f"{p['rank_tier']}（{p['rank_score']}分）",
            "commute": gd.commute_name(p.get("commute") or ""),
            "fund_savings": logic.fmt_money(p.get("fund_savings") or 0),
            "comp_leave": int(p.get("comp_leave") or 0),
            "updated": int(p["updated_at"]),
        }
