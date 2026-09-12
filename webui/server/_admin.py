"""WebUIServer 的 _AdminMixin：玩家/配置/公司/JSON 等管理端 API（拆分自原 webui/server.py，现为 webui/server/ 包成员）。"""

import asyncio
import contextlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import ClassVar

from ._const import (
    CONFIG_HIDDEN_KEYS,
    CONFIG_READONLY_KEYS,
    GAMEDATA_DIR,
    JWT_SECRET_MIN_BYTES,
    SCHEMA_PATH,
)
from ._deps import gd, logic
from ._util import _json


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 5) -> None:
    """os.replace 的短重试版（Windows 专用坑）。

    Windows 上 MoveFileEx(MOVEFILE_REPLACE_EXISTING) 在【目标文件正被另一个
    句柄打开】时直接报 WinError 5 拒绝访问，而 CPython 的 open()/read_text()
    不带 FILE_SHARE_DELETE —— 游戏侧正在读这份 JSON、或另一个保存的 replace
    刚巧落在同一时刻，都会让一次正常保存变成「写入失败」。

    重试 5 次 × 20ms 覆盖这种毫秒级的瞬时占用；仍然失败就如实抛给调用方
    （面板会显示「写入失败，请检查插件目录权限」），不会静默丢改动。
    """
    last: OSError | None = None
    for _attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as e:
            last = e
            time.sleep(0.02)
    raise last  # type: ignore[misc]


class _AdminMixin:
    # 玩家档案可在线编辑的列：{列名: (类型, 下限, 上限)}。
    # 与 webui/app.js 的 ADM_FIELDS 一一对应（cash/deposit/health/mind/
    # exp/salary/fund_savings/comp_leave/value），上限统一钳到 1e12 与
    # logic.MAX_AMOUNT 一致，防止 WebUI 写入超大数值冲垮经济系统。
    EDITABLE: ClassVar[dict[str, tuple]] = {
        "cash": (float, 0.0, 1e12),
        "deposit": (float, 0.0, 1e12),
        "health": (float, 0.0, 100.0),
        "mind": (float, 0.0, 100.0),
        "exp": (int, 0, 10**12),
        "salary": (float, 0.0, 1e12),
        "fund_savings": (float, 0.0, 1e12),
        "comp_leave": (int, 0, 100000),
        "value": (float, 0.0, 1e12),
    }

    async def _admin_get(self, request):
        """按 uid 精确 / 昵称·群名片模糊定位玩家。

        面板输入框承诺「用户ID或昵称」，此前却只走 get_player_row（严格 uid 相等），
        输昵称必然 404。「查看自己」类指令不能用模糊匹配（会串到别人的档案），
        但这里是管理员主动查询、且结果会在面板上回显匹配方式，因此放行模糊匹配，
        并用 matched 告诉前端「本次是按关键字命中的」，让运维能核对改的是不是本人。
        """
        gid = request.query.get("gid", "")
        uid = request.query.get("uid", "")
        if not (gid and uid):
            return _json({"error": "未找到"}, 404)
        p = await asyncio.to_thread(self.db.get_player_row, gid, uid)
        matched = "uid"
        if not p:
            p = await asyncio.to_thread(self.db.find_player_any, gid, uid)
            matched = "keyword" if p else ""
        if not p:
            return _json({"error": "未找到该玩家（可用用户ID、昵称或群名片）"}, 404)
        return _json(
            {
                "profile": self.build_profile(p, await self._company_names(gid)),
                "matched": matched,
            }
        )

    async def _admin_save(self, request):
        """按 uid 保存玩家档案数值列。

        返回里 rejected（NaN/inf/类型不符，整列不写）与 clamped（超出上下限被
        【钳制后写入】）是两件事，必须分开回传：此前钳制是静默的 —— 提交
        {"mind": -5, "fund_savings": 1e15} 会落库 0.0 / 1e12，rejected 为空，
        面板弹的是绿字「已保存玩家数据」。运维看到「保存成功」就以为生效了，
        刷新才发现数值停在下限/上限上，只会反复重试。
        """
        body = await self._body(request)
        gid = str(body.get("gid", ""))
        uid = str(body.get("uid", ""))
        p = await asyncio.to_thread(self.db.get_player_row, gid, uid)
        if not p:
            return _json({"error": "未找到"}, 404)
        rejected = []
        clamped: dict[str, dict] = {}
        for k, (tp, lo, hi) in self.EDITABLE.items():
            if k not in body:
                continue
            try:
                v = tp(body[k])
            except (ValueError, TypeError, OverflowError):
                # OverflowError：json.loads 接受 Infinity / 1e400，int(inf) 抛的是
                # OverflowError，漏掉它会把一个本该 400 的请求变成 500
                rejected.append(k)
                continue
            if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
                rejected.append(k)
                continue
            clamped_to = max(lo, min(hi, v))
            if clamped_to != v:
                clamped[k] = {"from": round(v, 2) if tp is float else int(v), "to": clamped_to}
            p[k] = round(clamped_to, 2) if tp is float else int(clamped_to)
        # 面板只改主表数值列，绝不该动背包/技能/冷却：save_player 见到这三个键
        # 就会 DELETE + 按快照重建对应子表，把玩家在这几百毫秒里买到的道具、
        # 刚进入的冷却一起抹掉。去掉这几个键即让 _save_children 整体跳过。
        writeback = {
            k: v for k, v in p.items() if k not in ("items", "cds", "_cds", "skills", "_skills")
        }
        await asyncio.to_thread(self.db.save_player, writeback)
        return _json(
            {
                "ok": True,
                "rejected": rejected,
                "clamped": clamped,
                "profile": self.build_profile(p, await self._company_names(gid)),
            }
        )

    async def _admin_delete(self, request):
        body = await self._body(request)
        gid = str(body.get("gid", ""))
        uid = str(body.get("uid", ""))
        if not gid or not uid:
            return _json({"error": "缺参数"}, 400)
        await asyncio.to_thread(self.db.delete_player, gid, uid)
        return _json({"ok": True})

    async def _schema_async(self) -> dict:
        """读取并缓存配置 schema（磁盘 IO 放线程，别在事件循环上做）。

        运维在面板外改了 _conf_schema.json 后，下次访问 _admin_config 会
        自动重读——按 mtime 检测即可，避免重启插件。
        """
        mtime = await asyncio.to_thread(self._schema_mtime_of)
        if self._schema is None or self._schema_mtime != mtime:
            self._schema = await asyncio.to_thread(self._load_schema)
            self._schema_mtime = mtime
        return self._schema

    @staticmethod
    def _schema_mtime_of() -> int:
        try:
            return int(SCHEMA_PATH.stat().st_mtime)
        except OSError:
            return 0

    async def _admin_config(self, request):
        schema = await self._schema_async()
        # 直接读实时配置对象：快照会让面板显示过期值，
        # 并让一次「基于旧表单的保存」把别处的改动写回去
        cfg = {}
        for k, meta in schema.items():
            v = self._live_config.get(k, meta.get("default"))
            cfg[k] = "" if k in CONFIG_HIDDEN_KEYS else v
        return _json(
            {
                "schema": schema,
                "config": cfg,
                "hidden_keys": sorted(CONFIG_HIDDEN_KEYS & set(schema)),
            }
        )

    def _coerce(self, tp: str, raw, meta: dict):
        """按 schema 类型转换并做范围钳制；非法值抛 ValueError/TypeError。"""
        if tp == "bool":
            if isinstance(raw, str):
                raw = raw.strip().lower() in ("1", "true", "on", "yes", "是")
            return bool(raw)
        if tp in ("int", "float"):
            v = float(raw)
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("nan/inf")
            lo, hi = meta.get("min"), meta.get("max")
            if lo is not None:
                v = max(float(lo), v)
            if hi is not None:
                v = min(float(hi), v)
            return int(v) if tp == "int" else v
        if tp == "list":
            if isinstance(raw, str):
                raw = raw.replace("，", "\n").replace(",", "\n").split("\n")
            elif not isinstance(raw, (list, tuple)):
                raw = [raw]
            # 元素类型以 schema 的 default 为准：default 是数值列表时，把前端
            # 提交的 "60" 这类字符串归一化回 int/float。
            # 不这么做的话，配置面板每次保存都会把 shopping_weights=[60,30,10]
            # 写成 ["60","30","10"]，而消费侧（life_daily.shopping）的类型守卫
            # 会把它判成「配置写歪」并静默退回等权 —— 运维只改 push_hour 也会
            # 连带把三档购物命中率从 60/30/10 变成各 33%，且没有任何提示。
            dflt = meta.get("default")
            nums = [x for x in dflt if not isinstance(x, bool)] if isinstance(dflt, list) else []
            want_num = bool(nums) and all(isinstance(x, (int, float)) for x in nums)
            want_int = want_num and all(isinstance(x, int) for x in nums)
            result = []
            for x in raw:
                if isinstance(x, bool):
                    continue
                if want_num:
                    # 数值列表里混入非数字：整项拒绝（抛错由调用方记入 notes），
                    # 而不是把字符串塞进去让消费侧静默退回默认值
                    try:
                        f = float(x)
                    except (TypeError, ValueError):
                        raise ValueError(f"list 元素 {x!r} 不是数字") from None
                    if f != f or f in (float("inf"), float("-inf")):
                        raise ValueError("list 元素是 nan/inf")
                    result.append(int(f) if want_int else f)
                    continue
                if isinstance(x, (int, float)):
                    result.append(x)
                    continue
                s = str(x).strip()
                if s:
                    result.append(s[:100])
            return result[:200]
        return str(raw).strip()[:500]

    async def _admin_config_save(self, request):
        body = await self._body(request)
        values = body.get("values") or {}
        if not isinstance(values, dict):
            return _json({"error": "bad body"}, 400)
        schema = await self._schema_async()
        if not schema:
            # schema 读不到时，下面的 schema.get(k) 会把每个键都跳过，
            # 于是「已保存 0 项」和成功长得一模一样，改密/改端口静默失效
            return _json({"error": "配置 schema 不可用，已拒绝保存以避免静默丢弃"}, 500)
        target = self._live_config
        # 清空访问密码 = 关闭鉴权，只允许「当前进程实际绑定在本机」时执行。
        # 判据必须是 self.host（本次监听真正 bind 的地址），不能用本次保存后的
        # 配置值：webui_host 改了要重载插件才会重新 bind，而 auth_on 是立刻生效的。
        # 一次 {"webui_host":"127.0.0.1","webui_password":""} 就能在 socket 仍监听
        # 0.0.0.0 的情况下把整个管理 API 变成无鉴权。
        # 同时也要求「保存后的配置」仍是本机，否则重载后就成了全网卡无鉴权。
        eff_host = str(target.get("webui_host") or self.host)
        if isinstance(values.get("webui_host"), str) and values["webui_host"].strip():
            eff_host = values["webui_host"].strip()
        local_only = {"127.0.0.1", "localhost", "::1"}
        host_is_local = str(self.host) in local_only and eff_host in local_only
        allow_clear_pwd = bool(body.get("confirm_disable_auth"))
        applied = 0
        notes: list[str] = []
        pwd_changed = False
        for k, raw in values.items():
            meta = schema.get(k)
            if not meta:
                continue
            try:
                v = self._coerce(meta.get("type", "string"), raw, meta)
            except (TypeError, ValueError) as e:
                # 不静默跳过：非法值必须让运维在面板上看到，否则「保存成功但没生效」
                # 与「保存成功且生效」长得一模一样
                notes.append(f"{k} 的值不合法，已保持原值（{e}）")
                continue
            if k == "webui_password":
                # 密码特殊处理：写入 _live_config 的必须是 Argon2id 哈希，明文只活在
                # 本次请求的局部变量里。空串表示"保持原值"，仅本机监听 + 显式确认
                # 才允许真的清空（清空 = 关闭鉴权）。
                if v == "":
                    if not host_is_local:
                        notes.append(
                            "非本机监听下不允许清空访问密码，已保持原值"
                            f"（当前监听 {self.host}，保存后 {eff_host}）"
                        )
                    elif not allow_clear_pwd:
                        notes.append("清空访问密码需显式确认（confirm_disable_auth），已保持原值")
                    else:
                        target[k] = ""
                        applied += 1
                        pwd_changed = True
                else:
                    new_hash = v if v.startswith("$argon2id$") else await self._ahash_pwd(v)
                    if new_hash != self.password_stored:
                        target[k] = new_hash  # 落盘前先写成哈希
                        applied += 1
                        pwd_changed = True
                continue
            if k in CONFIG_READONLY_KEYS:
                # 插件自身的内部状态（如「下次登录必须改密」）：面板读不到真实值，
                # 也不接受写回 —— 否则一次普通保存就把它翻转了。
                notes.append(f"{k} 是插件内部状态，忽略面板提交的值")
                continue
            if k == "webui_jwt_secret" and v:
                # HMAC(HS256) 的密钥强度就是令牌的不可伪造性：3 个字节的密钥
                # 用 pyjwt 自己的话是 "below the minimum recommended length of
                # 32"。面板此前照单全收并回 200 成功，运维以为「轮换已完成」，
                # 实际上签名强度掉回可爆破区间，而 pyjwt 只在签发时打一条
                # warning —— 面板里看不到。不合法就保持原值并让运维看到。
                raw_bytes = len(str(v).encode("utf-8"))
                if raw_bytes < JWT_SECRET_MIN_BYTES:
                    notes.append(
                        f"{k} 太短（{raw_bytes} 字节 < 建议下限 {JWT_SECRET_MIN_BYTES} 字节"
                        "≈ 64 个十六进制字符），已保持原值"
                    )
                    continue
            if k in CONFIG_HIDDEN_KEYS and v == "":
                # 其余敏感键留空 = 保持原值
                continue
            target[k] = v
            applied += 1
        save = getattr(target, "save_config", None)
        persisted = False
        if callable(save):
            try:
                await asyncio.to_thread(save)  # 落盘是同步文件写，别堵事件循环
                persisted = True
            except Exception as e:  # noqa: BLE001
                self.log.error(f"[上班族物语] 配置保存失败：{e}")
                return _json({"error": f"配置保存失败：{e}"}, 500)
        # JWT 签名密钥即时生效：面板改了 webui_jwt_secret 后，必须同步到
        # 内存里的 _jwt_secret，否则旧的 HS256 密钥继续签发/校验，旋转等于没生效；
        # 且旧 cookie 全部作废，强制用新密钥重新登录。
        new_secret = str(target.get("webui_jwt_secret") or "")
        if new_secret and new_secret != self._jwt_secret:
            try:
                await asyncio.to_thread(self.db.revoke_all_webui_sessions)
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[上班族物语] JWT 轮换撤销会话失败：{e}")
            self._jwt_secret = new_secret
        # 密码修改即时生效（无需重载插件）；同时让旧 cookie 全部失效
        if pwd_changed:
            new_pwd = str(target.get("webui_password") or "")
            # 哈希已在上面的循环里写回 target，这里只做内存态同步 + 撤销旧会话。
            # 哈希可能因平台不同而不相等，仅当实际改了口令时才撤销会话，避免每次
            # 保存配置都把管理员踢下线。
            if new_pwd != self.password_stored:
                try:
                    await asyncio.to_thread(self.db.revoke_all_webui_sessions)
                except Exception as e:  # noqa: BLE001
                    self.log.warning(f"[上班族物语] 撤销会话失败：{e}")
                self.password_stored = new_pwd
            self.auth_on = bool(self.password_stored)
        return _json({"ok": True, "applied": applied, "persisted": persisted, "notes": notes})

    async def _admin_companies(self, request):
        return _json({"companies": gd.companies()})

    async def _admin_companies_save(self, request):
        body = await self._body(request)
        raw_list = body.get("companies", [])
        if not isinstance(raw_list, list) or not raw_list:
            return _json({"error": "empty"}, 400)

        def num(raw, key, default, lo, hi):
            try:
                v = float(raw.get(key, default))
            except (TypeError, ValueError):
                v = float(default)
            return round(min(hi, max(lo, v)), 4)

        cleaned: list[dict] = []
        seen_names: set[str] = set()
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()[:40]
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            cleaned.append(
                {
                    # id 由前端传入，非数字不能让整个保存 500：0 表示"新行"
                    "id": int(num(raw, "id", 0, 0, 999_999)),
                    "name": name,
                    "tag": str(raw.get("tag") or "综合").strip()[:12],
                    "salary": num(raw, "salary", 3500, 0, 9_999_999),
                    "intensity": num(raw, "intensity", 5, 0, 24),
                    "risk": num(raw, "risk", 0.01, 0, 1),
                    "min_exp": int(num(raw, "min_exp", 0, 0, 999_999)),
                    "desc": str(raw.get("desc") or "").strip()[:120],
                    "perks": [str(x).strip()[:30] for x in (raw.get("perks") or [])][:6],
                }
            )
        if not cleaned:
            return _json({"error": "empty"}, 400)

        # 规律性保证：按薪资升序排列后统一重编号为 1..N
        cleaned.sort(key=lambda c: (c["salary"], c["min_exp"], c["name"]))
        prev_ids = {int(c["id"]) for c in gd.companies()}
        claimed: set[int] = set()
        remap: dict[int, int] = {}
        for i, c in enumerate(cleaned, start=1):
            oid = int(c["id"])
            c["id"] = i
            # 一个旧 ID 只能被一行认领：前端若给新行分配了「已存在的 ID」，
            # 后续行不会再冒充它，避免把被删公司的员工划给一家无关新公司
            if oid > 0 and oid in prev_ids and oid not in claimed:
                claimed.add(oid)
                if oid != i:
                    remap[oid] = i
        unemploy = [oid for oid in sorted(prev_ids) if oid not in claimed]

        cp = GAMEDATA_DIR / "companies.json"
        content = json.dumps({"companies": cleaned}, ensure_ascii=False, indent=2)
        try:
            await asyncio.to_thread(self._write_json_file, cp, content)
        except OSError as e:
            self.log.error(f"[上班族物语] 写入 companies.json 失败：{e}")
            return _json({"error": "写入公司数据失败，请检查插件目录权限"}, 500)
        if remap or unemploy:
            await asyncio.to_thread(self.db.remap_company_ids, remap, unemploy)
        await asyncio.to_thread(gd.load_all, force=True)
        return _json({"ok": True, "count": len(cleaned)})

    @staticmethod
    def _write_json_file(path: Path, content: str):
        """原子落盘：先写同目录临时文件再替换，避免写一半崩掉留下坏 JSON。

        临时文件名必须唯一（tempfile.mkstemp）：固定的 "<名字>.json.tmp" 会让
        两个并发保存同一文件互相踩——A 写到一半、B 把同一个临时文件覆盖掉、
        A 再 replace，最终落盘的是半截内容（公司表/文案表都可能这么坏掉）。
        失败路径同样要清临时文件：以前磁盘满或权限不足时会在目录里留下
        *.json.tmp，下次读目录的人（运维、备份脚本）会把它当数据文件。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        tmp = Path(tmp_name)
        try:
            try:
                fh = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
            except BaseException:
                # fdopen 自身抛错（内存不足、参数校验、被取消）时那个 fd 仍归我们
                # 所有：不关就是一次泄漏，进程 fd 用尽后所有读写都会失败。
                # 注意 os.fdopen 成功后绝不能在这里 close(fd)——那会和文件对象
                # 的析构双重关闭，可能关掉别的线程刚拿到的同号 fd。
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
            with fh:  # 由 with 负责关闭（含异常路径）
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())  # 先落盘再 replace，断电也不会留下空文件
            _replace_with_retry(tmp, path)
        except BaseException:
            # 无论是编码/磁盘/权限错误还是被取消，都不留半成品
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise

    async def _json_get(self, request):
        name = request.query.get("name", "")
        if not re.fullmatch(r"[a-z0-9_]+", name or ""):
            return _json({"error": "bad name"}, 400)
        p = self._texts_root() / f"{name}.json"
        try:
            raw_text = await asyncio.to_thread(p.read_text, "utf-8")
            data = json.loads(raw_text)
        except (OSError, ValueError):
            return _json({"error": "未找到"}, 404)
        return _json({"name": name, "data": data})

    async def _json_save(self, request):
        body = await self._body(request)
        name = str(body.get("name") or "")
        data = body.get("data")
        if not re.fullmatch(r"[a-z0-9_]+", name):
            return _json({"error": "bad name"}, 400)
        if not isinstance(data, dict) or not data:
            return _json({"error": "empty"}, 400)
        for k, v in data.items():
            if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_]+", k):
                return _json({"error": f"非法键名：{str(k)[:30]}"}, 400)
            # 值允许任意 JSON 类型。文案库里既有数组（逐条文案、对象数组），
            # 也有 titles / labels 这类字典、pick_auto 这类裸字符串、_comment
            # 注释串。早期只放行数组，导致 32 个文案库里有 22 个整个分类都存
            # 不下去（顶层有 titles 即被拒），与 README「32 个文本库在线配置
            # 热重载」的承诺不符。
            # 键名仍要递归校验：它们会被前端拼进 HTML 属性与 onclick，是存储型
            # XSS 的入口，与值是什么类型无关。
            bad = self._bad_nested_key(v)
            if bad:
                return _json({"error": f"键 {k} 内含非法字段名：{bad[:30]}"}, 400)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        target_path = self._texts_root() / f"{name}.json"
        try:
            await asyncio.to_thread(self._write_json_file, target_path, content)
        except OSError as e:
            self.log.error(f"[上班族物语] 写入文案 {name}.json 失败：{e}")
            return _json({"error": "写入文案失败，请检查插件目录权限"}, 500)
        await asyncio.to_thread(gd.load_all, force=True)
        # 文案改了 → Jinja2 编译过的模板也要清，否则下次截图仍是旧模板
        if self._renderer is not None and hasattr(self._renderer, "clear_template_cache"):
            self._renderer.clear_template_cache()
        return _json({"ok": True, "keys": len(data)})

    @classmethod
    def _bad_nested_key(cls, node, depth: int = 0) -> str:
        """递归找出嵌套结构里第一个非法字段名（返回空串表示全部合法）。"""
        if depth > 6:
            return "__too_deep__"
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_]+", k):
                    return str(k)
                bad = cls._bad_nested_key(v, depth + 1)
                if bad:
                    return bad
        elif isinstance(node, list):
            for item in node:
                bad = cls._bad_nested_key(item, depth + 1)
                if bad:
                    return bad
        return ""

    async def _admin_players(self, request):
        gid = request.query.get("gid", "")
        page = logic.parse_int(request.query.get("page", "1"), default=1, lo=1) or 1
        size = 20
        if gid:
            total, players = await asyncio.to_thread(self.db.page_players, gid, page, size)
        else:
            total, players = 0, []
        names = await self._company_names(gid)
        return _json(
            {
                "total": total,
                "page": page,
                "players": [self.build_profile(p, names) for p in players],
            }
        )

    async def _admin_events_clear(self, request):
        # 走 db 层：与游戏内写入共用同一把写锁，不再在这里裸开连接
        n = await asyncio.to_thread(self.db.clear_events)
        return _json({"ok": True, "deleted": n})
