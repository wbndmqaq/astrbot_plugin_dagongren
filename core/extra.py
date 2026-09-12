"""扩展玩法（facade）：年终奖、技能、社交、成就称号、红包、刮刮乐等。

指令层统一 `from ..core import extra` 后按名调用；__all__ 声明 re-export 的
公开面（否则静态检查会把这些转发导入报成"未使用"）。
"""

from .extra_achievement import (
    achievements_def,
    my_achievements,
    set_title,
    unset_title,
)
from .extra_bonus import learn_skill, my_skills, skill_list, year_bonus
from .extra_redpacket import claim_redpacket, send_redpacket
from .extra_scratch import scratch_lottery
from .extra_social import annual_leave, gossip, side_hustle_upgrade, social_network

__all__ = [
    "achievements_def",
    "annual_leave",
    "claim_redpacket",
    "gossip",
    "learn_skill",
    "my_achievements",
    "my_skills",
    "scratch_lottery",
    "send_redpacket",
    "set_title",
    "side_hustle_upgrade",
    "skill_list",
    "social_network",
    "unset_title",
    "year_bonus",
]
