"""WebUIServer 的 _CoreMixin：__init__、配置读取、Host/Origin/代理判定、限流与鉴权（拆分自 webui/server.py，现为 webui/server/ 包成员）。"""

import asyncio
import ipaddress
import json
import time
from collections.abc import MutableMapping
from pathlib import Path

import jwt

from ._const import (
    COOKIE,
    JWT_ALG,
    JWT_ISSUER,
    LOGIN_BLOCK,
    LOGIN_GLOBAL_MAX,
    LOGIN_MAX_FAILS,
    LOGIN_TRACK_MAX,
    LOGIN_WINDOW,
    PUBLIC_PATHS,
    SCHEMA_PATH,
    TEXTS_DIR,
    WEBUI_DIR,
)
from ._deps import hash_password, verify_password
from ._util import _json


class _CoreMixin:
    def __init__(
        self,
        db,
        backups,
        market,
        host,
        port,
        version,
        logger,
        password="",
        config_data: dict | None = None,
        app_id_getter=None,
        jwt_secret: str = "",
        renderer=None,
    ):
        self.db = db
        self.backups = backups
        self.market = market
        # 空 host 不再回落 0.0.0.0：那等于把管理后台暴露到全部网卡
        self.host = host or "127.0.0.1"
        self.port = int(port)
        self.version = version
        self.log = logger
        self.password_stored = str(password or "")  # Argon2id 哈希；_verify_pwd 统一校验
        # QQ 官方机器人的 appid 要到收到第一条消息才知道，所以取回调而非快照
        self._app_id_getter = app_id_getter
        # 实时引用插件配置对象本身（不是快照）：面板读到的永远是当前值。
        # 用 MutableMapping 而非 dict 判断：AstrBotConfig 可能是 dict 子类之外的
        # 可变映射实现，只认 dict 会让面板配置读写静默落到空 dict。
        self._live_config = config_data if isinstance(config_data, MutableMapping) else {}
        self.auth_on = bool(self.password_stored)
        # JWT 签名密钥：main.py 启动期生成并持久化到 cfg。这里不做兜底生成——
        # 空密钥会让 HS256 退化成"任何人都能签发"，必须让 start() 直接失败。
        self._jwt_secret = str(jwt_secret or "")
        if not self._jwt_secret:
            raise ValueError("WebUI 缺少 JWT 签名密钥，拒绝启动（空密钥可被伪造令牌）")
        self._fails: dict[str, list] = {}  # ip -> [失败次数, 窗口起点, 解封时间]
        # 全站失败计数 [窗口内失败数, 窗口起点]：定长，不随来源 IP 增长。
        # 存在的理由见 _global_rate_limited（轮换 XFF 换限流桶）。
        self._global_fails: list = [0, 0.0]
        # Argon2id 是 64MiB/次的内存硬函数，且登录是未鉴权路径：不限并发时
        # 线程池能同时跑满 min(32, cpu+4) 个 → 峰值 2GB，足以拖垮宿主进程
        self._pwd_sem = asyncio.Semaphore(2)
        self._pwd_inflight = 0  # 在飞 + 排队中的密码校验数（见 PWD_QUEUE_MAX）
        self.dir = WEBUI_DIR
        self._schema: dict | None = None
        self._schema_mtime: int = -1
        self._runner = None
        self._session_gc_task = None  # WebUI 自己的过期会话清理任务（start/stop 管理）
        self._renderer = renderer  # 用于文案/模板编辑后失效 Jinja2 缓存

    def _c(self, key, default=None):
        """读 WebUI 实例自身的配置偏好：_live_config 是实时引用，operator 在 AstrBot
        / WebUI 里改了立刻可见，无需重启插件。"""
        try:
            v = self._live_config.get(key) if hasattr(self._live_config, "get") else None
        except Exception:  # noqa: BLE001 - 配置对象异常不应让面板打不开
            return default
        return default if v is None else v

    def _app_id(self) -> str:
        try:
            return str(self._app_id_getter() or "") if self._app_id_getter else ""
        except Exception:  # noqa: BLE001 - 头像取不到不影响面板
            return ""

    def _allowed_hosts(self) -> set[str]:
        names = {self.host, "127.0.0.1", "localhost", "::1", "[::1]"}
        return {f"{n}:{self.port}" for n in names if n} | {n for n in names if n}

    def _netloc_ok(self, netloc: str) -> bool:
        """Host / Origin 的 netloc 白名单判定。

        配置为具体地址时维持严格精确匹配；仅当绑定全部网卡
        （webui_host=0.0.0.0 / ::，即局域网访问场景）时，额外放行
        「端口匹配 + IP 字面量或 localhost」的 netloc：

        - 域名一律拒绝：DNS rebinding 与跨站伪造用的都是域名 Host/Origin；
        - 端口必须等于本面板端口：本机其它端口页面借同 IP Origin 打 CSRF
          仍被挡住；
        - 修复前 0.0.0.0 部署的局域网用户 Host 是内网 IP:port，不在
          白名单里，所有请求一律 400，绑定全部网卡等于完全没法远程访问。
        """
        netloc = (netloc or "").strip()
        if not netloc:
            return False
        if netloc in self._allowed_hosts():
            return True
        if str(self.host).strip("[]") not in ("0.0.0.0", "::", ""):
            return False
        # 解析 host[:port]（兼容 [IPv6]:port 写法）
        if netloc.startswith("["):
            inner = netloc[1:].split("]", 1)
            host_part = inner[0]
            rest = inner[1] if len(inner) > 1 else ""
            port_part = rest[1:] if rest.startswith(":") else ""
        elif ":" in netloc:
            host_part, _, port_part = netloc.rpartition(":")
        else:
            host_part, port_part = netloc, ""
        if not port_part.isdigit() or int(port_part) != self.port:
            return False
        hp = host_part.strip("[]").lower()
        if hp == "localhost":
            return True
        try:
            ip = ipaddress.ip_address(hp)
        except ValueError:
            return False  # 域名 Host：拒绝
        return ip.is_loopback or ip.is_private

    def _host_ok(self, request) -> bool:
        """防 DNS rebinding：Host 必须是配置的地址、回环名，或（仅绑定
        全部网卡时）回环/内网 IP 字面量且端口匹配。"""
        host = (request.headers.get("Host") or "").strip()
        if not host:
            return False
        return self._netloc_ok(host)

    def _origin_ok(self, request) -> bool:
        """跨站写保护：带 Origin 时必须同源。

        aiohttp 的 request.json() 不校验 Content-Type，
        没有这道检查时任意网页都能用表单/fetch 打到管理接口。
        """
        origin = request.headers.get("Origin") or ""
        if not origin:
            return True  # 同源的简单请求通常不带 Origin
        try:
            netloc = origin.split("://", 1)[1]
        except IndexError:
            return False
        return self._netloc_ok(netloc)

    def _rate_limited(self, ip: str) -> bool:
        now = time.time()
        rec = self._fails.get(ip)
        if not rec:
            return False
        if rec[2] > now:
            return True
        if now - rec[1] > LOGIN_WINDOW:
            self._fails.pop(ip, None)
        return False

    def _note_fail(self, ip: str):
        now = time.time()
        if len(self._fails) > LOGIN_TRACK_MAX:
            # 先清已过期条目；仍超上限时再按「最近失败时间」最久优先淘汰，
            # 避免分布式爆破在窗口期内把字典撑得无限大。被临时封禁的条目跳过，
            # 保留其封禁效果。
            for k, v in list(self._fails.items()):
                if v[2] < now and now - v[1] > LOGIN_WINDOW:
                    self._fails.pop(k, None)
            if len(self._fails) > LOGIN_TRACK_MAX:
                for k in sorted(self._fails.keys(), key=lambda k: self._fails[k][1]):
                    if len(self._fails) <= LOGIN_TRACK_MAX:
                        break
                    if self._fails[k][2] < now:
                        self._fails.pop(k, None)
            # 硬上限：分布式爆破时（大量不同 IP 各打满 5 次）所有条目都处于封禁态，
            # 上面两轮淘汰会全部跳过，而末尾的 setdefault 是无条件插入 —— 字典会
            # 无界增长。限流本身是尽力而为的防护，不该拿内存换保真，超限就淘汰最旧。
            while len(self._fails) > LOGIN_TRACK_MAX:
                oldest = min(self._fails, key=lambda k: self._fails[k][1])
                self._fails.pop(oldest, None)
        rec = self._fails.setdefault(ip, [0, now, 0.0])
        if now - rec[1] > LOGIN_WINDOW:
            rec[0], rec[1] = 0, now
        rec[0] += 1
        if rec[0] >= LOGIN_MAX_FAILS:
            rec[2] = now + LOGIN_BLOCK

    def _global_rate_limited(self) -> bool:
        """全站登录失败闸门：与来源 IP 无关。

        为什么 per-IP 限流不够：对端是回环（默认部署）时 _trusted_proxy 把它
        当作同机反代，_client_ip 就采信 X-Forwarded-For 的最后一跳 —— 攻击者
        每个请求换一个 XFF 值就换一个限流桶，5 次上限永远攒不满，Argon2 于是
        变成一台可以无限次尝试的密码预言机。这里换一个维度：窗口内全站失败
        总数超过 LOGIN_GLOBAL_MAX 就一律 429，与来源、与 XFF 都无关。

        代价是「一个人爆破会短期挡住所有人登录」，所以阈值取 60：正常运维在
        一个 5 分钟窗口里失败 0~1 次，只有自动化爆破才够得到。窗口滑过自动
        清零，数据结构是定长列表，不留任何无界字典。
        """
        now = time.time()
        if now - self._global_fails[1] > LOGIN_WINDOW:
            self._global_fails[0], self._global_fails[1] = 0, now
            return False
        return self._global_fails[0] >= LOGIN_GLOBAL_MAX

    def _note_global_fail(self):
        now = time.time()
        if now - self._global_fails[1] > LOGIN_WINDOW:
            self._global_fails[0], self._global_fails[1] = 0, now
        self._global_fails[0] += 1
        # 只在【刚好触顶】那一次出声：触顶后所有登录请求都会被上面的闸门挡在
        # 计数之前，计数停在阈值上直到窗口滑动，因此每个窗口最多一条 warning。
        if self._global_fails[0] == LOGIN_GLOBAL_MAX:
            self.log.warning(
                f"[上班族物语] 疑似爆破：{LOGIN_WINDOW} 秒内全站登录失败已达 "
                f"{LOGIN_GLOBAL_MAX} 次，已全站临时拒绝登录（先查来源，勿盲目放宽阈值）"
            )

    def _unauth(self):
        return _json({"error": "未登录"}, 401)

    def _verify_pwd(self, plain: str) -> bool:
        return verify_password(plain, self.password_stored)

    async def _averify_pwd(self, plain: str) -> bool:
        """Argon2id 校验：入线程（CPU 密集）+ 限并发（每次 64MiB 内存）。

        登录是未鉴权路径，不限并发时攻击者可用并行错误密码把内存放大到
        64MiB × 线程池上限，直接压垮 AstrBot 宿主进程。
        """
        async with self._pwd_sem:
            return await asyncio.to_thread(self._verify_pwd, plain)

    # 允许在 _pwd_sem 上排队的登录请求上限。限流只在【入口】查一次失败计数，
    # 而「首次并发突发」还没有失败计数：一次打几千个并发登录会全部通过限流检查，
    # 然后无界排队等信号量（2 并发 × 单次校验 ~70ms 实测，弱 CPU / 与截图抢核时
    # 会上到 150~300ms），队列要几分钟才排空，期间连接、Task、请求体全部驻留内存，
    # 正常管理员登录被彻底阻塞（aiohttp 默认无并发上限）。
    PWD_QUEUE_MAX = 8

    async def _averify_pwd_or_busy(self, plain: str) -> bool | None:
        """校验密码；在飞+排队已达上限时返回 None，让调用方直接回 429 不进队列。"""
        if self._pwd_inflight >= self.PWD_QUEUE_MAX:
            return None
        self._pwd_inflight += 1
        try:
            return await self._averify_pwd(plain)
        finally:
            self._pwd_inflight -= 1

    async def _ahash_pwd(self, plain: str) -> str:
        """与 _averify_pwd 共用同一把并发闸（哈希与校验成本相同）。"""
        async with self._pwd_sem:
            return await asyncio.to_thread(hash_password, plain)

    def _trusted_proxy(self, request) -> bool:
        """对端是否可信反代——决定是否采信 X-Forwarded-* 头。

        默认只信回环：直连场景下这些头完全由客户端控制，采信它们等于
        让攻击者自选限流桶（绕过 per-IP 限流）与 cookie secure 标记。
        运维在反代后部署时用 webui_trusted_proxies 显式列出反代地址。
        """
        peer = str(request.remote or "")
        if not peer:
            return False
        allow = [str(x).strip() for x in (self._c("webui_trusted_proxies") or []) if str(x).strip()]
        if peer in allow:
            return True
        try:
            return ipaddress.ip_address(peer).is_loopback
        except ValueError:
            return False

    def _client_ip(self, request) -> str:
        """限流用的客户端标识：可信反代下取 X-Forwarded-For 最右侧的非反代跳。

        直接用 request.remote 时，反代后全体用户共用一个桶——攻击者 5 次
        错密码就能把所有管理员一起锁 300 秒。
        """
        peer = str(request.remote or "?")
        if not self._trusted_proxy(request):
            return peer
        xff = request.headers.get("X-Forwarded-For", "")
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        return hops[-1][:64] if hops else peer

    def _is_https(self, request) -> bool:
        if request.scheme == "https":
            return True
        if not self._trusted_proxy(request):
            return False
        return request.headers.get("X-Forwarded-Proto", "").lower() == "https"

    async def _authed(self, request):
        """JWT + 服务端会话表双校验：

        1. JWT 签名/issuer/exp/iat/jti 校验（PyJWT 抛 PyJWTError 即拒绝）
        2. 会话表里 jti 必须存在且未过期
        3. last_seen_at 每 60s 续期一次（避免每请求写库）

        会话表在主库上，同步 sqlite3 一旦撞上 busy_timeout（最长 15s）会把
        整条事件循环卡死、全部群消息停摆，因此所有库操作必须走 to_thread。
        """
        if not self.auth_on:
            return True
        raw = request.cookies.get(COOKIE, "")
        if not raw:
            return False
        try:
            claims = jwt.decode(
                raw,
                self._jwt_secret,
                algorithms=[JWT_ALG],
                issuer=JWT_ISSUER,
                options={"require": ["exp", "iat", "jti"]},
            )
        except jwt.PyJWTError:
            return False
        jti = str(claims.get("jti", ""))
        if not jti:
            return False
        # get_webui_session 默认已过滤过期行（expires_at > now），过期行的物理删除
        # 由 DB.cleanup_old_data 一处负责（每天在 push_hour 之后跑一次）。
        sess = await asyncio.to_thread(self.db.get_webui_session, jti)
        if not sess:
            return False
        now = int(time.time())
        if now - sess["last_seen_at"] > 60:
            try:
                await asyncio.to_thread(self.db.touch_webui_session, jti)
            except Exception:  # noqa: BLE001, S110 - 续期失败不阻断当前请求
                pass
        return True

    async def _guard(self, request, handler):
        if not self._host_ok(request):
            return _json({"error": "Host 不被允许"}, 400)
        if request.method not in ("GET", "HEAD") and not self._origin_ok(request):
            return _json({"error": "跨站请求已被拒绝"}, 403)
        # 白名单之外的一切路径默认需要登录：以后新增路由不会忘记加鉴权
        if request.path not in PUBLIC_PATHS and not await self._authed(request):
            return self._unauth()
        return await handler(request)

    def _load_schema(self) -> dict:
        try:
            return json.loads(SCHEMA_PATH.read_text("utf-8"))
        except (OSError, ValueError) as e:
            self.log.warning(f"[上班族物语] 配置 schema 读取失败（{SCHEMA_PATH}）：{e}")
            return {}

    def _texts_root(self) -> Path:
        return TEXTS_DIR
