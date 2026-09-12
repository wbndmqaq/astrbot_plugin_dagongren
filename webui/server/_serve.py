"""WebUIServer 的 _ServeMixin：aiohttp 服务的启停、路由注册与静态资源分发（拆分自原 webui/server.py，现为 webui/server/ 包成员）。"""

import asyncio
import traceback

from aiohttp import ContentTypeError, web

from ._deps import MoneyIntegrityError
from ._util import _json

# runner 的优雅关闭上限：等 in-flight 请求收尾的时间，超过就取消它们。
# 单独提成常量是因为 stop() 的超时兜底必须按它算（见 _stop_runner）。
SHUTDOWN_TIMEOUT = 2


class _ServeMixin:
    async def start(self):
        # 重入保护：第二次 start() 会新建 runner/site 覆盖 self._runner，而
        # stop() 只能关掉最后一个 —— 第一个 socket 会一直监听着、GC 任务也没人
        # 取消（表现为「重启插件后旧端口仍被占用，且越占越多」）。已经起来就
        # 直接返回，让 start 成为幂等操作（main.py 的失败重试路径也依赖这点）。
        if self._runner is not None:
            return
        # aiohttp 用 __middleware_version__ 区分新旧式中间件，绑定方法上无法直接
        # 打 @web.middleware（属性不可写），必须在这里包一层模块级函数。
        # 少了这层，aiohttp 会按旧式工厂用 (app, handler) 调用 _guard，
        # 于是 _host_ok 收到 Application 而非 Request，全部请求 500。
        @web.middleware
        async def guard(request, handler):
            return await self._guard(request, handler)

        @web.middleware
        async def handle_errors(request, handler):
            # 兜底异常中间件（在 guard 外层）：任何 handler 抛出的未捕获异常
            # 统一转成干净 JSON，只把堆栈写日志——绝不把含文件路径/内部细节的
            # 异常消息串回给客户端（aiohttp 默认 500 会把异常 str 明文回传）。
            try:
                return await handler(request)
            except web.HTTPRequestEntityTooLarge:
                # 必须在 HTTPException 之前：它是 HTTPException 的子类。
                # aiohttp 默认把这个 413 渲染成 text/plain
                # "Maximum request body size 1048576 exceeded."：前端 api()
                # 解 JSON 失败只会弹一个「413」，同时把内部阈值明文回给已鉴权客户端。
                return _json({"error": "请求体过大，请减少提交的数据量后重试"}, 413)
            except web.HTTPException:
                raise  # 正常 HTTP 状态（如 404）继续按原样走
            except asyncio.CancelledError:
                raise
            except MoneyIntegrityError as e:
                # 资金完整性冲突（管理端写入与玩家在飞指令撞车）：这是可重试的
                # 409，不是 500。消息本身只含 gid/uid 与列名，可以安全回给运维，
                # 否则他只会看到「服务器内部错误」而不知道改动为什么没生效。
                self.log.warning(f"[上班族物语] WebUI 写入被资金完整性检查拒绝：{e}")
                return _json({"error": f"写入冲突，请重试：{e}"}, 409)
            except Exception:  # noqa: BLE001 - 统一兜底，避免泄漏内部信息
                tb = traceback.format_exc()
                self.log.error(f"[上班族物语] WebUI 请求处理异常：\n{tb}")
                return _json({"error": "服务器内部错误"}, 500)

        app = web.Application(middlewares=[handle_errors, guard])
        r = app.router
        r.add_get("/", self._index)
        r.add_get("/webui/style.css", self._style_css)
        r.add_get("/webui/app.js", self._app_js)
        r.add_get("/api/meta", self._meta)
        r.add_post("/api/auth/login", self._login)
        r.add_post("/api/auth/logout", self._logout)
        r.add_get("/api/auth/check", self._check)
        r.add_post("/api/auth/change-password", self._change_password)
        r.add_get("/api/auth/sessions", self._list_sessions)
        r.add_post("/api/auth/sessions/revoke", self._revoke_session)
        r.add_post("/api/auth/sessions/revoke-others", self._revoke_other_sessions)
        r.add_get("/api/overview", self._overview)
        r.add_get("/api/groups", self._groups)
        r.add_get("/api/ranking", self._ranking)
        r.add_get("/api/search", self._search)
        r.add_get("/api/stocks", self._stock_list)
        r.add_post("/api/stocks/edit-batch", self._stock_edit_batch)
        r.add_post("/api/stocks/fluctuate", self._stock_fluctuate)
        r.add_post("/api/stocks/randomize", self._stock_randomize)
        r.add_get("/api/backups", self._backup_list)
        r.add_post("/api/backups/create", self._backup_create)
        r.add_post("/api/backups/restore", self._backup_restore)
        r.add_post("/api/backups/delete", self._backup_delete)
        r.add_get("/api/admin/player", self._admin_get)
        r.add_get("/api/admin/config", self._admin_config)
        r.add_post("/api/admin/config/save", self._admin_config_save)
        r.add_get("/api/admin/companies", self._admin_companies)
        r.add_post("/api/admin/companies/save", self._admin_companies_save)
        r.add_get("/api/admin/json/get", self._json_get)
        r.add_post("/api/admin/json/save", self._json_save)
        r.add_get("/api/admin/players", self._admin_players)
        r.add_post("/api/admin/events/clear", self._admin_events_clear)
        r.add_post("/api/admin/player/save", self._admin_save)
        r.add_post("/api/admin/player/delete", self._admin_delete)
        self._runner = web.AppRunner(
            app,
            access_log=None,
            shutdown_timeout=SHUTDOWN_TIMEOUT,  # 不等 keep-alive 长连接，快速释放端口
            # 请求头读取超时。aiohttp 默认 keepalive_timeout=75s：一个空闲的
            # keep-alive 连接（不必发完整请求、不必通过鉴权）会在服务端占满
            # 75s 的连接槽与内存，而 aiohttp 没有并发连接上限——绑定 0.0.0.0
            # 时局域网里一台机器开几百个慢速连接就能把面板挤到没法用。
            # 取 15s 的理由：面板自己的轮询间隔是 10s，正常会话的连接在
            # 超时前就已被复用（不会因为变短而反复重连）；反向看，任何闲置
            # 连接最多占 15s。同时它远大于 shutdown_timeout=2，关闭时
            # 先关的就是这些空闲连接，不影响优雅退出。
            keepalive_timeout=15,
        )
        await self._runner.setup()
        runner = self._runner  # 本地的这一份才是「本次真的接管了的 runner」
        # 跨平台自愈：仅对「端口占用(EADDRINUSE/10048)」做短重试——旧实例
        # 完全释放前有一小段失败窗口。注意：
        # - PermissionError(10013)=端口被系统保留(Hyper-V/WSL)或防火墙拦截，
        #   重试无意义，立即失败并在上层给出可操作提示；
        # - 每轮必须新建 TCPSite——失败的 site 已注册进 runner，复用会报重复注册。
        last_exc: Exception | None = None
        for _attempt in range(3):
            # reuse_address 保持 aiohttp 默认（None → Windows 上 False、
            # POSIX 上 True）。不要显式传 True：Windows 的 SO_REUSEADDR 语义与
            # POSIX 不同，它允许同机另一个进程绑定【完全相同的】端口并接管新
            # 连接——本服务不带 TLS，抢占者能拿到管理员明文密码。已实测：
            # 显式 True 时第二个进程 bind 成功；默认值下 WinError 10048。
            # TIME_WAIT 由上面的 EADDRINUSE 短重试覆盖，不需要靠复用地址兜底。
            site = web.TCPSite(runner, self.host, self.port)
            try:
                await site.start()
                # 站点起来了才有必要起后台清理；失败路径（下面的 cleanup +
                # raise）不会留下悬空任务。已有一个（不该发生）就不重复建，
                # 否则旧任务再没人持有引用、停不掉。
                if self._session_gc_task is None:
                    self._session_gc_task = asyncio.create_task(self._session_gc_loop())
                return
            except PermissionError as e:
                last_exc = e
                break
            except OSError as e:
                last_exc = e
                await asyncio.sleep(0.6)
        await runner.cleanup()
        # 只在「self._runner 仍是本地的这一份」时清空：万一期间有人接管了
        # self._runner，清空它等于让那个在监听的 runner 再也关不掉。
        if self._runner is runner:
            self._runner = None
        raise last_exc  # type: ignore[misc]

    async def stop(self):
        # 先停后台清理任务：它自己会 sleep，取消后立刻退出，不会在事件循环
        # 关闭时留下 "Task was destroyed but it is pending"
        await self._stop_session_gc()
        if self._runner:
            runner, self._runner = self._runner, None
            # 超时必须【比 cleanup 自己的预期耗时更宽】才有意义：cleanup()
            # 内部先 shutdown()（最多等 SHUTDOWN_TIMEOUT 让 in-flight 请求收尾，
            # 超时就取消），再关站点与连接，正常总是 ~SHUTDOWN_TIMEOUT 内返回。
            # 之前写死的 10s 比它宽太多，那条例外的 warning 实际不可达，
            # 「卡住时会出声」的承诺等于没有。
            budget = max(5.0, SHUTDOWN_TIMEOUT * 2)
            try:
                await asyncio.wait_for(runner.cleanup(), timeout=budget)
            except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
                # 超时不能静默 pass：cleanup 卡住通常意味着有请求没结束，这是
                # 需要运维知道的事情（否则表现为「端口好像还占着」）。日志之后
                # 再手动兜一层：停掉站点（关监听 socket，不再接新连接）并清理
                # 已建立的连接，避免这里的 Task 被丢弃后连接一直挂着。
                self.log.warning(
                    f"[上班族物语] WebUI 关闭超时（{budget:g}s，"
                    f"shutdown_timeout={SHUTDOWN_TIMEOUT}s），已强制释放监听端口；"
                    "可能有请求仍在处理中"
                )
                # 必须先逐个停掉站点再 shutdown()：aiohttp 的 cleanup() 是
                # 「先 site.stop()（关监听 socket）再 shutdown()」，而 shutdown()
                # 本身只跑关闭回调与连接清理，根本不碰监听 socket —— 只调它的话
                # 端口依然被占着，上面那句「已强制释放监听端口」就是空话
                # （新加的 test_stop_timeout_budget_is_reachable 就是这么发现的）。
                for site in list(getattr(runner, "sites", ())):
                    try:
                        await site.stop()
                    except Exception as e:  # noqa: BLE001 - 兜底清理不再抛
                        self.log.warning(f"[上班族物语] WebUI 强制关闭站点失败：{e}")
                try:
                    await runner.shutdown()
                except Exception as e:  # noqa: BLE001 - 兜底清理不再抛
                    self.log.warning(f"[上班族物语] WebUI 强制关闭清理失败：{e}")

    async def _index(self, request):
        return await self._file("index.html", "text/html")

    async def _style_css(self, request):
        return await self._file("style.css", "text/css")

    async def _app_js(self, request):
        return await self._file("app.js", "application/javascript")

    async def _file(self, fname, ctype):
        try:
            body = await asyncio.to_thread((self.dir / fname).read_bytes)
            return web.Response(
                body=body,
                content_type=ctype,
                charset="utf-8",
                headers={
                    "Cache-Control": "no-store",
                    "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    # 页面里没有任何内联脚本/内联 handler（交互全走 data-act + 事件
                    # 委托），脚本只从本站加载，所以 script-src 收紧为 'self'；
                    # style-src 仍保留 'unsafe-inline'，因为页面大量使用 style 属性
                    # （元素级样式不受 'self' 约束，去掉会让整个面板排版崩掉）。
                    # 外部脚本、内嵌框架、表单外发一律禁掉，
                    # 头像只允许 https/data，杜绝把面板数据带去第三方。
                    "Content-Security-Policy": (
                        "default-src 'self'; "
                        "img-src 'self' https: data:; "
                        "style-src 'self' 'unsafe-inline'; "
                        "script-src 'self'; "
                        "connect-src 'self'; "
                        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    ),
                },
            )
        except OSError as e:
            # 静态资源在 PUBLIC_PATHS 里（未登录可达），str(e) 会带绝对安装路径，
            # 只能落日志不能回客户端
            self.log.warning(f"[上班族物语] WebUI 静态资源读取失败（{fname}）：{e}")
            return _json({"error": "面板资源缺失，请检查插件安装完整性"}, 500)

    async def _body(self, request) -> dict:
        """统一读 JSON 体：畸形请求返回空 dict，而不是抛 500 带堆栈。"""
        if not request.can_read_body:
            return {}
        try:
            data = await request.json()
        # ContentTypeError 在 aiohttp 顶层而非 aiohttp.web；写成 web.ContentTypeError
        # 会在异常发生时求值 except 元组失败，反而把畸形请求变成 500。
        # 它是 ClientResponseError 的子类，和 ValueError 无继承关系，必须显式列出。
        except (ValueError, TypeError, ContentTypeError):
            return {}
        return data if isinstance(data, dict) else {}
