"""扩展玩法第二批（门面）：批次命名的历史遗留，实体已按域拆到别的模块。

本文件名里的 “2” 是【开发批次编号】，不是业务域 —— 同一个文件曾经混装了六块
互不相干的功能。实体现在按域落位、名字可预测：
  - core/extra_party.py   年会抽奖
  - core/extra_loan.py    借钱给群友
  - core/extra_advice.py  职场生存建议
  - core/extra_work.py    工位升级／加班餐补贴／年度体检（职场福利）

函数已按域拆分，但文案表仍沿用历史命名 resources/texts/extra2.json（改表名 = 换文案，
静态漂移扫描会红）。指令层统一 `from ..core import extra2` 后按名调用，这里按原名转出；
__all__ 声明 re-export 的公开面（否则静态检查会把转发导入报成"未使用"）。
"""

from .extra_advice import career_advice
from .extra_loan import lend_money
from .extra_party import party_lottery
from .extra_work import health_checkup, overtime_meal, upgrade_workstation

__all__ = [
    "career_advice",
    "health_checkup",
    "lend_money",
    "overtime_meal",
    "party_lottery",
    "upgrade_workstation",
]
