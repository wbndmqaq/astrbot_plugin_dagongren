"""move_house 等功能（由 life.py 拆分）。"""

import asyncio

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 life_housing.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life_housing", key, variables)


_title = make_title("life_housing")


async def move_house(db, gid, uid, nickname, keyword, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    kw = (keyword or "").strip()
    # 「自购房」的下标与名称都来自 houses.json（owned=true 的那一项），
    # 不再把 7 与「自购小窝」写死在代码里
    own_i = gd.owned_house_index()
    own_name = gd.house(own_i)["name"]
    if not kw:
        hs = [h for h in gd.houses() if h["i"] != own_i]
        # 租金是【每次打卡扣一次】，即按天计（见 career_work.checkin），
        # 写成「月租」会让玩家把 1500 元/天的别墅当成 1500 元/月来估算
        lines = [
            _t(
                "lines_house_item",
                {
                    "i": h_["i"],
                    "name": h_["name"],
                    "rent": h_["rent"],
                    "deposit": h_["deposit"],
                    "recover": h_["recover"],
                },
            )
            for h_ in hs
        ]
        return R(
            tmpl="panel",
            data={
                "icon": "🏠",
                "title": _title("title_rental_list", "租房中介 · 房源列表"),
                "accent": "#7fd1ff",
                "lines": lines,
                "foot": (
                    _t("foot_house_owned", {"name": own_name})
                    if int(p.get("house_owned") or 0) == 1
                    else _t("foot_house_rent", {"name": own_name})
                ),
            },
            text=_t(
                "text_rental_list",
                {
                    "list": "、".join(
                        _t(
                            "val_house_brief",
                            {"name": h_["name"], "deposit": h_["deposit"]},
                        )
                        for h_ in hs
                    )
                },
            ),
        )
    if int(p.get("house_owned") or 0) == 1:
        return R(err=_t("err_own_no_rent", {"name": own_name}))
    # 优先数字索引；其它按 houses.json 名称子串匹配（取第一个）。
    # 单一来源 houses.json：以前在 HOUSE_KEYS 里写死的别名表已删除。
    all_houses = gd.houses()
    valid_idx = {int(h_["i"]) for h_ in all_houses}
    target_i = None
    if logic.is_num(kw) and int(kw) in valid_idx:
        target_i = int(kw)
    if target_i is None:
        for h_ in all_houses:
            if kw in h_["name"]:
                target_i = h_["i"]
                break
    if target_i is None:
        return R(err=_t("err_no_kw", {"kw": kw}))
    if target_i == own_i:
        # 自购房只能通过「#买房」全款获得，禁止借「#租房 <下标>」白嫖
        return R(err=_t("err_owned_only", {"name": own_name}))
    cur = gd.house(int(p["house"]))
    tgt = gd.house(target_i)
    if tgt["i"] == cur["i"]:
        return R(err=_t("err_already_live", {"name": cur["name"]}))
    if float(p["cash"]) < tgt["deposit"]:
        return R(err=_t("err_deposit_short", {"name": tgt["name"], "deposit": tgt["deposit"]}))
    p["cash"] = round(float(p["cash"]) - tgt["deposit"], 2)
    p["house"] = tgt["i"]
    p["mind"] = round(min(100.0, float(p["mind"]) + 5), 1)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("life", "house_move"))
    return R(
        tmpl="panel",
        data={
            "icon": "🔑",
            "title": f"{_title('title_move_house', '乔迁之喜')} · {tgt['name']}",
            "accent": "#7fd1ff",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_deposit"),
                    "value": _t("val_minus_yuan", {"amount": tgt["deposit"]}),
                },
                {
                    "label": _t("lbl_daily_rent"),
                    "value": _t("val_daily_rent", {"rent": tgt["rent"]}),
                },
                {
                    "label": _t("lbl_daily_recover"),
                    "value": _t("val_daily_recover", {"recover": tgt["recover"]}),
                },
                {"label": _t("lbl_house_desc"), "value": tgt["desc"]},
            ],
        },
        text=_t(
            "text_move_house",
            {"name": tgt["name"], "deposit": tgt["deposit"], "desc": tgt["desc"]},
        ),
    )


async def buy_house(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    own_i = gd.owned_house_index()
    own = gd.house(own_i)
    if int(p.get("house_owned") or 0) == 1:
        return R(err=_t("err_already_owned"))
    price = float(logic.cfg_get(cfg, "house_price", 100000))
    fund_pool = float(p.get("fund_savings") or 0)
    offset_rate = float(logic.cfg_get(cfg, "house_fund_offset_max_rate", 0.3))
    offset = round(min(fund_pool, price * offset_rate), 2)
    due = round(price - offset, 2)
    fmt = logic.fmt_money
    if float(p["cash"]) < due:
        return R(
            err=_t(
                "err_house_downpay",
                {
                    "price": fmt(price),
                    "offset": fmt(offset),
                    "fund": fmt(fund_pool),
                    "due": fmt(due),
                    "cash": fmt(p["cash"]),
                },
            )
        )
    p["cash"] = round(float(p["cash"]) - due, 2)
    p["house_owned"] = 1
    p["house"] = own_i
    # 公积金只消耗实际抵扣的 offset，多余部分保留（此前整笔清零会造成资金蒸发）
    p["fund_savings"] = round(fund_pool - offset, 2)
    p["mind"] = round(min(100.0, float(p["mind"]) + 30), 1)
    await asyncio.to_thread(db.save_player, p)
    name = logic.name_of(p, uid)
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_buy_house"),
        _t("ev_house_buy", {"name": name, "house": own["name"], "due": fmt(due)}),
    )
    await asyncio.to_thread(
        db.add_transaction,
        gid,
        uid,
        _t("kind_buy_house"),
        -due,
        _t("house_buy_note", {"offset": fmt(offset)}),
    )
    line = logic.pick(gd.t("life", "house_owned_texts"))
    return R(
        tmpl="panel",
        data={
            "icon": "🏡",
            "title": _title("title_buy_house", "恭喜！你在这个城市有家了"),
            "accent": "#ffd86f",
            "lines": [line],
            "blocks": [
                {"label": _t("lbl_house_price"), "value": _t("val_yuan", {"amount": fmt(price)})},
                {
                    "label": _t("lbl_fund_offset"),
                    "value": _t("val_minus_yuan", {"amount": fmt(offset)}),
                },
                {"label": _t("lbl_paid"), "value": _t("val_minus_yuan", {"amount": fmt(due)})},
                {
                    "label": _t("lbl_from_now"),
                    "value": _t("val_from_now", {"recover": f"{own['recover']:g}"}),
                },
            ],
            "foot": own.get("desc") or "",
        },
        text=_t(
            "text_buy_house",
            {"due": fmt(due), "offset": fmt(offset), "line": line},
        ),
    )


async def set_commute(ctx_db, gid, uid, mode):
    modes = gd.commute_modes()
    if not modes:
        # commute.json 空/损坏时 next(iter(modes)) 会抛 StopIteration，
        # 在协程里等于「指令执行异常」而不是一句可读的提示
        return R(err=_t("err_commute_invalid", {"names": ""}))
    names = " / ".join(modes)
    mode = (mode or "").strip()
    if not mode:
        p = await asyncio.to_thread(ctx_db.get_player, gid, uid)
        cur = str(p.get("commute") or next(iter(modes)))
        lines = [
            _t(
                "lines_commute_item",
                {
                    "name": name,
                    "cost": f"{m['cost']:g}",
                    "health": f"{m['health']:+g}",
                    "mind": f"{m['mind']:+g}",
                    "rate": f"{m['late_rate'] * 100:g}",
                },
            )
            for name, m in modes.items()
        ]
        return R(
            tmpl="panel",
            data={
                "icon": "🚇",
                "title": (
                    f"{_title('title_commute_list', '通勤方案')}"
                    + _t("val_commute_cur", {"cur": cur})
                ),
                "accent": "#7fd1ff",
                "lines": lines,
                "foot": _t("foot_commute_list", {"names": names}),
            },
            text=_t("text_commute_list", {"list": "、".join(modes)}),
        )
    if mode not in modes:
        return R(err=_t("err_commute_invalid", {"names": names}))
    p = await asyncio.to_thread(ctx_db.get_player, gid, uid)
    p["commute"] = mode
    await asyncio.to_thread(ctx_db.save_player, p)
    m = modes[mode]
    return R(
        tmpl="panel",
        data={
            "icon": "🚇",
            "title": f"{_title('title_commute_set', '已切换通勤方式')}：{mode}",
            "accent": "#6fe08c",
            "blocks": [
                {
                    "label": _t("lbl_one_way_cost"),
                    "value": _t("val_yuan", {"amount": f"{m['cost']:g}"}),
                },
                {
                    "label": _t("lbl_feel"),
                    "value": _t(
                        "val_commute_feel",
                        {"health": f"{m['health']:+g}", "mind": f"{m['mind']:+g}"},
                    ),
                },
                {
                    "label": _t("lbl_late_rate"),
                    "value": _t("val_late_rate", {"pct": f"{m['late_rate'] * 100:g}"}),
                },
            ],
            "foot": _t("foot_new_route"),
        },
        text=_t("text_commute_set", {"mode": mode}),
    )
