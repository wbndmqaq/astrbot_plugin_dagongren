"""DB __init__ 等功能 Mixin（由 db.py 拆分）。"""

import json
import sqlite3
import time
from pathlib import Path

from ._const import (
    COLUMNS,
    DEFAULTS,
    DELTA_FLOAT_COLUMNS,
    DELTA_INT_COLUMNS,
    LEGACY_JSON_COLUMNS,
    MIN_REAL_TIMESTAMP,
    MONEY_GUARD_COLUMNS,
    PLAYER_COLUMN_DDL,
    SCHEMA,
    SCHEMA_CONSTRAINTS,
    SCHEMA_INDEXES,
    SCHEMA_STAMP,
    START_CONFIG_KEYS,
    TABLE_COLUMNS,
    MoneyIntegrityError,
    _write_lock,
    logger,
    new_player,
)


class _CoreMixin:
    def __init__(self, path, cfg=None):
        self.path = Path(path)
        # 建号初始值统一从插件配置读取（cfg 为实时配置对象）。集中在存储层，
        # 避免各调用点漏传参数而退回硬编码默认值。
        self.cfg = cfg if hasattr(cfg, "get") else {}
        # 不要写成 int(self._cfg(k, d) or d)：_cfg 已处理 None，多余的 or 会把
        # 合法的 0 当成"未配置"回退到默认值（events_max_rows=0 本意是不保留）
        self.busy_timeout = max(1000, int(self._cfg("db_busy_timeout_ms", 15000)))

    def _cfg(self, key, default=None):
        v = self.cfg.get(key) if hasattr(self.cfg, "get") else None
        return default if v is None else v

    def start_values(self) -> dict:
        """按配置生成新玩家的初始列值。"""
        out = {}
        for col, key in START_CONFIG_KEYS.items():
            v = self._cfg(key)
            if v is None:
                continue
            out[col] = int(v) if isinstance(DEFAULTS[col], int) else float(v)
        return out

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            # executescript 会隐式提交并抢 SQLite 的写锁：init 不只在启动时跑，
            # 「#恢复备份」之后也会再跑一次，那时其它群的写操作正在进行。
            # 和 _migrate 一样纳入进程内写锁，避免与它们互相 SQLITE_BUSY。
            with _write_lock:
                conn.executescript(SCHEMA)
                conn.commit()
            self._migrate(conn)
            # 索引必须在补列之后建：idx_players_value 等引用的是 _migrate 才补上的
            # 列，先建会在老库上 "no such column"。
            with _write_lock:
                try:
                    conn.executescript(SCHEMA_INDEXES)
                    conn.commit()
                except sqlite3.Error as e:
                    conn.rollback()
                    logger.warning(f"[上班族物语] 建索引失败（功能不受影响，仅查询变慢）：{e}")
                # 唯一约束逐条建、逐条报：它失败意味着库里已有重复行，数据完整性
                # 保护为零，不能用「仅查询变慢」的措辞糊过去。绝不能把它们放进一个
                # executescript —— 脚本里第一条失败会中止整个脚本，于是 A 表重复
                # 会静默取消 B 表的索引（实测 archives 就这样丢了防重），运维只能
                # 从日志里看到其中一条。
                for name, sql, hint in SCHEMA_CONSTRAINTS:
                    try:
                        conn.execute(sql)
                        conn.commit()
                    except sqlite3.Error as e:
                        conn.rollback()
                        logger.error(
                            f"[上班族物语] 唯一约束 {name} 建立失败：{e}。说明{hint}，"
                            "数据库层的防重保护未生效，请清理重复行后重载插件。"
                            "应用层前置检查仍在，正常操作不受影响。"
                        )
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection):
        """把已有库补齐到当前 schema。

        SCHEMA 里全是 CREATE TABLE IF NOT EXISTS，所以【新表】会自动建好，但
        players 表一旦存在就再也不会被改动——给玩家加一个字段后，老库的每一次
        save_player 都会 "no such column"，而症状只是「指令执行异常」，完全指不到
        schema。所以这里显式对比列集并补 ALTER TABLE ADD COLUMN。

        幂等：每次都重新读 PRAGMA table_info，已存在的列不会被再加一次。
        """
        # init() 里的 executescript 建表失败会直接抛出，所以这里 players 表一定存在
        have = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
        missing = [c for c in COLUMNS if c not in have]
        if missing:
            added = []
            with _write_lock:
                for col in missing:
                    ddl = PLAYER_COLUMN_DDL.get(col)
                    if not ddl:  # _const 的导入期断言已挡住，这里只是双保险
                        logger.error(
                            f"[上班族物语] 列 {col} 在 COLUMNS 里但 SCHEMA 没定义，无法迁移"
                        )
                        continue
                    conn.execute(f"ALTER TABLE players ADD COLUMN {col} {ddl}")
                    added.append(col)
                conn.commit()
            if added:  # 只报真正加上的，别把跳过的也算进去
                logger.info(
                    f"[上班族物语] 数据库已升级：players 表补齐 {len(added)} 个字段"
                    f"（{', '.join(added)}）"
                )
        # v1 的 players.items/skills/cds JSON 列 → v2 的三张子表。
        # 判据是「老列还在且内容非空」；搬完把那些行的老列清成空串，所以第二次
        # init 就匹配不到了（幂等）。注意 _save_children 是全量替换，因此这条
        # 路径只适用于「子表还没有数据」的 v1→v2 首次升级。
        legacy = [c for c in LEGACY_JSON_COLUMNS if c in have]
        if legacy:
            self._migrate_legacy_json(conn, legacy)
        # 其余 16 张表：同一个兜底（此前只覆盖了 players）
        self._migrate_tables(conn)

    def _migrate_tables(self, conn: sqlite3.Connection):
        """把 players 之外的每张表补齐到 SCHEMA 定义的列集。

        为什么需要：`CREATE TABLE IF NOT EXISTS` 只保证【新表】建得出来。表一旦
        存在，之后往 SCHEMA 里加列（redpackets 的 remain_amount、webui_sessions
        的 subject、archives 的 payload、lottery_* / player_* / push_groups /
        group_info …）对老库完全无效，直到某条指令撞上 "no such column"，而症状
        只是「指令执行异常」，运维根本指不到 schema。

        版本门（`PRAGMA user_version` 里存 SCHEMA 的列集指纹 SCHEMA_STAMP）：指纹
        一致曾经直接 return。但那只证明【上一次 init 时】这个库补齐过，不能证明
        【现在】还齐 —— 实测手工 `ALTER TABLE redpackets DROP COLUMN claimed_records`
        之后，stamp 仍然一致，下一次 init 直接跳过，随后 #抢红包 抛
        "no such column: claimed_records"（而 players 不受影响，因为它的列检查没有
        门禁）。所以现在 stamp 相同时也照样做一次只读 `PRAGMA table_info` 比对
        （17 次 PRAGMA，微秒级），只有确实需要 ADD COLUMN 时才走补列/盖章这条路径；
        stamp 只用来决定「需不需要再写一次」（已经是它就不重复写，省掉一次无意义
        的写事务）。
        """
        try:
            stamp = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except (sqlite3.Error, TypeError, IndexError, ValueError):
            stamp = -1  # 读不出来就当没补过，宁可多扫一次
        added: list[str] = []
        failed: list[str] = []
        with _write_lock:
            pending: list[tuple[str, str]] = []  # [(表.列, DDL)]
            for table, cols in TABLE_COLUMNS.items():
                have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if not have:
                    # 表不存在：建表脚本没跑成功过，或表被外部工具删了。这里不硬造
                    # 一张可能缺索引/约束的表，但【必须计入 failed】：盖章等于宣称
                    # 「这个库已是最新」，而缺表是比缺列更严重的不一致，盖章后每次
                    # init 都跳过它、问题永远不再被发现。
                    failed.append(f"{table}（表不存在）")
                    continue
                for col, ddl in cols.items():
                    if col not in have:
                        pending.append(
                            (f"{table}.{col}", f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                        )
            for label, ddl in pending:
                # 表名/列名/DDL 均来自本地 SCHEMA 常量（_const.TABLE_COLUMNS），
                # 不含任何外部输入；SQLite 也不支持给标识符绑定参数。
                try:
                    conn.execute(ddl)
                except sqlite3.Error as e:
                    # 典型情况：新列写成 NOT NULL 却没有 DEFAULT —— SQLite 拒绝
                    # 往已有数据的表里加这种列。单独记下来继续补其它列，但【不盖章】：
                    # 盖章等于宣称「这个库已是最新」，后面的 "no such column"
                    # 就会静默复发。留在日志里每次启动都提示，直到 SCHEMA 补上默认值。
                    failed.append(f"{label}（{e}）")
                    continue
                added.append(label)
            if failed:
                logger.error(
                    "[上班族物语] 数据库与 schema 不一致，补齐失败（"
                    + "；".join(failed[:10])
                    + "）。缺列的请给这些列补 DEFAULT，缺表的请确认它为何被删，"
                    "然后重载插件；未盖章，每次启动都会重报。"
                )
            elif stamp != SCHEMA_STAMP:
                # 全部补完（或确认无需补）才盖章；指纹已经一致就不重复写
                conn.execute(f"PRAGMA user_version = {int(SCHEMA_STAMP)}")
            conn.commit()
        if added:
            logger.info(
                f"[上班族物语] 数据库已升级：{len(added)} 个字段补齐"
                f"（{', '.join(added[:20])}{'…' if len(added) > 20 else ''}）"
            )

    def _migrate_legacy_json(self, conn: sqlite3.Connection, legacy: list[str]):
        """把 v1 的 JSON 列内容搬进 player_items / player_skills / player_cds。"""
        cols = ",".join(legacy)
        # 空串 / '{}' / '[]' 都算「没有内容」，搬完的行会被清成空串因此不会重复搬
        empty = "('', '{}', '[]')"
        where = " OR ".join(f"COALESCE({c},'') NOT IN {empty}" for c in legacy)
        moved = 0
        skipped = 0
        migrated_keys: list[tuple[str, str]] = []
        with _write_lock:
            rows = conn.execute(
                f"SELECT gid, uid, {cols} FROM players WHERE {where}"  # noqa: S608 - 列名来自本地常量白名单
            ).fetchall()
            for row in rows:
                gid, uid = str(row["gid"]), str(row["uid"])
                items = self._parse_items(row["items"]) if "items" in legacy else None
                cds = self._parse_items(row["cds"]) if "cds" in legacy else None
                skills = self._parse_skills(row["skills"]) if "skills" in legacy else None
                if not (items or cds or skills):
                    skipped += 1  # 老列有内容但解析不出东西：坏 JSON
                    continue
                self._save_children(conn, gid, uid, items, cds, skills)
                moved += 1
                migrated_keys.append((gid, uid))
            # 只清【真的搬过】的行：解析失败的行保留原始串，运维还有手工抢救的
            # 余地（一并清掉就只剩一句 warning 了）
            if migrated_keys:
                sets = ",".join(f"{c}=''" for c in legacy)
                conn.executemany(
                    f"UPDATE players SET {sets} WHERE gid=? AND uid=?",  # noqa: S608 - 列名来自本地常量白名单
                    migrated_keys,
                )
            conn.commit()
        if moved:
            logger.info(f"[上班族物语] 数据库已升级：{moved} 名玩家的背包/技能/冷却已迁入子表")
        if skipped:
            logger.warning(
                f"[上班族物语] {skipped} 名玩家的旧 JSON 列无法解析，"
                "已保留原始内容未迁移（可在 WebUI 导出后手工处理）"
            )

    def _conn(self) -> sqlite3.Connection:
        timeout_s = self.busy_timeout / 1000.0
        conn = sqlite3.connect(self.path, timeout=timeout_s)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout}")
        except Exception:
            # PRAGMA 也会抛（库被另一个进程独占、文件被换成非 WAL 快照时
            # "database is locked"）。不显式关掉的话这条连接只能等 CPython 的
            # 引用计数回收，失败面还覆盖【每一条】DB 调用（包括纯读）。
            conn.close()
            raise
        return conn

    def get_player(self, gid, uid, nickname: str = "", start_cash: float | None = None) -> dict:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM players WHERE gid=? AND uid=?", (str(gid), str(uid))
            ).fetchone()
            if row is not None:
                p = self._row_to_player(conn, row)
                # 只在昵称为空时回填：nickname 是"首次得到的名字"，不能每次 load
                # 都被 anick 返回的群名片(card)覆盖，否则与 set_card 里
                # CASE WHEN nickname='' 的语义矛盾，且会污染昵称字段。
                if nickname and not p["nickname"]:
                    p["nickname"] = nickname
                return p
        finally:
            conn.close()
        start = self.start_values()
        if start_cash is not None:  # 调用方显式指定优先（兼容旧签名）
            start["cash"] = float(start_cash)
        return self._create_player(gid, uid, nickname, start)

    def _create_player(self, gid, uid, nickname: str, start: dict) -> dict:
        """建号：INSERT OR IGNORE + 回读。

        不能走 save_player：_save_full 是 ON CONFLICT DO UPDATE 全列覆盖，
        且 _save_children 无条件清空背包/技能/冷却。两条首次指令并发（或本次
        SELECT 与他人建号交错）时，后到者会把先到者的现金重置成初始值、
        背包清空。OR IGNORE 让"已存在"变成无操作，再回读真实行。
        """
        p = new_player(gid, uid, nickname, start)
        cols = list(COLUMNS)
        values = [p.get(c, DEFAULTS.get(c, 0)) for c in cols]
        with _write_lock:
            conn = self._conn()
            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO players ({','.join(cols)}) "  # noqa: S608 - 列名来自本地常量
                    f"VALUES ({','.join('?' for _ in cols)})",
                    values,
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM players WHERE gid=? AND uid=?",
                    (str(gid), str(uid)),
                ).fetchone()
                if row is not None:
                    return self._row_to_player(conn, row)
            finally:
                conn.close()
        return self.normalize(p)  # 理论不可达；保证任何情况下都返回可用 dict

    def normalize(self, p: dict) -> dict:
        d = dict(DEFAULTS)
        for k, dv in d.items():
            if k not in p or p[k] is None:
                p[k] = dv
        p["gid"] = str(p["gid"])
        p["uid"] = str(p["uid"])
        # 子表合并键的统一默认值：保证任何路径返回的玩家 dict 都有这些键
        p.setdefault("items", "{}")
        p.setdefault("skills", "[]")
        p.setdefault("cds", "{}")
        p.setdefault("_cds", {})
        p.setdefault("_skills", [])
        # 快照基准值：save_player 据此把并发列换算成增量写回
        p["_orig"] = {
            c: p.get(c, DEFAULTS.get(c, 0)) for c in (*DELTA_FLOAT_COLUMNS, *DELTA_INT_COLUMNS)
        }
        return p

    def _row_to_player(self, conn, row) -> dict:
        """把 players 行 + 子表数据合并成业务层可见的玩家 dict。

        合并后与旧版 JSON 列格式完全一致（items/skills/cds 为 JSON 字符串，
        _cds/_skills 为解析后的对象），业务层零改动即可使用。
        """
        p = self.normalize(dict(row))
        gid, uid = p["gid"], p["uid"]
        items = {}
        for r in conn.execute(
            "SELECT item_key, count FROM player_items WHERE gid=? AND uid=?",
            (gid, uid),
        ):
            items[r["item_key"]] = int(r["count"])
        p["items"] = json.dumps(items, ensure_ascii=False)
        cds = {}
        for r in conn.execute(
            "SELECT cd_key, expires_at FROM player_cds WHERE gid=? AND uid=?",
            (gid, uid),
        ):
            cds[r["cd_key"]] = int(r["expires_at"])
        p["cds"] = json.dumps(cds, ensure_ascii=False)
        p["_cds"] = cds
        skills = [
            r["skill_name"]
            for r in conn.execute(
                "SELECT skill_name FROM player_skills WHERE gid=? AND uid=? ORDER BY id",
                (gid, uid),
            )
        ]
        p["skills"] = json.dumps(skills, ensure_ascii=False)
        p["_skills"] = skills
        return p

    def _rows_to_players(self, conn, rows) -> list[dict]:
        """批量版 _row_to_player：三张子表各发一条 IN 查询，而不是每行 3 条。

        榜单/名录/分页列表都要一次转换几十上百行；逐行调 _row_to_player 会变成
        1+3N 条 SQL（500 人的群渲染一次财富榜 ≈ 1500 条），全压在同一个线程与
        同一个连接上。
        """
        rows = list(rows)
        if not rows:
            return []
        players = [self.normalize(dict(r)) for r in rows]
        keys = [(p["gid"], p["uid"]) for p in players]
        # 按 (gid,uid) 精确配对查询，避免旧的 gid IN(...) AND uid IN(...) 在多群时
        # 返回跨群笛卡尔组合行（会多查无效行）；并分片控制占位符总数，防止单群
        # uid 超 SQLite 变量上限（默认 999）时报 "too many SQL variables"。
        items: dict[tuple, dict] = {}
        cds: dict[tuple, dict] = {}
        skills: dict[tuple, list] = {}
        batch = 200  # 每批最多 200 组 key → 400 个占位符，留足安全余量
        for start in range(0, len(keys), batch):
            chunk = keys[start : start + batch]
            cond = " OR ".join("(gid=? AND uid=?)" for _ in chunk)
            args = [v for k in chunk for v in k]
            for r in conn.execute(
                f"SELECT gid, uid, item_key, count FROM player_items WHERE {cond}",  # noqa: S608 - 条件由 len(chunk) 拼装，占位符参数化
                args,
            ):
                items.setdefault((r["gid"], r["uid"]), {})[r["item_key"]] = int(r["count"])
            for r in conn.execute(
                f"SELECT gid, uid, cd_key, expires_at FROM player_cds WHERE {cond}",  # noqa: S608
                args,
            ):
                cds.setdefault((r["gid"], r["uid"]), {})[r["cd_key"]] = int(r["expires_at"])
            for r in conn.execute(
                f"SELECT gid, uid, skill_name FROM player_skills "  # noqa: S608
                f"WHERE {cond} ORDER BY id",
                args,
            ):
                skills.setdefault((r["gid"], r["uid"]), []).append(r["skill_name"])

        for p, key in zip(players, keys, strict=True):
            it = items.get(key, {})
            cd = cds.get(key, {})
            sk = skills.get(key, [])
            p["items"] = json.dumps(it, ensure_ascii=False)
            p["cds"] = json.dumps(cd, ensure_ascii=False)
            p["_cds"] = cd
            p["skills"] = json.dumps(sk, ensure_ascii=False)
            p["_skills"] = sk
        return players

    def save_player(self, p: dict) -> bool:
        """单事务写回：并发列增量 + 其余列覆盖 + 子表（背包/技能/冷却）全量替换。

        子表数据来源：p["_cds"]（dict）、p["_skills"]（list）、p["items"]
        （JSON 字符串或 dict）。skills/cds 同 items：若下划线解析版缺失，
        回退到 JSON 字符串版（p["skills"]/p["cds"]）归一，避免只改 JSON 串
        时被静默丢弃——三种子表两种写法一律兼容。

        返回 True = 已提交；返回 False = 目标行在本次指令执行期间被删档，本次
        写入按「不复活」处理（见 _save_delta/_save_full 的行不存在分支）。

        资金列下限被击穿（超额消费）不在这里返回 False，而是抛
        MoneyIntegrityError 整笔回滚：调用方有 81 处且没有一处检查返回值，
        静默回滚会让 handler 照常发出「购买成功」面板。
        """
        p = dict(p)
        orig = p.pop("_orig", None)
        # 三张子表的写入源是否在 dict 里出现过：若调用方给的字典根本没有对应键，
        # 说明它只是改主表字段，_save_children 就不该碰那张子表——否则 DELETE +
        # 空重建会把背包/技能/冷却静默清空（例如补录一张缺 items 的玩家档案）。
        has_items = "items" in p
        has_cds = "_cds" in p or "cds" in p
        has_skills = "_skills" in p or "skills" in p
        # 不能用 `or`：业务层只改 p["_cds"]（如 cds.pop 消耗掉最后一个冷却），
        # 而 p["cds"] 还是加载时的旧 JSON 串。`{} or 旧串` 会取旧串，
        # 把刚刚消耗掉的冷却/护盾原样写回去。空 dict 是有效值，只有 None 才算缺失。
        cds = p.pop("_cds", None)
        if cds is None:
            cds = self._parse_cds(p.get("cds"))
        skills = p.pop("_skills", None)
        if skills is None:
            skills = self._parse_skills(p.get("skills"))
        items = p.pop("items", None)
        p.pop("skills", None)
        p.pop("cds", None)
        p["updated_at"] = int(time.time())
        gid, uid = str(p["gid"]), str(p["uid"])
        with _write_lock:
            conn = self._conn()
            try:
                applied: dict = {}
                if orig is None:
                    # 调用方没带快照（手工构造的补录 dict）：按 upsert 全量写
                    self._save_full(conn, p)
                else:
                    ok, applied, breached = self._save_delta(conn, p, orig)
                    if not ok:
                        # 带了快照却找不到行 = 玩家在本次指令执行期间被删档了。
                        # 绝不能 _save_full 把它按旧快照 INSERT 回来：那会静默撤销
                        # 管理员的删档，还原的是删档前的现金/背包。放弃本次写入。
                        conn.rollback()
                        logger.warning(
                            f"[上班族物语] 玩家 {gid}/{uid} 的档案已不存在，"
                            "本次写回已放弃（通常是指令执行期间被管理员删档）"
                        )
                        return False
                    if breached:
                        # 资金列击穿：钳到 0 后照常提交等于承认「花了两次、只扣一次」，
                        # 商品/道具会白送。整笔回滚，玩家的钱与商品都留在原状。
                        # 抛异常而不是 return False：调用方没有一处检查返回值，
                        # 静默回滚会让 handler 照常发成功面板（详见 MoneyIntegrityError）。
                        conn.rollback()
                        raise MoneyIntegrityError(
                            f"玩家 {gid}/{uid} 的资金列 {','.join(breached)} "
                            "下限被击穿，本次写回已整笔放弃"
                            "（超额消费：同一笔余额被并发花了两次）"
                        )
                self._save_children(
                    conn,
                    gid,
                    uid,
                    items if has_items else None,
                    cds if has_cds else None,
                    skills if has_skills else None,
                )
                conn.commit()
                # 基准值只能在 commit 成功后推进：同一个快照被保存两次时，第二次的
                # 增量必须是 0 而不是再算一遍完整差值（orig 与调用方的 p["_orig"]
                # 是同一个对象）。提前推进的话，_save_children/commit 抛错回滚后，
                # 同一请求内的下一次 save_player 算出的增量是 0，这次修改静默丢失。
                if applied:
                    orig.update(applied)
                return True
            finally:
                conn.close()

    def _parse_items(self, items) -> dict:
        """把业务层的 items 表示（JSON 字符串 / dict / None）归一成 {key: count}。"""
        if isinstance(items, dict):
            return items
        if isinstance(items, str):
            try:
                v = json.loads(items)
                return v if isinstance(v, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _parse_skills(self, skills) -> list:
        """把技能列的表示（JSON 字符串 / list / None）归一成 list。"""
        if isinstance(skills, list):
            return skills
        if isinstance(skills, str):
            try:
                v = json.loads(skills)
                return v if isinstance(v, list) else []
            except json.JSONDecodeError:
                return []
        return []

    def _parse_cds(self, cds) -> dict:
        """把冷却列的表示（JSON 字符串 / dict / None）归一成 {key: expires_at}。"""
        if isinstance(cds, dict):
            return cds
        if isinstance(cds, str):
            try:
                v = json.loads(cds)
                return v if isinstance(v, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_children(self, conn, gid, uid, items, cds, skills):
        """全量替换子表（背包/冷却/技能）。调用方负责持锁与 commit。

        items / cds / skills 传 None 表示"调用方字典里没有对应键，别碰这张子表"；
        传空 dict/list 表示"键存在但为空，清空这张子表"。save_player 已按键是否
        出现决定传 None 还是解析值。
        """
        now = int(time.time())
        if items is not None:
            conn.execute("DELETE FROM player_items WHERE gid=? AND uid=?", (gid, uid))
            item_rows = []
            for k, v in self._parse_items(items).items():
                try:
                    c = max(0, int(v))
                except (TypeError, ValueError):
                    c = 1
                if c > 0 and str(k):
                    item_rows.append((gid, uid, str(k)[:64], c, now))
            if item_rows:
                conn.executemany(
                    "INSERT INTO player_items (gid, uid, item_key, count, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    item_rows,
                )
        if cds is not None:
            conn.execute("DELETE FROM player_cds WHERE gid=? AND uid=?", (gid, uid))
            cds = cds if isinstance(cds, dict) else {}
            cds_rows = []
            for k, v in cds.items():
                try:
                    exp = int(v)
                except (TypeError, ValueError):
                    continue
                if str(k):
                    cds_rows.append((gid, uid, str(k)[:64], exp, now))
            if cds_rows:
                conn.executemany(
                    "INSERT INTO player_cds (gid, uid, cd_key, expires_at, updated_at) "
                    "VALUES (?,?,?,?,?)",
                    cds_rows,
                )
        if skills is not None:
            conn.execute("DELETE FROM player_skills WHERE gid=? AND uid=?", (gid, uid))
            skills = skills if isinstance(skills, list) else []
            skill_rows = [(gid, uid, str(s).strip()[:64], now) for s in skills if str(s).strip()]
            if skill_rows:
                conn.executemany(
                    "INSERT INTO player_skills (gid, uid, skill_name, learned_at) VALUES (?,?,?,?)",
                    skill_rows,
                )

    def _save_delta(
        self, conn: sqlite3.Connection, p: dict, orig: dict
    ) -> tuple[bool, dict, list[str]]:
        """增量写回既有行：并发列提交变化量，其余列照常覆盖。

        返回 (ok, applied, breached)：
        - ok=False 表示目标行已不存在（如被管理员删档），由调用方决定如何处理；
        - applied 是本次真实写入的并发列值，**必须等 commit 成功后**才能用它推进
          调用方的基准快照（提前推进的话，_save_children/commit 抛错回滚后，同一
          请求内的下一次 save_player 算出的增量会是 0，这次修改静默丢失）；
        - breached 是资金列下限被击穿的列名，非空代表出现了超额消费。

        重要约束：DELTA_FLOAT_COLUMNS 上的 SQL MIN/MAX 只能作为【最终钳制】，不能
        阻断合理 delta。比如 cash 已经被 atomic 列更新增到 80，本进程要扣 -30，
        数据库里 round(80 + (-30), 2) = 50，这是正常的，不会被钳。

        下限钳制（lo）是防负资产的硬底线，但它有个副作用：当「快照 delta」叠加
        到已被别处改小的当前值上会击穿 lo 时，差额会被钳掉——那意味着上层出现了
        超额消费（同一笔余额被花了两次）。这种钳制必须可观测，因此下面先读一次
        当前值；命中 MONEY_GUARD_COLUMNS 时进 breached 让调用方整笔回滚，
        命中 mind/value 这类状态列时只打 warning（钳到下限就是正确结果）。
        """
        sets, args = [], []
        applied = {}
        breached: list[str] = []
        # 有钳制（上限或下限）的并发列都要先读当前值：一是用于下限击穿的告警，
        # 二是用于把【钳制后】的真实值写回 applied（见函数末尾的基准值推进）。
        # 所有并发浮点列都要先读当前值：DELTA_FLOAT_COLUMNS 的每一项都带下限
        # （lo 全非空），读出来既用于下限击穿告警，也用于把【钳制后】的真实值
        # 写回 applied。这里直接取全表而不加 lo/hi 过滤 —— 那个条件恒真，留着
        # 只会让人以为存在「无钳制」的列，而那样的列根本走不到下面的 effective 计算。
        clamped_cols = list(DELTA_FLOAT_COLUMNS)
        current = {}
        row = conn.execute(
            f"SELECT {','.join(clamped_cols)} FROM players WHERE gid=? AND uid=?",  # noqa: S608 - 列名来自本地常量 DELTA_FLOAT_COLUMNS
            (str(p["gid"]), str(p["uid"])),
        ).fetchone()
        if row is None:
            return False, {}, []  # 行已不存在，由 save_player 决定如何处理
        current = {c: float(row[c] or 0) for c in clamped_cols}
        for c in COLUMNS:
            if c in ("gid", "uid"):
                continue
            new = p.get(c, DEFAULTS.get(c, 0))
            if c in DELTA_FLOAT_COLUMNS:
                nd, lo, hi = DELTA_FLOAT_COLUMNS[c]
                delta = round(float(new) - float(orig.get(c) or 0), nd)
                # 不要用 MIN/MAX 包裹「当前值」，否则与 atomic 列更新并发时
                # 会吞掉已入账的余额；这里只钳最终结果。
                expr = f"round({c}+?,{nd})"
                effective = round(current[c] + delta, nd)
                if hi is not None:
                    expr = f"MIN({hi},{expr})"  # 钳到上限
                    effective = min(float(hi), effective)
                if lo is not None:
                    expr = f"MAX({lo},{expr})"  # 钳到下限（不会无故抬升）
                    cur_v = current.get(c, 0.0)
                    if round(cur_v + delta, nd) < float(lo) - 1e-9:
                        if c in MONEY_GUARD_COLUMNS:
                            breached.append(c)
                        logger.warning(
                            f"[上班族物语] {c} 下限钳制：玩家 {p['gid']}/{p['uid']} "
                            f"当前 {cur_v} + 增量 {delta} < {lo}，已钳到 {lo}"
                            "（通常意味着同一笔余额被并发花了两次）"
                        )
                    effective = max(float(lo), effective)
                sets.append(f"{c}={expr}")
                args.append(delta)
                # 记【钳制后】的值：记请求值的话，钳制生效时 orig 会推进到一个
                # 数据库里从未出现过的数字，下一次保存的增量就是错的。
                applied[c] = round(effective, nd)
            elif c in DELTA_INT_COLUMNS:
                sets.append(f"{c}={c}+?")
                args.append(int(new) - int(orig.get(c) or 0))
                applied[c] = new
            else:
                sets.append(f"{c}=?")
                args.append(new)
        args += [str(p["gid"]), str(p["uid"])]
        sql = f"UPDATE players SET {','.join(sets)} WHERE gid=? AND uid=?"  # noqa: S608 - SET 子句由 COLUMNS 常量生成，值全走 ? 绑定
        cur = conn.execute(sql, args)
        if cur.rowcount <= 0:
            return False, {}, []
        return True, applied, breached

    def _save_full(self, conn: sqlite3.Connection, p: dict):
        """整行 upsert：仅用于调用方没带 _orig 快照的场景（补录/导入）。

        这里没有增量语义，所以直接对并发浮点列做 [lo, hi] 钳制。不加的话，
        这条分支就是资金护栏的一个缺口——同一份「不能有负资产」的约束
        _save_delta 严格守着，而它按 COLUMNS 原样落库。生产调用点目前都经
        get_player 注入 _orig（normalize 里），故这条分支实际不可达；但一旦
        有人用「手构造的 dict」补录数据，缺口就会静默生效。

        与 _save_delta 的差别是【有意的】，别急着把两者合并：
        - _save_delta 知道调用方读到的旧值，下限被击穿意味着「同一笔余额被花了
          两次」，必须抛 MoneyIntegrityError 让整笔失败；
        - 这条分支没有基准值，无从判断是并发击穿还是补录数据本身就带负数，
          所以只能钳制 + warning（钳到 0 是唯一安全的收敛点）。
        代价是 ON CONFLICT DO UPDATE 具备 INSERT 能力：补录一个已被删档的
        (gid, uid) 会把它复活。这条路径没有生产调用方，若将来要开给运维用，
        应先补一次「行已存在」的前置校验（同 _save_delta 的 not-ok 分支）。
        """
        cols = list(COLUMNS)
        values = []
        for c in cols:
            v = p.get(c, DEFAULTS.get(c, 0))
            spec = DELTA_FLOAT_COLUMNS.get(c)
            if spec is not None:
                _nd, lo, hi = spec
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    f = float(DEFAULTS.get(c, 0))
                clamped = f
                if hi is not None:
                    clamped = min(float(hi), clamped)
                if lo is not None:
                    clamped = max(float(lo), clamped)
                if clamped != f:
                    # 与 _save_delta 同口径：钳制必须可观测，否则「数据被悄悄改了」
                    logger.warning(
                        f"[上班族物语] _save_full 钳制 {c}：玩家 {p.get('gid')}/"
                        f"{p.get('uid')} 提交 {f}，已按 [{lo}, {hi}] 钳到 {clamped}"
                    )
                values.append(clamped)
            else:
                values.append(v)
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=?" for c in cols if c not in ("gid", "uid"))
        sql = (
            f"INSERT INTO players ({','.join(cols)}) VALUES ({placeholders}) "  # noqa: S608 - 列名来自本地常量 COLUMNS，值全走 ? 绑定
            f"ON CONFLICT(gid,uid) DO UPDATE SET {updates}"
        )
        args = values + [v for c, v in zip(cols, values, strict=True) if c not in ("gid", "uid")]
        conn.execute(sql, args)

    def close(self):
        """卸载时执行 WAL checkpoint 将 -wal 数据并回主库，避免残留。

        checkpoint(TRUNCATE) 是【写】操作：init/save_player/cleanup_old_data 都
        显式持 _write_lock，这里不持就会与在飞的写操作抢锁、拿到 SQLITE_BUSY，
        而下面的 except 会把异常吞掉 —— docstring 承诺的"避免 -wal 残留"就静默失效。
        """
        try:
            with _write_lock:
                conn = self._conn()
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                finally:
                    conn.close()
        except Exception:  # noqa: BLE001, S110 - checkpoint 失败不影响 SQLite 正常关闭
            pass

    # 调用方漏传时的占位（见 _redpacket 同名注释）
    _KIND_REFUND = "红包退回"

    def cleanup_old_data(self, refund_note: str = "", refund_kind: str = ""):
        """定期批量清理超量事件/流水、旧红包、旧周榜快照与过期 WebUI 会话。

        用「取第 N 新一行的 id 作为水位线，删掉更旧的」代替 NOT IN 反连接：
        前者只需沿主键索引倒扫 N 行，后者是全表自连接。

        refund_note 是「红包超时退回」写进流水备注的玩家可见文案，由调用方
        从 resources/texts 取；存储层不持有游戏文案。
        """
        # 至少留 1 天：redpacket_retention_days 是「历史红包保留多久」，不是开关。
        # 运维填 0 想表达「不保留历史红包」，直接算出的 week_ago == now 会把群里
        # 所有【正在抢】的红包一次性强制退款销毁。
        days = max(1.0, float(self._cfg("redpacket_retention_days", 7)))
        week_ago = int(time.time() - days * 86400)
        caps = (
            ("events", int(self._cfg("events_max_rows", 800))),
            ("transactions", int(self._cfg("transactions_max_rows", 50000))),
            # 每天一行 + 一个 winners JSON blob，原先完全没有上限
            ("lottery_draws", int(self._cfg("lottery_draws_max_rows", 400))),
        )
        keep_weeks = max(1, int(self._cfg("archive_retention_weeks", 52)))
        refunded = 0
        with _write_lock:
            conn = self._conn()
            try:
                for table, keep in caps:
                    if keep <= 0:
                        # keep=0 表示不保留：直接清空，别去算 OFFSET -1
                        conn.execute(f"DELETE FROM {table}")  # noqa: S608 - 表名来自本地常量
                        continue
                    row = conn.execute(
                        f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1 OFFSET ?",  # noqa: S608
                        (keep - 1,),
                    ).fetchone()
                    if row:
                        conn.execute(
                            f"DELETE FROM {table} WHERE id < ?",  # noqa: S608
                            (int(row["id"]),),
                        )
                # 过期未抢完的红包：退还剩余金额给发包人再删除。只删已领完的会让
                # 「没人抢完」的包永久留在表里，且永久可抢——钱既不退也不作废。
                stale = conn.execute(
                    "SELECT id, gid, sender_uid, remain_amount FROM redpackets "
                    "WHERE remain_count > 0 AND created_at < ?",
                    (week_ago,),
                ).fetchall()
                for r in stale:
                    amt = round(float(r["remain_amount"] or 0), 2)
                    # 发包人可能已被管理员删档：UPDATE 影响 0 行时钱无处可退，
                    # 这时绝不能再插一条「已退回」的流水（假账 + 日志虚报）
                    cur = (
                        conn.execute(
                            "UPDATE players SET cash=round(cash+?,2) WHERE gid=? AND uid=?",
                            (amt, str(r["gid"]), str(r["sender_uid"])),
                        )
                        if amt > 0
                        else None
                    )
                    if cur is not None and cur.rowcount > 0:
                        conn.execute(
                            "INSERT INTO transactions "
                            "(gid, uid, kind, amount, note, created_at) "
                            "VALUES (?,?,?,?,?,?)",
                            (
                                str(r["gid"]),
                                str(r["sender_uid"]),
                                refund_kind or self._KIND_REFUND,
                                amt,
                                refund_note,
                                int(time.time()),
                            ),
                        )
                        refunded += 1
                    conn.execute("DELETE FROM redpackets WHERE id=?", (int(r["id"]),))
                conn.execute(
                    "DELETE FROM redpackets WHERE remain_count <= 0 AND created_at < ?",
                    (week_ago,),
                )
                # 周榜快照：每群每周一行，不清理就是无上限增长。
                # 必须 DISTINCT：OFFSET 数的是行数，而同一周有多少行取决于群数，
                # 直接数行会让「保留 52 周」在 10 个群时变成只保留 5 周。
                row = conn.execute(
                    "SELECT year, week FROM archives GROUP BY year, week "
                    "ORDER BY year DESC, week DESC LIMIT 1 OFFSET ?",
                    (keep_weeks - 1,),
                ).fetchone()
                if row:
                    conn.execute(
                        "DELETE FROM archives WHERE (year * 100 + week) < ?",
                        (int(row["year"]) * 100 + int(row["week"]),),
                    )
                conn.execute(
                    "DELETE FROM webui_sessions WHERE expires_at < ?",
                    (int(time.time()),),
                )
                # 早已到期的冷却行：只有「该玩家又存了一次档」才会被顺带重建，
                # 长期不活跃的玩家会把过期行永久留在表里。留一天缓冲再删，
                # 避免和正在读 _cds 的指令抢同一批行。
                #
                # 下界 MIN_REAL_TIMESTAMP 不可省：护盾卡/拉屎卡这类长效标记与冷却
                # 共用本表，历史版本把它们写成 expires_at=1（1970 年），无下界的
                # DELETE 会把玩家花钱买的、仍然有效的道具当过期冷却删掉。新版本已
                # 改写远未来时间戳（logic.flag_set），这里的下界负责保住旧库里的存量。
                conn.execute(
                    "DELETE FROM player_cds WHERE expires_at >= ? AND expires_at < ?",
                    (MIN_REAL_TIMESTAMP, int(time.time()) - 86400),
                )
                # 群维度表（push_groups / group_info）只增不减：每见过一个群就常驻
                # 1~2 行，群退了也不删。量级只有「群数」，但它是唯一没有任何收敛
                # 机制的表。判据用「本群已无任何玩家 + 超过 30 天没动过」，避免把
                # 刚建群、还没发过 #上班 的群误删（那会丢掉它的推送开关）。
                stale = int(time.time()) - 30 * 86400
                # push_groups 额外放过「enabled=1 且真的推送过」的行：那是一位运维
                # 显式开过、且确实在用的推送开关，静默把它删掉等于把开关偷偷关回
                # 去（push_enabled 变 False，之后群里再也不会收到早报），比多留一行
                # 糟得多；而它是有界的（上限就是「曾经开过并用过推送的群数」）。
                # 从未推送过（last_push=''）的 enabled=1 行仍按陈旧处理，否则
                # 「开关被打开过但一天都没用上」的群会永久堆积。
                conn.execute(
                    "DELETE FROM push_groups WHERE updated_at < ? AND gid NOT IN "
                    "(SELECT DISTINCT gid FROM players) AND (enabled = 0 OR last_push = '')",
                    (stale,),
                )
                # group_info 仍按原判据清理（它只是群名缓存，删掉会自动重建），
                # 但删除数量要留一条 info 日志：静默回收运维会以为表还在长。
                info_cur = conn.execute(
                    "DELETE FROM group_info WHERE updated_at < ? AND gid NOT IN "
                    "(SELECT DISTINCT gid FROM players)",
                    (stale,),
                )
                dropped_info = max(0, int(info_cur.rowcount or 0))
                conn.commit()
            finally:
                conn.close()
        if refunded:
            logger.info(f"[上班族物语] 已退回 {refunded} 个超时未领完红包的剩余金额")
        if dropped_info:
            logger.info(
                f"[上班族物语] 已清理 {dropped_info} 条 30 天无活动且无玩家的群名缓存"
                "（group_info；群再出现时会自动重建）"
            )
