"""股票市场：股票池由 resources/data/stocks.json 播种，每日自动波动、买卖与持仓管理。"""

import asyncio
import json
import logging
import math
import random
from pathlib import Path
from typing import NamedTuple

from .db import _escape_like, _write_lock
from .logic import today_str as _today

try:  # 允许脱离 AstrBot 的脚本/测试单独导入本模块
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger("dagongren.stocks")

SEED_FILE = Path(__file__).resolve().parent.parent / "resources" / "data" / "stocks.json"

# 默认单日涨跌幅上下限（±9.8%，模拟 A 股涨跌停），可由配置覆盖
DEFAULT_PCT_LIMIT = 9.8
DEFAULT_DRIFT = 0.15
DEFAULT_VOLATILITY = 3.2
DEFAULT_MIN_PRICE = 0.5


class BatchEditResult(NamedTuple):
    """admin_edit_batch 的结果：两类「没写进去」必须是分开的字段。

    合在一个 set 里回传时，调用方只能给出同一条文案（WebUI 就一律说
    「代码不存在」），而「价格低于地板价」是运维自己能改的输入错误、
    「代码不存在」是选错了标的 —— 两者要的处置完全不同。
    """

    invalid: set[str]  # 价格不在 _min_price() <= p < 1e6 区间内
    missing: set[str]  # 库里没有这个代码


class StockMarket:
    def __init__(self, db, cfg):
        self.db = db
        self.cfg = cfg

    def _c(self, key, default):
        from . import logic

        return logic.cfg_get(self.cfg, key, default)

    def _random_pct(self) -> float:
        """单日涨跌幅（%）。drift 为正即长期持有稳赚，所以三个参数都可配。"""
        drift = float(self._c("stock_drift", DEFAULT_DRIFT))
        vol = abs(float(self._c("stock_volatility", DEFAULT_VOLATILITY)))
        limit = abs(float(self._c("stock_daily_limit_pct", DEFAULT_PCT_LIMIT)))
        return max(-limit, min(limit, random.gauss(drift, vol)))

    def _min_price(self) -> float:
        return max(0.01, float(self._c("stock_min_price", DEFAULT_MIN_PRICE)))

    def min_price(self) -> float:
        """地板价的公开读法：WebUI 要把它下发给面板输入框的 min 属性。

        面板此前把 0.5 写死在 HTML 上，运维调高 stock_min_price 后就出现
        「输入框说合法、保存说非法」的错位。
        """
        return self._min_price()

    # ---------- 基础 ----------

    def _conn(self):
        """复用 DB 层的连接构造，别再自己 connect。

        原先这里把 timeout 与 busy_timeout 硬编码成 15s，于是
        db_busy_timeout_ms 这个看起来是全局配置的键对股市的所有连接完全无效，
        而且少设了 journal_mode=WAL。
        """
        return self.db._conn()

    def ensure_seeded(self):
        """首次使用时从 stocks.json 播种全部股票（幂等，靠 INSERT OR IGNORE）。

        本方法是同步函数，由调用方（main.py 的 initialize()）用
        asyncio.to_thread 包装后执行。种子文件损坏时只记日志并放弃播种：
        直接抛出去会让整个插件加载失败——而缺股票只该让股市玩法不可用。
        """
        if not SEED_FILE.exists():
            return
        try:
            data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
            # s 可能不是 dict（种子文件被改坏成 ["sh600037", ...]）：没有
            # AttributeError 时这一行会直接冒到 initialize()，让整个插件加载
            # 失败，而这条路径的承诺只是「缺股票 = 股市玩法不可用」。
            seeds = [s for s in data["stocks"] if s.get("code")]
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            logger.error(f"[上班族物语] stocks.json 解析失败，股市未播种：{e}")
            return
        if not seeds:
            return
        conn = self._conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
            # 与种子条数比较，而不是写死 100：运维往 stocks.json 加第 101 支股票时，
            # 写死的阈值会让新股永远播不进去
            if n >= len(seeds):
                return
            today = _today()
            # 逐条取值必须在这里完成：把 s["name"] / s["price"] 直接写在下面的
            # INSERT 里时，单条目缺字段就抛 KeyError 一路冒到 initialize()，让
            # 整个插件加载失败（docstring 明确承诺不会）。单条坏数据只跳过它自己。
            rows = []
            skipped: list[str] = []
            for s in seeds:
                try:
                    price = float(s["price"])
                    rows.append((s["code"], s["name"], s.get("sector", ""), price, price, today))
                except (KeyError, TypeError, ValueError):
                    skipped.append(str(s.get("code") or s)[:32])
            if skipped:
                logger.warning(
                    f"[上班族物语] stocks.json 有 {len(skipped)} 条种子缺少 "
                    f"name/price，已跳过：{', '.join(skipped)}"
                )
            if not rows:
                return
            with _write_lock:
                conn.executemany(
                    "INSERT OR IGNORE INTO stocks (code,name,sector,price,prev,day) "
                    "VALUES (?,?,?,?,?,?)",
                    rows,
                )
                conn.commit()
        finally:
            conn.close()

    # ---------- 每日波动 ----------

    async def settle_if_needed(self) -> int:
        """懒结算：跨天后的第一次访问触发全部未结算股票的价格更新。

        旧实现把「读旧价」放在 _write_lock 之外、只在 UPDATE 时拿锁：两条指令
        并发结算（各自经 asyncio.to_thread）会读到同一份 pre-settlement 价格，
        后到者用旧价算 new_price 覆盖先到者刚写入的新价——一天的波动被静默吞掉，
        prev 也记错。这里把「读 + 逐行更新」整个放进同一把写锁内，保证每次
        UPDATE 读到的都是连接上最新值。
        """
        today = _today()

        def _do():
            conn = self._conn()
            try:
                # 读与写都在同一把写锁内：并发结算时后到者看到的 price 已是
                # 先到者更新过的最新值，而不是提交前的旧快照。
                with _write_lock:
                    rows = conn.execute(
                        "SELECT code, price FROM stocks WHERE day != ?", (today,)
                    ).fetchall()
                    if not rows:
                        return 0
                    floor_price = self._min_price()
                    for r in rows:
                        new_price = max(
                            floor_price,
                            round(float(r["price"]) * (1 + self._random_pct() / 100), 2),
                        )
                        conn.execute(
                            "UPDATE stocks SET prev=price, price=?, day=? WHERE code=?",
                            (new_price, today, r["code"]),
                        )
                    conn.commit()
                return len(rows)
            finally:
                conn.close()

        return await asyncio.to_thread(_do)

    # ---------- 查询 ----------

    def list_stocks(self, limit: int = 100) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute("SELECT * FROM stocks ORDER BY code LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            chg = (
                round((float(r["price"]) - float(r["prev"])) / float(r["prev"]) * 100, 2)
                if float(r["prev"])
                else 0.0
            )
            out.append(
                {
                    "code": r["code"],
                    "name": r["name"],
                    "sector": r["sector"],
                    "price": float(r["price"]),
                    "prev": float(r["prev"]),
                    "chg": chg,
                }
            )
        return out

    def get_stock(self, key: str) -> dict | None:
        conn = self._conn()
        try:
            return self._get_stock_on(conn, key)
        finally:
            conn.close()

    @staticmethod
    def _get_stock_on(conn, key: str) -> dict | None:
        """在【调用方的连接】上解析股票并读当前价。

        买卖必须用它、并且在写锁内调用：单独开连接查价再进锁下单，中间
        settle_if_needed / WebUI 改价都可能把价格改掉，成交价就是过期的。
        """
        k = str(key).strip()
        # LIKE 模式必须转义：不转义时「#买入 % 1000」里的 % 是通配符，会命中
        # 「排在最前的任意一支股票」——用户以为买的是自己指定的标的。
        # ESCAPE 与 _escape_like 的转义字符保持一致。
        r = conn.execute(
            "SELECT * FROM stocks WHERE code=? OR name=? OR name LIKE ? ESCAPE '\\' LIMIT 1",
            (k, k, f"%{_escape_like(k)}%"),
        ).fetchone()
        if not r:
            return None
        chg = (
            round((float(r["price"]) - float(r["prev"])) / float(r["prev"]) * 100, 2)
            if float(r["prev"])
            else 0.0
        )
        return {
            "code": r["code"],
            "name": r["name"],
            "price": float(r["price"]),
            "chg": chg,
        }

    # ---------- 交易 ----------

    async def buy(
        self, gid, uid, key: str, amount_yuan: float, fee_rate: float
    ) -> dict | str | None:
        """按金额买入。

        返回 None = 股票不存在 / 金额非法 / 单价过高买不到 1 手；
        "too_many" = 持仓只数超上限；"insufficient" = 现金不足；
        成功时返回 {"stock", "shares", "amount", "fee"}。
        """
        min_amt = float(self._c("stock_min_buy_amount", 1))
        if amount_yuan <= 0 or amount_yuan < min_amt:
            return None
        await self.settle_if_needed()

        def _do():
            max_pos = int(self._c("stock_max_positions", 50))
            conn = self._conn()
            try:
                with _write_lock:
                    # 报价在锁内读：在锁外查价会让成交价可能早已被并发的
                    # settle_if_needed / WebUI 改价覆盖（按旧价成交）
                    st = self._get_stock_on(conn, key)
                    if not st:
                        return None
                    fee = round(amount_yuan * fee_rate, 2)
                    cost = round(amount_yuan + fee, 2)  # 入账总额 = 本金 + 手续费
                    # 份额按全额本金算（fee-on-top 模式）：玩家付 amount+fee，
                    # 拿到 amount/price 股；手续费在买入端只收一次。
                    # 之前用 (amount-fee)/price 算份额导致 fee 被扣两次
                    # （现金扣 amount+fee，份额只值 amount-fee），avg_cost 被抬高。
                    # 向下取整到 4 位：round 会把份额往上凑，等于凭空多给股份。
                    shares = math.floor(amount_yuan / st["price"] * 10000) / 10000
                    if shares <= 0:
                        # 单价过高导致买到 0 股：绝不能扣款后插一条 0 股仓位
                        return None
                    # 原子扣款：余额不足时 rowcount=0
                    cur = conn.execute(
                        "UPDATE players SET cash=round(cash-?,2) WHERE gid=? AND uid=? AND cash>=?",
                        (cost, str(gid), str(uid), cost),
                    )
                    if cur.rowcount == 0:
                        conn.rollback()
                        return "insufficient"
                    row = conn.execute(
                        "SELECT shares, cost FROM portfolio WHERE gid=? AND uid=? AND code=?",
                        (str(gid), str(uid), st["code"]),
                    ).fetchone()
                    if not row:
                        # 新开一只仓位前检查只数上限：用单条 INSERT WHERE NOT EXISTS
                        # 避免两次并发 buy 都看到「当前持仓 N < max」然后都 INSERT，
                        # 导致 portfolio 持仓只数悄悄越过 max_pos。
                        inserted = conn.execute(
                            "INSERT INTO portfolio (gid,uid,code,shares,cost) "
                            "SELECT ?,?,?,?,? WHERE "
                            "(SELECT COUNT(*) FROM portfolio WHERE gid=? AND uid=? AND shares>0) < ?",
                            (
                                str(gid),
                                str(uid),
                                st["code"],
                                shares,
                                cost,
                                str(gid),
                                str(uid),
                                max_pos,
                            ),
                        )
                        if inserted.rowcount == 0:
                            conn.rollback()
                            return "too_many"
                    if row:
                        ns = round(float(row["shares"]) + shares, 4)
                        nc = round(float(row["cost"]) + cost, 2)
                        conn.execute(
                            "UPDATE portfolio SET shares=?, cost=? WHERE gid=? AND uid=? AND code=?",
                            (ns, nc, str(gid), str(uid), st["code"]),
                        )
                    # 注：新建仓位分支在上面用条件 INSERT 完成
                    conn.commit()
                return {
                    "stock": st,
                    "shares": shares,
                    "amount": amount_yuan,
                    "fee": fee,
                }
            finally:
                conn.close()

        res = await asyncio.to_thread(_do)
        # 用哨兵字符串而不是抛异常做流控：调用方只丢弃异常消息，
        # 而「现金不足」是正常业务分支，不该走 except。
        return res

    async def sell(self, gid, uid, key: str, ratio: float, fee_rate: float) -> dict | None:
        if ratio <= 0:
            return None
        await self.settle_if_needed()

        def _do():
            # 持仓读取、份额计算、删/改仓与入账全部放在同一事务里：
            # 旧实现先在事务外读持仓再凭快照写回，两条并发卖出同一仓位会
            # 各自按旧份额清算、双双入账（清仓时 DELETE 均不校验 rowcount）。
            conn = self._conn()
            try:
                with _write_lock:
                    # 报价同样在锁内读，理由同 buy()
                    st = self._get_stock_on(conn, key)
                    if not st:
                        return None
                    row = conn.execute(
                        "SELECT shares, cost FROM portfolio WHERE gid=? AND uid=? AND code=?",
                        (str(gid), str(uid), st["code"]),
                    ).fetchone()
                    if not row:
                        return None
                    shares = float(row["shares"])
                    if shares <= 0:
                        return None
                    sell_shares = round(shares * ratio, 4)
                    # 极小仓位按比例取整后可能归零：按全仓卖出，避免残留灰尘份额
                    if sell_shares <= 0:
                        sell_shares = shares
                    sell_shares = min(sell_shares, shares)
                    cash = round(sell_shares * st["price"], 2)
                    fee = round(cash * fee_rate, 2)
                    income = round(max(0.0, cash - fee), 2)
                    remain = round(shares - sell_shares, 4)
                    remain_cost = (
                        round(float(row["cost"]) * (remain / shares), 2) if shares else 0.0
                    )
                    if remain <= 0:
                        conn.execute(
                            "DELETE FROM portfolio WHERE gid=? AND uid=? AND code=?",
                            (str(gid), str(uid), st["code"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE portfolio SET shares=?, cost=? WHERE gid=? AND uid=? AND code=?",
                            (remain, remain_cost, str(gid), str(uid), st["code"]),
                        )
                    # 同事务入账到账金额。必须校验 rowcount：玩家行被管理端删掉时
                    # 这条 UPDATE 影响 0 行，仓位却已经删了——钱凭空蒸发。
                    credited = conn.execute(
                        "UPDATE players SET cash=round(cash+?,2) WHERE gid=? AND uid=?",
                        (income, str(gid), str(uid)),
                    )
                    if credited.rowcount == 0:
                        conn.rollback()
                        return None
                    conn.commit()
                avg_cost = round(float(row["cost"]) / shares, 2) if shares else 0.0
                return {
                    "name": st["name"],
                    "shares": sell_shares,
                    "income": income,
                    "fee": fee,
                    "profit": round(income - avg_cost * sell_shares, 2),
                }
            finally:
                conn.close()

        return await asyncio.to_thread(_do)

    async def position_of(self, gid, uid, key: str) -> dict | None:
        await self.settle_if_needed()

        def _do():
            st = self.get_stock(key)
            if not st:
                return None
            conn = self._conn()
            try:
                r = conn.execute(
                    "SELECT shares, cost FROM portfolio WHERE gid=? AND uid=? AND code=?",
                    (str(gid), str(uid), st["code"]),
                ).fetchone()
            finally:
                conn.close()
            if not r:
                return None
            shares = float(r["shares"])
            cost = float(r["cost"])
            return {
                "code": st["code"],
                "name": st["name"],
                "shares": shares,
                "cur_price": st["price"],
                "avg_cost": round(cost / shares, 2) if shares else 0,
                "total_cost": cost,
                "market_value": round(shares * st["price"], 2),
            }

        return await asyncio.to_thread(_do)

    async def my_positions(self, gid, uid) -> list[dict]:
        await self.settle_if_needed()

        def _do():
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT code, shares, cost FROM portfolio WHERE gid=? AND uid=? "
                    "AND shares > 0 ORDER BY cost DESC",
                    (str(gid), str(uid)),
                ).fetchall()
                # 一次性批量取行情，避免对每只持仓单独开连接（get_stock）
                stock_rows = conn.execute("SELECT code, name, price FROM stocks").fetchall()
                stock_map = {str(r["code"]): r for r in stock_rows}
            finally:
                conn.close()
            out = []
            for r in rows:
                st = stock_map.get(str(r["code"]))
                if st:
                    out.append(
                        {
                            "code": st["code"],
                            "name": st["name"],
                            "shares": round(float(r["shares"]), 4),
                            "cur_price": st["price"],
                            "market_value": round(float(r["shares"]) * float(st["price"]), 2),
                        }
                    )
            return out

        return await asyncio.to_thread(_do)

    async def sell_all(self, gid, uid, fee_rate: float) -> list[dict]:
        """一键清仓：全部持仓在【一个事务】里卖出并入账。

        旧实现是在指令层 for 循环调 sell()，每笔都要 settle_if_needed + 独立
        事务 + 独立流水，50 个持仓 ≈ 150 次线程往返、100 次抢全局写锁，
        期间其它群的所有写操作都在排队。这里结算一次、事务一次。
        """
        await self.settle_if_needed()

        def _do():
            conn = self._conn()
            try:
                with _write_lock:
                    stock_map = {
                        str(r["code"]): r
                        for r in conn.execute("SELECT code, name, price FROM stocks")
                    }
                    rows = conn.execute(
                        "SELECT code, shares, cost FROM portfolio "
                        "WHERE gid=? AND uid=? AND shares > 0 ORDER BY cost DESC",
                        (str(gid), str(uid)),
                    ).fetchall()
                    out, total_income = [], 0.0
                    for r in rows:
                        st = stock_map.get(str(r["code"]))
                        shares = float(r["shares"])
                        if not st or shares <= 0:
                            continue
                        price = float(st["price"])
                        cash = round(shares * price, 2)
                        fee = round(cash * fee_rate, 2)
                        income = round(max(0.0, cash - fee), 2)
                        cost = float(r["cost"])
                        conn.execute(
                            "DELETE FROM portfolio WHERE gid=? AND uid=? AND code=?",
                            (str(gid), str(uid), st["code"]),
                        )
                        total_income = round(total_income + income, 2)
                        out.append(
                            {
                                "code": st["code"],
                                "name": st["name"],
                                "shares": round(shares, 4),
                                "income": income,
                                "fee": fee,
                                "profit": round(income - cost, 2),
                            }
                        )
                    if total_income > 0:
                        # 同 sell()：玩家行不存在就整笔回滚，不能只删仓位不给钱
                        credited = conn.execute(
                            "UPDATE players SET cash=round(cash+?,2) WHERE gid=? AND uid=?",
                            (total_income, str(gid), str(uid)),
                        )
                        if credited.rowcount == 0:
                            conn.rollback()
                            return []
                    conn.commit()
                    return out
            finally:
                conn.close()

        return await asyncio.to_thread(_do)

    # ---------- 管理端 ----------

    def admin_edit(self, code: str, name: str | None = None, price: float | None = None) -> bool:
        """管理端改名/改价。代码不存在时返回 False（UPDATE 影响 0 行）。"""
        if price is not None:
            # 价格必须 > 0：buy() 里 shares = amount / price，价格被改成 0 会让
            # 每一次买入都 ZeroDivisionError（这支股票从此彻底买不了）
            try:
                price = float(price)
            except (TypeError, ValueError):
                return False
            if not (self._min_price() <= price < 1e6):
                return False
        conn = self._conn()
        try:
            with _write_lock:
                if name is not None and price is not None:
                    cur = conn.execute(
                        "UPDATE stocks SET name=?, price=?, day=? WHERE code=?",
                        (name, price, _today(), code),
                    )
                elif name is not None:
                    cur = conn.execute("UPDATE stocks SET name=? WHERE code=?", (name, code))
                elif price is not None:
                    cur = conn.execute(
                        "UPDATE stocks SET price=?, day=? WHERE code=?",
                        (price, _today(), code),
                    )
                else:
                    return False
                conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def admin_edit_batch(self, edits: dict[str, float]) -> BatchEditResult:
        """管理端批量改价：一条连接、一把写锁、一个事务。

        返回 BatchEditResult(invalid=价格非法未写入, missing=代码不存在)。
        判据与 admin_edit 完全一致（_min_price() <= price < 1e6），语义与逐条
        调用 admin_edit 等价，但只建一条 sqlite 连接。两类失败【分开】回传：
        合并成一个 set 时，WebUI 只能一律报「代码不存在」——运维提交一个低于
        _min_price() 的价格（面板 min 属性不参与提交校验，服务端此前用的是
        0 < price 这个更宽的下限）会得到「代码不存在」，而那个代码明明存在、
        现价也纹丝不动，只能反复重试。

        为什么需要它：WebUI 的批量改价单请求最多 500 条，逐条
        asyncio.to_thread(admin_edit, ...) 就是 500 次串行线程池往返 + 500 条
        连接，一次请求能把默认线程池占很久，期间玩家指令的 db 操作全在排队。
        不改 admin_edit 本身：单支改名的调用点（以及它的 0 行返回约定）不动。
        """
        clean: dict[str, float] = {}
        invalid: set[str] = set()
        floor = self._min_price()
        for code, price in (edits or {}).items():
            try:
                p = float(price)
            except (TypeError, ValueError):
                invalid.add(str(code))
                continue
            if not (floor <= p < 1e6):
                invalid.add(str(code))
                continue
            clean[str(code)] = p
        if not clean:
            return BatchEditResult(invalid=invalid, missing=set())
        conn = self._conn()
        try:
            with _write_lock:
                day = _today()
                marks = ",".join("?" * len(clean))
                existing = {
                    str(r["code"])
                    for r in conn.execute(
                        f"SELECT code FROM stocks WHERE code IN ({marks})",  # noqa: S608 - 占位符由 len() 生成，参数照旧绑定
                        tuple(clean),
                    ).fetchall()
                }
                todo = [(p, day, c) for c, p in clean.items() if c in existing]
                if todo:
                    conn.executemany(
                        "UPDATE stocks SET price=?, day=? WHERE code=?",
                        todo,
                    )
                    conn.commit()
            return BatchEditResult(invalid=invalid, missing=set(clean) - existing)
        finally:
            conn.close()

    def admin_fluctuate_all(self) -> int:
        """强制全部股票立刻波动一次（无视日期）。"""
        conn = self._conn()
        try:
            # 读价与逐行更新必须在同一把写锁内（与 settle_if_needed 同口径）：
            # 在锁外读时，若懒结算或另一次管理操作在「读完」与「拿锁」之间提交，
            # 本次会用过期价格算新价，还把过期价写成 prev，污染当日涨跌幅。
            with _write_lock:
                rows = conn.execute("SELECT code, price FROM stocks").fetchall()
                floor = self._min_price()
                for r in rows:
                    np_ = round(
                        max(floor, float(r["price"]) * (1 + self._random_pct() / 100)),
                        2,
                    )
                    conn.execute(
                        "UPDATE stocks SET prev=price, price=? WHERE code=?",
                        (np_, r["code"]),
                    )
                conn.commit()
            return len(rows)
        finally:
            conn.close()

    def admin_set_price_all_random(self) -> int:
        conn = self._conn()
        try:
            # 同上：行集也要在锁内取，避免与并发写入交错
            with _write_lock:
                rows = conn.execute("SELECT code FROM stocks").fetchall()
                for r in rows:
                    base = round(
                        random.uniform(
                            float(self._c("stock_reset_price_min", 6.0)),
                            float(self._c("stock_reset_price_max", 180.0)),
                        ),
                        2,
                    )
                    conn.execute(
                        "UPDATE stocks SET price=?, prev=? WHERE code=?",
                        (base, base, r["code"]),
                    )
                conn.commit()
            return len(rows)
        finally:
            conn.close()
