"""WebUIServer 的 _ApiMixin：概览/群组/榜单/股票/备份等面板 API（拆分自原 webui/server.py，现为 webui/server/ 包成员）。"""

import asyncio

from ._deps import BatchEditResult, gd, logic
from ._util import _json


class _ApiMixin:
    async def _overview(self, request):
        stats = await asyncio.to_thread(self.db.event_stats)
        raw_events = await asyncio.to_thread(self.db.recent_events, 30)
        events = [
            {
                "gid": e["gid"],
                "uid": e["uid"],
                "kind": e["kind"],
                "summary": e["summary"],
                "time": int(e["created_at"]),
            }
            for e in raw_events
        ]
        return _json({"stats": stats, "events": events, "news": gd.news_of_day()})

    async def _groups(self, request):
        names = await asyncio.to_thread(self.db.all_group_names)
        gids = await asyncio.to_thread(self.db.group_ids)
        groups = [{"gid": g, "count": n, "name": names.get(g, "")} for g, n in gids]
        return _json({"groups": groups})

    async def _ranking(self, request):
        gid = request.query.get("gid", "")
        kind = request.query.get("kind", "wealth")
        if not gid:
            return _json({"error": "缺 gid"}, 400)
        # 白名单来自 resources/data/rankings.json，与群内榜单、路由别名同一来源
        valid = [str(r["key"]) for r in gd.rankings() if r.get("key")]
        if kind not in valid:
            kind = valid[0] if valid else "wealth"
        # 与群内榜单同源：都读 ranking_top_n，不再一边 10 条一边 15 条。
        # 走 parse_int：配置被填成非数字时不能让整个排行榜接口 500。
        top_n = logic.parse_int(self._c("ranking_top_n", 10), default=10, lo=1, hi=100) or 10
        if kind == "level":
            players = await asyncio.to_thread(self.db.top_level, gid, top_n)
            col = "lvl"
        elif kind == "wealth":
            players = await asyncio.to_thread(self.db.top_wealth, gid, top_n)
            col = "total"
        else:
            # 与群内榜单同源：rankings.json 的 key 就是 players 表的列名，直接按它
            # 排序。top_by_column 内部做列名白名单校验，非法（运维把 key 写错）才
            # 退回 value —— 旧实现把非 level/wealth/exp 一律当 value，新加的榜
            # 标题对了但排序与数值仍是身价。
            col = kind
            try:
                players = await asyncio.to_thread(self.db.top_by_column, gid, col, top_n)
            except ValueError:
                col = "value"
                players = await asyncio.to_thread(self.db.top_by_column, gid, col, top_n)
        rows = []
        names = await self._company_names(gid)
        for p in players:
            pos = gd.position(int(p.get("lvl", 1)))["title"]
            if kind == "level":
                score = f"L{p['lvl']} · {pos}"
            elif kind == "exp":
                score = f"{p['exp']} 点"
            elif kind == "wealth":
                score = logic.fmt_money(p.get("total", 0))
            else:
                score = logic.fmt_money(p.get(col, 0))
            rows.append(
                {
                    "rank": p.get("rank", 0),
                    "uid": p["uid"],
                    "nickname": logic.name_of(p),
                    "score": score,
                    "position": pos,
                    "company": gd.display_company(p.get("company", -1), names),
                }
            )
        return _json({"kind": kind, "rows": rows})

    async def _search(self, request):
        gid = request.query.get("gid", "")
        kw = request.query.get("kw", "")
        p = await asyncio.to_thread(self.db.find_player_any, gid, kw) if gid and kw else None
        if not p:
            return _json({"results": []})
        return _json({"results": [self.build_profile(p, await self._company_names(gid))]})

    async def _stock_list(self, request):
        await asyncio.to_thread(self.market.ensure_seeded)
        await self.market.settle_if_needed()
        stocks = await asyncio.to_thread(self.market.list_stocks, 100)
        # 地板价一并下发：面板输入框的 min 属性其实是 [0.5] 这个硬编码常量，
        # 运维把 stock_min_price 调高之后，浏览器提示仍写着 0.5、提交后服务端
        # 才拒绝，于是表现成「输入框说合法、保存却失败」。前端拿这个值渲染
        # min 与提示文案，面板与 server 端判据始终同源。
        return _json({"stocks": stocks, "min_price": self.market.min_price()})

    async def _stock_edit_batch(self, request):
        """批量改价：一次请求改多支股票。

        前端「保存改价」按钮此前是逐个 code 串行 POST —— 改 20 支就是 20 次
        往返，首个失败即中断且不报告已完成的部分。这里一次处理完，返回
        成功/失败清单，前端按结果如实提示。

        这里也是【唯一】的改价入口：旧的 POST /api/stocks/edit（单支）前端从
        不调用，且两条路径的价格校验规则不同（单支用 0<price<1e6 且允许改
        股票名），留着就是一个迟早改错一边的陷阱，已连同路由一起删除。

        下限必须用市场自己的 _min_price()（可配置，默认 0.5），不能写死 0：
        写死 0 时 0.3 这种价格能过这里的初筛，却被 db 层按 _min_price() 拒收，
        两类失败又被合并回同一条文案，最终表现成「代码不存在」这种谬误 ——
        运维提交一个真实存在的代码、得到一个假的诊断，只能反复重试。
        """
        body = await self._body(request)
        edits = body.get("edits")
        if not isinstance(edits, list) or not edits:
            return _json({"ok": False, "error": "没有要保存的改动"}, 400)
        if len(edits) > 500:
            return _json({"ok": False, "error": "单次最多 500 条"}, 400)
        floor = self.market.min_price()
        ceil = 1_000_000.0
        # 提示里带上具体区间（运维才知道该填多少）；上限用整数写法，
        # 别让 1e+06 这种科学计数法出现在面板提示里。
        price_err = f"价格非法（需 {floor:g} ~ {ceil:.0f} 元）"
        applied: list[str] = []
        failed: list[dict] = []
        # 先在这里把「非法价格」筛掉，剩下的合法改动合成一批交给 db 层：
        # 逐条 to_thread 会为每支股票新开一条 sqlite 连接（最多 500 次串行
        # 往返），长时间占着默认线程池，玩家的其它 db 操作都要排队。
        valid: dict[str, float] = {}
        for item in edits:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            try:
                price = float(item.get("price"))
            except (TypeError, ValueError):
                failed.append({"code": code, "error": f"价格不是数字（{price_err}）", "kind": "price"})
                continue
            if not (floor <= price < ceil):
                failed.append({"code": code, "error": price_err, "kind": "price"})
                continue
            valid[code] = price
        if valid:
            try:
                # 一次 to_thread：锁 + 单事务里批量 UPDATE
                result = await asyncio.to_thread(self.market.admin_edit_batch, valid)
            except Exception as e:  # noqa: BLE001 - 批量失败不能让整请求 500
                self.log.warning(f"[上班族物语] 批量改价失败：{e}")
                result = BatchEditResult(invalid=set(), missing=set(valid))
            for code in valid:
                # 两类失败分开回传（BatchEditResult），前端也分开提示：
                # 「代码不存在」与「价格不在允许区间」要的处置完全不同。
                if code in result.invalid:
                    failed.append({"code": code, "error": price_err, "kind": "price"})
                elif code in result.missing:
                    failed.append({"code": code, "error": "代码不存在", "kind": "code"})
                else:
                    applied.append(code)
        return _json({"ok": True, "applied": applied, "failed": failed, "min_price": floor})

    async def _stock_fluctuate(self, request):
        n = await asyncio.to_thread(self.market.admin_fluctuate_all)
        return _json({"ok": True, "fluctuated": n})

    async def _stock_randomize(self, request):
        n = await asyncio.to_thread(self.market.admin_set_price_all_random)
        return _json({"ok": True, "reset": n})

    async def _backup_list(self, request):
        items = await asyncio.to_thread(self.backups.list)
        return _json(
            {
                "backups": [
                    {"name": i["name"], "size_kb": i["size"] // 1024, "time": i["time"]}
                    for i in items
                ]
            }
        )

    async def _backup_create(self, request):
        body = await self._body(request)
        info = await asyncio.to_thread(self.backups.create, str(body.get("label", "")))
        return _json({"ok": True, "name": info["name"], "size_kb": info["size"] // 1024})

    async def _backup_restore(self, request):
        body = await self._body(request)
        item = await asyncio.to_thread(self.backups.restore, str(body.get("name", "")))
        if not item:
            return _json({"error": "未找到（名称需完整）"}, 404)
        if item.get("error"):
            return _json({"error": item["error"]}, 400)
        # 恢复后重跑建表，保证索引/表结构齐全
        await asyncio.to_thread(self.db.init)
        return _json({"ok": True, "restored": item["name"]})

    async def _backup_delete(self, request):
        body = await self._body(request)
        item = await asyncio.to_thread(self.backups.delete, str(body.get("name", "")))
        if not item:
            return _json({"error": "未找到"}, 404)
        return _json({"ok": True, "deleted": item["name"]})
