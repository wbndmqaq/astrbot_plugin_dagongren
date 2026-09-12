"""加载 resources 下的静态数据与文本 JSON。"""

import json
import logging
import random
import time
from pathlib import Path

try:  # 允许脱离 AstrBot 的脚本单独导入本模块
    from astrbot.api import logger
except ImportError:  # pragma: no cover
    logger = logging.getLogger("shangbanzu.gamedata")

# 自建公司的 players.company 取值 = CUSTOM_BASE + custom_companies.id
# （编码约定属于存储层，这里只做转发，保证全局单一来源）
from .db import CUSTOM_BASE

BASE = Path(__file__).resolve().parent.parent / "resources"
DATA_DIR = BASE / "data"
TEXTS_DIR = BASE / "texts"
TMPL_DIR = BASE / "templates"

_cache: dict = {}
# 已完成过一轮加载的标记：与 _cache 的真假分开。缺了它，一旦某份 JSON 坏掉
# 导致 _cache 停留在 {}，此后【每一条指令】都会在事件循环上重新 glob 两个目录
# 并解析十几个 JSON（load_all 被 gd.t/gd.position/... 约 120 处直接调用）。
_loaded = False


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_dir(directory: Path, prev: dict) -> dict:
    """逐文件加载并容错：坏掉的那一份沿用上次的值，其余照常刷新。

    整体 try/except 会让一个逗号写错就清空全部游戏数据；
    逐文件兜底则只丢失被改坏的那一份，其它玩法照常运行。
    """
    out = {}
    for p in sorted(directory.glob("*.json")):
        try:
            out[p.stem] = _load_json(p)
        except (OSError, ValueError) as e:
            logger.error(f"[上班族物语] 游戏数据 {p.name} 解析失败，沿用上一份：{e}")
            if p.stem in prev:
                out[p.stem] = prev[p.stem]
    return out


def load_all(force: bool = False) -> dict:
    """加载/返回全部静态数据。首次由 main.initialize 在线程里预热。

    运行期调用是纯内存读取；只有 force=True（WebUI 改了 JSON）才真正读盘。
    """
    global _cache, _loaded
    if _loaded and not force:
        return _cache
    data = _load_dir(DATA_DIR, _cache)
    data["texts"] = _load_dir(TEXTS_DIR, _cache.get("texts") or {})
    # 预排序：position()/house() 在榜单里对每个玩家都会调一次，
    # 不该每次都重新 sorted() 一遍。排序在 _load_dir 的逐文件 try/except
    # 之外，但这里用 try 单独兜底：positions.json/houses.json 里任一条目
    # 缺 i 键或 i 不可比时，只丢这两个预排序索引、沿用上一份，而不会像
    # 旧实现那样抛 KeyError 让所有 gd.* 调用整体不可用。
    data["_positions"] = _sorted_by_i(data.get("positions", {}).get("positions", []))
    data["_houses"] = _sorted_by_i(data.get("houses", {}).get("houses", []))
    _cache = data
    _loaded = True
    return _cache


def _sorted_by_i(items):
    """按 i 升序排序；单条目损坏时返回原列表（逐项兜底），而非抛错击穿加载。"""
    try:
        return sorted(items, key=lambda x: int(x["i"]))
    except (KeyError, TypeError, ValueError):
        # 某一条目缺 i 键或 i 不可比：这次不能排序，返回原列表。调用方
        # position()/house() 本身有下标钳制与缺表兜底，不会因未排序而崩溃。
        return list(items)


def companies() -> list[dict]:
    """在招公司列表。

    返回浅拷贝 + 逐项 dict 拷贝：调用方修改列表或内部字典都不会污染缓存。
    """
    return [dict(c) for c in load_all().get("companies", {}).get("companies", [])]


def company_by_id(cid: int) -> dict | None:
    for c in load_all().get("companies", {}).get("companies", []):
        if c["id"] == cid:
            return dict(c)
    return None


def _lost_company() -> dict:
    """公司已删 / 无法解析时的占位公司，字段与静态公司同构。

    显示文案走 texts/system.json（缺键回退默认），不再散落成第二份硬编码。
    做成函数而不是模块级常量：模块级会在 import 期触发 JSON 文件 IO，
    与其它访问器的惰性加载设计不一致。
    """
    return {
        "id": 1,
        "name": s("system", "lost_company_name", "失联企业"),
        "tag": s("system", "lost_company_tag", "倒闭"),
        "salary": 3500.0,
        "intensity": 5.0,
        "risk": 0.0,
        "min_exp": 0,
        "desc": s("system", "lost_company_desc", "原公司已解散"),
        "perks": [],
    }


def custom_company(db, cid: int) -> dict | None:
    """把 custom_companies 行包装成与静态公司同构的 dict。"""
    if db is None or int(cid) < CUSTOM_BASE:
        return None
    custom_id = int(cid) - CUSTOM_BASE
    cc = db.get_custom_company(custom_id)
    if not cc:
        return None
    return {
        "id": int(cid),
        "tag": cc.get("tag") or s("career_business", "custom_company_tag", "自建企业"),
        "name": cc["name"],
        "salary": float(cc.get("salary", 6000.0)),
        "intensity": 5.0,
        "risk": 0.01,
        "min_exp": 0,
        "desc": f"群友自建公司，老板：{cc.get('boss_uid')}",
        "perks": ["自建福利", "企业分红池"],
        "is_custom": True,
        "custom_id": custom_id,
        "boss_uid": str(cc.get("boss_uid") or ""),
        "balance": float(cc.get("balance") or 0),
    }


def resolve_company(cid, db=None) -> dict | None:
    """统一的公司解析入口：静态公司与自建公司都能解析。

    简历/工资条/同事录/职级榜/考评一律走这里，否则自建公司的员工
    会因为 company_by_id 返回 None 而在各处显示成「失业」。
    未就业（-1）返回 None；公司已被删除/越界时回退「失联企业」占位，防止
    None 下标崩溃。

    兜底链里【不能】再挂 company_by_id(1)：id=1 是真实存在的公司，只要它在，
    后面的「失联企业」就永远取不到，被删档/越界的玩家会被显示成那家真公司，
    同时与 display_company 的「失联企业」两处对同一情形给出不同答案。
    """
    cid = int(cid)
    if cid < 0:
        return None
    if cid >= CUSTOM_BASE:
        return custom_company(db, cid) or _lost_company()
    return company_by_id(cid) or _lost_company()


def company_names(custom_rows=()) -> dict[int, str]:
    """{players.company 取值: 公司名} 映射。

    榜单/名录这类要展示几十个玩家的场景用它，一次查出本群自建公司即可，
    不必对每个玩家单独查库。custom_rows 为 db.custom_companies_of_group 的结果。
    """
    m = {int(c["id"]): str(c["name"]) for c in load_all().get("companies", {}).get("companies", [])}
    for r in custom_rows or ():
        m[CUSTOM_BASE + int(r["id"])] = str(r["name"])
    return m


def display_company(cid, names: dict[int, str], jobless: str | None = None) -> str:
    """按映射表取展示用公司名；未就业返回 jobless，公司已删返回「失联企业」。

    与 resolve_company 的兜底同源（都走 _lost_company），同一情形不会两处不同名。
    jobless 缺省时读 texts/system.json，不再在代码里留第二份「无业」。
    """
    cid = int(cid)
    if cid < 0:
        return jobless if jobless is not None else s("system", "jobless", "无业")
    return names.get(cid) or _lost_company()["name"]


def positions() -> list[dict]:
    """职级表（按 i 升序）。排序在 load_all 里做过，这里只出拷贝。"""
    return [dict(p) for p in load_all().get("_positions", [])]


# 表整体缺失时的兜底行。字段必须与 positions.json / houses.json 的真实字段
# 完全一致：调用方普遍用 h["recover"] / nxt["cost"] 这类直接下标（因为真实
# 数据一定有这些键），缺一个键就会在「JSON 被改坏」这条本该降级的路径上
# 变成 KeyError，把兜底的意义反过来。
# 显示名读 texts/system.json：同一概念此前在代码、模板、JSON 里各写一份
# （群租房 / 桥洞风水宝地 / 无业游民），改一处另两处不会跟着变。
def _fallback_position() -> dict:
    return {
        "i": 0,
        "title": s("system", "fallback_position_title", "无业游民"),
        "mult": 1.0,
        "need": 0,
        "cost": 0,
    }


def _fallback_house() -> dict:
    return {
        "i": 0,
        "name": s("system", "fallback_house_name", "桥洞风水宝地"),
        "rent": 0.0,
        "recover": 0,
        "deposit": 0,
        "desc": "",
    }


def position(i: int) -> dict:
    pos = load_all().get("_positions", [])
    if not pos:
        return _fallback_position()
    i = max(0, min(len(pos) - 1, int(i)))
    return dict(pos[i])


def houses() -> list[dict]:
    """房产表（按 i 升序）。排序在 load_all 里做过，这里只出拷贝。"""
    return [dict(h) for h in load_all().get("_houses", [])]


def house(i: int) -> dict:
    hs = load_all().get("_houses", [])
    if not hs:
        return _fallback_house()
    i = max(0, min(len(hs) - 1, int(i)))
    return dict(hs[i])


def owned_house_index() -> int:
    """「自购房」在 houses 表里的下标（houses.json 中 owned=true 的那一项）。

    买房逻辑原先写死下标 7 与名称「自购小窝」，改 JSON 就会错位；
    改成按标记查找，找不到则退回最后一项（最贵的那档）。
    """
    hs = load_all().get("_houses", [])
    for h in hs:
        if h.get("owned"):
            return int(h.get("i", 0))
    return int(hs[-1]["i"]) if hs else 0


def meals() -> dict[str, dict]:
    """{吃法名: {cost, health, mind, key}}（数值表见 resources/data/meals.json）。"""
    return {str(m["name"]): dict(m) for m in load_all().get("meals", {}).get("meals", [])}


def commute_modes() -> dict[str, dict]:
    """{通勤方式: {cost, health, mind, late_rate}}（resources/data/commute.json）。"""
    return {str(c["name"]): dict(c) for c in load_all().get("commute", {}).get("commute", [])}


def commute_mode(name: str) -> dict:
    """取通勤方式；未知名称回退到表中第一项，避免 KeyError。"""
    modes = commute_modes()
    if not modes:
        return {"name": "", "cost": 0.0, "health": 0.0, "mind": 0.0, "late_rate": 0.0}
    return modes.get(str(name)) or next(iter(modes.values()))


def commute_name(name: str) -> str:
    """展示用通勤方式名：空/未知回退到 commute.json 第一项，而不是写死「地铁」。"""
    return str(commute_mode(name).get("name") or "")


def certs() -> dict[str, dict]:
    """{证书名: {cost, exp}}（resources/data/certs.json）。"""
    return {str(c["name"]): dict(c) for c in load_all().get("certs", {}).get("certs", [])}


def skills() -> dict[str, dict]:
    """{技能名：{cost, exp}}（resources/data/skills.json）。"""
    return {str(s["name"]): dict(s) for s in load_all().get("skills", {}).get("skills", [])}


def scratch_table() -> tuple[list[dict], dict]:
    """刮刮乐奖项表 (中奖档位列表, 未中奖档位)。

    金额以「相对售价的倍数」存储，返奖率由调用方按配置缩放，
    这样运维改售价不会连带破坏返奖率。
    """
    data = load_all().get("scratch", {}) or {}
    return [dict(x) for x in data.get("prizes", [])], dict(data.get("lose", {}))


def shop_items() -> list[dict]:
    return [dict(s) for s in load_all().get("shop", {}).get("shop", [])]


def opponents() -> list[dict]:
    """卷王大赛对手池（resources/data/opponents.json）。"""
    return [dict(o) for o in load_all().get("opponents", {}).get("opponents", [])]


def rank_events() -> list[dict]:
    return [dict(e) for e in load_all().get("rankevents", {}).get("events", [])]


def tier_names() -> list[str]:
    """段位名称表（resources/data/rankevents.json 的 tiers，logic.tier_of 的单一来源）。"""
    raw = load_all().get("rankevents", {}).get("tiers") or []
    return [str(x) for x in raw]


def tier_scores() -> list[int]:
    """段位积分阈值表（resources/data/rankevents.json 的 tier_scores）。"""
    raw = load_all().get("rankevents", {}).get("tier_scores") or []
    return [int(x) for x in raw]


def side_hustle_titles() -> list[str]:
    """副业等级称号表（resources/data/workstations.json 中的 side_hustle_titles）。

    替代 core/extra.py 里硬编码的 names 列表；运维改 JSON 即可换文案。
    """
    raw = load_all().get("workstations", {}).get("side_hustle_titles") or []
    return [str(x) for x in raw]


def workstations() -> list[dict]:
    """工位配置（resources/data/workstations.json 中的 workstations）。"""
    return [dict(w) for w in load_all().get("workstations", {}).get("workstations", [])]


def pets() -> list[dict]:
    """宠物表（resources/data/pets.json）：type/cost/mind_bonus/icon/action/desc。"""
    return [dict(x) for x in load_all().get("pets", {}).get("pets", [])]


def pet_action_aliases() -> list[str]:
    """与会话宠物无关的通用互动别名（pets.json 的 actions_extra）。

    此前硬编码在 handlers/life2_cmds.py 的 _pet_action_pattern 里，而该函数
    的 docstring 声称互动名「同样来自 pets.json」。
    """
    raw = load_all().get("pets", {}).get("actions_extra") or []
    return [str(x) for x in raw if str(x).strip()]


def pet_of(pet_type: str) -> dict | None:
    """按 type 取宠物；未知返回 None。图标/互动指令名都在 JSON 里。"""
    for x in load_all().get("pets", {}).get("pets", []):
        if str(x.get("type")) == str(pet_type):
            return dict(x)
    return None


def pet_icon(pet_type: str) -> str:
    return str((pet_of(pet_type) or {}).get("icon") or "🐾")


def review_grades() -> list[dict]:
    """年终考评档位表（resources/data/review.json）。

    档位名/配色/图标是展示数据，阈值与奖金倍数只存"配置键 + 默认值"，
    实际数值仍由插件配置决定（运维调数值不必改 JSON，改文案不必改代码）。
    最后一项是兜底档（threshold_key 为 null）。
    """
    raw = load_all().get("review", {}).get("grades") or []
    return [dict(g) for g in raw]


def year_bonus_tiers() -> list[dict]:
    """年终奖档位表（resources/data/yearbonus.json），与 review_grades 同一范式。"""
    raw = load_all().get("yearbonus", {}).get("tiers") or []
    return [dict(t) for t in raw]


def rankings() -> list[dict]:
    """排行榜定义（resources/data/rankings.json）：key/name/unit/aliases。

    榜单名、单位、触发别名此前分散在 core/social.py 的两张 dict、
    handlers/system_cmds.py 的 RANK_KINDS 与路由正则四处，加一个榜要改四处。
    """
    raw = load_all().get("rankings", {}).get("rankings") or []
    return [dict(r) for r in raw]


def lottery_rules() -> dict:
    """双色球玩法（resources/data/lottery.json）：号码池、每注红球个数、奖级表。"""
    return dict(load_all().get("lottery", {}) or {})


def match_opponent(score: int) -> dict:
    """按积分就近匹配一名 NPC 对手。

    兜底字典必须与 opponents.json 的条目同构：social.rank_join 会读 effect
    渲染面板，而渲染发生在积分/冷却/出场费都已落库之后 —— 少一个键就是
    「状态已提交、指令报异常」。o.get 同理：单条目缺 score 也不该炸。
    """
    pool = load_all().get("opponents", {}).get("opponents", [])
    near = [o for o in pool if abs(float(o.get("score") or 0) - int(score)) <= 300]
    if not near and not pool:
        return {
            "name": s("social", "opponent_fallback_name", "神秘选手"),
            "score": int(score),
            "avatar": "🙂",
            "effect": "",
        }
    out = dict(random.choice(near or pool))
    out.setdefault("effect", "")
    out.setdefault("name", s("social", "opponent_fallback_name", "神秘选手"))
    out.setdefault("score", int(score))
    return out


def news_of_day() -> str:
    heads = t("news", "headlines")
    if not heads:
        return ""
    rng = random.Random(int(time.strftime("%Y%m%d")))
    return rng.choice(heads)


def t(name: str, key: str):
    """取文本列表：t('work','slack_ok')。返回副本，避免调用方就地修改缓存。

    若 JSON 里该键被误配成字符串，list("你好") 会拆成单字符 ['你','好']，
    直接 isinstance 守卫掉，保持与其它调用方一致的「列表或空列表」语义。
    """
    v = load_all()["texts"].get(name, {}).get(key, [])
    return list(v) if isinstance(v, list) else []


# 已经报过「回退到 Python 默认值」的 (表, 键)。
#
# 只在【真的命中默认值】时才写日志：gd.s 是极热路径（一次出图几十次调用），
# 正常命中 JSON 时打日志会把日志冲爆。
# 集合大小上界 = resources/texts 全部键数（33 个库约 1600 个键），与调用次数无关；
# 就算运维把整个 texts 目录删了，也只是最多 1600 条 warning 而不是无限增长。
_fallback_warned: set[tuple[str, str]] = set()


def _note_text_fallback(table: str, key: str) -> None:
    """文案缺失/为空而回退到代码默认值：同一 (表, 键) 只报一次。"""
    tag = (table, key)
    if tag in _fallback_warned:
        return
    _fallback_warned.add(tag)
    logger.warning(
        f"[上班族物语] 文案缺失：texts/{table}.json 的 {key} 不存在或为空，"
        "已回退到代码内置默认值（游戏内会显示旧文案，请检查该 JSON）"
    )


def s(name: str, key: str, default: str = "") -> str:
    """取单条固定文案（非随机）：texts/<name>.json 里的一个字符串或单元素列表。

    面板标题/固定提示这类「结构文案」也从 JSON 读，运维可改而不动代码；
    JSON 缺键或损坏时回退到 default（即原硬编码值），行为保持稳定。

    回退到 default 时会打一条 warning（同一条只打一次，见 _note_text_fallback）：
    这是「Python 里的第二份文案」在生产中唯一会自己暴露的机会 —— 运维把
    resources/texts 里的键删了/改了，游戏内会静默显示代码里的旧文案，
    没有这条日志就只能等玩家发现。
    """
    obj = load_all()["texts"].get(name, {}) or {}
    # 优先取顶层键；兼容集中存在 titles 子对象里的写法
    v = obj.get(key)
    if v is None:
        v = (obj.get("titles") or {}).get(key, "")
    out = (str(v[0]) if v else "") if isinstance(v, list) else (str(v) if v else "")
    if not out:
        _note_text_fallback(name, key)
        return default
    return out


def atmosphere_events() -> list[dict]:
    """今日职场氛围事件池（resources/texts/atmosphere.json）。

    返回副本，调用方不可就地修改缓存。缺失时返回空列表，由调用方兜底。
    """
    raw = load_all().get("texts", {}).get("atmosphere", {}).get("events") or []
    return [dict(e) for e in raw]


def achievements() -> list[dict]:
    """成就表（resources/texts/achievements.json）：{id,name,desc,metric,threshold}。

    判定口径（metric）与阈值（threshold）也在 JSON 里，运维改阈值不必改代码，
    desc 里的数字也不会和实际行为脱节。
    """
    raw = load_all().get("texts", {}).get("achievements", {}).get("items") or []
    return [dict(a) for a in raw]


def template_path(name: str) -> Path:
    return TMPL_DIR / f"{name}.html"


def template_texts() -> dict:
    """HTML 模板里用到的文案（resources/texts/templates.json）。

    面板与指令层的文案早已外置，但 resources/templates/*.html 一直是硬编码中文的
    重灾区（8 个模板约 60 处，resume.html 一个就 33 处，还写死了一个来自
    houses.json 的数据值）。main._render 每次渲染都把这份注入模板数据，模板改用
    {{ t.key }} 读取，运维改文案不必再动 HTML，也不会与 JSON 里的说法脱节。
    """
    return dict(load_all().get("texts", {}).get("templates") or {})
