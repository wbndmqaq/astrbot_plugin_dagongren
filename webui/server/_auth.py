"""WebUIServer 的 _AuthMixin：登录/登出/改密/会话管理（拆分自原 webui/server.py，现为 webui/server/ 包成员）。"""

import asyncio
import time
import uuid

import jwt

from ._const import (
    COOKIE,
    JWT_ALG,
    JWT_ISSUER,
    TTL,
)
from ._deps import gd
from ._util import _json, _jti_from_request, _verified_jti, texts_catalog


class _AuthMixin:
    async def _login(self, request):
        if not self.auth_on:
            return _json({"ok": True, "msg": "未启用密码"})
        ip = self._client_ip(request)
        # 全站闸门先判：它不看来源 IP，堵的正是「每个请求换一个 X-Forwarded-For
        # 就换一个限流桶」（回环对端默认被视为同机反代，见 _global_rate_limited）。
        if self._global_rate_limited():
            return _json({"error": "全站登录失败过多，已临时停止登录，请稍后再试"}, 429)
        if self._rate_limited(ip):
            return _json({"error": "失败次数过多，请稍后再试"}, 429)
        body = await self._body(request)
        pwd = str(body.get("password", ""))
        # Argon2id 校验是 CPU + 内存密集（m=64MiB, t=3, p=4；实测单次 ~70ms，
        # 弱 CPU 或与截图渲染抢核时 150~300ms）：
        # _averify_pwd_or_busy 负责入线程、限并发，并在排队已满时直接回 429
        # ——不进队列才能挡住「首次并发突发」（那批请求还没有失败计数，
        # 入口限流放行后会在信号量上无界堆积）。
        verified = await self._averify_pwd_or_busy(pwd)
        if verified is None:
            return _json({"error": "服务器繁忙，请稍后再试"}, 429)
        if not verified:
            self._note_fail(ip)
            self._note_global_fail()
            await asyncio.sleep(0.5)
            return _json({"error": "密码错误"}, 401)
        self._fails.pop(ip, None)
        # 启动期临时密码标记：登录后强制改密
        must_change = bool(self._live_config.get("_webui_must_change_password"))
        now = int(time.time())
        jti = uuid.uuid4().hex
        ua = request.headers.get("User-Agent", "")[:256]
        try:
            await asyncio.to_thread(self.db.create_webui_session, jti, ip, ua, TTL)
        except Exception as e:  # noqa: BLE001 - DB 失败不能让密码错误遮盖真实原因
            self.log.error(f"[上班族物语] 创建会话失败：{e}")
            return _json({"error": "服务器内部错误"}, 500)
        token = jwt.encode(
            {
                "iss": JWT_ISSUER,
                "sub": "admin",
                "jti": jti,
                "iat": now,
                "exp": now + TTL,
            },
            self._jwt_secret,
            algorithm=JWT_ALG,
        )
        resp = _json({"ok": True, "must_change_password": must_change, "jti": jti})
        # 仅在 HTTPS 下设置 secure，否则 LAN HTTP 访问时浏览器不回传 cookie。
        # X-Forwarded-Proto 只在可信反代（_trusted_proxy）后采信——直连时
        # 该头由客户端任意伪造。
        resp.set_cookie(
            COOKIE,
            token,
            max_age=TTL,
            httponly=True,
            samesite="Lax",
            path="/",
            secure=self._is_https(request),
        )
        return resp

    async def _logout(self, request):
        # 登出是公开路径（未登录也能清 cookie），因此不能信未经校验的 cookie。
        # 必须先验签名再撤销会话，否则任何人伪造一个带 jti 的 cookie 就能
        # 在知道对方 jti 的情况下强行注销其会话。
        jti = _verified_jti(request, self._jwt_secret)
        if jti:
            try:
                await asyncio.to_thread(self.db.revoke_webui_session, jti)
            except Exception:  # noqa: BLE001, S110 - 服务端清理失败不影响客户端登出
                pass
        resp = _json({"ok": True})
        resp.del_cookie(COOKIE, path="/")
        return resp

    async def _change_password(self, request):
        """改密：需要已登录 + 提供旧密码 + 新密码。

        成功后：
        - cfg 里 webui_password 改成新密码的 Argon2id 哈希
        - 清掉 _webui_must_change_password 标记
        - 撤销全部活跃会话（含当前会话），强制用新密码重新登录
        - 持久化到 AstrBot 配置（save_config 落盘）
        """
        if not await self._authed(request):
            return self._unauth()
        body = await self._body(request)
        old = str(body.get("old_password", ""))
        new = str(body.get("new_password", ""))
        if not await self._averify_pwd(old):
            return _json({"error": "旧密码错误"}, 400)
        if len(new) < 8 or new != new.strip():
            return _json({"error": "新密码需 8 位以上且无首尾空格"}, 400)
        if new == old:
            return _json({"error": "新密码不能与旧密码相同"}, 400)
        self.password_stored = await self._ahash_pwd(new)
        self._live_config["webui_password"] = self.password_stored
        self._live_config.pop("_webui_must_change_password", None)
        try:
            await asyncio.to_thread(self.db.revoke_all_webui_sessions)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[上班族物语] 撤销会话失败：{e}")
        save = getattr(self._live_config, "save_config", None)
        if callable(save):
            try:
                await asyncio.to_thread(save)
            except Exception as e:  # noqa: BLE001
                self.log.warning(f"[上班族物语] 持久化新密码失败：{e}")
        resp = _json({"ok": True, "message": "密码已更新，请用新密码重新登录"})
        resp.del_cookie(COOKIE, path="/")
        return resp

    async def _list_sessions(self, request):
        """返回当前所有活跃会话（含当前设备标记）。"""
        if not await self._authed(request):
            return self._unauth()
        current_jti = _jti_from_request(request)
        try:
            rows = await asyncio.to_thread(self.db.list_webui_sessions)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[上班族物语] 列出会话失败：{e}")
            return _json({"sessions": []})
        out = [
            {
                "jti": str(r["jti"]),
                "ip": str(r.get("ip") or ""),
                "user_agent": str(r.get("user_agent") or ""),
                "created_at": int(r["created_at"]),
                "last_seen_at": int(r["last_seen_at"]),
                "expires_at": int(r["expires_at"]),
                "current": str(r["jti"]) == current_jti,
            }
            for r in rows
        ]
        return _json({"sessions": out})

    async def _revoke_session(self, request):
        """撤销单个会话：当前会话则同时清 cookie。"""
        if not await self._authed(request):
            return self._unauth()
        body = await self._body(request)
        target = str(body.get("jti", ""))
        if not target:
            return _json({"error": "缺 jti"}, 400)
        current_jti = _jti_from_request(request)
        try:
            n = await asyncio.to_thread(self.db.revoke_webui_session, target)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[上班族物语] 撤销会话失败：{e}")
            return _json({"error": "服务器内部错误"}, 500)
        resp = _json({"ok": True, "revoked": n, "current_revoked": target == current_jti})
        if target == current_jti:
            resp.del_cookie(COOKIE, path="/")
        return resp

    async def _revoke_other_sessions(self, request):
        """一次撤销除当前会话外的全部会话（单条 DELETE，非 N 次往返）。"""
        if not await self._authed(request):
            return self._unauth()
        current_jti = _jti_from_request(request)
        if not current_jti:
            return _json({"error": "无法识别当前会话"}, 400)
        try:
            n = await asyncio.to_thread(self.db.revoke_other_webui_sessions, current_jti)
        except Exception as e:  # noqa: BLE001
            self.log.warning(f"[上班族物语] 批量撤销会话失败：{e}")
            return _json({"error": "服务器内部错误"}, 500)
        return _json({"ok": True, "revoked": n})

    async def _check(self, request):
        ok = (not self.auth_on) or await self._authed(request)
        return _json({"required": self.auth_on, "ok": ok})

    # 面板自己的过期会话清理周期（秒）。取 1 小时：
    # - 库层唯一的物理删除点原来只有 cleanup_old_data，而它由游戏推送循环
    #   「每天 push_hour 之后一次」触发 —— 过期会话行最长滞留约一天，推送循环
    #   异常退出时更是完全没人清、会话表无界增长；
    # - 会话 TTL 是 12 小时（_const.TTL），每小时扫一次已经是它的 12 倍频度，
    #   再密只是白白去抢那把全局写锁。
    SESSION_GC_INTERVAL = 3600

    async def _session_gc_loop(self):
        """低频清理过期 WebUI 会话；由 _ServeMixin.start() 起、stop() 取消。"""
        while True:
            # 先睡再干：启动期本来就不缺这一次清理（session 只增不减的前提是
            # 长期运行），错开启动高峰也少一次数据库竞争
            await asyncio.sleep(self.SESSION_GC_INTERVAL)
            try:
                n = await asyncio.to_thread(self.db.purge_expired_webui_sessions)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 清理失败不能拖垮面板
                self.log.warning(f"[上班族物语] WebUI 过期会话清理失败：{e}")
                continue
            if n:
                self.log.info(f"[上班族物语] WebUI 已清理 {n} 条过期会话")

    async def _stop_session_gc(self):
        """取消并 await 清理任务：不 await 会在事件循环关闭时留下 pending Task。"""
        task, self._session_gc_task = self._session_gc_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110 - 兜底，不阻塞关闭
            pass

    async def _meta(self, request):
        """面板元数据。未登录时只回最低限度。

        /api/meta 在 PUBLIC_PATHS 里（登录页要用 auth_required），而 version /
        port / 榜单定义都是给别人做指纹的低价值信息：能不给就不给。登录成功后
        前端 loadAll() 会再拉一次，这次带上完整负载。
        """
        out = {
            "name": "astrbot_plugin_dagongren",
            "display": "打工人·上班族物语",
            "auth_required": self.auth_on,
        }
        if not await self._authed(request):
            return _json(out)
        # rankings 一并下发：榜单页的分类标签由前端按它渲染，
        # 不再在 index.html 里写死第五份「富豪榜/卷王榜/身价榜/职级榜」
        # texts 一并下发：文案库分类清单（库名 + 中文标签），前端不再写死
        # 8 个库名——目录里其实有 33 个 json。
        out.update(
            {
                "version": self.version,
                "port": self.port,
                "rankings": [
                    {
                        "key": str(r.get("key") or ""),
                        "name": str(r.get("name") or ""),
                        "unit": str(r.get("unit") or ""),
                    }
                    for r in gd.rankings()
                    if r.get("key")
                ],
                # 目录扫描是同步 IO，别在事件循环上做（而且这是未鉴权可达路径）
                "texts": await asyncio.to_thread(texts_catalog),
                "now": int(time.time()),
            }
        )
        return _json(out)
