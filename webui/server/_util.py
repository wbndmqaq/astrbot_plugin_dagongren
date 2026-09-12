"""WebUI 侧的小工具：JSON 响应、JWT jti 提取、文案库清单。

从 _const.py 拆出来（原来常量与函数混在一个模块里）。本模块只依赖 _const，
不依赖任何 Mixin，也不 import core。
"""

import json
import re

import jwt
from aiohttp import web

from ._const import (
    COOKIE,
    JWT_ALG,
    JWT_ISSUER,
    TEXTS_DIR,
    TEXTS_LABELS,
)


def _json(obj, status=200):
    return web.Response(
        text=json.dumps(obj, ensure_ascii=False),
        status=status,
        content_type="application/json",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _jti_from_request(request) -> str:
    """从当前 cookie 解码 JWT 拿 jti；任何异常返回空串。"""
    raw = request.cookies.get(COOKIE, "")
    if not raw:
        return ""
    try:
        # 不过期校验：调用方通常已经 _authed 过，这里只取 jti
        claims = jwt.decode(
            raw,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_iss": False,
            },
        )
    except jwt.PyJWTError:
        return ""
    return str(claims.get("jti", "")) if isinstance(claims, dict) else ""


def _verified_jti(request, secret: str) -> str:
    """验签后从当前 cookie 解码 JWT 拿 jti；验签失败返回空串。

    用于公开路径（如登出）——不能像 _jti_from_request 那样跳过签名，
    否则伪造 cookie 即可针对任意已知 jti 撤销会话。
    """
    raw = request.cookies.get(COOKIE, "")
    if not raw or not secret:
        return ""
    try:
        claims = jwt.decode(
            raw,
            secret,
            algorithms=[JWT_ALG],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "jti"]},
        )
    except jwt.PyJWTError:
        return ""
    return str(claims.get("jti", "")) if isinstance(claims, dict) else ""


def texts_catalog() -> list[dict]:
    """文案库分类清单：[{"name": 库名, "label": 中文标签或空串}]，按库名排序。

    目录不可读时返回空列表（面板退化成只有「公司」，不会 500）。
    只放行 [a-z0-9_]+ 的库名，与 _json_get/_json_save 的白名单一致 ——
    面板拿着这个名字去拼 URL，不放行就是拒绝。
    """
    try:
        names = sorted(
            p.stem for p in TEXTS_DIR.glob("*.json") if re.fullmatch(r"[a-z0-9_]+", p.stem)
        )
    except OSError:
        return []
    return [{"name": n, "label": TEXTS_LABELS.get(n, "")} for n in names]
