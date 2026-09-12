"""生活扩展服务（门面）：批次命名的历史遗留，实体已按域拆到别的模块。

本文件名里的 “2” 是【开发批次编号】，不是业务域 —— 同一个文件曾经混装了四块
互不相干的玩法。实体现在按域落位、名字可预测：
  - core/life_office.py  开会／帮带饭／回消息／抢会议室／和同事吃饭／帮领导做事／行业峰会
  - core/life_pet.py     养宠物（领养 + 每日互动）
  - core/life_cert.py    考行业证书
  - core/life_travel.py  出去旅游

函数已按域拆分，但文案表仍沿用历史命名 resources/texts/life2.json（改表名 = 换文案，
静态漂移扫描会红）。指令层统一 `from ..core import life2` 后按名调用，这里按原名转出；
__all__ 声明 re-export 的公开面（否则静态检查会把转发导入报成"未使用"）。
"""

from .life_cert import get_cert
from .life_office import (
    boss_task,
    bring_food,
    eat_with,
    meeting,
    meeting_room,
    reply_msg,
    summit,
)
from .life_pet import adopt_pet, pet_interact
from .life_travel import travel

__all__ = [
    "adopt_pet",
    "boss_task",
    "bring_food",
    "eat_with",
    "get_cert",
    "meeting",
    "meeting_room",
    "pet_interact",
    "reply_msg",
    "summit",
    "travel",
]
