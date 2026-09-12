"""打工人·上班族物语 —— AstrBot 群聊职场生存模拟插件（模块化主入口）。

架构：
    main.py            仅保留插件生命周期 + 输出渲染，指令经声明式路由安装
    handlers/          指令路由表（按域拆分：职业/生活/理财/股票/彩票/社交/管理）
    core/              业务服务层 + SQLite 存储 + Playwright 渲染器
    webui/             独立端口 WebUI 面板（aiohttp）
    resources/         游戏文本 JSON / 静态数据 JSON / HTML 渲染模板

说明：
    AstrBot 以 handler.__module__ 与插件主模块做【精确匹配】来绑定插件实例
    （star_manager 中 get_handlers_by_module_name + functools.partial 绑定），
    因此 handlers/ 中的路由函数在装饰前由 install() 将 __module__ 重写为本
    模块路径。升级 AstrBot 后若指令全部失效，优先检查 handlers/base.py。

依赖：
    playwright（需执行一次 python -m playwright install chromium）
    aiohttp、jinja2、argon2-cffi、pyjwt
"""

import asyncio
import time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

# 只支持「以包的形式被导入」：AstrBot 的 star_manager 始终以 <插件目录名>.main
# 加载插件，下面的相对导入必然可用。
#
# 这里曾有一段「相对导入失败就用顶层名兜底」的逻辑，但那条分支**跑不通**：
# handlers/*.py 内部写的是 `from ..core import ...`（handlers/base.py、每个
# *_cmds.py），一旦以顶层名导入 handlers，它的父包是 ""，`..` 直接越界，
# 抛 "attempted relative import beyond top-level package" —— 而异常发生在
# 兜底分支的 import 语句上，报错信息与真实原因毫不相干。更糟的是它在失败前
# 已经删掉了 sys.modules 里的 core/handlers/webui 条目并把插件目录塞进
# sys.path，给进程留下半清理状态。
#
# 一条永远走不通、失败时还会掩盖真实原因并污染全局状态的「安全网」，比没有
# 更差。删掉它，让导入以真实原因失败，是唯一诚实的做法。
from .core import gamedata as gd
from .core import logic
from .core.backup import BackupManager
from .core.context import GameCtx
from .core.db import DB
from .core.lottery import broadcast_lines, fmt_draw, make_judge, random_number
from .core.renderer import PlaywrightRenderer
from .core.stocks import StockMarket
from .core.web_auth import hash_password, random_jwt_secret, random_password
from .handlers import ALL_ROUTES, install
from .webui.server import WebUIServer

PLUGIN_NAME = "astrbot_plugin_shangbanzu"
VERSION = "1.0.1"


class Shangbanzu(Star):
    """🏢 打工人·上班族物语 —— 大型群聊职场生存模拟。

    所有指令需带 # 前缀触发（如「#打卡」）；发送「#帮助」查看全部指令。
    数据与截图存放于 data/plugin_data/astrbot_plugin_shangbanzu/，更新/重装不丢失。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.db = DB(self._data_dir() / "shangbanzu.db", cfg=self.config)
        self.ctx = GameCtx(self, self.db, self.config)
        self.renderer = PlaywrightRenderer(
            self._data_dir() / "screenshots",
            scale=float(logic.cfg_get(self.config, "render_scale", 2.0)),
            logger=logger,
            max_concurrency=int(logic.cfg_get(self.config, "render_max_concurrency", 3)),
            max_keep=int(logic.cfg_get(self.config, "screenshot_max_keep", 60)),
            viewport_width=int(logic.cfg_get(self.config, "render_viewport_width", 780)),
            timeout_ms=int(logic.cfg_get(self.config, "render_timeout_ms", 15000)),
        )
        self.market = StockMarket(self.db, self.config)
        self._webui = None
        self._last_cleanup_day = ""
        # 模板源码缓存：{template: (mtime, 源码)}，只有 8 套模板，容量天然有界
        self._tmpl_src: dict[str, tuple[int, str]] = {}
        # 后台任务注册表：initialize() 创建的所有任务登记在此，terminate() 统一取消
        self._bg_tasks: set[asyncio.Task] = set()
        # 每用户指令锁表由 install() 的 handler 闭包 lazy-init 到本实例字段
        self._player_locks = None
        self.backups = BackupManager(
            self._data_dir() / "shangbanzu.db",
            self._data_dir() / "backups",
            logger,
            max_keep=int(logic.cfg_get(self.config, "backup_max_keep", 20)),
        )

    def _spawn(self, coro) -> asyncio.Task:
        """登记并启动一个后台任务，结束自动移出注册表。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def initialize(self):
        await asyncio.to_thread(self.db.init)
        # handlers 的路由正则（榜单别名、宠物种类）在模块导入期就读过一次 JSON，
        # 所以这里通常是命中缓存的空操作；保留它是为了「导入期恰好读失败」时补一次。
        await asyncio.to_thread(gd.load_all)
        await asyncio.to_thread(self.market.ensure_seeded)
        if bool(logic.cfg_get(self.config, "webui_enabled", True)):
            await self._start_webui()
        self._spawn(self._push_loop())
        # 用 install() 的真实安装数量打日志：AstrBot 升级导致 __module__ 绑定失效时，
        # 数到的 cmd_* 属性数量不会变，只有安装计数会掉到 0，日志才能暴露问题。
        bound = sum(1 for a in dir(self) if a.startswith("cmd_"))
        logger.info(
            f"[上班族物语] 插件已加载，共注册 {bound} 条指令"
            f"（声明 {len(ALL_ROUTES)} 条，安装 {_ROUTE_COUNT} 条）"
        )
        if bound < len(ALL_ROUTES):
            logger.error(
                "[上班族物语] 指令绑定数少于声明数：AstrBot 内核可能改了 handler "
                "绑定方式，请检查 handlers/base.py 的 __module__ 重写"
            )

    async def _start_webui(self):
        host = str(logic.cfg_get(self.config, "webui_host", "127.0.0.1") or "127.0.0.1")
        port = int(logic.cfg_get(self.config, "webui_port", 17817))
        stored = str(logic.cfg_get(self.config, "webui_password", "") or "")
        # 自动密码模式：
        #   - 未配置（空字符串）
        #   - 仍是旧 PBKDF2 哈希（pbkdf2$ 前缀）—— 不再做透明升级，老哈希作废
        # 任一情况都生成临时密码 → 哈希后写回 cfg → 启动 WebUI → 一次性打印到日志
        bootstrap = False
        temp_pwd = None
        if not stored or stored.startswith("pbkdf2$"):
            temp_pwd = random_password()
            self.config["webui_password"] = await asyncio.to_thread(hash_password, temp_pwd)
            stored = self.config["webui_password"]
            bootstrap = True
            self.config["_webui_must_change_password"] = True
            # 立即落盘：不能依赖后面 jwt_secret 分支"恰好需要保存"来兜底持久化，
            # 否则用户预填过 webui_jwt_secret 时临时密码哈希不落盘，
            # 每次重启都会重新生成一个新临时密码
            save = getattr(self.config, "save_config", None)
            if callable(save):
                try:
                    await asyncio.to_thread(save)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[上班族物语] 持久化临时密码哈希失败：{e}")
        # 明文提交即哈希：旧部署若直接填了明文，自动哈希化一次（仅识别非 $argon2id$ 与非 pbkdf2$ 字符串）。
        # 已废弃的 pbkdf2$ 前缀落到上面的 bootstrap 分支处理。
        elif not stored.startswith("$argon2id$"):
            self.config["webui_password"] = await asyncio.to_thread(hash_password, stored)
            stored = self.config["webui_password"]
            save = getattr(self.config, "save_config", None)
            if callable(save):
                try:
                    await asyncio.to_thread(save)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[上班族物语] 自动迁移明文密码为哈希失败：{e}")
        # JWT 签名密钥：首次启动生成 32 字节 hex，持久化到 cfg（重启后旧 JWT 仍可验）
        jwt_secret = str(self.config.get("webui_jwt_secret") or "")
        if not jwt_secret:
            jwt_secret = random_jwt_secret()
            self.config["webui_jwt_secret"] = jwt_secret
            save = getattr(self.config, "save_config", None)
            if callable(save):
                try:
                    await asyncio.to_thread(save)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[上班族物语] 持久化 JWT 密钥失败：{e}")
        self._webui = WebUIServer(
            self.db,
            self.backups,
            self.market,
            host,
            port,
            self._get_version(),
            logger,
            password=stored,
            config_data=self.config,
            app_id_getter=lambda: self.ctx.app_id,
            jwt_secret=jwt_secret,
            renderer=self.renderer,
        )
        try:
            await self._webui.start()
            if bootstrap:
                # 临时密码只在启动瞬间打印一次（明文），配置里存的是 Argon2id 哈希
                border = "=" * 60
                msg = (
                    f"\n{border}\n"
                    "[上班族物语] WebUI 首次启动：自动生成 18 位临时密码（仅显示一次）。\n"
                    f"      临时密码：{temp_pwd}\n"
                    f"      访问地址：http://{host}:{port}\n"
                    "      用临时密码登录后请立即在「插件配置」页改密。\n"
                    "      密码以 Argon2id 哈希存储（m=64MiB, t=3, p=4，比 OWASP 建议更保守），"
                    "令牌走 JWT(HS256)+ 服务端会话表(12h TTL)，可单独撤销任意会话。\n"
                    f"{border}"
                )
                logger.warning(msg)
            logger.info(f"[上班族物语] WebUI(aiohttp) 已启动：http://{host}:{port} 🔒")
        except PermissionError:
            logger.warning(
                "[上班族物语] WebUI 启动失败：端口 "
                f"{port} 被系统保留或被防火墙拦截（WinError 10013）。"
                "常见于 Hyper-V/WSL 动态保留端口，请在插件配置中更换 webui_port 后重载。"
            )
            self._webui = None
        except OSError as e:
            logger.warning(
                f"[上班族物语] WebUI 启动失败（端口 {port} 被占用？）：{e}；"
                "本次运行将没有 WebUI，其余功能不受影响"
            )
            self._webui = None
        except Exception as e:  # noqa: BLE001 - WebUI 挂掉绝不能拖垮整个插件
            # 兜底非 OSError 的启动失败：依赖版本不匹配（如 aiohttp<3.9 不认
            # AppRunner(shutdown_timeout=) 会抛 TypeError）、TLS 配置错误等。
            # 不接住的话异常会一路冒到 initialize()，让 95 条指令全部注册失败。
            logger.error(
                f"[上班族物语] WebUI 启动异常（{type(e).__name__}: {e}）；"
                "本次运行将没有 WebUI，其余功能不受影响。"
                "若提示 shutdown_timeout 相关，请升级 aiohttp>=3.9",
                exc_info=True,
            )
            self._webui = None
        if self._webui is not None and host not in ("127.0.0.1", "localhost", "::1"):
            # 服务端本身不支持 TLS：host 一改成 0.0.0.0/公网 IP，登录密码与 12h
            # 有效的会话 cookie 就在链路上明文传输，同网段嗅探即可接管后台。
            trusted = logic.cfg_get(self.config, "webui_trusted_proxies") or []
            if not trusted:
                logger.warning(
                    f"[上班族物语] WebUI 正监听非回环地址 {host}:{port} 且未配置 "
                    "webui_trusted_proxies —— 本服务不提供 TLS，密码与会话 cookie 将"
                    "明文过网。请置于 nginx/caddy 反代之后启用 HTTPS，并把反代地址"
                    "写入 webui_trusted_proxies（它需要回传 X-Forwarded-Proto: https）。"
                )

    async def terminate(self):
        """卸载/重载时按序收口：锁表 → 后台任务 → 会话缓存 → WebUI → 渲染器 → DB。"""
        # 1. 先清指令锁表：卸载后没有人能再新来；持锁中的旧协程按自身退出自然释放。
        locks = getattr(self, "_player_locks", None)
        if locks is not None:
            try:
                locks.clear()
            except Exception:  # noqa: BLE001
                logger.warning("[上班族物语] 清理指令锁表异常（已忽略）")
            self._player_locks = None
        # 2. 取消全部后台任务（推送循环等），不放过任何一个挂起的协程
        pending = list(self._bg_tasks)
        for t in pending:
            t.cancel()
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=10)
            except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
                logger.warning("[上班族物语] 后台任务取消超时（已忽略）")
            except Exception as e:  # noqa: BLE001 - 收尾异常不阻断卸载
                logger.warning(f"[上班族物语] 后台任务收尾异常（已忽略）：{e}")
        self._bg_tasks.clear()
        # 3. 释放 GameCtx 内存缓存（昵称/会话定位）与模板源码缓存
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001, S110 - 收尾清理失败不阻断卸载
            pass
        self._tmpl_src.clear()
        # 4. 停止 WebUI，显式 cleanup 释放端口与连接
        if self._webui:
            try:
                await asyncio.wait_for(self._webui.stop(), timeout=15)
                logger.info("[上班族物语] WebUI 已停止，端口已释放")
            except Exception as e:  # noqa: BLE001 - 停止失败不阻断卸载
                logger.warning(f"[上班族物语] WebUI 停止异常（已忽略）：{e}")
            self._webui = None
        # 5. 关闭 Playwright 浏览器进程（幂等，彻底释放内存）
        try:
            await self.renderer.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[上班族物语] 渲染器关闭异常（已忽略）：{e}")
        # 6. DB WAL checkpoint，避免 -wal 残留
        try:
            await asyncio.to_thread(self.db.close)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[上班族物语] DB WAL checkpoint 异常（已忽略）：{e}")

    async def _push_loop(self):
        """定时任务：到点后向开启推送的群发送每日早报，并做每周排行归档。"""
        while True:
            try:
                # 这个周期同时决定彩票开奖的时间精度（到点后最多晚一个周期）
                interval = float(logic.cfg_get(self.config, "push_check_interval_minutes", 10))
                await asyncio.sleep(max(30.0, interval * 60))
                lt = time.localtime()
                today = logic.today_str()
                # 彩票开奖必须在 push_hour 门禁【之前】：它有自己的
                # lottery_draw_hour，两个键在 schema 里完全独立、无交叉校验。
                # 放在门禁之后时，把 draw_hour 配得比 push_hour 早（例如开奖 6 点、
                # 早报 8 点）会让每期开奖、补开欠期与孤儿奖池滚存全部被推迟到
                # push_hour 才执行 —— 而 _lottery_maybe_draw 内部已按小时自门禁，
                # 每次巡检进来都是安全的空操作，不该再叠一层。
                try:
                    await self._lottery_maybe_draw()
                except Exception as e:  # noqa: BLE001 - 开奖失败不影响推送
                    logger.warning(f"[上班族物语] 彩票开奖异常：{e}")
                if lt.tm_hour < int(logic.cfg_get(self.config, "push_hour", 8)):
                    continue
                # 归档与清理都是「每天一次」的重活（清理要扫全表并持写锁），
                # 用日期水位线挡住，否则从推送时间点起每 10 分钟就来一轮
                if self._last_cleanup_day != today:
                    self._last_cleanup_day = today
                    if bool(logic.cfg_get(self.config, "weekly_archive_enabled", True)):
                        try:
                            await asyncio.to_thread(self._weekly_archive)
                        except Exception as e:  # noqa: BLE001 - 归档失败不影响推送
                            logger.warning(f"[上班族物语] 每周归档失败：{e}")
                    try:
                        await asyncio.to_thread(
                            self.db.cleanup_old_data,
                            gd.s(
                                "extra_redpacket",
                                "tx_refund_note",
                                "红包超时未领完，剩余金额已退回",
                            ),
                            gd.s("extra_redpacket", "kind_refund", "红包退回"),
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[上班族物语] 数据清理异常：{e}")
                await self._daily_push()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.error(f"[上班族物语] 推送循环异常：{e}")

    # 单条推送/播报的发送超时：_push_loop 是串行循环，某个适配器把
    # send_message 永久挂住就等于早报、归档、清理、开奖全部再也不触发
    SEND_TIMEOUT = 15.0

    async def _send(self, umo, text) -> bool:
        try:
            await asyncio.wait_for(
                self.context.send_message(umo, MessageChain().message(text)),
                timeout=self.SEND_TIMEOUT,
            )
            return True
        except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
            logger.warning(f"[上班族物语] 消息发送超时（{self.SEND_TIMEOUT:g}s），已跳过")
            return False
        except Exception as e:  # noqa: BLE001 - 单群失败不阻断其他群
            logger.warning(f"[上班族物语] 消息发送失败：{e}")
            return False

    async def _daily_push(self):
        today = logic.today_str()
        for gid in await asyncio.to_thread(self.db.push_group_ids):
            gid = str(gid)
            umo = self.ctx.umos.get(gid)
            if not umo:
                continue  # 本轮未见过该群消息，无法定位会话
            if await asyncio.to_thread(self.db.push_last_date, gid) == today:
                continue
            text = await asyncio.to_thread(self._daily_report_text)
            if await self._send(umo, text):
                await asyncio.to_thread(self.db.mark_pushed, gid, today)

    def _daily_report_text(self) -> str:
        """每日早报文本。文案走 resources/texts/extra3.json 的 daily_push。

        早报是【全服共享】的同一份内容（见 system.json 的 foot_news），
        不含任何群数据，因此不需要 gid 参数。
        """
        tx = gd.t("extra3", "daily_push")
        tpl = tx[0] if isinstance(tx, list) and tx and isinstance(tx[0], dict) else {}
        empty = str(tpl.get("news_empty") or "暂无")
        lines = [
            logic.fill(
                tpl.get("news") or "📰 今日职场早报：{news}",
                {"news": gd.news_of_day() or empty},
            )
        ]
        try:
            stocks = self.market.list_stocks(100)
            ups = sorted((s for s in stocks if s["chg"] > 0), key=lambda s: -s["chg"])[:3]
            downs = sorted((s for s in stocks if s["chg"] < 0), key=lambda s: s["chg"])[:3]
            if ups:
                lines.append(
                    str(tpl.get("up") or "📈 领涨：")
                    + " ".join(f"{s['name']} +{s['chg']}%" for s in ups)
                )
            if downs:
                lines.append(
                    str(tpl.get("down") or "📉 领跌：")
                    + " ".join(f"{s['name']} {s['chg']}%" for s in downs)
                )
        except Exception:  # noqa: BLE001, S110 - 股市摘要失败仅省略该段
            pass
        cta = tpl.get("cta")
        if cta:
            lines.append(str(cta))
        return "\n".join(lines)

    async def _lottery_maybe_draw(self):
        """到达开奖时间且当期有售票时，自动开奖并向购票群播报结果。"""
        hour = int(logic.cfg_get(self.config, "lottery_draw_hour", 21))
        lt = time.localtime()
        if lt.tm_hour < hour:
            return
        today = logic.today_str()
        # 1. 补开欠下的期次：机器人在某期开奖时间之后一直离线时，那期的票永远
        #    等不到结算，票款也会被 carry_unsettled_pool 跳过（它刻意不动仍有
        #    存票的期次），钱既不派奖也不滚存。必须在滚存之前补开。
        stale_dates = await asyncio.to_thread(self.db.lottery_pending_dates, today)
        for stale in stale_dates:
            try:
                await self._draw_one(str(stale), late=True)
            except Exception as e:  # noqa: BLE001 - 补开失败不影响今天
                logger.warning(f"[上班族物语] 补开 {stale} 期彩票失败：{e}")
        # 2. 把【历史期次】的孤儿奖池（开奖日已过但当期无人购票、因此 settle
        #    从不滚存的余额）并入今天。注意实现只处理 draw_date < today：
        #    搬走今天的池行会让本次开奖的中奖者到手 0 元。
        try:
            await asyncio.to_thread(self.db.lottery_carry_unsettled_pool, today)
        except Exception as e:  # noqa: BLE001 - 滚存失败不影响开奖
            logger.warning(f"[上班族物语] 孤儿奖池滚存失败：{e}")
        # 3. 今天：已开过就不再开
        if await asyncio.to_thread(self._lottery_already_drawn, today):
            return
        await self._draw_one(today, late=False)

    async def _draw_one(self, date_str: str, late: bool):
        """开一期奖并向当期购票群播报。date_str 为期号（YYYY-MM-DD）。"""
        gids = await asyncio.to_thread(self.db.lottery_today_gids, date_str)
        if not gids:
            return
        number = random_number()
        result = await asyncio.to_thread(
            self.db.lottery_settle, date_str, number, make_judge(number, self.config)
        )
        if not result:
            return
        logger.info(
            f"[上班族物语] 双色球{'补' if late else ''}开奖 {date_str}："
            f"{fmt_draw(number)}，奖池 {result['pool']} 元，"
            f"派奖 {result['paid']} 元，滚存 {result['carry']} 元"
        )
        # 向当期购票群播报（未在本轮见过消息的群无法定位会话，跳过）
        lines = broadcast_lines(result, late=late)
        text = "\n".join(lines)
        for gid in gids:
            umo = self.ctx.umos.get(str(gid))
            if umo:
                await self._send(umo, text)

    def _lottery_already_drawn(self, today):
        last = self.db.lottery_last_draw()
        return last and last.get("date") == today

    def _weekly_archive(self):
        """每周首次触发时，把上一周各群的财富榜快照写入 archives。"""
        # 同步方法，由调用方 asyncio.to_thread 包裹

        # 上一周必须用 logic.prev_iso_week：读取端（#上周榜）用的是同一个函数，
        # 两边同源才对得上。用 12-31 反查会算出错年/当前周。
        prev = logic.prev_iso_week(*logic.iso_week())
        top_n = int(logic.cfg_get(self.config, "archive_top_n", 10))
        # 逐群判据，而不是一次全局「已归档周」水位线：归档是逐群写的，用全局
        # 水位线时只要有任意一群写成功，本周内后续每天都会整体跳过 —— 中途抛
        # 异常的群与本周新进的群那一周的 #上周榜 就永久缺失。逐群幂等后，
        # 每天的巡检都会把漏掉的群补齐。
        for gid, _n in self.db.group_ids():
            try:
                if self.db.has_archive(gid, prev[0], prev[1]):
                    continue
                top = self.db.top_wealth(gid, top_n)
                if not top:
                    continue
                payload = {
                    "week": f"{prev[0]}-W{prev[1]:02d}",
                    "top": [
                        {
                            "name": logic.display(p),
                            "total": p.get("total", 0),
                            "level": p.get("lvl", 1),
                        }
                        for p in top
                    ],
                }
                self.db.save_archive(gid, prev[0], prev[1], payload)
            except Exception as e:  # noqa: BLE001 - 单群失败不该拖垮其余群
                logger.warning(f"[上班族物语] 群 {gid} 的周榜归档失败：{e}")

    # ------------------------------------------------------------------
    # 数据目录与输出渲染（独立 Playwright 渲染器，失败回退纯文本）
    # ------------------------------------------------------------------

    def _data_dir(self) -> Path:
        """持久化数据目录：data/plugin_data/astrbot_plugin_shangbanzu/。"""
        try:
            return StarTools.get_data_dir(PLUGIN_NAME)
        except Exception:  # noqa: BLE001 - 兜底路径，保证任何内核版本都能初始化
            return Path("data") / "plugin_data" / PLUGIN_NAME

    async def _render(self, template, data):
        if not template or not bool(logic.cfg_get(self.config, "use_image", True)):
            return None
        try:
            from astrbot.core.config.default import VERSION as AB_VER

            data.setdefault("plugin_version", self._get_version())
            data.setdefault("astrbot_version", AB_VER)
            # 模板文案统一注入：8 套模板里的标题/字段名/兜底值都从这里取，
            # 免得 HTML 成为文案外置的最后一个例外（见 gd.template_texts）
            data.setdefault("t", gd.template_texts())
            tmpl_str = await self._template_source(template)
            if tmpl_str is None:
                return None
            html = await asyncio.to_thread(self.renderer.render_template, tmpl_str, data)
            return await self.renderer.screenshot(html, name=f"{template}_{int(time.time())}")
        except Exception as e:  # noqa: BLE001 - 渲染失败必须回退文本而非中断指令
            logger.warning(f"[上班族物语] 渲染失败回退文本：{e}")
            return None

    async def _template_source(self, template: str) -> str | None:
        """按 mtime 缓存模板源码：只有 8 套模板，不该每次出图都重读一遍磁盘。

        缓存键带 mtime，运维直接改 resources/templates/*.html 后下次出图即生效
        （与 renderer.clear_template_cache 一起保证 Jinja2 编译结果也会失效）。
        """
        path = gd.template_path(template)
        try:
            mtime = await asyncio.to_thread(lambda: int(path.stat().st_mtime))
        except OSError as e:
            logger.warning(f"[上班族物语] 模板 {template}.html 不可读：{e}")
            return None
        hit = self._tmpl_src.get(template)
        if hit and hit[0] == mtime:
            return hit[1]
        src = await asyncio.to_thread(path.read_text, "utf-8")
        self._tmpl_src[template] = (mtime, src)
        return src

    def _get_version(self) -> str:
        try:
            meta = self.context.get_registered_star(PLUGIN_NAME)
            if meta and meta.version:
                return meta.version
        except Exception:  # noqa: BLE001, S110 - 元数据不可用时用常量兜底
            pass
        return VERSION

    async def _emit_msg(self, event: AstrMessageEvent, r: dict):
        """把一条 R 结果转成消息：err > img > 模板渲染 > 纯文本。"""
        max_len = int(logic.cfg_get(self.config, "max_text_length", 1500))
        if r.get("err"):
            yield event.plain_result(str(r["err"])[:max_len])
            return
        img = r.get("img") or (await self._render(r.get("tmpl"), r.get("data") or {}))
        if img:
            yield event.image_result(img)
        else:
            text = str(r.get("text") or "").strip()
            # 空输出的兜底文案同样外置（resources/texts/system.json）
            yield event.plain_result(
                text[:max_len] if text else gd.s("system", "done_placeholder", "（执行完成）")
            )


# 安装全部指令路由（handlers/ 目录按业务域维护）。
# 用真实安装数量打日志：AstrBot 升级导致绑定失效时，日志不应仍显示「已注册 N 条」。
_ROUTE_COUNT = install(Shangbanzu, filter, __name__, ALL_ROUTES)
