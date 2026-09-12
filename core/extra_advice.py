"""职场生存建议：从 texts/extra2.json 的 career_advice 里随机取一条。

由 core/extra2.py 按业务域拆分而来（“2” 是开发批次编号，不是业务域）。
本函数只有 14 行，仍单独立文件【而不是】并进 extra_social.py：那边一行
`_title = make_title("extra_social")` 是整模块共用的文案表绑定，并过去就得把函数体
里的 _t/_title 全改成 extra2 表（= 换文案、改行为），独立成文件才能一字不动。

文案表仍沿用历史命名 resources/texts/extra2.json。
"""

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 extra2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("extra2", key, variables)


_title = make_title("extra2")


async def career_advice():
    advices = gd.t("extra2", "career_advice")
    tip = logic.pick(advices)
    return R(
        tmpl="panel",
        data={
            "icon": "💡",
            "title": _title("title_advice", "职场生存建议"),
            "accent": "#b48cff",
            "lines": [tip],
            "foot": _t("foot_advice"),
        },
        text=_t("text_advice", {"tip": tip}),
    )
