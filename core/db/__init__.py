"""SQLite 存储层（facade）：DB 类由各域 Mixin 组合。"""

from ._company import _CompanyMixin
from ._const import (
    COLUMNS,
    CUSTOM_BASE,
    MoneyIntegrityError,
    _escape_like,
    _write_lock,
    new_player,
)
from ._core import _CoreMixin
from ._events import _EventsMixin
from ._lottery import _LotteryMixin
from ._money import _MoneyMixin
from ._players import _PlayersMixin
from ._ranking import _RankingMixin
from ._redpacket import _RedpacketMixin
from ._session import _SessionMixin


class DB(
    _CoreMixin,
    _PlayersMixin,
    _RankingMixin,
    _EventsMixin,
    _CompanyMixin,
    _LotteryMixin,
    _MoneyMixin,
    _RedpacketMixin,
    _SessionMixin,
):
    """SQLite 存储层：WAL 模式，每操作独立连接，写操作共享 _write_lock。"""


__all__ = [
    "COLUMNS",
    "CUSTOM_BASE",
    "DB",
    "MoneyIntegrityError",
    "_escape_like",
    "_write_lock",
    "new_player",
]
