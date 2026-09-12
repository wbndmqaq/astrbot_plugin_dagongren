"""生活系统服务（facade）：吃饭、健身、住房、通勤、商店、工资条等。

指令层统一 `from ..core import life` 后按名调用；__all__ 声明 re-export 的
公开面（否则静态检查会把这些转发导入报成"未使用"）。
"""

from .life_career import payslip, resume, train_self
from .life_daily import eat, gym, nap, shopping, stall, team_building
from .life_housing import buy_house, move_house, set_commute
from .life_items import my_bag, shop_buy, shop_page, use_item

__all__ = [
    "buy_house",
    "eat",
    "gym",
    "move_house",
    "my_bag",
    "nap",
    "payslip",
    "resume",
    "set_commute",
    "shop_buy",
    "shop_page",
    "shopping",
    "stall",
    "team_building",
    "train_self",
    "use_item",
]
