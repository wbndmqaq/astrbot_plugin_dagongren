"""指令处理基座：声明式路由 + 每用户锁（TTL 淘汰）。

AstrBot 通过 handler.__module__ 与插件主模块做【精确匹配】来绑定插件实例
（star_manager 中 get_handlers_by_module_name + functools.partial 绑定），
因此所有被 @filter.regex / @filter.permission_type 装饰的 handler 必须归属
主模块。install() 在装饰前重写 __module__，从而允许把路由表安全拆分到
handlers/ 各业务域子模块，main.py 只保留插件生命周期。

注意：此写法依赖 AstrBot 内部 get_handlers_by_module_name 的精确匹配行为，
升级 AstrBot 后若指令全部失效，优先检查这里。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..core import gamedata as gd

# 两句全局提示同样走 resources/texts/system.json（缺键回退到这里的默认值）。
# 惰性读取：install() 在模块导入期就跑，那时 gamedata 还没预热。
_GID_HINT_DEFAULT = "该功能只能在群聊中使用"
_ERR_HINT_DEFAULT = "指令执行异常，已记录日志，请稍后再试"


def gid_hint() -> str:
    return gd.s("system", "gid_hint", _GID_HINT_DEFAULT)


def err_hint() -> str:
    return gd.s("system", "err_hint", _ERR_HINT_DEFAULT)


def gid_of(event) -> str:
    """群 ID（私聊返回空串）。所有指令统一用它做群聊守卫，避免各文件重复抄写。"""
    return event.get_group_id() or ""


class PlayerLockTable:
    """每 (群, 用户) 一把 asyncio.Lock，串行化单用户的多指令并发。

    - 带 TTL 空闲淘汰：超过 IDLE_TTL 未被使用的锁自动清除，防止群/用户
      持续增长导致锁表无限膨胀（旧版仅靠容量上限，低频长尾用户会常驻）；
    - 只淘汰【未被持有】的锁：在飞的指令与新指令若各持一把不同的锁，
      防双花的窗口会重新打开，因此持锁中的锁绝不淘汰。
    """

    SOFT_CAP = 10000
    IDLE_TTL = 600.0  # 空闲 10 分钟未使用即淘汰
    SWEEP_INTERVAL = 60.0  # 至少每 60s 顺带清一次 TTL 过期锁

    def __init__(self):
        # key -> [lock, last_used_monotonic]
        self._locks: dict[tuple[str, str], list] = {}
        self._last_sweep = time.monotonic()
        # 硬天花板上的清扫「注定徒劳」标记：见 get() 里的说明
        self._hard_stuck = False

    def get(self, gid, uid) -> asyncio.Lock:
        key = (str(gid or ""), str(uid))
        now = time.monotonic()
        item = self._locks.get(key)
        if item is not None:
            item[1] = now  # 刷新使用时间
            return item[0]
        # 只在超 SOFT_CAP 时淘汰，会让 TTL 在一万把锁以下形同虚设（与类
        # docstring 的承诺不符），所以再加一道时间水位：每 SWEEP_INTERVAL
        # 秒顺带扫一次，长尾用户的锁不会一直常驻。
        #
        # 两道闸门都要【受间隔节流】：早先 `len > SOFT_CAP` 分支绕过间隔门禁，
        # 于是锁表一到上限，之后每一个新用户的第一条指令都要在事件循环里做一次
        # 全表淘汰（实测 10k 把锁时 4.16ms/新 key）——持续有新用户时等于按新增
        # 用户的比例吃掉整个进程的响应能力。现在 SOFT_CAP 是真正的「软」上限
        # （允许在两次清扫之间短暂超出），另设 2×SOFT_CAP 的硬天花板兜住突发。
        #
        # 但硬天花板本身还有个「注定徒劳」的死角：上一轮把它做成了「越过就多清
        # 扫一次」，而当【全部锁都被持有/等待】时，_evict 一把都删不掉（持锁中的
        # 锁绝不淘汰），于是每一次插入都白付一次 O(n) 全表扫描，表长还继续涨
        # （实测放大 SOFT_CAP 后，9 把锁全被持有时再插 300 个新 key，300 次
        # _evict 一个都没删掉）。所以记录「这次硬天花板清扫有没有真的删掉东西」：
        # 没有就置 _hard_stuck，接下来的插入直接放行、跳过扫描，等表长回落
        # （锁释放 + TTL 清扫）后再重新尝试。触发条件需要 >2 万把锁同时持有，
        # 真实不可达，属结构性场景；真正的收敛靠时间闸门那道 TTL 清扫。
        over_hard = len(self._locks) > self.SOFT_CAP * 2
        if not over_hard:
            self._hard_stuck = False  # 回到天花板以下，下次越过时允许再试一次
        hard_due = over_hard and not self._hard_stuck
        if self._locks and (now - self._last_sweep > self.SWEEP_INTERVAL or hard_due):
            self._last_sweep = now
            before = len(self._locks)
            self._evict(now)
            if over_hard and len(self._locks) >= before:
                self._hard_stuck = True
        lock = asyncio.Lock()
        self._locks[key] = [lock, now]
        return lock

    def _evict(self, now: float):
        """淘汰空闲锁：先清 TTL 过期项，仍超上限再淘汰空闲锁。

        淘汰顺序用 dict 的插入顺序（「最久未刷新」的近似），而不是
        `sorted(..., key=mtime)`：排序是 O(n log n)，而这条路径挂在「每个新
        用户的第一条指令」上。少一点精确性，换 O(n) 的代价。
        """
        stale = [
            k for k, v in self._locks.items() if not v[0].locked() and (now - v[1]) > self.IDLE_TTL
        ]
        for k in stale:
            self._locks.pop(k, None)
        if len(self._locks) <= self.SOFT_CAP:
            return
        for k in list(self._locks):
            if len(self._locks) <= self.SOFT_CAP:
                break
            if not self._locks[k][0].locked():
                self._locks.pop(k, None)

    def clear(self) -> None:
        """热重载 / 卸载时清空锁表。不抢持锁中的锁，让它们按协程退出自然释放。"""
        self._locks.clear()


@dataclass
class Route:
    """一条指令路由声明。

    run 为 handlers/*_cmds.py 里的业务函数，签名 (ctx, event) -> R(dict)。
    群聊守卫由 install() 统一前置：group_only=True（默认）时私聊直接回提示。
    完全不依赖群 ID 的指令（备份维护、帮助、早报、今日氛围）显式标
    group_only=False，否则管理员在私聊里连备份都做不了。
    """

    pattern: str
    name: str
    doc: str
    run: Callable[..., Awaitable]
    admin: bool = False
    priority: int = 0
    group_only: bool = True


def _accept_fullwidth_hash(pattern: str) -> str:
    """让所有路由同时接受半角 # 与全角 ＃ 前缀。

    中文输入法下 ＃ 是高频误输入，而 95 条路由全部写死 `^#`，用户发「＃摸鱼」
    会命中零条路由、拿到零反馈。在这里统一改写，比在 95 个字面量里各加一次
    可靠（新增路由自动继承）。

    注意：handlers 里用 `^#` 锚定的【二次解析】正则必须同步写成 `^[#＃]`，
    否则全角输入虽然进得来，参数却提不出来 —— 用户从「零反馈」变成拿到一句
    「公司名不能为空」这类莫名其妙的报错。已改的 8 处见 grep `^[#＃]`。
    """
    return "^[#＃]" + pattern[2:] if pattern.startswith("^#") else pattern


def install(cls, flt, module_path: str, routes) -> int:
    """把路由安装到插件类上，返回安装数量。

    统一职责：刷新昵称 → 每用户串行锁 → 执行 run → 输出渲染(_emit_msg) →
    stop_event 终止传播。与旧 CmdMixin._handle 行为一致。
    """
    installed = 0
    for route in routes:

        async def handler(self, event, _route=route):
            # 群聊守卫集中在这里：本插件绝大多数玩法都要求 gid。
            # 旧实现把这段守卫复制进每个业务函数（84 处一模一样的 if not gid）。
            # 统一收拢后，新增指令无需再手写守卫；业务函数里仍会自己调 gid_of
            # 拿 gid 传给服务层，但不再需要手写「if not gid」的拦截。
            gid = gid_of(event)
            if _route.group_only and not gid:
                # 这里【绝不能】stop_event()：本插件在私聊里并没有处理这条消息。
                # 88 条路由是 group_only，其中 #状态 #菜单 #备份 #推送 #转账 这类
                # 别名在 AstrBot 生态里撞名概率很高——一旦终止传播，真正拥有该
                # 指令的插件在私聊里就永久收不到消息，用户只看到本插件那句
                # 「只能在群聊中使用」。给出提示、放行事件，才是不越界的做法。
                #
                # 但必须掐掉 LLM：regex 过滤器命中即被视为「唤醒」，AstrBot 的
                # process_stage 会在「有 result、未 stop、未禁 LLM」时继续走
                # LLM 链路，于是私聊里发一句 #状态 会变成「提示 + LLM 再回一条」
                # 两条回复。should_call_llm(False) 只关 AstrBot 默认 LLM 链路，
                # 不影响事件向其它插件传播，正好对上这里要守的边界。
                event.should_call_llm(False)
                yield event.plain_result(gid_hint())
                return
            try:
                await self.ctx.refresh_card(event)
            except Exception as e:  # noqa: BLE001 - 昵称拉取失败不影响指令
                # 不记日志的话，若 refresh_card 持续失败会静默无痕、难以排查
                self.ctx.log_error(f"{_route.name}.refresh_card", e)
            try:
                # 锁表 lazy-init 到插件实例字段，terminate() 时清空
                locks = getattr(self, "_player_locks", None)
                if locks is None:
                    locks = PlayerLockTable()
                    self._player_locks = locks
                # 锁只包住业务逻辑（读改写玩家档案）。渲染/发图不进锁：
                # Playwright 截图最坏要等 render_timeout_ms(默认 15s) + 并发信号量
                # 排队，把它圈进锁里会让同一用户的下一条指令白等十几秒，
                # 而防双花只需要保护数据读写这一段。
                async with locks.get(gid, event.get_sender_id()):
                    r = await _route.run(self.ctx, event)
                async for msg in self._emit_msg(event, r):
                    yield msg
            except Exception as e:  # noqa: BLE001 - 兜底：绝不让异常吞掉回复
                self.ctx.log_error(_route.name, e)
                yield event.plain_result(err_hint())
            finally:
                # 无论成功失败都必须终止事件传播，否则异常指令会继续下发给
                # 其它插件 / LLM，用户看到的是两条互相矛盾的回复。
                event.stop_event()

        handler.__name__ = route.name
        handler.__qualname__ = f"{cls.__name__}.{route.name}"
        handler.__doc__ = route.doc
        handler.__module__ = module_path

        # regex 必须先装：AstrBot 的 get_handler_or_create 对同一个函数只建一次
        # metadata，第二个装饰器传的 kwargs（priority）会被静默丢弃。反过来装的话
        # admin=True 的路由 priority 永远是 0。
        handler = flt.regex(_accept_fullwidth_hash(route.pattern), priority=route.priority)(handler)
        if route.admin:
            handler = flt.permission_type(flt.PermissionType.ADMIN)(handler)
        setattr(cls, route.name, handler)
        installed += 1
    return installed
