"""DB create_redpacket_atomic 等功能 Mixin（由 db.py 拆分）。"""

import json
import random
import time

from ._const import _write_lock


class _RedpacketMixin:
    # 「调用方漏传时的占位」而非本层持有的文案：与 gd.s(table, key, default) 同一
    # 模式（全库 143 处）。正式文案由 handlers/服务层从 resources/texts 传入，
    # kind 是玩家可见列（#工资条 第二列 + WebUI 动态流），必须可本地化。
    _KIND_SEND = "发群红包"
    _KIND_CLAIM = "抢群红包"

    def create_redpacket_atomic(
        self,
        gid,
        sender_uid,
        sender_name: str,
        amount: float,
        count: int,
        note: str = "",
        kind: str = "",
    ) -> tuple[bool, str, int | None]:
        """单事务完成「条件扣款 + 创建红包 + 记流水」。

        返回 (ok, reason, packet_id)：
        - (False, "bad_amount", None) 金额/份数非正
        - (False, "no_player", None) 玩家不存在（未入档）
        - (False, "insufficient", None) 余额不足
        - (True, "ok", packet_id) 成功
        防止扣完款但因崩溃/异常留下「钱扣了红包没建」的孤儿资金。

        「玩家不存在」与「余额不足」的 rowcount 都是 0，必须先查一次是否入档
        才能区分——否则给未入档的人发红包会被提示成"现金不足"。

        另外拒绝「每份不足 1 角」的包：拆包算法（claim_redpacket）保证
        「剩余金额 ≥ 剩余份数 × 0.1」，靠的是「总量 ≥ 份数 × 0.1」这个入口不变量。
        当前唯一调用点（extra_redpacket.send_redpacket）已用 `amt < cnt` 挡住，
        这里再钉一次是为了让这条不变量属于存储层自己 —— 否则将来的新调用点或
        放宽后的校验会悄悄造出「份数永远兑不了」的僵尸包。
        """
        amount = round(float(amount), 2)
        count = int(count)
        if amount <= 0 or count <= 0:
            return False, "bad_amount", None
        if amount < count * 0.1:
            return False, "bad_amount", None
        with _write_lock:
            conn = self._conn()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM players WHERE gid=? AND uid=?",
                    (str(gid), str(sender_uid)),
                ).fetchone()
                if not exists:
                    return False, "no_player", None
                cur = conn.execute(
                    "UPDATE players SET cash=round(cash-?,2) WHERE gid=? AND uid=? AND cash>=?",
                    (amount, str(gid), str(sender_uid), amount),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return False, "insufficient", None
                cur = conn.execute(
                    "INSERT INTO redpackets (gid, sender_uid, sender_name, total_amount, "
                    "total_count, remain_amount, remain_count, claimed_records, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(sender_uid),
                        sender_name,
                        amount,
                        count,
                        amount,
                        count,
                        "[]",
                        int(time.time()),
                    ),
                )
                packet_id = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO transactions "
                    "(gid, uid, kind, amount, note, created_at) VALUES (?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(sender_uid),
                        kind or self._KIND_SEND,
                        -amount,
                        note,
                        int(time.time()),
                    ),
                )
                conn.commit()
                return True, "ok", packet_id
            finally:
                conn.close()

    def _no_live_packet_reason(self, gid, uid) -> tuple[str, None]:
        """没有「还活着」的红包时，区分「你已经抢过」与「本群没有红包」。

        候选查询要求 remain_count > 0，于是最后一份被领完之后该包立刻退出候选，
        再发「#抢红包」原本一律回 empty（红包已抢完）——与事实不符。这里回查最近的
        若干封包记录，命中本人即 already。
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT claimed_records FROM redpackets WHERE gid=? ORDER BY id DESC LIMIT 20",
                (str(gid),),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            try:
                claimed = json.loads(row["claimed_records"] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(claimed, list):
                continue
            if any(isinstance(r, dict) and str(r.get("uid")) == str(uid) for r in claimed):
                return "already", None
        return "empty", None

    def claim_redpacket(
        self, gid, uid, nickname: str, note_tpl: str = "", kind: str = ""
    ) -> tuple[str, tuple | None]:
        """原子抢红包：拆包 + 入账 + 记流水在同一个事务里。

        返回 ("ok", (packet, get_amt, remain_amt, remain_cnt))
            | ("empty", None) | ("already", None) | ("no_player", None)。

        入账必须和拆包同事务：分成两步时，中间失败会把份额记成「已领」却不打钱，
        而 already 检查又挡住重试，这一份就凭空消失了。

        候选是【多个】而不是「最新那一个」：只取 id DESC LIMIT 1 的话，群里同时有
        两个未抢完的红包时，抢过新包的人再发指令只会拿到 already，老包对他永远
        不可达，得等新包过期（默认 7 天）才轮得到。
        """

        with _write_lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM redpackets WHERE gid=? AND remain_count > 0 "
                    "AND remain_amount > 0 ORDER BY id DESC LIMIT 20",
                    (str(gid),),
                ).fetchall()
                if not rows:
                    return self._no_live_packet_reason(gid, uid)
                packet = None
                seen_any = False
                for row in rows:
                    cand = dict(row)
                    try:
                        cand_claimed = json.loads(cand["claimed_records"] or "[]")
                    except (json.JSONDecodeError, TypeError):
                        cand_claimed = []
                    if not isinstance(cand_claimed, list):
                        cand_claimed = []
                    # r 可能不是 dict（手工改库/备份污染）：r.get 会抛 AttributeError
                    if any(
                        isinstance(r, dict) and str(r.get("uid")) == str(uid) for r in cand_claimed
                    ):
                        seen_any = True
                        continue  # 这个包本人已领，看下一个
                    packet, claimed = cand, cand_claimed
                    break
                if packet is None:
                    # 有包但全都领过了：仍然回 already，用户看到的提示才准确
                    return ("already", None) if seen_any else ("empty", None)

                remain_amt = float(packet["remain_amount"])
                # 候选查询已过滤 remain_count > 0，且本次读同一连接、同一把写锁，
                # 中间不会有别的写；因此这里不需要再判 remain_cnt <= 0
                remain_cnt = max(1, int(packet["remain_count"]))

                if remain_cnt == 1:
                    get_amt = round(remain_amt, 2)
                else:
                    # 不变量（由 create_redpacket_atomic 钉住）：剩余金额 ≥ 剩余份数 × 0.1。
                    # 本次最多拿「剩余金额 - 给后面每人留 0.1」，于是每一份都必然兑得出。
                    max_take = round(remain_amt - (remain_cnt - 1) * 0.1, 2)
                    if max_take < 0.1:
                        # 老库/手工改库留下的僵尸包不满足该不变量：拒绝本次，份额留在
                        # 包里，到期由 cleanup_old_data 把剩余金额全额退回发包人。
                        # 不在此处「一人领光」——那会毁掉「N 份」这个对玩家的承诺。
                        return "empty", None
                    max_possible = (remain_amt / remain_cnt) * 2
                    # uniform 的上界必须 ≥ 下界，否则抛 "ValueError: empty range"，
                    # 该红包从此永久抢不动（剩余人均 < 0.25 元时会出现）。
                    get_amt = round(random.uniform(min(0.5, max_possible), max_possible), 2)
                    get_amt = max(0.1, min(get_amt, max_take))
                # 0.00 元不算抢到：让份额留在包里给别人，也不写一条 0 元流水。
                if round(get_amt, 2) <= 0:
                    return "empty", None

                new_remain_amt = round(remain_amt - get_amt, 2)
                new_remain_cnt = remain_cnt - 1
                claimed.append(
                    {
                        "uid": str(uid),
                        "name": nickname,
                        "amount": get_amt,
                        "time": int(time.time()),
                    }
                )
                cur = conn.execute(
                    "UPDATE redpackets SET remain_amount=?, remain_count=?, claimed_records=? "
                    "WHERE id=? AND remain_count > 0",
                    (
                        new_remain_amt,
                        new_remain_cnt,
                        json.dumps(claimed, ensure_ascii=False),
                        packet["id"],
                    ),
                )
                if cur.rowcount == 0:
                    conn.rollback()
                    return "empty", None
                if not self._credit_income_on(conn, gid, uid, get_amt):
                    # 领取人档案已不存在：整笔回滚，份额留在包里给别人
                    conn.rollback()
                    return "no_player", None
                conn.execute(
                    "INSERT INTO transactions "
                    "(gid, uid, kind, amount, note, created_at) VALUES (?,?,?,?,?,?)",
                    (
                        str(gid),
                        str(uid),
                        kind or self._KIND_CLAIM,
                        get_amt,
                        note_tpl.replace("{sender}", str(packet["sender_name"])),
                        int(time.time()),
                    ),
                )
                conn.commit()
                return "ok", (packet, get_amt, new_remain_amt, new_remain_cnt)
            finally:
                conn.close()
