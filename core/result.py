"""统一的指令返回结构。"""

from typing import Any


def R(
    tmpl: str | None = None,
    data: dict | None = None,
    text: str = "",
    err: str | None = None,
    img: str | None = None,
) -> dict[str, Any]:
    """构造一条指令结果。

    优先级：err > img > tmpl+data（渲染） > text。
    """
    return {"err": err, "tmpl": tmpl, "data": data or {}, "text": text, "img": img}
