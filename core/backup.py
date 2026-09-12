"""数据备份：基于 sqlite3 在线 backup API 的安全快照。"""

import sqlite3
import time
from pathlib import Path

from .career_common import tt
from .db import COLUMNS, _write_lock

# 保留的最大快照数量：超出后自动淘汰最旧的，避免备份目录无限增长
MAX_KEEP = 20

# 在线 backup 的分片参数：每复制 BACKUP_PAGES 页就让出 SQLite 层的锁小睡一下，
# 让并发写有机会插进来。整库一口气复制会长时间独占数据库，几十 MB 时可达数秒。
BACKUP_PAGES = 256
BACKUP_SLEEP = 0.005


def _t(key: str, variables: dict | None = None) -> str:
    """取 backup.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("backup", key, variables)


class BackupManager:
    def __init__(self, db_path: Path, backups_dir: Path, logger=None, max_keep: int = MAX_KEEP):
        self.db_path = Path(db_path)
        self.dir = Path(backups_dir)
        self.log = logger
        self.max_keep = max(1, int(max_keep))

    def _log(self, msg):
        if self.log:
            self.log.info(f"[上班族物语][备份] {msg}")

    def create(self, label: str = "") -> dict:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe_label = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:20]
        name = f"{stamp}_{safe_label}" if safe_label else stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        # 同一秒内重复创建不再互相覆盖
        base, seq = name, 1
        while (self.dir / f"{name}.db").exists():
            seq += 1
            name = f"{base}-{seq}"
        dest_path = self.dir / f"{name}.db"
        src = None
        dest = None
        try:
            src = sqlite3.connect(self.path(), timeout=15)
            dest = sqlite3.connect(dest_path, timeout=15)
            # 不持 _write_lock：那是全插件唯一的写锁，整库复制期间握着它等于
            # 让所有玩家的写操作排队。sqlite3 的在线 backup 本身就为并发写设计
            # ——源库在复制中被改写时会自动重启复制，快照始终一致；
            # pages/sleep 分片让出 SQLite 层的锁，避免长时间独占数据库。
            src.backup(dest, pages=BACKUP_PAGES, sleep=BACKUP_SLEEP)
            dest.commit()
        finally:
            if dest is not None:
                dest.close()
            if src is not None:
                src.close()
        size = dest_path.stat().st_size
        self._log(f"创建备份 {name} ({size // 1024} KB)")
        self.prune()
        return {"name": name, "file": str(dest_path), "size": size}

    def prune(self) -> int:
        """只保留最新的 max_keep 个快照，返回删除数量。"""
        items = self.list()
        removed = 0
        for it in items[self.max_keep :]:
            try:
                (self.dir / f"{it['name']}.db").unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        if removed:
            self._log(f"淘汰旧备份 {removed} 个（上限 {self.max_keep}）")
        return removed

    def path(self) -> Path:
        return self.db_path

    def list(self) -> list[dict]:
        """按创建时间倒序列出快照（最新在前）。"""
        if not self.dir.is_dir():
            return []
        out = []
        for p in self.dir.glob("*.db"):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": p.stem,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    # 同一秒内创建的多份（带 -2/-3 后缀、或同秒的不同标签）mtime
                    # 相同，用文件名倒序打破平局：后缀越大越新。
                    "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                }
            )
        # 按 mtime 倒序，而不是按文件名倒序：快照名虽然以时间戳开头，但同一秒内
        # 创建的多份其文件名顺序与创建顺序无关（"…_admin.db" 排在 "…_admin-3.db"
        # 之后），prune 取 items[max_keep:] 时会把最新的那份当成最旧的删掉 ——
        # 与「只保留最新的 max_keep 个」的承诺正好相反。
        out.sort(key=lambda it: (it["mtime"], it["name"]), reverse=True)
        return out

    def verify(self, target: Path) -> str:
        """恢复前校验快照：完整性 + players 表列集必须覆盖当前 schema。

        返回 "" 表示可用，否则返回不可用原因（供上层直接回显）。
        """
        try:
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=15)
        except sqlite3.Error as e:
            return _t("err_open_fail", {"error": e})
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                return _t("err_integrity")
            cols = {r[1] for r in conn.execute("PRAGMA table_info(players)")}
            if not cols:
                return _t("err_no_players")
            missing = [c for c in COLUMNS if c not in cols]
            if missing:
                return _t(
                    "err_missing_cols",
                    {
                        "cols": "、".join(missing[:6]),
                        "more": _t("val_ellipsis") if len(missing) > 6 else "",
                    },
                )
        except sqlite3.Error as e:
            return _t("err_verify_fail", {"error": e})
        finally:
            conn.close()
        return ""

    def restore(self, name_or_index) -> dict | None:
        """恢复快照到运行中的主库。

        返回 None 表示没找到；返回 dict 且带 "error" 键表示校验未通过。
        """
        item = self._resolve(name_or_index)
        if item is None:
            return None
        target = self.dir / f"{item['name']}.db"
        reason = self.verify(target)
        if reason:
            self._log(f"拒绝恢复 {item['name']}：{reason}")
            return {**item, "error": reason}
        # 用在线 backup API 恢复到运行中的主库，避免直接覆盖文件
        # 与 WAL 日志产生一致性风险。
        # 恢复必须持 _write_lock 全程：这是破坏性覆盖，不能让并发写落在
        # 半恢复的库上（与 create 不同——那只是读快照，无需互斥）。
        src = None
        dst = None
        try:
            src = sqlite3.connect(target, timeout=15)
            dst = sqlite3.connect(self.path(), timeout=15)
            with _write_lock:
                src.backup(dst)
                dst.commit()
        finally:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()
        self._log(f"恢复备份 {item['name']}")
        return item

    def delete(self, name_or_index) -> dict | None:
        item = self._resolve(name_or_index)
        if item is None:
            return None
        (self.dir / f"{item['name']}.db").unlink(missing_ok=True)
        self._log(f"删除备份 {item['name']}")
        return item

    def _resolve(self, name_or_index) -> dict | None:
        """解析备份标识：纯数字按 1 起序号，否则要求名称精确匹配。

        故意不做模糊/子串匹配——恢复与删除都是破坏性操作，
        「差不多像」的输入必须失败而不是命中一个碰巧排在前面的快照。
        """
        items = self.list()
        s = str(name_or_index or "").strip()
        if not items or not s:
            return None
        if s.isdigit() and len(s) <= 6:
            idx = int(s) - 1
            return items[idx] if 0 <= idx < len(items) else None
        for it in items:
            if s == it["name"]:
                return it
        return None
