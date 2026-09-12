"""WebUI 常量（纯数据；函数见 _util.py）。

这里只放常量与「兜底导入」用的 _ensure_core_importable：
_json / _jti_from_request / _verified_jti / texts_catalog 都在 _util.py。

本模块不允许 import core：_deps.py 的三级相对导入失败时（以文件方式加载的
旧版内核）要先调 _ensure_core_importable() 才能退回顶层名 `core`，那一刻
本模块已经加载完毕，不能再依赖任何尚未导入的东西。
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# 路径基准：本模块位于 <插件根>/webui/server/，因此
#   parent      = webui/server
#   WEBUI_DIR   = webui        （index.html / app.js / style.css）
#   PLUGIN_ROOT = 插件根       （_conf_schema.json / resources/）
# 集中在这里定义，避免各 Mixin 各写一份 parent 链——server.py 拆成包时
# 正是因为这些散落的相对层级没同步 +1，面板静态文件与 schema 全部 404。
# ---------------------------------------------------------------------------
WEBUI_DIR = Path(__file__).resolve().parent.parent

PLUGIN_ROOT = WEBUI_DIR.parent

SCHEMA_PATH = PLUGIN_ROOT / "_conf_schema.json"

TEXTS_DIR = PLUGIN_ROOT / "resources" / "texts"

GAMEDATA_DIR = PLUGIN_ROOT / "resources" / "data"


COOKIE = "sbz_session"


TTL = 12 * 3600


JWT_ALG = "HS256"


JWT_ISSUER = "astrbot_plugin_shangbanzu"


CONFIG_HIDDEN_KEYS = {"webui_password", "webui_jwt_secret", "_webui_must_change_password"}


# 面板【只读】的内部状态键：只由插件自身维护（main.py 首次启动置位、改密流程
# 清除），既不该在表单上回显真实值，也不该被「保存配置」写回。
#
# 只把它们放进 CONFIG_HIDDEN_KEYS 是不够的：那只会让下发值变成空串，而该键在
# schema 里是 bool，前端渲染成 checkbox —— 勾选/取消都会提交，一次普通的
# 「保存配置」就把「下次登录必须改密」静默翻转掉（面板上看起来只是保存成功）。
# 所以保存侧也要显式跳过，前端把它渲染成禁用控件。
CONFIG_READONLY_KEYS = {"_webui_must_change_password"}


# ---------------------------------------------------------------------------
# 文案库分类清单
#
# 库清单的唯一来源是 resources/texts/ 目录本身（见 texts_catalog）：以前前端
# app.js 里写死了 8 个库名，而这里有 33 个 json —— 后端 _json_get/_json_save
# 对任意 [a-z0-9_]+ 都放行，纯粹是前端漏了，另外 25 个库在面板上根本选不到。
# 中文标签是「锦上添花」：没登记的库不会被隐藏，前端降级成显示库名，
# 因此新增一个 json 无需改任何代码。
# ---------------------------------------------------------------------------
TEXTS_LABELS = {
    "achievements": "🏆 成就面板",
    "atmosphere": "🌤️ 氛围",
    "backup": "💾 备份",
    "career_business": "🏗️ 创业",
    "career_growth": "🎓 成长",
    "career_job": "🔎 求职",
    "career_work": "💼 职业",
    "company": "🎲 公司事件",
    "duel": "⚔️ 对线",
    "extra": "🧩 扩展Ⅰ",
    "extra2": "🧩 扩展Ⅱ",
    "extra3": "🧩 扩展Ⅲ",
    "extra_achievement": "🏅 成就",
    "extra_bonus": "🧨 年终奖",
    "extra_redpacket": "🧧 红包",
    "extra_social": "👥 社交扩展",
    "finance": "💰 理财",
    "help": "❓ 帮助",
    "life": "🏠 生活",
    "life2": "🏡 生活Ⅱ",
    "life_career": "🧑💼 履历",
    "life_daily": "🍜 日常",
    "life_housing": "🏘️ 住房",
    "life_items": "🎒 道具",
    "lottery": "🎰 彩票",
    "news": "📰 早报",
    "push": "📢 推送",
    "review": "📝 考评",
    "social": "👥 社交",
    "stocks": "📈 股市",
    "system": "⚙️ 系统",
    "templates": "🖼️ 模板",
    "work": "💼 上班",
}


PUBLIC_PATHS = {
    "/",
    "/webui/style.css",
    "/webui/app.js",
    "/api/meta",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/check",
    "/api/auth/change-password",
}


LOGIN_MAX_FAILS = 5


LOGIN_WINDOW = 300


LOGIN_BLOCK = 300


LOGIN_TRACK_MAX = 512


# 全站登录失败闸门（与来源 IP 无关）。见 _CoreMixin._note_global_fail：
# 对端是回环时 _trusted_proxy 认它是同机反代，于是每个请求换一个
# X-Forwarded-For 就换一个 per-IP 限流桶 —— 只有这道不看 IP 的闸门能封住
# 「轮换 XFF 无限试密码」。正常运维同一个窗口内失败 0~1 次，60 次只有爆破
# 才够得到；窗口滑过自动清零（不留任何无界字典）。
LOGIN_GLOBAL_MAX = 60


# JWT HS256 签名密钥的最小长度（字节）：pyjwt 对更短的 HMAC 密钥会告警，
# 而密钥强度直接等于令牌不可伪造性。main.py 生成的是 secrets.token_hex(32)
# = 32 字节 / 64 个十六进制字符。
JWT_SECRET_MIN_BYTES = 32


def _ensure_core_importable():
    """兜底：让「以文件方式加载的旧版内核」能用顶层名 `core` 导入。

    只在 _deps.py 的三级相对导入抛 ImportError 时调用（见那里），因为那时
    插件根目录必须出现在 sys.path 上，`from core import ...` 才解析得到。

    两条刻意的取舍：
    - 用 append 而非 insert(0)：插到队首会抢在其它插件/宿主自己的同名顶层
      模块之前被命中（core / handlers / webui / main 都是极常见的名字），
      等于用一个插件的兜底路径污染整个进程的模块解析顺序；
    - 只在实际走兜底时执行：包名导入（AstrBot 的正常形态）下相对导入本来
      就成功，没有任何理由去动全局 sys.path。
    """
    import sys

    _root = str(PLUGIN_ROOT)
    if _root not in sys.path:
        sys.path.append(_root)
