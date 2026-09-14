"""跨业务域共用的工具：文案取用（tt/make_title）、在招公司池、延迟流水队列。"""

import asyncio
import logging

from . import gamedata as gd
from . import logic

try:  # 允许脱离 AstrBot 的脚本/测试单独导入
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger("dagongren.career_common")


class Txs:
    """延迟写流水队列：登记发生在内存里，flush() 必须在 save_player 成功之后调用。

    为什么不能即写即落：db.save_player 会在资金列下限被击穿时抛
    MoneyIntegrityError 并整笔回滚，而在此之前已提交的流水行【不会】回滚，
    于是出现「现金没变、流水却多了一笔工资/罚款」的账实不符。
    反过来（先落库、后记账）最坏只丢一条流水，绝不会凭空多出钱 —— 两害相权。
    social.py 是最早写明这条约定的地方，本类把它变成默认路径。

    用法：
        txs = Txs()
        txs.income(p, net_pay, _t("kind_salary"), note)   # 内存态结算
        await asyncio.to_thread(db.save_player, p)         # 先落库
        await txs.flush(db, gid, uid)                      # 后记账
    """

    __slots__ = ("_items",)

    def __init__(self):
        self._items: list[tuple[str, float, str]] = []

    def add(self, kind: str, amount: float, note: str = "") -> None:
        """登记一条流水（amount 带符号：收入为正、支出为负）。"""
        self._items.append((str(kind), float(amount), str(note)))

    def income(self, p: dict, amount: float, kind: str, note: str = "") -> None:
        """现金与累计总收入同时增加，并登记正数流水。

        三者必须同时更新：#工资条 把「累计总收入」与收支流水并排显示，漏掉
        任一项两个数字就永远对不上。此前这三行在 19 处逐个复制（连解释注释
        都抄了 6 遍），life_daily 的团建抽奖正是漏写 total_earned 的那一处。
        """
        amount = float(amount)
        p["cash"] = round(float(p["cash"]) + amount, 2)
        p["total_earned"] = round(float(p.get("total_earned") or 0) + amount, 2)
        self.add(kind, amount, note)

    async def flush(self, db, gid, uid) -> None:
        """按登记顺序落库。调用点必须在 save_player 之后（见类文档）。"""
        for kind, amount, note in self._items:
            await asyncio.to_thread(db.add_transaction, gid, uid, kind, amount, note)
        self._items.clear()


# 已经报过「文案表里没有这个键」的 (表, 键)。
#
# 去重口径与 gamedata._fallback_warned（gd.s 命中代码默认值时那条）完全一致：
# 同一个 (表, 键) 只打一条，集合上界 = 全仓 (表, 键) 点位 —— 静态扫描器实测
# 1270 余处 `_t("键")`（去重后约 1050 个 (表, 键)）+ 160 处 _title/gd.s，
# 与调用次数无关。tt/_t 是极热路径（一次出图几十次调用），不去重会把日志冲爆；
# 反过来，就算运维把整个 texts 目录删掉，也只是有限条 warning 而不是无限增长。
_missing_warned: set[tuple[str, str]] = set()


def _note_text_missing(table: str, key: str) -> None:
    """tt() 取不到文案（键缺失/值为空）：打一条 warning，同一 (表, 键) 只打一次。"""
    tag = (table, key)
    if tag in _missing_warned:
        return
    _missing_warned.add(tag)
    logger.warning(
        f"[上班族物语] 文案缺失：texts/{table}.json 里没有可用的 {key}，"
        "该处提示会变成空字符串（tt/_t 这条路径没有代码内置兜底文案，请补回该键）"
    )


def tt(table: str, key: str, variables: dict | None = None) -> str:
    """各业务域共用的文案取用 helper：取 <table>.json 的 key 并填充占位。

    key 缺变量时原样保留（logic.fill），variables 为空时只取不填。
    此前 career_business/growth/job/work、life2/life_career/life_daily、
    finance 各自复制了一份几乎相同的 _t；统一收拢到这一处，改文案结构只需动一处。

    这条路径【没有兜底默认值】（各模块的 `_t("键")` 就是它）：键不存在时
    pick([]) 返回空串，整句提示静默变空 —— 调用方 R(err="") 为假，会一路落到
    main 的 done_placeholder，玩家看到的是答非所问的「（执行完成）」。旧代码这些
    文案是硬编码中文、永远不可能为空，是按域拆分 + 外置之后才有的新失效模式。
    因此缺键必须出声（见 _note_text_missing），而不是静默降级。
    """
    texts = gd.t(table, key)
    if not texts:
        _note_text_missing(table, key)
        return ""
    if not variables:
        return logic.pick(texts)
    return logic.fill(logic.pick(texts), variables)


def make_title(table: str):
    """生成绑定到指定文本表的 _title 工厂。

    面板标题（结构文案）从 JSON 读、缺键回退默认值。此前 career_*/life_*/extra_*/
    finance/social/lottery/review 以及若干 handlers 域在 20+ 个文件里复制了
    几乎相同的 `def _title(key, default): return gd.s("<table>", key, default)`；
    统一收敛为一个工厂，各模块只需一行：`_title = make_title("finance")`。
    """

    def _title(key: str, default: str) -> str:
        return gd.s(table, key, default)

    return _title


def _hiring_pool(db, gid) -> list[dict]:
    """在招公司池 = 静态公司 + 本群自建公司（同步函数，调用方负责入线程）。

    自建公司必须出现在这里，否则群友永远投递不进去，
    企业金库就只剩「老板自己打卡」这一个来源。
    """
    pool = gd.companies()
    try:
        rows = db.custom_companies_of_group(gid)
    except Exception:  # noqa: BLE001 - 自建公司不可用时不影响正常求职
        rows = []
    for row in rows:
        c = gd.custom_company(db, gd.CUSTOM_BASE + int(row["id"]))
        if c:
            pool.append(c)
    return pool


async def hiring_pool(db, gid) -> list[dict]:
    return await asyncio.to_thread(_hiring_pool, db, gid)
