"""DB add_event 等功能 Mixin（由 db.py 拆分）。"""

import time

from ._const import _write_lock


class _EventsMixin:
    def add_event(self, gid, uid, kind: str, summary: str):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO events (gid, uid, kind, summary, created_at) VALUES (?,?,?,?,?)",
                    (str(gid), str(uid), kind, summary[:200], int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    def add_transaction(self, gid, uid, kind: str, amount: float, note: str = ""):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO transactions (gid, uid, kind, amount, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(uid),
                        kind,
                        round(float(amount), 2),
                        note[:100],
                        int(time.time()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def clear_events(self) -> int:
        """清空动态事件流（WebUI 管理操作）。返回删除行数。"""
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM events")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def recent_events(self, limit: int = 20) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def event_stats(self) -> dict:
        conn = self._conn()
        try:
            lt = time.localtime()
            today_start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
            total = conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
            groups = conn.execute("SELECT COUNT(DISTINCT gid) AS n FROM players").fetchone()["n"]
            today_events = conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE created_at >= ?", (today_start,)
            ).fetchone()["n"]
            richest = conn.execute(
                "SELECT nickname, gid, (cash+deposit+fund) AS total FROM players "
                "ORDER BY total DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return {
            "players": int(total),
            "groups": int(groups),
            "events_today": int(today_events),
            "richest": dict(richest) if richest else None,
        }
