"""SQLite 存储层常量：schema / 列集 / 增量列 / 建号默认值。"""

import datetime
import logging
import threading
import time
import zlib

try:  # 存储层可能被脱离 AstrBot 的脚本单独导入（备份/迁移工具）
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger("shangbanzu.db")

_write_lock = threading.Lock()


def _escape_like(kw) -> str:
    """转义 LIKE 模式里的通配符，让用户输入只能当字面量匹配。

    不转义时 kw="%" / "_" 会命中任意玩家——搜索、@目标解析、面板管理
    都用同一条 LIKE，等于给了一个"匹配任何人"的万能关键字。
    """
    return (
        str(kw if kw is not None else "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


CUSTOM_BASE = 10000


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    card TEXT DEFAULT '',
    cash REAL DEFAULT 0,
    deposit REAL DEFAULT 0,
    bank_level INTEGER DEFAULT 1,
    bank_limit REAL DEFAULT 5000,
    bank_upgrade_price REAL DEFAULT 200,
    last_interest INTEGER DEFAULT 0,
    health REAL DEFAULT 80,
    mind REAL DEFAULT 80,
    exp INTEGER DEFAULT 0,
    lvl INTEGER DEFAULT 1,
    company INTEGER DEFAULT -1,
    salary REAL DEFAULT 0,
    house INTEGER DEFAULT 0,
    fund REAL DEFAULT 0,
    fund_day TEXT DEFAULT '',
    attend_streak INTEGER DEFAULT 0,
    work_day TEXT DEFAULT '',
    leave_week TEXT DEFAULT '',
    leave_count INTEGER DEFAULT 0,
    value REAL DEFAULT 100,
    rank_score INTEGER DEFAULT 1000,
    rank_tier TEXT DEFAULT '',
    rank_matches INTEGER DEFAULT 0,
    duel_wins INTEGER DEFAULT 0,
    duel_losses INTEGER DEFAULT 0,
    fund_savings REAL DEFAULT 0,
    total_earned REAL DEFAULT 0,
    comp_leave INTEGER DEFAULT 0,
    commute TEXT DEFAULT '',
    house_owned INTEGER DEFAULT 0,
    social_pts INTEGER DEFAULT 0,
    side_lvl INTEGER DEFAULT 1,
    annual_leave INTEGER DEFAULT 3,
    annual_year TEXT DEFAULT '',
    year_bonus_year TEXT DEFAULT '',
    workstation INTEGER DEFAULT 0,
    party_year TEXT DEFAULT '',
    checkup_year TEXT DEFAULT '',
    meeting_day TEXT DEFAULT '',
    reply_day TEXT DEFAULT '',
    room_day TEXT DEFAULT '',
    pet_day TEXT DEFAULT '',
    pet TEXT DEFAULT '',
    title TEXT DEFAULT '',
    achievements TEXT DEFAULT '[]',
    review_year TEXT DEFAULT '',
    salary_bonus REAL DEFAULT 1,
    ot_day TEXT DEFAULT '',
    ot_count INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    PRIMARY KEY (gid, uid)
);
-- 玩家背包表（自 v2 起从 players.items JSON 列拆分而来）
CREATE TABLE IF NOT EXISTS player_items (
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    item_key TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    PRIMARY KEY (gid, uid, item_key)
);
-- 玩家技能表（自 v2 起从 players.skills JSON 列拆分而来）
CREATE TABLE IF NOT EXISTS player_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    learned_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_player_skills ON player_skills (gid, uid);
-- 玩家冷却表（自 v2 起从 players.cds JSON 列拆分而来）
CREATE TABLE IF NOT EXISTS player_cds (
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    cd_key TEXT NOT NULL,
    expires_at INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER DEFAULT 0,
    PRIMARY KEY (gid, uid, cd_key)
);
CREATE TABLE IF NOT EXISTS redpackets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    sender_uid TEXT NOT NULL,
    sender_name TEXT DEFAULT '',
    total_amount REAL NOT NULL,
    total_count INTEGER NOT NULL,
    remain_amount REAL NOT NULL,
    remain_count INTEGER NOT NULL,
    claimed_records TEXT DEFAULT '[]',
    created_at INTEGER DEFAULT 0
);
-- claim_redpacket 每次都要按 gid 找「还有剩余份数」的最新一个红包；没有这条
-- 索引时，群里没有可抢红包的常见情况会退化成整表倒扫（且发生在全局写锁内）。
CREATE INDEX IF NOT EXISTS idx_redpackets_live
    ON redpackets (gid, remain_count, id);
CREATE TABLE IF NOT EXISTS custom_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    boss_uid TEXT NOT NULL,
    name TEXT NOT NULL,
    tag TEXT DEFAULT '创业',
    salary REAL DEFAULT 5000,
    balance REAL DEFAULT 0,
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tx_player ON transactions (gid, uid, id);
CREATE TABLE IF NOT EXISTS push_groups (
    gid TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    last_push TEXT DEFAULT '',
    updated_at INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS group_info (
    gid TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    updated_at INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_archives ON archives (gid, year, week);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events (created_at);
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY,
    name TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    price REAL DEFAULT 10,
    prev REAL DEFAULT 10,
    day TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS portfolio (
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    code TEXT NOT NULL,
    shares REAL DEFAULT 0,
    cost REAL DEFAULT 0,
    PRIMARY KEY (gid, uid, code)
);

CREATE TABLE IF NOT EXISTS lottery_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gid TEXT NOT NULL,
    uid TEXT NOT NULL,
    name TEXT DEFAULT '',
    number TEXT NOT NULL,
    draw_date TEXT NOT NULL,
    created_at INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lottery_date ON lottery_tickets (draw_date);
CREATE TABLE IF NOT EXISTS lottery_draws (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draw_date TEXT NOT NULL UNIQUE,
    number TEXT NOT NULL,
    pool REAL DEFAULT 0,
    paid REAL DEFAULT 0,
    ticket_count INTEGER DEFAULT 0,
    winners TEXT DEFAULT '[]',
    created_at INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lottery_pool (
    draw_date TEXT PRIMARY KEY,
    pool REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webui_sessions (
    jti TEXT PRIMARY KEY,
    subject TEXT NOT NULL DEFAULT 'admin',
    user_agent TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webui_sessions_exp ON webui_sessions(expires_at);
"""

# 索引单独一段：它们引用 players 的 value / rank_score 等列，而老库要等
# _migrate 补完列才有这些字段。放在 SCHEMA 里会让 executescript 直接
# "no such column: value"，整个 init() 失败。DB.init 因此分两步执行。
SCHEMA_INDEXES = """
-- 以下索引补的都是 EXPLAIN QUERY PLAN 实测出来的全表扫描 / 临时排序：
-- 排行榜（#富豪榜/#职级榜）此前是 SEARCH players(gid) + USE TEMP B-TREE，
-- 每次出榜都在群内现场排序；删档要 SCAN events / SCAN lottery_tickets；
-- 自建公司按 boss 查是 SCAN custom_companies。
CREATE INDEX IF NOT EXISTS idx_players_cash ON players (gid, cash DESC);
CREATE INDEX IF NOT EXISTS idx_players_lvl ON players (gid, lvl DESC, salary DESC);
CREATE INDEX IF NOT EXISTS idx_players_value ON players (gid, value DESC);
CREATE INDEX IF NOT EXISTS idx_players_rank ON players (gid, rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_events_player ON events (gid, uid, id DESC);
CREATE INDEX IF NOT EXISTS idx_lottery_player ON lottery_tickets (gid, uid, draw_date);
"""

# 唯一约束逐条执行：它和上面的性能索引失败后果完全不同 —— 性能索引建不起来只是
# 查询变慢，唯一约束建不起来意味着库里已经有重复行、数据完整性保护为零，必须用
# error 级别提示运维清理，而不是跟着一句「功能不受影响，仅查询变慢」糊过去。
# 应用层（_company.create_custom_company_if_free）仍保留前置 SELECT 兜住这种老库。
#
# 【不能塞进一个 executescript】：executescript 里任何一条语句失败都会中止整个
# 脚本，于是 A 表有重复行会静默取消 B 表的索引。实测：只让 custom_companies 有
# 重复行 → init() 之后 idx_archives_uniq 永远没建，而日志只提自建公司，运维无从
# 得知 archives 已经失去防重（archives 的读端都没有 ORDER BY，#上周榜 仍可能随机
# 取到旧快照）；反向（只有 archives 重复）时 idx_company_boss 正常建好。所以这里
# 存成 (索引名, 建索引语句, 重复行说明) 三元组，由 init() 逐条 try/except，
# 各自的 error 文案里点名是哪张表的重复行。
SCHEMA_CONSTRAINTS = (
    # 一个群里同一个老板只能有一家自建公司。此前这条不变量只靠「先 SELECT 再
    # INSERT」+ 进程内写锁维持，数据库层没有任何约束。
    (
        "idx_company_boss",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_company_boss"
        " ON custom_companies (gid, boss_uid)",
        "同一群同一老板存在多家自建公司（custom_companies 有重复行）",
    ),
    # 一个群的一周只能有一份周榜快照。save_archive 走 DELETE→INSERT 且读端
    # （has_archive / get_archive）都没有 ORDER BY，库里一旦出现重复行，
    # #上周榜 会随机取到旧的那一份 —— 与上面 idx_company_boss 是同一类漏洞。
    (
        "idx_archives_uniq",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_archives_uniq"
        " ON archives (gid, year, week)",
        "同一群同一周存在多份周榜快照（archives 有重复行）",
    ),
)


def _parse_players_ddl(schema: str) -> dict[str, str]:
    """从 SCHEMA 里解析 players 表的列定义，供缺列自动迁移拼 ALTER TABLE。

    直接复用 SCHEMA 而不是再维护一份「列名 → 类型」映射：players 的字段定义
    已经散落在 SCHEMA / COLUMNS / DEFAULTS / DELTA_* / START_CONFIG_KEYS 多处，
    再加一份手写映射必然漂移。这里解析的是同一段 DDL，加列时只改 SCHEMA 即可。
    """
    body = schema.split("CREATE TABLE IF NOT EXISTS players (", 1)[-1]
    body = body.split("\n);", 1)[0]
    out: dict[str, str] = {}
    for raw in body.splitlines():
        line = raw.strip().rstrip(",").strip()
        if not line or line.startswith(("--", "PRIMARY KEY", "UNIQUE", "FOREIGN KEY")):
            continue
        name, _, rest = line.partition(" ")
        if name and rest.strip():
            out[name] = rest.strip()
    return out


COLUMNS = [
    "gid",
    "uid",
    "nickname",
    "card",
    "cash",
    "deposit",
    "bank_level",
    "bank_limit",
    "bank_upgrade_price",
    "last_interest",
    "health",
    "mind",
    "exp",
    "lvl",
    "company",
    "salary",
    "house",
    "fund",
    "fund_day",
    "attend_streak",
    "work_day",
    "leave_week",
    "leave_count",
    "value",
    "rank_score",
    "rank_tier",
    "rank_matches",
    "duel_wins",
    "duel_losses",
    "fund_savings",
    "total_earned",
    "comp_leave",
    "commute",
    "house_owned",
    "social_pts",
    "side_lvl",
    "annual_leave",
    "annual_year",
    "year_bonus_year",
    "workstation",
    "party_year",
    "checkup_year",
    "meeting_day",
    "reply_day",
    "room_day",
    "pet_day",
    "pet",
    "title",
    "achievements",
    "review_year",
    "salary_bonus",
    "ot_day",
    "ot_count",
    "created_at",
    "updated_at",
]


# {列名: "TYPE DEFAULT x"}，由 SCHEMA 解析而来，_CoreMixin.init 用它补缺列
PLAYER_COLUMN_DDL = _parse_players_ddl(SCHEMA)

# 解析器有两个静默失效模式：① 上面的定位串一旦和 SCHEMA 不再逐字一致，split
# 会返回整个 SCHEMA，解析出一堆垃圾键；② 列定义写成跨行会把类型和 DEFAULT 拆散。
# 两种情况下缺列都查不到 DDL，于是自动迁移退化成它本该防住的 "no such column"。
# 这个断言在【导入时】就炸，比运行到 save_player 才报错好得多。
if set(PLAYER_COLUMN_DDL) != set(COLUMNS):
    raise RuntimeError(
        "players 表的 DDL 解析结果与 COLUMNS 不一致，schema 自动迁移会失效。"
        f"DDL 多出 {sorted(set(PLAYER_COLUMN_DDL) - set(COLUMNS))}，"
        f"COLUMNS 多出 {sorted(set(COLUMNS) - set(PLAYER_COLUMN_DDL))}"
    )


def _parse_schema_tables(schema: str) -> dict[str, dict[str, str]]:
    """把整段 SCHEMA 解析成 {表名: {列名: "TYPE DEFAULT x"}}。

    players 之外的表此前没有任何补齐机制：SCHEMA 全是 CREATE TABLE IF NOT
    EXISTS，表一旦存在就再也不会变，往 SCHEMA 里给 redpackets / webui_sessions /
    archives / lottery_* / player_* / push_groups / group_info 加一列，老库连
    建表语句都不会重跑，直到某条指令撞上 "no such column"（症状只是「指令执行
    异常」，完全指不到 schema）。这份解析结果就是那 16 张表的同一个兜底。

    解析约束（与 _parse_players_ddl 一致，列定义必须单行、注释独占一行）：
    多行定义会让类型与 DEFAULT 被拆散、解析出的 DDL 不完整，因此下面用导入期
    断言把「解析结果 == players 的既有解析结果」钉死，漂移在导入时就炸。
    """
    out: dict[str, dict[str, str]] = {}
    for chunk in schema.split("CREATE TABLE IF NOT EXISTS ")[1:]:
        name = chunk.split(" (", 1)[0].strip()
        if not name:
            continue
        # 第一行是表头（"<表名> ("），列定义从第二行开始
        body = chunk.split("\n);", 1)[0]
        lines = body.splitlines()[1:]
        cols: dict[str, str] = {}
        for raw in lines:
            # 先切掉行尾注释：带着 "-- ..." 去拼 ALTER TABLE 是语法错误
            line = raw.split("--", 1)[0].strip().rstrip(",").strip()
            if not line or line.startswith(")"):
                continue
            col, _, rest = line.partition(" ")
            if col.upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
                continue
            if col and rest.strip():
                cols[col] = rest.strip()
        if cols:
            out[name] = cols
    return out


TABLE_COLUMNS = _parse_schema_tables(SCHEMA)

# 两个解析器必须给出同一份 players 列集：否则其中一处静默失效时，另一处也补不上
# 缺口（这正是「导入期断言」要防的事，比运行到某条指令才报错好得多）。
if TABLE_COLUMNS.get("players") != PLAYER_COLUMN_DDL:
    raise RuntimeError(
        "SCHEMA 的表解析结果与 players 专用解析器不一致，缺列自动迁移会失效。"
        f"表解析多出 {sorted(set(TABLE_COLUMNS.get('players', {})) - set(PLAYER_COLUMN_DDL))}，"
        f"专用解析多出 {sorted(set(PLAYER_COLUMN_DDL) - set(TABLE_COLUMNS.get('players', {})))}"
    )

# schema 指纹，写进 PRAGMA user_version 当「版本门」：
# - user_version 原本恒为 0（没人写过），既不能表达「这个库已经补齐到当前
#   schema」，也挡不住反复逐表比对；
# - 指纹取的是「表名 + 列名集合」的 crc32，所以往 SCHEMA 加一列就自动变化，
#   不需要人记得手改一个版本号（手写版本号必然有人忘了加）——指纹变了就会重扫
#   一遍并把缺列补上，扫完再写回，下次 init 直接跳过。
# 只取列名集合而不是完整 DDL：改默认值/类型不影响「缺不缺列」，不必重扫。
def schema_stamp(tables: dict[str, dict[str, str]]) -> int:
    """{表: 列} → 32 位正整数指纹（PRAGMA user_version 的取值范围）。"""
    payload = "\n".join(f"{t}:{','.join(sorted(c))}" for t, c in sorted(tables.items()))
    return zlib.crc32(payload.encode()) & 0x7FFFFFFF


SCHEMA_STAMP = schema_stamp(TABLE_COLUMNS)

# v1 曾把背包/技能/冷却存在 players 的 JSON 列里，v2 拆成了三张子表。
# 迁移时要把这些列的内容搬进子表，搬完保留原列（SQLite 删列需重建表，不值得）。
LEGACY_JSON_COLUMNS = ("items", "skills", "cds")


DELTA_FLOAT_COLUMNS = {
    "cash": (2, 0.0, None),
    "total_earned": (2, 0.0, None),
    "mind": (1, 0.0, 100.0),
    "value": (2, 0.0, None),
    "deposit": (2, 0.0, None),
    "fund": (2, 0.0, None),
    "fund_savings": (2, 0.0, None),
}


DELTA_INT_COLUMNS = ("duel_wins", "duel_losses")


# 资金列：这些列的下限被击穿意味着「同一笔余额被花了两次」，钱是被凭空创造出来的
# （超额消费换到了商品，余额却只扣了一次）。这类击穿必须让整笔写回失败，而不是钳到
# 0 后照常上报成功 —— 后者等于把超额消费宽恕成免费商品。
# 不包含 mind/value：它们的下限击穿只是「状态被并发改小了」，钳到 0 是正确结果；
# 也不包含 total_earned（只增不减的累计列）。
MONEY_GUARD_COLUMNS = ("cash", "deposit", "fund", "fund_savings")


# player_cds.expires_at 被认定为「真时间戳」的下界（2001-09-09）。小于它的值是
# 历史版本写进来的长效道具标记（护盾卡/拉屎卡写的是 1），清理过期冷却时必须跳过，
# 否则玩家花钱买的有效道具会被当成 1970 年的过期冷却删掉。
MIN_REAL_TIMESTAMP = 1_000_000_000


class MoneyIntegrityError(RuntimeError):
    """资金列下限被击穿：同一笔余额被并发花了两次，整笔写回已回滚。

    为什么抛异常而不是只返回 False：81 处 save_player 调用点没有一处检查返回值，
    默默回滚会让 handler 照常把成功面板发出去 —— 玩家看到「摸鱼成功，精神 +15」，
    实际冷却与状态都没写，可以立刻重发。抛出后由 handlers/base.py 的兜底捕获，
    带堆栈落日志、给用户一句「请稍后再试」，这才是诚实的表现。
    """


DEFAULTS = {
    "nickname": "",
    "card": "",
    "cash": 800.0,
    "deposit": 0.0,
    "bank_level": 1,
    "bank_limit": 5000.0,
    "bank_upgrade_price": 200.0,
    "last_interest": 0,
    "health": 80.0,
    "mind": 80.0,
    "exp": 0,
    "lvl": 1,
    "company": -1,
    "salary": 0.0,
    "house": 0,
    "fund": 0.0,
    "fund_day": "",
    "attend_streak": 0,
    "work_day": "",
    "leave_week": "",
    "leave_count": 0,
    "value": 100.0,
    "cds": "{}",
    "rank_score": 1000,
    "rank_matches": 0,
    "duel_wins": 0,
    "duel_losses": 0,
    "fund_savings": 0.0,
    "total_earned": 0.0,
    "comp_leave": 0,
    # rank_tier / commute 的真实取值分别来自 rankevents.json 的 tiers 与
    # commute.json 的第一项。存储层不能 import gamedata（会与 gamedata 反向
    # 依赖 CUSTOM_BASE 形成循环），所以这里留空串，由 logic.load_player
    # （rank_tier）与 gd.commute_mode / gd.commute_name（commute）解析兜底。
    "rank_tier": "",
    "commute": "",
    "house_owned": 0,
    "skills": "[]",
    "social_pts": 0,
    "side_lvl": 1,
    "annual_leave": 3,
    "annual_year": "",
    "year_bonus_year": "",
    "workstation": 0,
    "party_year": "",
    "checkup_year": "",
    "meeting_day": "",
    "reply_day": "",
    "room_day": "",
    "pet_day": "",
    "pet": "",
    "title": "",
    "achievements": "[]",
    "review_year": "",
    # 个人涨薪累积系数：谈薪/年终考评的加薪存在这里，晋升与跳槽重算基础薪资时
    # 会把它乘回去，否则「永久涨薪」会被一次晋升静默抹掉
    "salary_bonus": 1.0,
    "ot_day": "",
    "ot_count": 0,
    "created_at": 0,
    "updated_at": 0,
}


START_CONFIG_KEYS = {
    "cash": "start_cash",
    "health": "start_health",
    "mind": "start_mind",
    "value": "start_value",
    "rank_score": "start_rank_score",
    "bank_limit": "bank_initial_limit",
    "bank_upgrade_price": "bank_initial_upgrade_price",
    "annual_leave": "annual_leave_days",
}


def new_player(gid: str, uid: str, nickname: str = "", start: dict | None = None) -> dict:
    """建号。start 为 {列名: 初始值} 覆盖表（由 DB.start_values 按配置生成）。"""
    p = dict(DEFAULTS)
    p.update(start or {})
    p["gid"] = str(gid)
    p["uid"] = str(uid)
    p["nickname"] = nickname or ""
    now = int(time.time())
    p["created_at"] = now
    p["updated_at"] = now
    return p


def _next_date(date_str: str) -> str:
    """YYYY-MM-DD 的下一天（彩票开奖滚存用）。"""
    y, m, d = (int(x) for x in str(date_str).split("-"))
    return (datetime.date(y, m, d) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
