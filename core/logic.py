"""纯函数工具：数值规则、格式化、Elo、段位。"""

import asyncio
import json
import random
import re
import time
from datetime import date, timedelta


def cfg_get(cfg, key, default=None):
    v = cfg.get(key) if hasattr(cfg, "get") else None
    return default if v is None else v


def clamp(v, lo, hi):
    if v is None:
        return lo
    return max(lo, min(hi, float(v)))


def now_ts() -> int:
    return int(time.time())


def today_str() -> str:
    return time.strftime("%Y-%m-%d")


def iso_week(t: float | None = None) -> tuple[int, int]:
    dt = time.localtime(t) if t else time.localtime()
    y, w, _ = date(dt.tm_year, dt.tm_mon, dt.tm_mday).isocalendar()
    return y, w


def prev_iso_week(y: int, w: int) -> tuple[int, int]:
    """上一个 ISO 周，正确跨年（上一年可能有 52 或 53 周）。

    用「本周周一 - 7 天」而不是拿 12-31 反查：12-31 在很多年份属于【次年】
    第 1 周（2024-12-31 → (2025, 1)、2019-12-31 → (2020, 1)），按它回退会
    算出当前周或错年，让周报归档端与读取端各错一套、永远对不上。
    """
    y, w = int(y), int(w)
    # 防御非法周次：date.fromisocalendar 对「某年不存在的周」（如 52 周年的
    # w=53）直接抛 ValueError，会把周报归档/查询打成 500。先把 w 钳到该年
    # 最大合法 ISO 周数；对 year 超出 date 可表示范围（1~9999）的极端输入，
    # 直接从当前日期回退 7 天兜底，保证任何输入都不崩溃。
    try:
        _max = date(y, 12, 28).isocalendar()[1]
    except (ValueError, OverflowError):
        _max = 52
    w = max(1, min(w, _max))
    try:
        monday = date.fromisocalendar(y, w, 1) - timedelta(days=7)
    except (ValueError, OverflowError):
        monday = date.today() - timedelta(days=7)
    py, pw, _ = monday.isocalendar()
    return py, pw


def yearweek_str() -> str:
    y, w = iso_week()
    return f"{y}-W{w:02d}"


def fmt_money(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "0"
    if abs(v - round(v)) < 1e-9:
        return str(round(v))
    return f"{v:.2f}"


def fmt_remaining(seconds) -> str:
    s = max(0, int(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    hour = _system("val_unit_hour", "小时")
    minute = _system("val_unit_minute", "分")
    second = _system("val_unit_second", "秒")
    if h:
        return f"{h}{hour}{m}{minute}{sec}{second}"
    if m:
        return f"{m}{minute}{sec}{second}"
    return f"{sec}{second}"


def ri(lo: int, hi: int) -> int:
    """random.randint 的安全版（lo>hi 时自动交换）"""
    if lo > hi:
        lo, hi = hi, lo
    return random.randint(lo, hi)


def rf(lo: float, hi: float) -> float:
    return random.uniform(min(lo, hi), max(lo, hi))


def _tier_tables():
    """段位名称与阈值（单一来源：resources/data/rankevents.json）。

    惰性读取避免 import 时触发文件 IO；gamedata 在插件 initialize 已预热，
    运行期命中内存缓存。
    """
    from . import gamedata as gd

    names = gd.tier_names()
    scores = gd.tier_scores()
    if not names:
        # rankevents.json 缺失/损坏时的兜底档名，文案外置（system.json）
        names = [_system("tier_fallback_name", "菜鸟")]
    if not scores:
        scores = [0]
    return names, scores


def tier_of(score: int) -> str:
    names, scores = _tier_tables()
    name = names[0]
    for i, threshold in enumerate(scores):
        if score >= threshold:
            name = names[i] if i < len(names) else names[-1]
    return name


def tier_desc() -> str:
    """段位线描述（展示用），与 tier_of 同源，避免硬编码第三份段位表。"""
    names, scores = _tier_tables()
    if len(names) < 2 or len(scores) < len(names):
        return ""
    parts = [f"{names[0]}<{scores[1]}"]
    parts.extend(f"{names[i]}≥{scores[i]}" for i in range(1, len(names)))
    return "｜".join(parts)


def elo_change(my: int, opp: int, is_win: bool, k: int = 32) -> int:
    expected = 1 / (1 + 10 ** ((opp - my) / 400))
    diff = int(k * ((1 - expected) if is_win else (0 - expected)))
    # int() 向零截断在极端分差下会把变化削成 0（赢了不加分/输了不扣分），
    # 钳到 ±1 保证每场对战积分必有变动
    if diff == 0:
        diff = 1 if is_win else -1
    return diff


def fund_daily_change(cfg=None) -> float:
    """基金单日涨跌幅（百分比）。

    drift 为正意味着长期持有稳赚，是最容易被忽视的通胀口，
    所以漂移、波动率、单日涨跌停三个参数都开放给运维调。
    """
    drift = float(cfg_get(cfg, "fund_drift", 0.2))
    vol = abs(float(cfg_get(cfg, "fund_volatility", 4.5)))
    limit = abs(float(cfg_get(cfg, "fund_daily_limit_pct", 12.0)))
    return clamp(random.gauss(drift, vol), -limit, limit)


def interest_of(
    deposit: float,
    last_interest: int,
    rate_hourly: float,
    max_hours: int,
    now: int | None = None,
) -> float:
    now = now_ts() if now is None else now
    if deposit <= 0 or not last_interest:
        return 0.0
    hours = int((now - last_interest) // 3600)
    if hours < 1:
        return 0.0
    effective = min(hours, max_hours)
    return round(deposit * rate_hourly * effective, 2)


def salary_of(base_salary: float, mult: float) -> float:
    return round(float(base_salary) * float(mult), 0)


# 个人涨薪系数的上限：谈薪与年终 S 档都能永久提薪，无上限的话理论上可以
# 一路复利到天文数字。10 倍已经比「从最低职级爬到最高职级」的跨度还大。
MAX_SALARY_BONUS = 10.0


def salary_bonus_of(p: dict) -> float:
    """读取个人涨薪累积系数（老档没有这一列时按 1.0）。"""
    try:
        v = float(p.get("salary_bonus") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return clamp(v, 1.0, MAX_SALARY_BONUS)


def base_salary_of(p: dict, base_salary: float, mult: float) -> float:
    """按「公司基薪 × 职级系数 × 个人涨薪系数」重算薪资。

    晋升/跳槽必须走这里：直接 salary_of(comp, pos) 会把谈薪与年终考评拿到的
    永久涨薪整体覆盖掉（review.py 把它称作全插件最大的通胀杠杆，却会被一次
    晋升静默清零）。
    """
    return round(salary_of(base_salary, mult) * salary_bonus_of(p), 0)


def apply_raise(p: dict, pct: float) -> float:
    """记录一次永久涨薪：同时更新 salary 与 salary_bonus，返回新薪资。

    salary 必须按【钳制后】的系数比例涨，不能各自独立乘：salary_bonus 有 10 倍
    上限而 salary 没有，两边分开算的话顶到上限后就脱钩了——下一次晋升/跳槽走
    base_salary_of 重算时会把月薪静默砍掉一大截。
    """
    pct = max(0.0, float(pct))
    old_bonus = salary_bonus_of(p)
    new_bonus = round(clamp(old_bonus * (1 + pct), 1.0, MAX_SALARY_BONUS), 4)
    p["salary_bonus"] = new_bonus
    # 系数已到上限时 new_bonus / old_bonus == 1，薪资也就不再涨，两者始终同步
    p["salary"] = round(float(p["salary"]) * (new_bonus / old_bonus), 0)
    return float(p["salary"])


def workdays(cfg=None) -> int:
    """月薪折算日薪的工作日数。散落多处，统一从这里取。"""
    return max(1, int(cfg_get(cfg, "monthly_workdays", 22)))


def daily_pay(salary: float, perf: float, streak: int, cfg=None) -> float:
    cap = int(cfg_get(cfg, "attend_streak_bonus_days", 20))
    rate = float(cfg_get(cfg, "attend_streak_bonus_rate", 0.005))
    bonus = min(int(streak), cap) * rate
    return round(salary / workdays(cfg) * perf * (1 + bonus), 2)


def promote_rate(level_index: int, base: float, decay: float) -> float:
    return max(0.15, base - level_index * decay)


def avatar_of(uid, app_id: str = "") -> str:
    """生成头像 URL。

    - OneBot (纯数字 QQ 号)：走 q1.qlogo.cn
    - QQ 官方机器人 (32 位 openid / user_str 且包含 app_id)：走 q.qlogo.cn/qqapp/{app_id}/{user_str}/640
    """
    uid = str(uid).strip()
    if not uid:
        return ""
    if uid.isdigit():
        return f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"
    if app_id:
        return f"https://q.qlogo.cn/qqapp/{app_id}/{uid}/640"
    return ""


def display(p: dict) -> str:
    """展示名优先级：【称号】+ 群昵称(card) > 昵称 > 用户{id}"""
    return f"【{p['title']}】{name_of(p)}" if p.get("title") else name_of(p)


def name_of(p: dict, fallback: str = "") -> str:
    """不带称号的展示名：群名片(card) > 昵称 > fallback > 用户{uid}。

    事件文案/流水备注里要的是"这个人叫什么"，不带段位称号。此前
    `p.get("card") or p["nickname"] or uid` 在 11 处逐字复制，其中几处还用
    `p["nickname"]` 直接下标（补录的档案没有这一列就 KeyError）。
    """
    return p.get("card") or p.get("nickname") or str(fallback or "") or unknown_user(
        p.get("uid", "")
    )


def unknown_user(uid) -> str:
    """「用户{id}」兜底展示名，文案外置（system.json 的 unknown_user）。"""
    return fill(_system("unknown_user", "用户{uid}"), {"uid": str(uid or "")})


def pick(seq):
    return random.choice(seq) if seq else ""


def num_of(d, key: str, default: float = 0.0) -> float:
    """读事件条目里的数值字段，容忍 null / 字符串 / 缺失 / NaN。

    必须走这里而不是 d.get(key, default)：JSON 写成 `"health": null` 时
    `.get` 返回 None（默认值不生效），随后的 `float(None)` 直接抛 TypeError。
    resources 下的 JSON 由运维手改是设计内用法，事件条目的字段一律先归一。
    同一份文件里此前的两种态度（life2.meeting_room 专门 try/float，而
    _apply_checkin_event 直接相加）正是为了收口到这一个函数。
    """
    v = d.get(key, default) if isinstance(d, dict) else None
    if v is None:
        v = default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if f != f else f  # NaN 也回默认值


def int_of(d, key: str, default: int = 0) -> int:
    """num_of 的取整版本（经验值/次数这类整数计数用）。"""
    return int(num_of(d, key, default))


class _SafeVars(dict):
    """format_map 用的宽容字典：未提供的占位保持原样而不是抛 KeyError。"""

    def __missing__(self, key):
        return "{" + key + "}"


def fill(text, variables: dict | None = None) -> str:
    """把外置文案里的 {占位} 替换成实际数值。

    文案在 resources/**.json 里由运维维护，可能少写或多写占位符，
    因此用宽容映射：缺变量原样保留，多余的 { } 也不会让指令崩。
    """
    s = str(text or "")
    if not variables:
        return s
    try:
        return s.format_map(_SafeVars(variables))
    except (IndexError, ValueError, KeyError, TypeError, AttributeError):
        # IndexError/ValueError：裸 { } 或非法格式说明符；
        # KeyError/TypeError/AttributeError：文案写成 {a[0]} / {p.cash} 这类
        # 取下标/取属性的形式时，__missing__ 兜不住，format_map 会直接抛。
        # 文案由运维手改，任何写法错误都只能降级为原样输出，绝不打断指令。
        return s


def fill_all(seq, variables: dict | None = None) -> list[str]:
    """对文案列表逐条 fill，并丢掉空行。"""
    return [line for line in (fill(x, variables) for x in seq or ()) if line]


def pick_filled(seq, variables: dict | None = None) -> str:
    """随机取一条外置文案并填充占位。"""
    return fill(pick(seq), variables)


def weighted_layoff(risk: float, scale: float) -> bool:
    return random.random() < risk * scale


# ==================================================================
# 玩家状态与冷却辅助工具（统一在此管理，避免跨模块私有调用与重复定义）
# ==================================================================


async def load_player(db, gid, uid, nickname, cfg):
    start_cash = float(cfg_get(cfg, "start_cash", 800))
    p = await asyncio.to_thread(db.get_player, gid, uid, nickname, start_cash)
    # 段位是积分的派生值：建档时 DEFAULTS 写死「菜鸟」，与 start_rank_score
    # 对应的段位可能不一致（如积分 1000 应为「老油条」）。这里统一重算，
    # 保证本次调用返回的 rank_tier 与 rank_score 一致。注意这只是进程内的
    # 计算结果，不保证落库：resume/payslip/my_bag/my_skills/rank_show 这类
    # 纯读指令拿到 p 后直接渲染、不会 save_player，重算值不持久化，下次
    # 读取仍靠这里重新对齐。
    p["rank_tier"] = tier_of(int(p["rank_score"]))
    return p


def cd_left(p: dict, key: str) -> float:
    return float(p.get("_cds", {}).get(key, 0)) - time.time()


# 长效标记（护盾卡/拉屎卡这类「挂着等触发」的道具状态）的名义有效期。
# 它们和冷却共用 player_cds 表，而该表的清理条件是「expires_at 已过期就删」，
# 所以标记必须写成一个远未来的时间戳，否则会被每日清理当成过期冷却删掉
# —— 玩家花钱买的道具在下一次清理后凭空消失，且无任何日志。
FLAG_TTL_SECONDS = 10 * 365 * 86400


def flag_set(p: dict, key: str):
    """挂上一个长效道具标记（消耗方按真假判定，不看剩余时间）。"""
    cd_set(p, key, FLAG_TTL_SECONDS)


def cd_set(p: dict, key: str, seconds: float | int):
    """写入冷却到期时间戳。

    调用方多用「配置值 × 单位」拼秒数（如 teambuild_cooldown_days*86400）。
    若运维在 WebUI/JSON 里把这类配置误填成字符串，"7"*86400 会拼出 604800 字符
    的"秒数"而不是 7 天。这里统一强转数字兜底：解析失败按 0 处理（冷却立即可用），
    绝不把拼接产物当时间戳写进 _cds。
    """
    try:
        n = float(seconds)
    except (TypeError, ValueError):
        n = 0.0
    if n < 0:  # 负秒数同理视为无冷却
        n = 0.0
    p.setdefault("_cds", {})[key] = int(time.time()) + int(n)


def is_exempt(cfg, uid) -> bool:
    ids = [str(x) for x in (cfg_get(cfg, "cooldown_exempt_users") or [])]
    return str(uid) in ids


def clamp_status(p: dict):
    p["health"] = round(clamp(float(p["health"]), 0, 100), 1)
    p["mind"] = round(clamp(float(p["mind"]), 0, 100), 1)


def bag_of(p: dict) -> dict[str, int]:
    """读玩家背包：{道具key: 数量}，只保留数量为正的条目。

    items 列存的是 JSON 字符串。写入侧（db._parse_items）早就对损坏值兜底了，
    读取侧此前是裸 json.loads：一行坏数据就让「#我的背包」「#使用」「#购买」
    三条指令永久报异常。数量也统一转 int —— 字符串数量会让 `cnt > 0` 与
    `bag[k] -= 1` 直接 TypeError。
    """
    raw = p.get("items")
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
    out: dict[str, int] = {}
    for k, v in data.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0 and str(k):
            out[str(k)] = n
    return out


def achievements_of(p: dict) -> set[str]:
    """读玩家已解锁成就集合。与 bag_of 同构的容错：achievements 列存的是 JSON
    字符串，裸 json.loads 会让一行坏数据永久打断「#我的成就」「#佩戴称号」；
    非 list（如 `{}`）也会被 set() 静默解读成键集合，语义错但不报错。
    """
    raw = p.get("achievements")
    data = raw
    if not isinstance(data, list):
        try:
            data = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return set()
        if not isinstance(data, list):
            return set()
    return {str(x) for x in data if str(x)}


# ==================================================================
# 用户输入解析：一律走这里，绝不把原始文本喂给 int()/float()
# ==================================================================

# 金额/数量的最大位数。Python 3.11+ 对 int() 有 4300 位上限，
# int("1" * 5000) 会直接抛 ValueError 打断指令，所以必须先限长再转换。
MAX_ARG_DIGITS = 12
# 单笔金额上限：防止 1e18 这类数值把经济系统冲垮
MAX_AMOUNT = 1e12


# 从消息文本里抓「数字」的统一口径。必须带上小数点与紧跟空白的负号，否则
# 「#转账 @群友 500.5」会被抓成 500（吞掉 .5 且不报错）、「-500」会被抓成
# 500（静默翻转符号）。前置断言 (?<![\w.]) 保证不会把昵称里的「小明-1」当成
# 负数、也不会把 1.5 的小数部分再单独抓一次。
# 抓到非法值不是问题：金额一律交给 parse_int / parse_amount 校验，它们会回一句
# 格式提示，比静默改写用户意图好得多。
NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def numbers_in(text) -> list[str]:
    """按 NUM_RE 抓出文本里的所有数字串（保留小数与负号）。"""
    return NUM_RE.findall(str(text or ""))


def is_num(text) -> bool:
    """text 是否为「能安全喂给 int() 的十进制数字串」。

    不能用 str.isdigit()：它对上标/圈数字等 Unicode 数字也返回 True，而
    int('²') 直接抛 ValueError——群里发一句「#购买 ²」就能让指令报异常。
    isdecimal() + isascii() 只放过 0-9，再限长度避开 int() 的位数上限。
    """
    s = str(text if text is not None else "").strip()
    return bool(s) and s.isdecimal() and s.isascii() and len(s) <= MAX_ARG_DIGITS


def parse_int(text, default=None, lo: int | None = None, hi: int | None = None):
    """安全解析用户输入的非负整数；非法/超长返回 default。"""
    s = str(text if text is not None else "").strip()
    if not is_num(s):
        return default
    v = int(s)
    if lo is not None and v < lo:
        return default
    if hi is not None and v > hi:
        return default
    return v


def parse_amount(text, default=None, lo: float = 0.01, hi: float = MAX_AMOUNT):
    """安全解析用户输入的金额（支持小数）；非法/越界返回 default。"""
    s = str(text if text is not None else "").strip()
    if not s or len(s) > MAX_ARG_DIGITS + 3:
        return default
    try:
        v = float(s)
    except ValueError:
        return default
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return default
    if v < lo or v > hi:
        return default
    return round(v, 2)


# ==================================================================
# 跨模块高频提示文案（收敛到这里，改文案只改一处）
# ==================================================================


def not_in_game(action: str) -> str:
    """「对方还没入档」的通用提示。6 处社交/转账/借钱/带饭/对线共用同一前缀。

    文案走 resources/texts/system.json 的 not_in_game（缺键回退默认），
    用 _system 惰性加载避免给逻辑层引入顶层 gamedata 依赖。
    """
    return fill(
        _system("not_in_game", "对方还没有加入游戏（让 TA 先发一次「#上班」），{action}"),
        {"action": action},
    )


def no_record(kind: str) -> str:
    """「还没有 X」建档提示。简历/档案/参赛记录等共用同一后缀。

    文案走 resources/texts/system.json 的 no_record（缺键回退默认）。默认值
    必须与 system.json 的 no_record 逐字一致：本轮的静态漂移扫描器（见
    tests/test_audit_round4.py 的 TestTextDefaultsDoNotDrift）会把这一对值
    直接比对，一边改了另一边没跟就是红的。

    这里原先写成「你还没有{kind}…」，比 JSON 多一个「你」。因为 JSON 里一直
    有这个键，运行期取的是 JSON 的值，多出来的那个字只在「运维把键删了/库坏
    掉」时才浮现 —— 正是这类 fallback 默认值最典型的漂移形态。
    """
    return fill(_system("no_record", "还没有{kind}，先发一次「#上班」建档吧"), {"kind": kind})


def _system(key: str, default: str) -> str:
    """读 system.json 的单条固定文案（惰性加载，与 _tier_tables 同构）。"""
    from . import gamedata as gd

    return gd.s("system", key, default)
