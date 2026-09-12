"""职业系统服务（facade）：求职、打卡、晋升、跳槽、创业等，按域拆分子模块。

指令层统一 `from ..core import career` 后按名调用，无需关心子模块划分。
__all__ 是显式的公开面：既声明 re-export 意图（否则静态检查会报"导入未使用"），
也让「哪些算对外 API」有唯一答案。
"""

from .career_business import company_dividend, create_company
from .career_common import hiring_pool
from .career_growth import my_company, negotiate_salary, promote
from .career_job import find_job, job_hop, resign_job
from .career_work import (
    checkin,
    overtime,
    slack,
    take_comp_leave,
    take_leave,
    write_report,
)

__all__ = [
    "checkin",
    "company_dividend",
    "create_company",
    "find_job",
    "hiring_pool",
    "job_hop",
    "my_company",
    "negotiate_salary",
    "overtime",
    "promote",
    "resign_job",
    "slack",
    "take_comp_leave",
    "take_leave",
    "write_report",
]
