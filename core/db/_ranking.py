"""DB group_ids 等功能 Mixin（由 db.py 拆分）。"""

import json
import time

from ._const import COLUMNS, _write_lock


class _RankingMixin:
    def group_ids(self) -> list[tuple[str, int]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT gid, COUNT(*) AS n FROM players GROUP BY gid ORDER BY n DESC"
            ).fetchall()
        finally:
            conn.close()
        return [(r["gid"], int(r["n"])) for r in rows]

    def top_by_column(self, gid, column: str, n: int = 10) -> list[dict]:
        if column not in COLUMNS:  # 显式校验：assert 在 python -O 下会被剥离
            raise ValueError(f"bad column {column}")
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT * FROM players WHERE gid=? ORDER BY {column} DESC LIMIT ?",  # noqa: S608 - 列名已白名单校验
                (str(gid), n),
            ).fetchall()
            out = self._rows_to_players(conn, rows)
            for i, p in enumerate(out):
                p["rank"] = i + 1
            return out
        finally:
            conn.close()

    def top_wealth(self, gid, n: int = 10) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT *, (cash+deposit+fund) AS total FROM players WHERE gid=? "
                "ORDER BY total DESC LIMIT ?",
                (str(gid), n),
            ).fetchall()
            out = self._rows_to_players(conn, rows)
            for i, (p, r) in enumerate(zip(out, rows, strict=True)):
                p["total"] = round(float(r["total"]), 2)
                p["rank"] = i + 1
            return out
        finally:
            conn.close()

    def top_level(self, gid, n: int = 10) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM players WHERE gid=? ORDER BY lvl DESC, salary DESC LIMIT ?",
                (str(gid), n),
            ).fetchall()
            out = self._rows_to_players(conn, rows)
            for i, p in enumerate(out):
                p["rank"] = i + 1
            return out
        finally:
            conn.close()

    def push_enabled(self, gid: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT enabled FROM push_groups WHERE gid=?", (str(gid),)
            ).fetchone()
            return bool(row and row["enabled"])
        finally:
            conn.close()

    def toggle_push(self, gid: str) -> bool:
        """原子翻转本群推送开关，返回翻转后的状态。

        指令层的「读 push_enabled → 写 set_push(not cur)」跨两次线程调用，而
        install() 的指令锁只按 (群, 用户) 串行化：同群两个人同时发「#推送」会
        各自读到同一个旧值，其中一次翻转被静默吞掉（面板还显示成功）。
        """
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO push_groups (gid,enabled,updated_at) VALUES (?,1,?) "
                    "ON CONFLICT(gid) DO UPDATE SET enabled=1-enabled,"
                    "updated_at=excluded.updated_at",
                    (str(gid), int(time.time())),
                )
                row = conn.execute(
                    "SELECT enabled FROM push_groups WHERE gid=?", (str(gid),)
                ).fetchone()
                conn.commit()
                return bool(row and row["enabled"])
            finally:
                conn.close()

    def push_last_date(self, gid: str) -> str:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT last_push FROM push_groups WHERE gid=?", (str(gid),)
            ).fetchone()
            return str(row["last_push"] or "") if row else ""
        finally:
            conn.close()

    def mark_pushed(self, gid: str, date_str: str):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE push_groups SET last_push=?, updated_at=? WHERE gid=?",
                    (str(date_str), int(time.time()), str(gid)),
                )
                conn.commit()
            finally:
                conn.close()

    def push_group_ids(self) -> list:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT gid FROM push_groups WHERE enabled=1").fetchall()
            return [r["gid"] for r in rows]
        finally:
            conn.close()

    def set_group_name(self, gid, name: str):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO group_info (gid,name,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(gid) DO UPDATE SET name=excluded.name,"
                    "updated_at=excluded.updated_at",
                    (str(gid), str(name)[:60], int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    def all_group_names(self) -> dict[str, str]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT gid, name FROM group_info").fetchall()
            return {r["gid"]: r["name"] for r in rows if r["name"]}
        finally:
            conn.close()

    def has_archive(self, gid, year: int, week: int) -> bool:
        """该群是否已归档指定周。

        归档是【逐群】写入的，所以判据也必须逐群：早先用全局「所有群里最新的
        一周」当水位线时，只要有一个群写成功，本周内的后续每天都会直接跳过，
        中途写入失败的群与本周新进的群那一周的快照就永久丢了。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM archives WHERE gid=? AND year=? AND week=? LIMIT 1",
                (str(gid), int(year), int(week)),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def save_archive(self, gid, year: int, week: int, payload: dict):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "DELETE FROM archives WHERE gid=? AND year=? AND week=?",
                    (str(gid), year, week),
                )
                conn.execute(
                    "INSERT INTO archives (gid, year, week, payload, created_at) VALUES (?,?,?,?,?)",
                    (
                        str(gid),
                        year,
                        week,
                        json.dumps(payload, ensure_ascii=False),
                        int(time.time()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_archive(self, gid, year: int, week: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT payload FROM archives WHERE gid=? AND year=? AND week=?",
                (str(gid), year, week),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def last_review_payload(self, gid, prev_year: int, prev_week: int) -> dict | None:
        """取指定归档周的周报；没有则退回最新一份已归档周。

        「上一周」的 ISO 周计算由调用方（core/review.py）用 logic.prev_iso_week
        完成——归档写入端 main.py 用的是同一个函数，两边必须同源，
        否则一边按 (2024,52) 写、一边按 (2024,1) 读，永远命中不到。
        """
        data = self.get_archive(gid, int(prev_year), int(prev_week))
        if data is None:
            # 退回该群自己的最新一份归档周，而不是「所有群里最新的一周」：
            # 后者属于别的群时 get_archive(gid,...) 会拿到 None，
            # 让考评显示静默降级。
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT year, week FROM archives WHERE gid=? "
                    "ORDER BY year DESC, week DESC LIMIT 1",
                    (str(gid),),
                ).fetchone()
            finally:
                conn.close()
            if row:
                data = self.get_archive(gid, int(row["year"]), int(row["week"]))
        return data
