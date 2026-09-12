"""WebUI 管理面板服务（facade）：WebUIServer 由各域 Mixin 组合。

这里【不再】做任何 sys.path 兜底：包名导入（AstrBot 的正常形态）下三级相对
导入本来就能解析，而无条件把插件根目录插进 sys.path 会污染整个宿主进程的
顶层模块解析顺序（core / handlers / webui / main 都会命中本插件）。兜底只在
_deps.py 的 except ImportError 分支里执行——即导入真的失败时才做，且用 append。
"""

from ._admin import _AdminMixin
from ._api import _ApiMixin
from ._auth import _AuthMixin
from ._core import _CoreMixin
from ._profile import _ProfileMixin
from ._serve import _ServeMixin


class WebUIServer(
    _CoreMixin,
    _AuthMixin,
    _ServeMixin,
    _ApiMixin,
    _AdminMixin,
    _ProfileMixin,
):
    """独立端口管理面板：aiohttp + JWT + 服务端会话 + 全量管理 API。"""


__all__ = ["WebUIServer"]
