"""运行时上下文：给各指令模块提供统一的工具入口，避免直接耦合 Star。"""

import asyncio
import re
import time

import astrbot.api.message_components as Comp
from astrbot.api import logger

from . import logic

# 缓存容量与 TTL 的默认值，可由插件配置覆盖
DEFAULT_MAX_UMOS = 2000
DEFAULT_CARD_CACHE_MAX = 2000
DEFAULT_CARD_TTL_MINUTES = 10
DEFAULT_GROUP_TTL_MINUTES = 30


class GameCtx:
    def __init__(self, star, db, config):
        self.star = star
        self.db = db
        self.config = config
        self._card_cache = {}  # (gid, uid) -> (expire_ts, card)
        self.umos = {}  # gid -> unified_msg_origin（推送用，指令触发时刷新）
        self.app_id = ""  # QQ 官方机器人的 appid（用于拼接开放平台头像）
        self.max_umos = max(1, int(self.c("max_session_cache", DEFAULT_MAX_UMOS)))
        self.card_cache_max = max(16, int(self.c("card_cache_max", DEFAULT_CARD_CACHE_MAX)))
        self.card_ttl = max(
            10.0, float(self.c("card_cache_ttl_minutes", DEFAULT_CARD_TTL_MINUTES)) * 60
        )
        self.group_ttl = max(
            10.0,
            float(self.c("group_name_cache_ttl_minutes", DEFAULT_GROUP_TTL_MINUTES)) * 60,
        )

    def close(self):
        """卸载/重载时释放内存缓存（昵称缓存与会话定位表），避免残留引用。"""
        try:
            self._card_cache.clear()
        except Exception:  # noqa: BLE001, S110 - 卸载收尾，清不掉也无所谓
            pass
        try:
            self.umos.clear()
        except Exception:  # noqa: BLE001, S110 - 同上
            pass

    def c(self, key, default=None):
        return logic.cfg_get(self.config, key, default)

    @property
    def market(self):
        """股市服务（由 main 持有）。handler 不该穿透 ctx.star.market 去拿。"""
        return self.star.market

    @property
    def backups(self):
        """备份管理器（由 main 持有）。"""
        return self.star.backups

    @staticmethod
    def log_error(where: str, exc: BaseException):
        """路由兜底异常：带堆栈落日志，用户侧只看到一句友好提示。"""
        logger.error(f"[上班族物语] 指令 {where} 执行异常：{exc}", exc_info=True)

    async def anick(self, event, uid=None):
        """异步版本：内存缓存 → 线程化 DB（昵称/群名片）→ 事件提取兜底。"""
        uid = str(uid) if uid else str(event.get_sender_id())
        gid = event.get_group_id()
        if gid:
            hit = self._card_cache.get((str(gid), uid))
            if hit and hit[1]:
                return hit[1]
        name = ""
        if gid:
            p = await asyncio.to_thread(self.db.get_player_row, gid, uid)
            if p and p.get("card"):
                return p["card"]
            if p and p.get("nickname"):
                name = p["nickname"]
        if not name and uid == str(event.get_sender_id()):
            name = event.get_sender_name() or ""
        if not name:
            for comp in event.get_messages():
                if isinstance(comp, Comp.At) and str(comp.qq) == uid:
                    name = getattr(comp, "name", "") or ""
                    if name:
                        break
        return name or logic.unknown_user(uid)

    @staticmethod
    def ats(event) -> list[str]:
        out = []
        self_id = str(event.get_self_id())
        for comp in event.get_messages():
            if isinstance(comp, Comp.At):
                qq = str(comp.qq)
                if qq and qq not in ("all", self_id) and qq not in out:
                    out.append(qq)
        return out

    @staticmethod
    def nums(event, words: tuple[str, ...]) -> list[str]:
        """抓消息里的数字串。口径（含小数与负号）统一在 logic.numbers_in。"""
        s = event.message_str or ""
        for w in sorted(words, key=len, reverse=True):
            s = s.replace(w, " ")
        return logic.numbers_in(s)

    @staticmethod
    def amount_after(
        event, words: tuple[str, ...], exclude_uids: list[str] | tuple[str, ...] = ()
    ) -> str:
        """取消息里第一个数字作为金额，但先把 exclude_uids 的数字剔除。

        适配器（尤其 OneBot/CQ）常把 @ 组件渲染成带对方 QQ 数字的文本，
        若直接 nums() 会把对方 QQ 当成金额（例：转账 @123456 500 会转 123456）。
        这里把被 @ 的目标/自己等 uid 先剔掉再扫数字，只对这类场景生效。

        使用数字边界断言（(?<!\\d)...(?!\\d)）而非朴素 str.replace，
        避免 uid "123" 把 "123456" 里的子串误删变成 "456"。
        """
        s = event.message_str or ""
        for w in sorted(words, key=len, reverse=True):
            s = s.replace(w, " ")
        for u in exclude_uids:
            u = str(u)
            if u:
                s = re.sub(r"(?<!\d)" + re.escape(u) + r"(?!\d)", " ", s)
        nums = logic.numbers_in(s)
        return nums[0] if nums else ""

    @staticmethod
    def arg_token(event, words: tuple[str, ...]) -> str:
        """取指令词之后的第一个【完整 token】（按空白切分），不做数字提取。

        金额位必须用它而不是 nums()：nums 是在整句里扫数字，「#存款 1,000」会被
        扫成 ['1', '000']，取 nums[0] 就是静默存入 1 元 —— 路由放宽到 \\S+ 之后
        这类输入进得来了，必须由 logic.parse_int / parse_amount 拿【整个 token】
        判定合法性，非法才回一句格式提示。
        """
        s = event.message_str or ""
        for w in sorted(words, key=len, reverse=True):
            s = s.replace(w, " ")
        parts = s.replace("#", " ").replace("＃", " ").split()
        return parts[0] if parts else ""

    async def render(self, template, data):
        return await self.star._render(template, data)

    # ------------------------------------------------------------------
    # 群昵称拉取：aiocqhttp 走 OneBot API；QQ官方走官方 HTTP 接口（ATK鉴权
    # 由 botpy 的 BotHttp 自动管理 access_token）。
    # ------------------------------------------------------------------

    # 昵称拉取整体超时：平台网关无响应时不能把用户的指令协程一直挂住
    CARD_FETCH_TIMEOUT = 5.0

    async def refresh_card(self, event, extra_uids=()):
        """拉取发送者与 @ 目标的群名片/昵称；任何失败静默，不影响指令。"""
        gid = event.get_group_id()
        if not gid:
            return
        try:
            await asyncio.wait_for(
                self._refresh_card_impl(event, gid, extra_uids),
                timeout=self.CARD_FETCH_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 - 3.10 上两者是不同类
            logger.debug("[上班族物语] 昵称拉取超时，已跳过")
        except Exception:  # noqa: BLE001, S110 - 昵称只是显示优化，绝不影响指令
            pass

    async def _refresh_card_impl(self, event, gid, extra_uids):
        # umo 登记是同步操作且位于首个 await 之前：即使拉取超时被取消，
        # 推送用的会话定位也一定已经记录完成
        umo = getattr(event, "unified_msg_origin", None)
        if umo:
            key = str(gid)
            # 真 LRU：dict 保序，但重复赋值不会把键移到末尾，所以必须先 pop 再插入。
            # 不这么做的话，淘汰的是「最早第一次见到」的群，而不是最久没活动的群
            # —— 最老的活跃群反而先失去定时推送能力。
            self.umos.pop(key, None)
            while len(self.umos) >= self.max_umos:
                self.umos.pop(next(iter(self.umos)), None)
            self.umos[key] = umo
        bot = getattr(event, "bot", None)
        if bot is None:
            return

        # 尝试提取 QQ 官方平台的 appid
        appid = (
            getattr(bot, "appid", None)
            or getattr(bot, "bot_appid", None)
            or getattr(bot, "client_id", None)
        )
        if not appid and hasattr(bot, "_http"):
            appid = getattr(bot._http, "appid", None) or getattr(bot._http, "bot_appid", None)
        if not appid and hasattr(bot, "api") and hasattr(bot.api, "_http"):
            appid = getattr(bot.api._http, "appid", None) or getattr(
                bot.api._http, "bot_appid", None
            )
        if appid:
            self.app_id = str(appid)

        api = getattr(bot, "api", None)
        if hasattr(api, "call_action"):
            await self._refresh_card_onebot(event, gid, bot, extra_uids)
            return

        # 优先使用 bot.api 上的 _http，或 bot 自身绑定的 _http
        http = getattr(api, "_http", None) or getattr(bot, "_http", None)
        if http is not None:
            await self._refresh_card_qqofficial(event, gid, http, extra_uids)

    async def _collect_uids(self, event, extra_uids) -> list[str]:
        uids = [str(event.get_sender_id())]
        for comp in event.get_messages():
            if getattr(comp, "type", "") == "At":
                qq = str(getattr(comp, "qq", ""))
                if qq and qq != "all" and qq != str(event.get_self_id()):
                    uids.append(qq)
        uids.extend(str(uid) for uid in extra_uids or () if uid)
        return list(dict.fromkeys(uids))  # 去重且保持出现顺序

    def _cache_set(self, key, expire_ts, val, now):
        # 缓存容量保护，避免大型群/多群环境内存泄露
        cap = self.card_cache_max
        if len(self._card_cache) > cap:
            for k in [k for k, v in self._card_cache.items() if v[0] <= now]:
                self._card_cache.pop(k, None)
            if len(self._card_cache) > cap:
                for k in list(self._card_cache.keys())[: max(1, cap // 4)]:
                    self._card_cache.pop(k, None)
        self._card_cache[key] = (expire_ts, val)

    async def _refresh_card_onebot(self, event, gid, bot, extra_uids):
        now = time.time()
        uids = await self._collect_uids(event, extra_uids)

        for uid in uids:
            key = (str(gid), uid)
            hit = self._card_cache.get(key)
            if hit and hit[0] > now:
                continue
            # ---- 1. 群名片 (OneBot / aiocqhttp) ----
            if str(gid).isdigit() and str(uid).isdigit():
                try:
                    info = await bot.api.call_action(
                        "get_group_member_info",
                        group_id=int(gid),
                        user_id=int(uid),
                        no_cache=False,
                    )
                    card = (info.get("card") or info.get("nickname") or "").strip()
                    if card:
                        self._cache_set(key, now + self.card_ttl, card, now)
                        await asyncio.to_thread(self.db.set_card, gid, uid, card)
                        continue
                except Exception:  # noqa: BLE001, S110
                    pass

            # ---- 2. 群名片兜底 (OneBot get_group_name) ----
            gkey = ("__grp__", str(gid))
            if str(gid).isdigit() and not (
                self._card_cache.get(gkey) and self._card_cache[gkey][0] > now
            ):
                try:
                    ginfo = await bot.api.call_action(
                        "get_group_info", group_id=int(gid), no_cache=False
                    )
                    gname = (ginfo.get("group_name") or "").strip()
                    if gname:
                        self._cache_set(gkey, now + self.group_ttl, gname, now)
                        await asyncio.to_thread(self.db.set_group_name, gid, gname)
                except Exception:  # noqa: BLE001
                    self._cache_set(gkey, now + self.group_ttl / 3, "", now)

            self._cache_set(key, now + self.card_ttl / 2, "", now)

    async def _refresh_card_qqofficial(self, event, gid, http, extra_uids):
        """QQ 官方平台：
        - 群名：GET /v2/groups/{group_openid}/info 拉取并缓存入库，
          WebUI 显示真实群名；
        - 成员昵称：探测灰度接口 /v2/groups/{g}/members/{openid}，
          未开放时静默负缓存。
        """
        try:
            from botpy.http import Route
        except ImportError:  # 非 official 安装不含 botpy
            return

        now = time.time()

        # ---- 群名 ----
        gkey = ("__grp__", str(gid))
        hit = self._card_cache.get(gkey)
        if not (hit and hit[0] > now):
            try:
                route = Route(
                    "GET",
                    "/v2/groups/{group_openid}/info",
                    group_openid=str(gid),
                )
                info = await http.request(route)
                name = str((info or {}).get("group_name") or "").strip()
                if name:
                    self._cache_set(gkey, now + self.group_ttl, name, now)
                    await asyncio.to_thread(self.db.set_group_name, gid, name)
                else:
                    self._cache_set(gkey, now + self.group_ttl / 2, "", now)
            except Exception:  # noqa: BLE001
                self._cache_set(gkey, now + self.group_ttl / 3, "", now)

        # ---- 用户昵称（群场景：探测灰度接口 /v2/groups/{g}/members/{openid}）----
        uids = await self._collect_uids(event, extra_uids)
        await self._fetch_group_nicks(http, gid, uids, now)

    @staticmethod
    def _pick_nick(item: dict, uid: str) -> str:
        """从频道/群成员信息里尽力提取昵称。"""
        user = item.get("user") or {}
        uid_in_user = user.get("id")
        if uid and uid_in_user and str(uid_in_user) != uid:
            return ""
        return str(
            item.get("nick")
            or item.get("nickname")
            or item.get("card")
            or user.get("username")
            or user.get("nickname")
            or ""
        ).strip()

    async def _fetch_group_nicks(self, http, gid, uids, now):
        from botpy.http import Route

        for uid in uids:
            key = (str(gid), uid)
            hit = self._card_cache.get(key)
            if hit and hit[0] > now:
                continue
            card = ""
            try:
                route = Route(
                    "GET",
                    "/v2/groups/{group_openid}/members/{member_openid}",
                    group_openid=str(gid),
                    member_openid=uid,
                )
                info = await http.request(route)
                if isinstance(info, dict) and info:
                    card = self._pick_nick(
                        {
                            k: v
                            for k, v in info.items()
                            if k not in ("member_openid", "join_timestamp")
                        },
                        uid,
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[上班族物语] 群成员昵称探测未开放：{e}")
            await self._store_card(gid, uid, card, now)

    async def _store_card(self, gid, uid, card, now):
        key = (str(gid), str(uid))
        if card:
            self._cache_set(key, now + self.card_ttl, card, now)
            await asyncio.to_thread(self.db.set_card, gid, uid, card)
        else:
            self._cache_set(key, now + self.card_ttl / 2, "", now)
