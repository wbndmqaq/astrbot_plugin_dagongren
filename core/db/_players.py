"""DB remap_company_ids 等功能 Mixin（由 db.py 拆分）。"""

from ._const import CUSTOM_BASE, _escape_like, _write_lock


class _PlayersMixin:
    def remap_company_ids(self, mapping: dict[int, int], unemploy: list[int] | None = None) -> int:
        """批量重映射 players.company（旧公司ID → 新公司ID）。

        使用负数偏移两段式更新，避免 ID 交换场景下互相覆盖；
        unemploy 中的 ID 对应玩家置为失业(-1)。返回受影响玩家数。
        """
        changed = 0
        off = 1_000_000
        # old == new 的条目根本不需要搬：两段式里第二段会 continue 跳过它们，
        # 若第一段仍无条件偏移，这批玩家会永久停在 company = old - 1000000
        # 这个既非失业(-1)也非任何公司的坏值上。
        moving = {int(o): int(n) for o, n in mapping.items() if int(o) != int(n)}
        with _write_lock:
            conn = self._conn()
            try:
                for old in moving:
                    conn.execute(
                        "UPDATE players SET company=? WHERE company=?",
                        (old - off, old),
                    )
                for old, new in moving.items():
                    cur = conn.execute(
                        "UPDATE players SET company=? WHERE company=?",
                        (new, old - off),
                    )
                    changed += cur.rowcount
                for old in unemploy or []:
                    cur = conn.execute("UPDATE players SET company=-1 WHERE company=?", (int(old),))
                    changed += cur.rowcount
                conn.commit()
            finally:
                conn.close()
        return changed

    def delete_player(self, gid, uid):
        gid, uid = str(gid), str(uid)
        with _write_lock:
            conn = self._conn()
            try:
                # 该玩家作为【老板】的自建公司必须一并清掉，否则留下孤儿公司：
                # 老板档案已删、公司行还在，员工打卡继续用 add_custom_company_balance
                # 往金库充钱，而金库永远无人可提（提现要求 boss 行存在，否则
                # not_boss），公司也再无删除入口 —— 钱只进不出。
                # 员工按「公司已解散」处理，置为失业并清空薪资，与 _do_layoff 一致
                # （否则会留下「无业却带薪」的坏档案）。
                cids = [
                    int(r["id"])
                    for r in conn.execute(
                        "SELECT id FROM custom_companies WHERE gid=? AND boss_uid=?", (gid, uid)
                    ).fetchall()
                ]
                for cid in cids:
                    conn.execute(
                        "UPDATE players SET company=-1, salary=0 WHERE gid=? AND company=?",
                        (gid, CUSTOM_BASE + cid),
                    )
                    conn.execute("DELETE FROM custom_companies WHERE id=?", (cid,))
                conn.execute("DELETE FROM players WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM player_items WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM player_skills WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM player_cds WHERE gid=? AND uid=?", (gid, uid))
                # 清掉该玩家名下的孤儿数据，避免换号/销号后残留脏行。
                # 仅清理严格归属单人的表；redpackets/archives 是群内社交/历史记录
                # （收件人可能仍在游），刻意保留。
                conn.execute("DELETE FROM transactions WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM events WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM portfolio WHERE gid=? AND uid=?", (gid, uid))
                conn.execute("DELETE FROM lottery_tickets WHERE gid=? AND uid=?", (gid, uid))
                conn.commit()
            finally:
                conn.close()

    def count_players(self, gid) -> int:
        conn = self._conn()
        try:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM players WHERE gid=?", (str(gid),)
                ).fetchone()["n"]
            )
        finally:
            conn.close()

    def market_players(self, gid, n: int = 30) -> list[dict]:
        """同事录/人才市场用：按职级、月薪降序取前 n 人。

        原先是 all_players() 拉全表 + 在事件循环上 sort()；排序交给 SQL 后
        既省内存也省 CPU（500 人的群不再每次都全量转换 + 全量排序）。
        """
        n = max(1, min(200, int(n)))
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM players WHERE gid=? "
                "ORDER BY lvl DESC, salary DESC, cash DESC LIMIT ?",
                (str(gid), n),
            ).fetchall()
            return self._rows_to_players(conn, rows)
        finally:
            conn.close()

    def random_players(self, gid, n: int = 1, exclude_uid=None) -> list[dict]:
        """随机取 n 名本群玩家（八卦/随机事件用）。

        以前调用方是 all_players() + random.sample——为了挑两个人把整群读进
        内存（还带 3N 条子表查询）。ORDER BY RANDOM() LIMIT n 让 SQLite 做。
        """
        n = max(1, min(50, int(n)))
        conn = self._conn()
        try:
            if exclude_uid is None:
                rows = conn.execute(
                    "SELECT * FROM players WHERE gid=? ORDER BY RANDOM() LIMIT ?",
                    (str(gid), n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM players WHERE gid=? AND uid<>? ORDER BY RANDOM() LIMIT ?",
                    (str(gid), str(exclude_uid), n),
                ).fetchall()
            return self._rows_to_players(conn, rows)
        finally:
            conn.close()

    def page_players(self, gid, page: int = 1, size: int = 20) -> tuple[int, list[dict]]:
        """分页取玩家（WebUI 管理列表用）。

        避免 all_players 一次性把整组玩家读进内存再切片：
        直接带 LIMIT/OFFSET 只取当前页，另用 COUNT 拿总数做分页。
        """
        page = max(1, int(page))
        size = max(1, min(200, int(size)))
        offset = (page - 1) * size
        conn = self._conn()
        try:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM players WHERE gid=?", (str(gid),)
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT * FROM players WHERE gid=? ORDER BY cash DESC LIMIT ? OFFSET ?",
                (str(gid), size, offset),
            ).fetchall()
            return int(total), self._rows_to_players(conn, rows)
        finally:
            conn.close()

    def get_player_row(self, gid, uid) -> dict | None:
        """严格按 (gid, uid) 取玩家，不存在返回 None，绝不建档。

        「查看自己」的纯读指令必须用它而不是 find_player_any：后者是给
        「@某人 / 按名字找人」用的模糊匹配，uid LIKE '%123%' 在自己没有档案
        时会命中群里的 1234，于是简历/工资条会把【别人的】现金存款基金
        贴上你的名字显示出来。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM players WHERE gid=? AND uid=?", (str(gid), str(uid))
            ).fetchone()
            return self._row_to_player(conn, row) if row else None
        finally:
            conn.close()

    def find_player_any(self, gid, kw: str) -> dict | None:
        """按 uid 精确 / uid·昵称·群名片模糊匹配定位玩家。

        匹配优先级必须显式排序，不能靠「LIMIT 1 恰好先撞上哪行」：群里同时有
        123 和 1234 时，%123% 也命中 1234 的 uid，「#转账 500 @123」就会把钱
        打给 1234。ORDER BY 让精确 uid > 精确昵称/名片 > 模糊，同档再按 uid 稳定。

        LIKE 的 % 与 _ 必须转义：否则 kw="%" 会命中任意玩家，
        让「@某人」类指令与面板搜索都能被一个通配符糊过去。
        """
        raw = str(kw)
        like = f"%{_escape_like(kw)}%"
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM players WHERE gid=? AND (uid=? "
                "OR uid LIKE ? ESCAPE '\\' "
                "OR nickname LIKE ? ESCAPE '\\' "
                "OR card LIKE ? ESCAPE '\\') "
                "ORDER BY (uid=?) DESC, (nickname=?) DESC, (card=?) DESC, uid "
                "LIMIT 1",
                (str(gid), raw, like, like, like, raw, raw, raw),
            ).fetchone()
            return self._row_to_player(conn, row) if row else None
        finally:
            conn.close()

    def set_card(self, gid, uid, card: str):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET card=?, nickname=CASE WHEN nickname='' THEN ? ELSE nickname END "
                    "WHERE gid=? AND uid=?",
                    (card[:50], card[:50], str(gid), str(uid)),
                )
                conn.commit()
            finally:
                conn.close()

    def recent_transactions(self, gid, uid, limit: int = 15) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT kind, amount, note, created_at FROM transactions "
                "WHERE gid=? AND uid=? ORDER BY id DESC LIMIT ?",
                (str(gid), str(uid), limit),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
