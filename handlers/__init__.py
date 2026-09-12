"""指令业务函数包（声明式路由）。

本包各模块维护「(ctx, event) -> R dict」形式的指令业务函数，并在各自
末尾声明 ROUTES 路由列表。main.py 通过 install() 把 ALL_ROUTES 统一挂载
到插件类上（装饰时重写 __module__ 归属主模块），业务逻辑按域拆分在
各 *_cmds.py 中维护。
"""

from . import (
    backup_cmds,
    battle_cmds,
    career_cmds,
    company_cmds,
    extra2_cmds,
    extra_cmds,
    finance_cmds,
    life2_cmds,
    life_cmds,
    lottery_cmds,
    market_cmds,
    push_cmds,
    review_cmds,
    stock_cmds,
    system_cmds,
)
from .base import Route, install

ALL_ROUTES: list[Route] = [
    *system_cmds.ROUTES,
    *career_cmds.ROUTES,
    *company_cmds.ROUTES,
    *battle_cmds.ROUTES,
    *extra_cmds.ROUTES,
    *extra2_cmds.ROUTES,
    *life_cmds.ROUTES,
    *life2_cmds.ROUTES,
    *lottery_cmds.ROUTES,
    *market_cmds.ROUTES,
    *finance_cmds.ROUTES,
    *push_cmds.ROUTES,
    *review_cmds.ROUTES,
    *stock_cmds.ROUTES,
    *backup_cmds.ROUTES,
]

__all__ = ["ALL_ROUTES", "Route", "install"]
