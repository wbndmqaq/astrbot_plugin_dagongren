"""shop_page 等功能（由 life.py 拆分）。"""

import asyncio
import json

from . import gamedata as gd
from . import logic
from .career_common import make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    """取 life_items.json 文案并填充占位（缺变量原样保留，见 logic.fill）。"""
    return tt("life_items", key, variables)


_title = make_title("life_items")


async def shop_page():
    items = gd.shop_items()
    rows = []
    for it in items:
        eff = []
        if it.get("type") == "card":
            eff.append(_t("val_card_tag"))
        else:
            # 数值可能是负的（如「高端体检套餐」精神 -3）：写死 + 会渲染成
            # 「精神+-3」。用 :+g 让符号跟着数值走，与 shop_buy 保持一致。
            if it.get("health"):
                eff.append(_t("val_health_delta", {"delta": f"{it['health']:+g}"}))
            if it.get("mind"):
                eff.append(_t("val_mind_delta", {"delta": f"{it['mind']:+g}"}))
        rows.append(
            {
                "id": it["id"],
                "name": it["name"],
                "price": logic.fmt_money(it["price"]),
                "effect": " / ".join(eff) or _t("val_none"),
                "desc": it["desc"],
            }
        )
    return R(
        tmpl="shop",
        data={"items": rows},
        text=_t(
            "text_shop",
            {
                "list": "；".join(
                    _t(
                        "val_shop_item",
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "price": r["price"],
                            "effect": r["effect"],
                        },
                    )
                    for r in rows
                )
            },
        ),
    )


async def shop_buy(db, gid, uid, nickname, keyword, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    kw = (keyword or "").strip()
    if not kw:
        return R(err=_t("err_no_kw"))
    item = None
    if logic.is_num(kw):
        target_id = int(kw)
        item = next((x for x in gd.shop_items() if x["id"] == target_id), None)
    if item is None:
        # 精确名匹配优先
        item = next((x for x in gd.shop_items() if x["name"] == kw), None)
    if item is None:
        # 子串匹配只接受「唯一候选」，否则把全部候选列出来让用户改发
        matches = [x for x in gd.shop_items() if kw in x["name"]]
        if len(matches) == 1:
            item = matches[0]
        elif len(matches) > 1:
            names = " / ".join(f"「{x['name']}」" for x in matches[:6])
            return R(err=_t("err_multi_match", {"kw": kw, "names": names}))
    if item is None:
        return R(err=_t("err_not_in_shop", {"kw": kw}))
    price = float(item["price"])
    if float(p["cash"]) < price:
        return R(
            err=_t("err_balance_short", {"name": item["name"], "price": logic.fmt_money(price)})
        )
    # 恢复量走 num_of：shop.json 里写成 "health": null 时 .get 默认值不生效，
    # float(None) 会 TypeError，而这里已经扣过款、异常会让玩家「花了钱没效果」
    health_gain = logic.num_of(item, "health", 0)
    mind_gain = logic.num_of(item, "mind", 0)
    p["cash"] = round(float(p["cash"]) - price, 2)
    if item.get("type") == "card":
        # 道具卡入背包
        bag = logic.bag_of(p)
        ckey = item.get("card_key", str(item["id"]))
        bag[ckey] = int(bag.get(ckey, 0)) + 1
        p["items"] = json.dumps(bag)
    else:
        p["health"] = round(float(p["health"]) + health_gain, 1)
        p["mind"] = round(float(p["mind"]) + mind_gain, 1)
        logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    eff_text = []
    if item.get("type") == "card":
        eff_text.append(_t("val_card_stored"))
    else:
        if health_gain:
            eff_text.append(_t("val_health_delta", {"delta": f"{health_gain:+g}"}))
        if mind_gain:
            eff_text.append(_t("val_mind_delta", {"delta": f"{mind_gain:+g}"}))
    # desc/name 走 .get：save_player 已经落库，此时再因缺字段抛 KeyError
    # 就是「扣了钱、指令报错」，展示字段一律不该有这种能力
    item_name = str(item.get("name") or "")
    item_desc = str(item.get("desc") or "")
    return R(
        tmpl="panel",
        data={
            "icon": "🛍️",
            "title": f"{_title('title_shop_buy', '已购入')} · {item_name}",
            "accent": "#6fe08c",
            "lines": [item_desc],
            "blocks": [
                {
                    "label": _t("lbl_cost"),
                    "value": _t("val_minus_yuan", {"amount": logic.fmt_money(price)}),
                },
                {"label": _t("lbl_effect"), "value": "，".join(eff_text) or _t("val_none")},
                {"label": _t("lbl_health"), "value": _t("val_status_100", {"cur": p["health"]})},
                {"label": _t("lbl_mind"), "value": _t("val_status_100", {"cur": p["mind"]})},
            ],
        },
        text=_t(
            "text_shop_buy",
            {
                "name": item_name,
                "price": logic.fmt_money(price),
                "desc": item_desc,
            },
        ),
    )


async def my_bag(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    bag = logic.bag_of(p)
    items_meta = {it.get("card_key", str(it["id"])): it for it in gd.shop_items()}
    rows = []
    for k, cnt in bag.items():
        meta = items_meta.get(k) or {"name": k, "desc": _t("unknown_item")}
        rows.append(_t("lines_bag_item", {"name": meta["name"], "cnt": cnt, "desc": meta["desc"]}))
    if not rows:
        return R(err=_t("err_bag_empty"))
    return R(
        tmpl="panel",
        data={
            "icon": "🎒",
            "title": f"{p['nickname'] or uid} {_title('title_my_bag', '的职场道具包')}",
            "accent": "#ffd86f",
            "lines": rows,
            "foot": _t("foot_my_bag"),
        },
        text=_t("text_my_bag", {"list": "\n".join(rows)}),
    )


async def use_item(db, gid, uid, nickname, item_name, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    bag = logic.bag_of(p)
    item_name = (item_name or "").strip()
    if not item_name:
        return R(err=_t("err_use_none"))

    # 匹配道具 key：候选必须【背包里真的有】才算命中。只按名字 break 的话，
    # 「咖啡」若先撞上一张没买的卡就直接报「背包里没有」，而实际持有的另一张
    # 同关键字道具用不了。同时用 dict.fromkeys 去重保持 shop.json 顺序。
    candidates = []
    for it in gd.shop_items():
        if it.get("type") != "card":
            continue
        ckey = it.get("card_key", str(it["id"]))
        if item_name == ckey or item_name in it["name"] or it["name"] in item_name:
            candidates.append((ckey, it))
    owned = [(k, it) for k, it in candidates if bag.get(k, 0) > 0]
    if not owned:
        return R(err=_t("err_use_not_have", {"name": item_name}))
    if len(owned) > 1:
        exact = [(k, it) for k, it in owned if it["name"] == item_name or k == item_name]
        if len(exact) != 1:
            names = " / ".join(f"「{it['name']}」" for _k, it in owned[:6])
            return R(err=_t("err_multi_match", {"kw": item_name, "names": names}))
        owned = exact
    target_key, target_item = owned[0]

    # 消耗道具
    bag[target_key] -= 1
    p["items"] = json.dumps(bag)

    # 触发道具效果：数值变化与状态标记是游戏逻辑（留在代码），
    # 生效后的说明文案走 shop.json 的 use_text（运维可改），
    # 通过 {health}/{mind}/{news}/{layoff_scale} 占位注入实际数值。
    fmt_vars: dict[str, str] = {}
    if target_key == "coffee_pack":
        coffee_health = float(logic.cfg_get(cfg, "coffee_pack_health_bonus", 30))
        coffee_mind = float(logic.cfg_get(cfg, "coffee_pack_mind_bonus", 30))
        p["health"] = round(logic.clamp(float(p["health"]) + coffee_health, 0, 100), 1)
        p["mind"] = round(logic.clamp(float(p["mind"]) + coffee_mind, 0, 100), 1)
        # 只【缩短】加班冷却，绝不清零。原实现是 _cds.pop("ot")，配合「加班无每日
        # 次数上限」构成印钞机：买包(400) → 用包(冷却归零) → 加班(收入≈月薪/22)，
        # 月薪过万后每轮净赚数千元，速率只受打字速度限制。
        cut = max(0.0, float(logic.cfg_get(cfg, "coffee_pack_cd_cut_hours", 1.5))) * 3600
        left = logic.cd_left(p, "ot")
        if left > 0:
            logic.cd_set(p, "ot", max(0.0, left - cut))
        fmt_vars = {
            "health": f"{coffee_health:g}",
            "mind": f"{coffee_mind:g}",
            "cd_cut": logic.fmt_remaining(cut),
        }
    elif target_key == "shield":
        # 必须走 flag_set 写远未来时间戳：直接写 1 会被 cleanup_old_data 的
        # 「删掉已过期冷却」当成 1970 年的过期行删掉 —— 玩家花钱买的护盾在被
        # 真正用上之前就凭空消失，且无任何日志。
        logic.flag_set(p, "shield_active")
    elif target_key == "poop":
        logic.flag_set(p, "poop_active")
    elif target_key == "radar":
        scale = float(logic.cfg_get(cfg, "layoff_scale", 1.0))
        fmt_vars = {"news": gd.news_of_day() or _t("val_no_news"), "layoff_scale": f"{scale:.1f}"}
    msg_lines = logic.fill_all(target_item.get("use_text") or [], fmt_vars)
    if not msg_lines:
        msg_lines = [_t("use_fallback", {"name": target_item["name"]})]

    await asyncio.to_thread(db.save_player, p)
    return R(
        tmpl="panel",
        data={
            "icon": "✨",
            "title": f"{_title('title_use_item', '使用道具')} · {target_item['name']}",
            "accent": "#6fe08c",
            "lines": msg_lines,
            "blocks": [
                {"label": _t("lbl_item_state"), "value": _t("val_consumed_one")},
                {
                    "label": _t("lbl_item_left"),
                    "value": _t("val_count_pieces", {"count": bag[target_key]}),
                },
                {
                    "label": _t("lbl_health_mind"),
                    "value": _t("val_health_mind", {"health": p["health"], "mind": p["mind"]}),
                },
            ],
        },
        text=_t(
            "text_use_item",
            {"name": target_item["name"], "lines": " ".join(msg_lines)},
        ),
    )
