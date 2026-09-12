"""考行业证书：报名费 + 通过率，通过后加经验与身价（certs.json）。

由 core/life2.py 按业务域拆分而来（“2” 是开发批次编号，不是业务域）。
考证【没有】并进 life_career.py：那边一行 `_title = make_title("life_career")` 是
整模块共用的文案表绑定，搬过去就得逐个调用点改表名（= 换文案、改行为），独立成
文件才能保证函数体一字不动。

文案表仍沿用历史命名 resources/texts/life2.json。
"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 life2.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life2", key, variables)


_title = make_title("life2")


async def get_cert(ctx_db, gid, uid, nickname, cert_name, cfg):
    p = await logic.load_player(ctx_db, gid, uid, nickname, cfg)
    cert_name = (cert_name or "").strip()
    certs = gd.certs()
    if cert_name not in certs:
        return R(err=_t("cert_list", {"list": " / ".join(certs)}))
    cost = float(certs[cert_name]["cost"])
    if float(p["cash"]) < cost:
        return R(err=_t("cert_short", {"cert": cert_name, "cost": logic.fmt_money(cost)}))
    # _skills 正常路径必是 list（_row_to_player 保证），但被外部/旧数据写坏时
    # 可能不是可迭代对象；or [] 兜底，避免 in/append 对 None 抛异常
    skills = p.get("_skills") or []
    if cert_name in skills:
        return R(err=_t("cert_already", {"cert": cert_name}))
    p["cash"] = round(max(0.0, float(p["cash"]) - cost), 2)
    exp_gain = int(certs[cert_name].get("exp") or 30)
    value_rate = float(logic.cfg_get(cfg, "cert_value_bonus_rate", 0.1))
    if random.random() < float(logic.cfg_get(cfg, "cert_pass_rate", 0.55)):
        skills.append(cert_name)
        p["_skills"] = skills
        p["exp"] = int(p["exp"]) + exp_gain
        p["value"] = round(float(p["value"]) * (1 + value_rate), 2)
        await asyncio.to_thread(ctx_db.save_player, p)
        line = logic.pick(gd.t("extra3", "cert_ok"))
        return R(
            tmpl="panel",
            data={
                "icon": "📜",
                "title": f"{cert_name} {_title('title_cert_ok', '认证通过！')}",
                "accent": "#ffd86f",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_exam_fee"),
                        "value": _t("val_minus_yuan", {"amount": logic.fmt_money(cost)}),
                    },
                    {
                        "label": _t("lbl_value"),
                        "value": _t(
                            "val_value_up_pct",
                            {
                                "pct": f"{value_rate * 100:g}",
                                "cur": logic.fmt_money(p["value"]),
                            },
                        ),
                    },
                    {"label": _t("lbl_exp"), "value": _t("val_plus_num", {"num": exp_gain})},
                ],
            },
            text=_t(
                "text_cert_ok",
                {"cert": cert_name, "pct": f"{value_rate * 100:g}"},
            ),
        )
    line = logic.pick(gd.t("extra3", "cert_fail"))
    await asyncio.to_thread(ctx_db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "😞",
            "title": f"{cert_name} {_title('title_cert_fail', '考试未通过')}",
            "accent": "#fc6262",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_exam_fee"),
                    "value": _t("val_minus_yuan_wasted", {"amount": logic.fmt_money(cost)}),
                }
            ],
        },
        text=_t("text_cert_fail", {"cert": cert_name, "line": line}),
    )
