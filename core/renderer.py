"""独立 Playwright 渲染器：Jinja2 模板 → HTML → Chromium 截图。

- 浏览器懒启动、全局复用（启动阶段加锁，截图阶段用信号量限并发）
- 每次截图使用独立 BrowserContext，互不串扰，异常也保证关闭
- 截图保存到 plugin_data/screenshots/，自动清理旧图
- 任何失败返回 None，由调用方回退纯文本，绝不中断指令
"""

import asyncio
import threading
import time
import weakref
from collections import OrderedDict
from pathlib import Path

DEFAULT_MAX_KEEP = 60
# 同时进行的截图数量。Chromium 每个上下文都吃内存，放开太多会把小机器打爆，
# 所以按机器规格由运维配置（render_max_concurrency）。
DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_VIEWPORT_WIDTH = 780
DEFAULT_TIMEOUT_MS = 15000
# Jinja2 模板缓存容量：模板文件本身只有 8 套，但 WebUI 文案编辑会
# 强制 load_all(force=True)，每次渲染都要 from_string 重编一遍很浪费。
# 给一个 LRU 上限防止模板被外部修改后旧缓存长期驻留。
TMPL_CACHE_MAX = 32
# 刚产出的截图在这么久之内一律不删。适配器是【异步】读文件发送的，渲染器
# 拿不到「已发送完成」的回调；而 screenshot_max_keep 允许配到 1，只排除
# 「自己这一张」会让群 B 的清理删掉群 A 正在上传的图（注释承诺过它绝不能删）。
#
# 这个窗口是【稳态目录大小的上界】的一半：清理只删超过窗口的旧图，于是
# 稳态张数 ≈ 截图吞吐 × KEEP_GRACE_SECONDS + 保留下限。曾经是 120s，实测
# rate=2/s 时 max_keep=1 与 =60 都停在 240 张（screenshot_max_keep 完全失效，
# 而它每张截图都全目录扫描一次，目录越大越慢 → 正反馈）。适配器读走图片是在
# handler 返回后几秒内，30s 已经非常宽松，同时把上界压到原来的 1/4。
KEEP_GRACE_SECONDS = 30.0
# 清理节流：不再每张截图都扫一遍目录。两个闸门取「或」——
# - 距上次清理 ≥ CLEANUP_MIN_INTERVAL 秒：低频出图时按时间收口；
# - 上次清理后新产出 ≥ CLEANUP_EVERY_N_SHOTS 张：高频出图时按张数收口，
#   避免两次清扫之间堆积过多（10 秒闸门在 rate=2/s 时允许积 20 张，而 12 张
#   的闸门更早触发，稳态更贴住 rate×KEEP_GRACE_SECONDS 这个上界）。
# N 取 12：它只在「每 10 秒新增超过 12 张」（rate > 1.2/s）时才比时间闸门更早
# 触发，而单次清理的代价是 O(目录大小)，被摊到「每 10 秒一次」这个量级；
# 再大（16）对收敛没帮助，再小（8）会让高频场景白扫更多次。
CLEANUP_MIN_INTERVAL = 10.0
CLEANUP_EVERY_N_SHOTS = 12
# 整段截图（含 new_context / new_page / body.screenshot）的总超时下限。
# 这三处 await 此前都没有超时：浏览器进程还活着但协议管道被冻住时
# （进程被 SIGSTOP、容器被冻结、显卡/磁盘卡死）它们永不返回，timeout_ms
# 只作用于 set_content。总预算 = max(此下限, timeout_ms × 2)：2 倍是因为
# set_content 失败后还有一次 domcontentloaded 重试（最坏 2/3 × timeout_ms），
# 下限则保证运维把 timeout_ms 配到 1s 时不会把一次正常的慢渲染直接判死。
SHOT_TIMEOUT_MIN_MS = 30000
# 收尾（ctx.close / browser.close）的等待上限。超时路径上被关的对象正是那个
# 「已经冻住」的浏览器，不设上限的话 wait_for 把内部协程取消后仍会卡在
# finally 的 await 上，整个超时机制形同虚设。
CLOSE_TIMEOUT_S = 5.0
# 浏览器已断连时 Playwright 抛错的典型文本（is_connected() 之外的第二道判据，
# 因为 page 级错误可能在 is_connected() 尚未翻假的瞬间抛出）。
_DEAD_HINTS = (
    "Target closed",
    "Target page, context or browser has been closed",
    "Browser closed",
    "browser has been closed",
    "Connection closed",
    "Protocol error",
)


def _browser_dead(browser, err: BaseException) -> bool:
    """判断一次渲染失败是否意味着【浏览器实例本身】不可再用。"""
    if browser is None:
        return False
    try:
        if not browser.is_connected():
            return True
    except Exception:  # noqa: BLE001 - 探测失败按已死处理，宁可重建
        return True
    text = str(err)
    return any(hint in text for hint in _DEAD_HINTS)


class PlaywrightRenderer:
    # 模块级实例注册表：诊断「重载后旧渲染器是否泄漏」用（正常应恒为 1）。
    # 必须是 WeakSet：强引用集合会把 terminate() 没跑到的旧实例永久钉住，
    # 而 Python 对象本身不值钱——被钉住的是它持有的 Chromium 与 node 子进程
    # （每个几百 MB RSS），只有 close() 能让它们退出，GC 回收对象不会。
    _live_instances: weakref.WeakSet = weakref.WeakSet()

    def __init__(
        self,
        shot_dir: Path,
        scale: float = 2.0,
        logger=None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        max_keep: int = DEFAULT_MAX_KEEP,
        viewport_width: int = DEFAULT_VIEWPORT_WIDTH,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ):
        self.shot_dir = Path(shot_dir)
        self.scale = max(1.0, min(4.0, float(scale)))
        self.log = logger
        self.max_concurrency = max(1, min(16, int(max_concurrency)))
        self.max_keep = max(1, int(max_keep))
        self.viewport_width = max(320, min(4096, int(viewport_width)))
        self.timeout_ms = max(1000, min(120000, int(timeout_ms)))
        self._pw = None
        self._browser = None
        self._env = None
        self._tmpl_cache: OrderedDict[str, object] = OrderedDict()
        # render_template 由多个 asyncio.to_thread 工作线程并发调用，而
        # _tmpl_cache / _env 的读写都不是线程安全的：无锁时两个线程同时
        # 命中缓存未命中、或一边迭代一边插入，会抛
        # "RuntimeError: dictionary changed size during iteration"。
        self._tmpl_lock = threading.Lock()
        self._launch_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(self.max_concurrency)
        self._closing = False
        self._active = 0  # 在飞截图计数（close 时据此等待收尾）
        self._seq = 0
        self._dir_ready = False  # 截图目录只 mkdir 一次，不必每次截图都 syscall
        self._hinted = False  # 渲染环境未就绪的安装指引只打一次
        # 清理节流状态。_cleanup 跑在 asyncio.to_thread 的工作线程里，多个截图
        # 的工作线程会并发调它，计数/时间戳的读改写必须持锁才不会丢（丢一次
        # 计数就少清理一轮，节流窗口被拉长）。
        self._cleanup_lock = threading.Lock()
        self._shots_since_cleanup = 0
        self._last_cleanup_at: float | None = None  # None = 还没清理过，第一次必跑
        self._live_instances.add(self)

    # ---------- 模板 ----------

    def render_template(self, template_str: str, data: dict) -> str:
        # 缓存与 Jinja2 env 的懒初始化在 to_thread 工作线程里并发执行，必须持锁
        with self._tmpl_lock:
            if self._env is None:
                from jinja2 import Environment

                # 自动转义：昵称/群名片等用户可控内容不注入 HTML
                self._env = Environment(autoescape=True)
            # LRU 缓存编译后的 Template，避免每次截图重新解析；
            # key 用原始字符串（hash），避免大字符串拷贝。
            tmpl = self._tmpl_cache.get(template_str)
            if tmpl is None:
                tmpl = self._env.from_string(template_str)
                self._tmpl_cache[template_str] = tmpl
                while len(self._tmpl_cache) > TMPL_CACHE_MAX:
                    # 真 LRU：OrderedDict.popitem(last=False) 淘汰最久未使用的，
                    # 而不是「最早插入」的（后者会把被反复命中的热模板也挤掉，
                    # 与上面注释承诺的 LRU 不符）
                    self._tmpl_cache.popitem(last=False)
            else:
                self._tmpl_cache.move_to_end(template_str)
        return tmpl.render(**data)

    def clear_template_cache(self):
        """模板文件被外部修改（WebUI 文案编辑）时调用。"""
        with self._tmpl_lock:
            self._tmpl_cache.clear()

    # ---------- 截图 ----------

    async def screenshot(self, html: str, name: str = "") -> str | None:
        if self._closing:
            return None
        async with self._sem:
            # 拿到信号量后必须复查：close() 会置 _closing 并等待在飞任务收尾，
            # 但排队中的协程是在 close() 之前越过入口检查的。不复查的话，
            # 卸载/重载后仍会重新 launch 出一个没人负责关闭的 Chromium。
            if self._closing:
                return None
            self._active += 1
            try:
                browser = await self._ensure_browser()
                if browser is None:
                    return None
                try:
                    # 整段截图必须有自己的总超时：browser.new_context / ctx.new_page /
                    # body.screenshot 这三处 await 都没有超时（timeout_ms 只作用于
                    # set_content），浏览器进程存活但协议管道被冻住时它们永不返回，
                    # 于是 _active 不归零、信号量不释放；累计 max_concurrency 个
                    # 挂起之后全插件出图永久停摆（静默回退文本）。
                    res = await asyncio.wait_for(
                        self._shot_result(browser, html, name), timeout=self._shot_timeout_s()
                    )
                except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
                    # 只有这里的 TimeoutError 是【我们的总超时】：内部异常都被
                    # _shot_result 转成了返回值，不会与本条混淆（详见其 docstring）。
                    # 超时 = 浏览器不可用（真被 kill 时错误文本会命中 _DEAD_HINTS，
                    # 但那类判据覆盖不到「进程还在、管道不通」）：丢弃重建。
                    if self.log:
                        self.log.warning(
                            f"[上班族物语][Playwright] 截图超时（>{self._shot_timeout_s():g}s），"
                            "判定浏览器不可用并重新拉起"
                        )
                    await self._drop_browser(browser)
                    return None
                if isinstance(res, BaseException):
                    if self.log:
                        self.log.warning(f"[上班族物语][Playwright] 截图失败：{res}")
                    # 只有浏览器【真的断了】才丢掉引用让下次重新拉起。头像 CDN 超时、
                    # 截图目录 mkdir 失败、长名单页面超出 Chromium 截图尺寸上限都会
                    # 落到这里，它们是单次渲染的问题；无条件 _drop_browser 会把全群
                    # 共用的 Chromium 关掉，其它群在飞的截图一起失败，之后所有群还要
                    # 串行等一次 2~15s 的重启。
                    if _browser_dead(browser, res):
                        await self._drop_browser(browser)
                    return None
                return res
            finally:
                self._active -= 1

    async def _shot_result(self, browser, html: str, name: str):
        """出图；把内部异常当作【返回值】交给调用方。

        为什么转成返回值：wait_for 超时抛的是内置 TimeoutError，而 Playwright 的
        动作超时（"Timeout 15000ms exceeded"：new_context / screenshot 都可能抛）
        在部分版本上也是同一个内置类。不区分的话，「头像 CDN 超时」这类单次渲染
        失败会被当成「浏览器卡死」而把全群共用的 Chromium 丢掉重建（round-3 的
        test_screenshot_keeps_browser_on_transient_failure 盯的就是这条）。
        异常变成返回值后，screenshot() 里 except 到的 TimeoutError 必定来自总超时。
        """
        try:
            return await self._shoot(browser, html, name)
        except Exception as e:  # noqa: BLE001 - 交回调用方按「是否浏览器已死」分类
            return e

    def _shot_timeout_s(self) -> float:
        """整段截图的总预算（秒）：timeout_ms 的 2 倍，且有下限。"""
        return max(SHOT_TIMEOUT_MIN_MS, self.timeout_ms * 2) / 1000.0

    async def _shoot(self, browser, html: str, name: str) -> str | None:
        """建上下文 → 出图 → 清理旧图。被 screenshot() 用总超时包住调用。

        上下文由本协程自己保证关闭（含被 wait_for 取消的那条路径）。关闭本身也
        要限时：取消发生在「浏览器已经冻住」的超时路径上，裸 await ctx.close()
        会让 wait_for 等它到天荒地老，超时机制等于没做。
        """
        ctx = None
        try:
            ctx = await browser.new_context(
                viewport={"width": self.viewport_width, "height": 600},
                device_scale_factor=self.scale,
            )
            page = await ctx.new_page()
            try:
                await page.set_content(html, wait_until="networkidle", timeout=self.timeout_ms)
            except Exception:  # noqa: BLE001 - 网络资源(头像)超时也照常出图
                await page.set_content(
                    html,
                    wait_until="domcontentloaded",
                    timeout=max(1000, int(self.timeout_ms * 2 / 3)),
                )
            if not self._dir_ready:
                # 同步 mkdir 是文件系统 syscall，放线程里做一次即可，
                # 不该在每次截图时都落在事件循环上
                await asyncio.to_thread(self.shot_dir.mkdir, parents=True, exist_ok=True)
                self._dir_ready = True
            self._seq += 1
            fname = f"{name or 'shot'}_{self._seq}_{int(time.time())}.png"
            out = self.shot_dir / fname
            body = await page.query_selector("body")
            if body:
                await body.screenshot(path=str(out))
            else:
                await page.screenshot(path=str(out), full_page=True)
            await asyncio.to_thread(self._cleanup, out)
            return str(out)
        finally:
            if ctx is not None:
                try:
                    await asyncio.wait_for(ctx.close(), timeout=CLOSE_TIMEOUT_S)
                except Exception:  # noqa: BLE001, S110 - 关闭失败无需上抛
                    pass

    async def _ensure_browser(self):
        """返回可用浏览器实例；启动失败返回 None（调用方回退文本）。"""
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        async with self._launch_lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            try:
                await self._launch()
            except Exception as e:  # noqa: BLE001
                if self.log:
                    self.log.warning(f"[上班族物语][Playwright] 启动失败：{e}")
                return None
            return self._browser

    async def _drop_browser(self, expected):
        """丢弃一个疑似已损坏的浏览器实例（仅当它还是当前实例时）。

        关闭要限时：调用点之一是「截图总超时」，那时浏览器大概率已经冻住，
        裸 await close() 会把超时后本该立刻返回的 screenshot() 又挂在这里
        （进程内的残留句柄由 close()/terminate() 负责收尾）。
        """
        async with self._launch_lock:
            if self._browser is not expected:
                return  # 已被别人重建，不要误关新实例
            self._browser = None
        try:
            await asyncio.wait_for(expected.close(), timeout=CLOSE_TIMEOUT_S)
        except Exception:  # noqa: BLE001, S110
            pass

    async def _launch(self):
        if self._closing:
            raise RuntimeError("渲染器正在关闭，放弃本次启动")
        if self._pw is None:
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                self._hint_once(
                    "未安装 playwright。卡片渲染不可用（所有指令自动回退纯文本）。"
                    "如需图片，请执行：pip install playwright "
                    "&& python -m playwright install chromium"
                )
                raise RuntimeError("playwright 未安装") from e
            self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True)
        except Exception as e:
            self._browser = None
            if self._pw is not None:
                try:
                    await self._pw.stop()
                except Exception:  # noqa: BLE001, S110 - 关闭失败无需上抛
                    pass
                self._pw = None
            self._hint_once(
                "Chromium 未就绪，卡片渲染不可用（所有指令自动回退纯文本）。"
                "请执行一次：python -m playwright install chromium"
            )
            raise RuntimeError(
                "Chromium 启动失败。请先执行一次：python -m playwright install chromium"
            ) from e
        # 关键：启动期间的 await 是让出点，close() 可能恰好在这中间跑完并把
        # self._browser/_pw 取走置 None。若此时仍把新实例挂在 self 上，就没人会
        # 关闭它们了 —— 每个 Chromium 几百 MB RSS，而重载正是这条路径。
        # close() 也会争用 _launch_lock，两处互斥后这个窗口才真正关死。
        if self._closing:
            browser, pw = self._browser, self._pw
            self._browser = self._pw = None
            for obj, coro_name in ((browser, "close"), (pw, "stop")):
                if obj is None:
                    continue
                try:
                    await getattr(obj, coro_name)()
                except Exception:  # noqa: BLE001, S110 - 收尾失败无需上抛
                    pass
            raise RuntimeError("渲染器已关闭，放弃本次启动")
        if self.log:
            self.log.info("[上班族物语][Playwright] 渲染器已就绪")

    def _hint_once(self, msg: str):
        """渲染环境未就绪的安装指引，整个进程只打一次。

        每次渲染失败都打一遍会把日志刷满（截图是高频路径），
        但完全不打又会让运维只看到「渲染失败回退文本」而不知道该装什么。
        """
        if self._hinted or not self.log:
            return
        self._hinted = True
        self.log.warning(f"[上班族物语][Playwright] {msg}")

    def _cleanup(self, keep_path: Path | None = None):
        """节流地清理旧截图（调用点每张截图一次）。

        节流：距上次清理 ≥ CLEANUP_MIN_INTERVAL 秒，或新产出 ≥
        CLEANUP_EVERY_N_SHOTS 张才真正扫一遍目录。清理要删的只能是【超过
        宽限期的旧图】，而旧图不会因为晚扫几秒就消失，所以没必要每张截图都付
        一次 O(目录大小) 的 is_file()+stat()（实测 2400 个文件时单次 1050ms，
        而它此前每张截图都跑 → 目录越大越慢、清理跟不上、目录更大）。

        节流状态是实例字段且在这里的 to_thread 工作线程里并发读改写，全程持
        self._cleanup_lock：丢一次计数就少清一轮，窗口被拉长。
        """
        with self._cleanup_lock:
            self._shots_since_cleanup += 1
            now = time.time()
            last = self._last_cleanup_at
            # 时间闸门用 >= 比较；last 为 None（进程内还没清过）时必然到期。
            # 时钟回拨时 now - last 变负 → 时间闸门不触发，但张数闸门仍会到，
            # 不会出现「回拨之后再不清扫」。
            due = (
                last is None
                or now - last >= CLEANUP_MIN_INTERVAL
                or self._shots_since_cleanup >= CLEANUP_EVERY_N_SHOTS
            )
            if not due:
                return
            self._shots_since_cleanup = 0
            self._last_cleanup_at = now
            try:
                self._prune(now, keep_path)
            except OSError:
                pass

    def _prune(self, now: float, keep_path: Path | None):
        """按 mtime 保留最新 max_keep 张，删掉更旧的（调用方持 _cleanup_lock）。

        keep_path 是本次刚生成、还没被平台适配器读走的图；除此之外，所有
        【刚产出】的图（KEEP_GRACE_SECONDS 内）也一律不删 —— 发送图片是异步的，
        渲染器不知道适配器何时读完，只排除「自己这一张」会在跨群并发下让后一次
        清理删掉别的群正在上传的图（screenshot_max_keep 可以配到 1）。
        保留下限同时抬到 max(max_keep, 并发数+1)，避免并发本身就把窗口压没。

        mtime 钳制：age 用 now - min(mtime, now) 算，未来 mtime（时钟回拨、
        被外部 touch）按「刚产出」处理而不是「极旧」（直接删会误伤正在上传的图）。
        但只钳制还不够：钳制后它的 age 恒为 0，会被宽限期无限次豁免（旧代码用
        now - mtime < 120 判据时，未来 mtime 的文件同样一个都删不掉 —— 等价于
        「永不删」）。所以未来时间戳的图【不享受时间宽限】，只靠保留下限与
        keep_path 这两条数量型保护：它一旦落在 items[keep:] 之外就被删，目录
        在时钟回拨后仍能收敛。（我们自己的截图 mtime 恒 ≤ now，所以「时间戳在
        未来」这件事本身就证明它不是刚刚产出的那一张。）
        """
        items: list[tuple[Path, float]] = []
        for p in self.shot_dir.glob("*.png"):
            try:
                if p.is_file():
                    items.append((p, p.stat().st_mtime))
            except OSError:
                continue
        items.sort(key=lambda it: it[1], reverse=True)
        keep = max(self.max_keep, self.max_concurrency + 1)
        for path, mtime in items[keep:]:
            if keep_path is not None and path == keep_path:
                continue
            skewed = mtime > now
            if not skewed and now - min(mtime, now) < KEEP_GRACE_SECONDS:
                continue
            path.unlink(missing_ok=True)

    # ---------- 关闭 ----------

    async def close(self):
        """幂等关闭。先挡住新任务，再等在飞截图收尾，最后关浏览器与 Playwright。

        注意：不能靠「抽干信号量」等收尾——close 若持有全部信号量且不释放，
        排队等待信号量的截图协程将永久挂起（重载时成为内存泄漏源）。改为：
        置 _closing 挡新任务 → 轮询在飞计数归零（有界超时）→ 释放浏览器。
        """
        if self._closing and self._browser is None:
            return
        self._closing = True
        # get_running_loop 而非 get_event_loop：后者在协程里已弃用（3.12+ 会告警）
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10.0
        while self._active > 0:
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.05)
        # 先等在飞截图归零、再抢 _launch_lock —— 顺序不能反：反过来（先持锁再等）
        # 会与「持 _active、正卡在 _launch_lock 上的截图」互相等成死锁。
        # 抢锁是为了与 _launch 互斥：否则在它的 await 之间取走 browser/pw，
        # 它随后写入的新实例就永远没人关（重载路径上的 Chromium 泄漏）。
        acquired = False
        try:
            await asyncio.wait_for(self._launch_lock.acquire(), timeout=5.0)
            acquired = True
        except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
            # _launch 卡住时不能让 close 也挂死：退化为无锁收口（与修复前同风险，
            # 但至少卸载流程能走完），并留下明确日志
            if self.log:
                self.log.warning("[上班族物语][Playwright] close 抢锁超时，降级为无锁收口")
        try:
            browser, pw = self._browser, self._pw
            self._browser = self._pw = None
            self._live_instances.discard(self)
        finally:
            if acquired:
                self._launch_lock.release()
        for label, obj, coro_name in (
            ("browser", browser, "close"),
            ("playwright", pw, "stop"),
        ):
            if obj is None:
                continue
            try:
                await getattr(obj, coro_name)()
            except Exception as e:  # noqa: BLE001 - 关闭失败无需上抛
                if self.log:
                    self.log.warning(f"[上班族物语][Playwright] close {label}: {e}")
