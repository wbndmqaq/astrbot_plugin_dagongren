"""DB create_webui_session 等功能 Mixin（由 db.py 拆分）。"""

import time

from ._const import _write_lock


class _SessionMixin:
    def create_webui_session(
        self, jti: str, ip: str, ua: str, ttl: int, subject: str = "admin"
    ) -> None:
        now = int(time.time())
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO webui_sessions "
                    "(jti, subject, user_agent, ip, created_at, last_seen_at, expires_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        str(jti),
                        str(subject)[:32],
                        str(ua or "")[:256],
                        str(ip or "")[:64],
                        now,
                        now,
                        now + int(ttl),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_webui_session(self, jti: str, include_expired: bool = False) -> dict | None:
        """取会话。默认过滤已过期行（expires_at < now）。

        include_expired=True 用于诊断/审计场景；正常鉴权路径应保持默认。
        """
        conn = self._conn()
        try:
            if include_expired:
                row = conn.execute(
                    "SELECT jti, subject, user_agent, ip, created_at, last_seen_at, expires_at "
                    "FROM webui_sessions WHERE jti=?",
                    (str(jti),),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT jti, subject, user_agent, ip, created_at, last_seen_at, expires_at "
                    "FROM webui_sessions WHERE jti=? AND expires_at > ?",
                    (str(jti), int(time.time())),
                ).fetchone()
        finally:
            conn.close()
        if not row:
            return None
        d = dict(row)
        d["expires_at"] = int(d["expires_at"])
        d["created_at"] = int(d["created_at"])
        d["last_seen_at"] = int(d["last_seen_at"])
        return d

    def touch_webui_session(self, jti: str) -> bool:
        """滑动续期 last_seen_at。会话不存在或已过期返回 False。"""
        now = int(time.time())
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE webui_sessions SET last_seen_at=? WHERE jti=? AND expires_at > ?",
                    (now, str(jti), now),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def list_webui_sessions(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT jti, subject, user_agent, ip, created_at, last_seen_at, expires_at "
                "FROM webui_sessions WHERE expires_at > ? "
                "ORDER BY last_seen_at DESC",
                (int(time.time()),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def revoke_webui_session(self, jti: str) -> int:
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "DELETE FROM webui_sessions WHERE jti=?",
                    (str(jti),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def revoke_all_webui_sessions(self) -> int:
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM webui_sessions")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def revoke_other_webui_sessions(self, keep_jti: str) -> int:
        """撤销除 keep_jti 之外的全部会话，返回撤销条数。

        用一条 DELETE 代替「列出来再逐个调 revoke_webui_session」：后者是 N 次
        独立事务 + N 次抢写锁，中途失败会留下「撤销了一半」的状态，而面板只会
        报一句失败。
        """
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM webui_sessions WHERE jti != ?", (str(keep_jti),))
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def purge_expired_webui_sessions(self) -> int:
        """物理删除已过期的会话行，返回删除条数。

        与 cleanup_old_data 里那条 DELETE 同一口径，但单独成方法的理由是：
        cleanup_old_data 只在游戏推送循环里每天跑一次（还要扫全表、退红包、
        持写锁），推送循环一旦异常退出，webui_sessions 就再没人清理，会话表
        无上限增长。WebUI 自己的低频任务只需要「删过期会话」这一件事。
        """
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "DELETE FROM webui_sessions WHERE expires_at < ?",
                    (int(time.time()),),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
