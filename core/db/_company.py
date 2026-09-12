"""DB get_custom_company_by_boss 等功能 Mixin（由 db.py 拆分）。"""

import time

from ._const import CUSTOM_BASE, _write_lock


class _CompanyMixin:
    def get_custom_company_by_boss(self, gid: str, boss_uid: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM custom_companies WHERE gid=? AND boss_uid=?",
                (str(gid), str(boss_uid)),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_custom_company(self, cid: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM custom_companies WHERE id=?", (int(cid),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def custom_companies_of_group(self, gid) -> list[dict]:
        """本群全部自建公司（求职/跳槽市场用）。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM custom_companies WHERE gid=? ORDER BY id", (str(gid),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_custom_company(self, cid: int) -> int:
        """删除自建公司并让其员工失业（创业扣款失败时的回滚路径）。"""
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET company=-1, salary=0 WHERE company=?",
                    (CUSTOM_BASE + int(cid),),
                )
                cur = conn.execute("DELETE FROM custom_companies WHERE id=?", (int(cid),))
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def add_custom_company_balance(self, cid: int, amount: float):
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE custom_companies SET balance=round(balance+?, 2) WHERE id=?",
                    (float(amount), int(cid)),
                )
                conn.commit()
            finally:
                conn.close()

    # 调用方漏传时的占位（见 _redpacket 同名注释）
    _KIND_DIVIDEND = "企业分红"

    def withdraw_custom_company_dividend(
        self, gid: str, boss_uid: str, note_tpl: str = "", kind: str = ""
    ) -> tuple[dict | None, float, str, int | None]:
        """提取公司分红：清零金库 + 老板入账 + 记流水，三者同一事务原子完成。

        之前实现把「清零公司金库」与「老板 credit_income 入账」拆成两个独立事务，
        若进程在两步之间崩溃（或入账抛异常），金库已清零但老板没收到钱——资金凭空消失。
        这里把三步合并进单个事务，任一步失败整体回滚，彻底消除跨事务窗口。
        返回 (公司行, 分红额, 状态码, 交易id)。
        """
        with _write_lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT * FROM custom_companies WHERE gid=? AND boss_uid=?",
                    (str(gid), str(boss_uid)),
                ).fetchone()
                if not row:
                    return None, 0.0, "not_boss", None
                balance = float(row["balance"] or 0)
                if balance <= 0:
                    return dict(row), 0.0, "zero_balance", None
                # 单一事务：清零金库 → 老板现金/累计收入原子入账 → 记流水。
                # 全部走列级原子更新（cash+? / total_earned+?），不依赖快照写回。
                conn.execute("UPDATE custom_companies SET balance=0 WHERE id=?", (row["id"],))
                if not self._credit_income_on(conn, gid, boss_uid, balance):
                    # 老板档案已不存在：整笔回滚，钱留在企业金库里
                    conn.rollback()
                    return dict(row), 0.0, "not_boss", None
                cur = conn.execute(
                    "INSERT INTO transactions (gid, uid, kind, amount, note, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(boss_uid),
                        kind or self._KIND_DIVIDEND,
                        balance,
                        note_tpl.replace("{company}", str(row["name"])),
                        int(time.time()),
                    ),
                )
                conn.commit()
                return dict(row), balance, "ok", cur.lastrowid
            finally:
                conn.close()

    def create_custom_company_if_free(
        self,
        gid: str,
        boss_uid: str,
        name: str,
        tag: str,
        salary: float,
        balance: float,
    ) -> int | None:
        """仅在老板尚未拥有公司时创建，返回公司 ID；已存在返回 None（防连发竞态）。"""
        with _write_lock:
            conn = self._conn()
            try:
                # 两道防重，缺一不可：
                # 1) 前置 SELECT —— 唯一索引在【已有重复行的老库】上根本建不起来
                #    （init() 刻意兜住失败不阻断启动），那种库里只剩这一道；
                # 2) INSERT OR IGNORE + rowcount —— SELECT 与 INSERT 之间仍有窗口，
                #    索引存在时由数据库兜住连发竞态。
                row = conn.execute(
                    "SELECT id FROM custom_companies WHERE gid=? AND boss_uid=?",
                    (str(gid), str(boss_uid)),
                ).fetchone()
                if row:
                    return None
                cur = conn.execute(
                    "INSERT OR IGNORE INTO custom_companies "
                    "(gid, boss_uid, name, tag, salary, balance, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(boss_uid),
                        str(name),
                        str(tag),
                        float(salary),
                        float(balance),
                        int(time.time()),
                    ),
                )
                conn.commit()
                # rowcount==0 = 唯一索引挡住了插入（该老板本群已有公司）。
                # 必须显式返回 None：此时 lastrowid 是【上一次插入】留下的 id，
                # 直接返回会把别人的公司认成刚新建的那一家。
                if cur.rowcount <= 0:
                    return None
                return cur.lastrowid
            finally:
                conn.close()
