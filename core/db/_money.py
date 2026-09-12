"""DB try_debit_cash 等功能 Mixin（由 db.py 拆分）。"""

from ._const import _write_lock


class _MoneyMixin:
    def try_debit_cash(self, gid, uid, amount: float) -> bool:
        """条件扣款：余额不足时 rowcount=0 返回 False，不产生负资产。

        金额必须为正：amount=-500 时 `cash>=-500` 恒真且 `cash-(-500)` 是加钱，
        等于把扣款接口当印钞机用，因此非正数直接拒绝。
        """
        amount = round(float(amount), 2)
        if amount <= 0:
            return False
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE players SET cash=round(cash-?,2) WHERE gid=? AND uid=? AND cash>=?",
                    (amount, str(gid), str(uid), amount),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def add_cash_atomic(self, gid, uid, amount: float):
        """列级原子【入账】：现金与累计总收入同时增加（不校验下限，调用方保证语义）。

        只改 cash 会让 #工资条 的两个口径对不上 —— 它把「累计总收入」与收支
        流水并排显示。这里是「给同一群的另一个玩家打钱」的唯一入口（社交奖励），
        对方档案由他本人的并发指令维护，不能走 save_player 的快照写回，
        所以必须和 _credit_income_on 保持同一口径。
        """
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET cash=round(cash+?,2), "
                    "total_earned=round(total_earned+?,2) WHERE gid=? AND uid=?",
                    (float(amount), float(amount), str(gid), str(uid)),
                )
                conn.commit()
            finally:
                conn.close()

    # 入账（现金 + 累计总收入）的唯一 SQL。红包领取、彩票派奖、公司分红三处
    # 都在【自己的事务】里入账，所以不能各自调一个新开连接的方法——此前它们
    # 是三份逐字复制的 UPDATE，改一处就会漏两处。
    _CREDIT_INCOME_SQL = (
        "UPDATE players SET cash=round(cash+?,2), "
        "total_earned=round(total_earned+?,2) WHERE gid=? AND uid=?"
    )

    @classmethod
    def _credit_income_on(cls, conn, gid, uid, amount: float) -> bool:
        """在【调用方事务】里入账并同步累计总收入；返回是否真的命中玩家行。

        返回 False 表示收款人档案已不存在，调用方必须据此回滚，
        否则钱从付款方/奖池扣掉却没有落到任何人头上。
        """
        cur = conn.execute(
            cls._CREDIT_INCOME_SQL,
            (float(amount), float(amount), str(gid), str(uid)),
        )
        return cur.rowcount > 0

    def add_mind_atomic(self, gid, uid, delta: float):
        """列级原子增减精神（钳制 0~100）。"""
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET mind=MAX(0.0, MIN(100.0, round(mind+?,1))) "
                    "WHERE gid=? AND uid=?",
                    (float(delta), str(gid), str(uid)),
                )
                conn.commit()
            finally:
                conn.close()

    def bump_duel_win(self, gid, uid, v_up: float):
        """对线获胜方原子结算：身价 +，胜场 +1。"""
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET value=round(value+?,2), duel_wins=duel_wins+1 "
                    "WHERE gid=? AND uid=?",
                    (float(v_up), str(gid), str(uid)),
                )
                conn.commit()
            finally:
                conn.close()

    def bump_duel_loss(self, gid, uid, v_down: float):
        """对线落败方原子结算：身价 -（下限 20），负场 +1。"""
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE players SET value=MAX(20.0, round(value-?,2)), "
                    "duel_losses=duel_losses+1 WHERE gid=? AND uid=?",
                    (float(v_down), str(gid), str(uid)),
                )
                conn.commit()
            finally:
                conn.close()

    def transfer_cash(
        self, gid, from_uid, to_uid, amount: float, fee: float = 0.0
    ) -> tuple[bool, str]:
        """单事务内完成「扣总额(本金+手续费) → 对方入账本金」；任一步失败整体回滚。

        手续费与本金同事务扣除，避免两步提交间崩溃/并发导致手续费丢失。
        """
        amount = round(float(amount), 2)
        fee = round(float(fee), 2)
        if amount <= 0 or fee < 0:
            return False, "bad_amount"  # 负数会让"扣款"变加钱
        total = round(amount + fee, 2)
        with _write_lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE players SET cash=round(cash-?,2) WHERE gid=? AND uid=? AND cash>=?",
                    (total, str(gid), str(from_uid), total),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return False, "insufficient"
                cur = conn.execute(
                    "UPDATE players SET cash=round(cash+?,2) WHERE gid=? AND uid=?",
                    (amount, str(gid), str(to_uid)),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return False, "no_target"
                conn.commit()
                return True, "ok"
            finally:
                conn.close()
