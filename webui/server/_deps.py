"""核心包（core/）的统一导入口，兼容两种加载方式。

- 正常情况：插件作为 AstrBot 包加载，core 是同一个包内的子包，走三级相对导入
  `from ...core import ...`（拿到的是与游戏逻辑同一份模块对象，这是必须的：
  另取一份会让 gd 缓存、_write_lock、DB 锁全部变成两套）。
- 兜底情况：以「文件方式」直接加载旧版内核时 webui 成为顶层包，三级相对导入会
  报 "attempted relative import beyond top-level package"，只能退回顶层名
  `core`。此时才需要把插件根目录放进 sys.path（_ensure_core_importable，且是
  append 而不是 insert(0)）—— 放在包 __init__ 顶部无条件执行会把插件根目录
  塞给整个 AstrBot 进程，让 core/handlers/webui/main 这些极常见的顶层名在
  其它插件的相对导入之前被命中；更糟的是兜底分支一旦执行就会拿到【另一份】
  core（两套 _write_lock 与 DB 锁），那正是本模块存在要避免的事。

调用必须写在这里，不能提到包 __init__：except 分支先 sys.path 兜底、再导入，
顺序颠倒的话 `from core import ...` 当场就 ImportError。

五个 Mixin 原来各自复制了一份 try/except + sys.path 兜底，改一处漏一处；
现在只有这一份，各 Mixin 只留一句 `from ._deps import gd, logic`。
"""

try:  # 包内正常加载：core 与游戏逻辑共用同一份模块
    from ...core import gamedata as gd
    from ...core import logic
    from ...core.db import MoneyIntegrityError
    from ...core.stocks import BatchEditResult
    from ...core.web_auth import hash_password, verify_password
except ImportError:  # pragma: no cover - 仅「以文件方式加载内核」的旧模式会走到
    from ._const import _ensure_core_importable

    _ensure_core_importable()

    from core import gamedata as gd
    from core import logic
    from core.db import MoneyIntegrityError
    from core.stocks import BatchEditResult
    from core.web_auth import hash_password, verify_password

__all__ = [
    "BatchEditResult",
    "MoneyIntegrityError",
    "gd",
    "hash_password",
    "logic",
    "verify_password",
]
