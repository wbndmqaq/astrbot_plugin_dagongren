"""DB 彩票（购票/奖池/开奖结算）功能 Mixin。"""

import json
import time

from ._const import _next_date, _write_lock, logger


class _LotteryMixin:
    # 开奖的「取快照 → 锁外判奖 → 校验后写回」最多重试几次（判奖期间有人给
    # 同一期购票就得重算）。开奖由推送循环串行触发，重试次数给足即可。
    _SETTLE_RETRIES = 5

    def lottery_current_pool(self, date_str: str) -> float:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT pool FROM lottery_pool WHERE draw_date=?", (str(date_str),)
            ).fetchone()
            return float(row["pool"]) if row else 0.0
        finally:
            conn.close()

    def lottery_today_count(self, gid, uid, date_str: str) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM lottery_tickets WHERE gid=? AND uid=? AND draw_date=?",
                (str(gid), str(uid), str(date_str)),
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    # 调用方漏传时的占位（见 _redpacket 同名注释）
    _KIND_BUY = "购买彩票"

    def lottery_purchase(
        self,
        gid,
        uid,
        name: str,
        numbers: list,
        date_str: str,
        price: float,
        limit: int,
        note_tpl: str = "",
        kind: str = "",
    ):
        """单事务购票：限购裁剪 → 条件扣款 → 写票 → 奖池入金 → 记流水。

        原先是「扣款」「写票」「入池」三个独立事务，中间任何一步失败都会留下
        对不上的账：票写了钱没进池（开奖时少派这一份），或钱扣了票没出（只能靠
        调用方补偿退款）。合成一个事务后不存在中间态，也不再需要退款分支。

        返回 {"written": 实际出票数, "charged": 实际扣款, "bought_before": 本期
        购票前的已购数, "reason": 失败原因或 ''}。调用方用 bought_before 判断
        是否触顶（core/lottery.py 的限购提示）。
        """
        now = int(time.time())
        with _write_lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM lottery_tickets "
                    "WHERE gid=? AND uid=? AND draw_date=?",
                    (str(gid), str(uid), str(date_str)),
                ).fetchone()
                bought = int(row["n"])
                remaining = max(0, int(limit) - bought)
                if remaining <= 0:
                    return {"written": 0, "charged": 0.0, "reason": "limit"}
                picks = list(numbers)[:remaining]
                if not picks:
                    # 防御：空号码列表会让 total=0，`WHERE cash>=0` 恒真，
                    # 结果是 0 张票 + 一条 pool=0 的期次行 + 一条 0 元流水
                    return {"written": 0, "charged": 0.0, "reason": "empty"}
                total = round(float(price) * len(picks), 2)
                cur = conn.execute(
                    "UPDATE players SET cash=round(cash-?,2) WHERE gid=? AND uid=? AND cash>=?",
                    (total, str(gid), str(uid), total),
                )
                if cur.rowcount <= 0:
                    conn.rollback()
                    return {"written": 0, "charged": 0.0, "reason": "cash"}
                conn.executemany(
                    "INSERT INTO lottery_tickets "
                    "(gid, uid, name, number, draw_date, created_at) VALUES (?,?,?,?,?,?)",
                    [
                        (
                            str(gid),
                            str(uid),
                            str(name)[:50],
                            str(n),
                            str(date_str),
                            now,
                        )
                        for n in picks
                    ],
                )
                conn.execute(
                    "INSERT INTO lottery_pool (draw_date, pool) VALUES (?, ?) "
                    "ON CONFLICT(draw_date) DO UPDATE SET pool=round(pool+?,2)",
                    (str(date_str), total, total),
                )
                conn.execute(
                    "INSERT INTO transactions "
                    "(gid, uid, kind, amount, note, created_at) VALUES (?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(uid),
                        kind or self._KIND_BUY,
                        -total,
                        note_tpl.replace("{count}", str(len(picks))).replace(
                            "{date}", str(date_str)
                        ),
                        now,
                    ),
                )
                conn.commit()
                return {
                    "written": len(picks),
                    "charged": total,
                    "bought_before": bought,
                    "reason": "",
                }
            finally:
                conn.close()

    def lottery_pending_dates(self, today: str) -> list:
        """还有存票、但开奖日已过的期次（升序）。

        机器人在某期开奖时间之后一直离线时，那期的票永远等不到 lottery_settle，
        而 lottery_carry_unsettled_pool 又刻意跳过「仍有存票」的期次——结果票款
        既不派奖也不滚存，被永久锁死。调用方据此把欠下的期次补开。
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT draw_date FROM lottery_tickets "
                "WHERE draw_date < ? ORDER BY draw_date",
                (str(today),),
            ).fetchall()
            return [r["draw_date"] for r in rows]
        finally:
            conn.close()

    def lottery_carry_unsettled_pool(self, today: str) -> float:
        """把【历史期次】的孤儿奖池滚存到 today，并删除那些历史池行。

        孤儿奖池 = 开奖日已过、但当期一张票都没有的池行。lottery_settle 对
        「无人购票」直接返回 None、不做滚存，这些钱会被永久锁在历史行里。

        严格只处理 draw_date < today：
        - today 自己的池行绝不能动——调用方紧接着就要用它开奖，搬走等于
          让当期中奖者到手 0 元；
        - 仍有存票的历史期次也不动，那笔钱属于还没结算的票。

        返回滚存总额（仅诊断）。
        """
        with _write_lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT draw_date, pool FROM lottery_pool WHERE draw_date < ? "
                    "AND draw_date NOT IN (SELECT DISTINCT draw_date FROM lottery_tickets)",
                    (str(today),),
                ).fetchall()
                if not rows:
                    return 0.0
                total = round(sum(float(r["pool"] or 0) for r in rows), 2)
                for r in rows:
                    conn.execute("DELETE FROM lottery_pool WHERE draw_date=?", (r["draw_date"],))
                if total > 0:
                    conn.execute(
                        "INSERT INTO lottery_pool (draw_date, pool) VALUES (?,?) "
                        "ON CONFLICT(draw_date) DO UPDATE SET pool=round(pool+?,2)",
                        (str(today), total, total),
                    )
                conn.commit()
                return total
            finally:
                conn.close()

    def lottery_settle(self, date_str: str, number: str, judge):
        """开奖结算（单事务）。

        number 为本期开奖号码（格式 '03,07,12|05'）；
        judge(tickets, pool) -> (winners, paid)：
        tickets 为当期票行（含 number 字符串），由调用方（core.lottery）实现
        双色球判奖与奖金分摊等游戏规则；本方法只负责事务、入账与滚存。
        返回结算结果 dict，无人购票（或已被开过）返回 None。

        judge 在【全局写锁之外】调用，这是刻意的：judge 是由调用方注入的任意
        代码，在持锁时调用它有两个代价 ——
        - 只要它将来直接/间接碰一次 DB，同一线程重入非可重入的 _write_lock
          就是【永久自锁】，全插件写操作连同 WebUI 一起挂死，且没有超时保护；
        - 锁的持有时间被拉长到与票数成正比（500 人 × 5 注 = 2500 条纯 Python
          遍历），期间所有写操作排队。
        因此改成「锁内取快照 → 锁外判奖 → 锁内校验快照未变再写回」；快照期间
        若有人给同一期购票，就重取重算（最多 _SETTLE_RETRIES 次）。
        对 judge 的要求：它是纯函数，不得访问 DB。
        """
        for _ in range(self._SETTLE_RETRIES):
            with _write_lock:
                conn = self._conn()
                try:
                    if conn.execute(
                        "SELECT 1 FROM lottery_draws WHERE draw_date=?", (str(date_str),)
                    ).fetchone():
                        return None  # 已被开过：幂等返回，不重复派奖
                    tickets = conn.execute(
                        "SELECT id, gid, uid, name, number FROM lottery_tickets WHERE draw_date=?",
                        (str(date_str),),
                    ).fetchall()
                    if not tickets:
                        return None
                    pool_row = conn.execute(
                        "SELECT pool FROM lottery_pool WHERE draw_date=?", (str(date_str),)
                    ).fetchone()
                    # 保留两位小数：票价 100% 入池是 2 位小数，取整会永久磨掉角分
                    pool = round(float(pool_row["pool"]), 2) if pool_row else 0.0
                    snapshot = [tuple(t) for t in tickets]
                finally:
                    conn.close()

            winners, paid = judge([dict(t) for t in tickets], pool)

            with _write_lock:
                conn = self._conn()
                try:
                    fresh = conn.execute(
                        "SELECT id, gid, uid, name, number FROM lottery_tickets WHERE draw_date=?",
                        (str(date_str),),
                    ).fetchall()
                    if [tuple(r) for r in fresh] != snapshot:
                        continue  # 判奖期间有人购票：这一份快照作废，重取重算
                    # 派奖上限为奖池，永不透支：总额超池时按比例缩放各票奖金。
                    # pool 可能为 0（孤儿池被滚存/手动干预），此时 gross>0 也须走缩放，
                    # 把各票奖金缩到 0，避免"先全额入账、paid 却按池上限 0"凭空派钱。
                    gross = round(sum(float(w["amount"]) for w in winners), 2)
                    if gross > pool:
                        scale = pool / gross if gross else 0.0
                        for w in winners:
                            w["amount"] = round(float(w["amount"]) * scale, 2)
                    # paid 必须等于【实际入账总额】，否则 carry = pool - paid 会凭空
                    # 多出或少掉钱：缩放改的是 w["amount"]，judge 返回的 paid 已失效；
                    # 且删档玩家的 UPDATE 影响 0 行，那份奖金没人收到，只能退回滚存。
                    credited = []
                    for w in winners:
                        amt = round(float(w["amount"]), 2)
                        if amt <= 0:
                            continue
                        if self._credit_income_on(conn, w["gid"], w["uid"], amt):
                            credited.append(w)
                    winners = credited
                    paid = round(min(sum((float(w["amount"]) for w in winners), 0.0), pool), 2)
                    conn.execute(
                        "INSERT OR REPLACE INTO lottery_draws "
                        "(draw_date, number, pool, paid, ticket_count, winners, created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            str(date_str),
                            str(number),
                            pool,
                            paid,
                            len(tickets),
                            json.dumps(winners, ensure_ascii=False),
                            int(time.time()),
                        ),
                    )
                    # 清掉已开票，滚存进下一期
                    conn.execute("DELETE FROM lottery_tickets WHERE draw_date=?", (str(date_str),))
                    carry = round(pool - paid, 2)
                    if carry > 0:
                        tomorrow = _next_date(date_str)
                        conn.execute(
                            "INSERT INTO lottery_pool (draw_date, pool) VALUES (?,?) "
                            "ON CONFLICT(draw_date) DO UPDATE SET pool=round(pool+?,2)",
                            (tomorrow, carry, carry),
                        )
                    conn.execute("DELETE FROM lottery_pool WHERE draw_date=?", (str(date_str),))
                    conn.commit()
                    return {
                        "date": str(date_str),
                        "number": str(number),
                        "pool": pool,
                        "paid": paid,
                        "carry": carry,
                        "winners": winners,
                        "ticket_count": len(tickets),
                    }
                finally:
                    conn.close()
        logger.warning(
            f"[上班族物语] 彩票 {date_str} 期开奖重试 {self._SETTLE_RETRIES} 次仍被"
            "并发购票打断，本期延后到下一次巡检再开"
        )
        return None

    def lottery_last_draw(self):
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM lottery_draws ORDER BY draw_date DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["date"] = d.get("draw_date")  # 与 lottery_settle 返回结构对齐
            d["carry"] = round(float(d.get("pool") or 0) - float(d.get("paid") or 0), 2)
            try:
                d["winners"] = json.loads(d.get("winners") or "[]")
            except json.JSONDecodeError:
                d["winners"] = []
            return d
        finally:
            conn.close()

    def lottery_my_tickets(self, gid, uid, date_str: str) -> list:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT number FROM lottery_tickets "
                "WHERE gid=? AND uid=? AND draw_date=? ORDER BY id",
                (str(gid), str(uid), str(date_str)),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def lottery_today_all_count(self, date_str: str) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM lottery_tickets WHERE draw_date=?",
                (str(date_str),),
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def lottery_today_gids(self, date_str: str) -> list:
        """当期购票群列表（开奖播报用）。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT gid FROM lottery_tickets WHERE draw_date=?",
                (str(date_str),),
            ).fetchall()
            return [r["gid"] for r in rows]
        finally:
            conn.close()
