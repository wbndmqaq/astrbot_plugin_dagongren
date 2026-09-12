"""checkin 等功能（由 career.py 拆分）。"""

import asyncio
import random

from . import gamedata as gd
from . import logic
from .career_common import Txs, make_title, tt
from .result import R


def _t(key: str, variables: dict | None = None) -> str:
    return tt("career_work", key, variables)


_title = make_title("career_work")


async def _do_layoff(db, gid, uid, p, comp, cfg, today):
    """裁员分支：结算补偿、落库、记事件，返回给玩家的面板。"""
    severance = round(
        float(p["salary"]) * float(logic.cfg_get(cfg, "layoff_severance_rate", 0.3)),
        2,
    )
    p["company"] = -1
    p["salary"] = 0.0
    p["attend_streak"] = 0
    p["cash"] = round(float(p["cash"]) + severance, 2)
    p["total_earned"] = round(float(p.get("total_earned") or 0) + severance, 2)
    p["work_day"] = today
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    money = logic.fmt_money(severance)
    # 补偿金是收入，必须进流水——这里的不变式是「流水合计 == 现金列的变动额」，
    # 不是「流水合计 == 累计总收入」（后者永远不成立，见下面打卡计薪处的说明）。
    # 补偿金真的进了现金，不记流水就会让 #工资条 的流水合计与「现金+存款」对不上
    #（finance.py 同款口径）。
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_layoff"), severance, _t("layoff_tx_note")
    )
    await asyncio.to_thread(
        db.add_event,
        gid,
        uid,
        _t("kind_layoff"),
        _t(
            "ev_layoff",
            {
                "name": logic.name_of(p, uid),
                "company": comp["name"],
                "severance": money,
            },
        ),
    )
    return R(
        tmpl="panel",
        data={
            "icon": "📦",
            "title": _title("title_layoff", "很遗憾，你被优化了"),
            "accent": "#fc6262",
            "lines": [logic.pick(gd.t("work", "layoff_texts"))],
            "blocks": [
                {
                    "label": _t("lbl_severance"),
                    "value": _t("val_severance", {"severance": money}),
                },
                {
                    "label": _t("lbl_current_status"),
                    "value": _t("val_status_unemployed"),
                },
            ],
        },
        text=_t("text_layoff", {"severance": money}),
    )


def _pay_rent(p, cfg, blocks, lines, txs):
    """扣当日房租；付不起就降级到第 0 档住房。返回本次生效的住房档。

    流水只登记进 txs，由 checkin 在 save_player 之后统一落库：即写即落的话，
    后续 save_player 一旦回滚（资金列下限被击穿会抛 MoneyIntegrityError），
    这笔已提交的流水不会跟着回滚，账面就会凭空多一条房租。
    """
    h = gd.house(int(p["house"]))
    if not (h["rent"] > 0 and logic.cfg_get(cfg, "rent_auto_deduct", True)):
        return h
    if float(p["cash"]) >= h["rent"]:
        p["cash"] = round(float(p["cash"]) - h["rent"], 2)
        blocks.append(
            {
                "label": _t("lbl_today_rent"),
                "value": _t("val_today_rent", {"rent": f"{h['rent']}"}),
            }
        )
        lines.append(logic.pick(gd.t("life", "rent_paid")))
        txs.add(_t("kind_rent"), -h["rent"], h["name"])
        return h
    p["house"] = 0
    p["mind"] = float(p["mind"]) - 10
    h = gd.house(0)
    lines.append(logic.pick(gd.t("life", "rent_failed")))
    blocks.append(
        {
            "label": _t("lbl_house_change"),
            "value": _t("val_house_downgrade", {"name": h["name"]}),
        }
    )
    return h


def _apply_commute(p, txs) -> tuple[str, float, bool]:
    """扣通勤费并结算体感，返回 (展示用方式名, 实付金额, 是否迟到)。

    流水登记进 txs（由调用方在 save_player 之后 flush），本函数因此不需要
    db/gid/uid。
    """
    commute = gd.commute_mode(str(p.get("commute") or ""))
    mode = str(commute["name"])
    c_cost = float(commute["cost"])
    paid = c_cost
    late_rate = float(commute["late_rate"])
    if float(p["cash"]) >= c_cost:
        p["cash"] = round(max(0.0, float(p["cash"]) - c_cost), 2)
        txs.add(_t("kind_commute"), -c_cost, mode)
        p["health"] = float(p["health"]) + float(commute["health"])
        p["mind"] = float(p["mind"]) + float(commute["mind"])
    else:
        # 余额不足付通勤费，按步行处理：不扣费也不加通勤效果。
        # 迟到率必须一起退化：只跳过费用的话，「打车」的低迟到率(0.05)在
        # 没钱时被白拿，反而比老实付钱骑车(0.2)更划算。步行不在
        # commute.json 里，取表中最差的迟到率当步行档，运维改数据即生效。
        mode = _t("commute_walk")
        paid = 0.0
        late_rate = max(
            (float(m.get("late_rate") or 0) for m in gd.commute_modes().values()),
            default=late_rate,
        )
    return mode, paid, random.random() < late_rate


def _apply_checkin_event(p, lines, txs) -> tuple[dict, int]:
    """抽一条打卡随机事件并结算，返回 (事件, 附加经验)。

    字段一律走 logic.num_of：JSON 写成 "health": null 时 .get 会返回 None，
    默认值不生效，随后的 float(None) 直接 TypeError。
    """
    ev = logic.pick(gd.t("work", "checkin_events"))
    if not isinstance(ev, dict):
        ev = {
            "text": _t("ev_checkin_fallback"),
            "cash": 0,
            "health": 0,
            "mind": 0,
            "exp": 0,
        }
    # 事件文本回写进 ev：下游（checkin 的 text 组装、事件池）都直接用 ev["text"]，
    # 在这里补一次缺失键，避免「JSON 漏写 text」把整条打卡打成 KeyError
    ev["text"] = str(ev.get("text") or "")
    cash_before = float(p["cash"])
    p["cash"] = round(max(0.0, cash_before + logic.num_of(ev, "cash", 0)), 2)
    p["health"] = float(p["health"]) + logic.num_of(ev, "health", 0)
    p["mind"] = float(p["mind"]) + logic.num_of(ev, "mind", 0)
    # 记账必须记【实际】变动额：checkin_events 里有 -500 这类扣款，
    # 余额不足时 max(0.0, …) 会把它截断，直接记名义值会让流水出现
    # 「-500」而现金只掉了 100，账面和余额永远对不上。
    actual = round(float(p["cash"]) - cash_before, 2)
    if actual:
        # 正向事件奖金是【收入】，要一并计入累计总收入；扣款事件不计（total_earned
        # 是只增不减的累计列）。口径与 extra2.party_lottery 的「amount > 0 才记」一致。
        # 记流水的原因是它真的动了现金（不变式：流水合计 == 现金变动额），
        # 与「累计总收入」是否等于流水合计无关。
        if actual > 0:
            p["total_earned"] = round(float(p.get("total_earned") or 0) + actual, 2)
        txs.add(_t("kind_work_event"), actual, ev["text"][:40])
    lines.append(ev["text"])
    return ev, logic.int_of(ev, "exp", 0)


def _checkin_blocks(
    p, *, net_pay, insurance, perf, late, penalty_rate, commute_mode, commute_paid, exp_gain
):
    """打卡成功面板的 blocks（纯展示，无副作用）。"""
    rate = f"{round((1 - penalty_rate) * 100):g}"
    income = (
        _t(
            "val_today_income_late",
            {"net": logic.fmt_money(net_pay), "perf": f"{perf:.2f}", "rate": rate},
        )
        if late
        else _t(
            "val_today_income",
            {"net": logic.fmt_money(net_pay), "perf": f"{perf:.2f}"},
        )
    )
    commute_value = (
        f"{commute_mode}"
        + (_t("val_commute_cost", {"cost": logic.fmt_money(commute_paid)}) if commute_paid else "")
        + (_t("val_commute_late") if late else "")
    )
    return [
        {"label": _t("lbl_today_income"), "value": income},
        {
            "label": _t("lbl_insurances"),
            "value": _t(
                "val_insurances",
                {
                    "insurance": logic.fmt_money(insurance),
                    "fund": logic.fmt_money(p["fund_savings"]),
                },
            ),
        },
        {"label": _t("lbl_commute"), "value": commute_value},
        {
            "label": _t("lbl_attend_streak"),
            "value": _t("val_attend_streak", {"days": p["attend_streak"]}),
        },
        {
            "label": _t("lbl_exp"),
            "value": _t("val_exp_gain", {"gain": exp_gain, "exp": p["exp"]}),
        },
        {
            "label": _t("lbl_health_mind"),
            "value": _t("val_health_mind", {"health": f"{p['health']}", "mind": f"{p['mind']}"}),
        },
    ]


async def checkin(db, gid, uid, nickname, cfg):
    """每日打卡：裁员判定 → 房租 → 通勤/迟到 → 计薪 → 随机事件 → 出图。

    各步骤拆成上面的 _do_layoff / _pay_rent / _apply_commute /
    _apply_checkin_event / _checkin_blocks，本函数只负责串起顺序 ——
    顺序本身是业务规则（先扣房租再发薪，事件最后结算），所以留在一处可读。
    """
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_unemployed_checkin"))

    today = logic.today_str()
    if p["work_day"] == today:
        return R(err=_t("err_already_checkin"))

    comp = await asyncio.to_thread(gd.resolve_company, int(p["company"]), db)
    scale = float(logic.cfg_get(cfg, "layoff_scale", 1.0))
    if scale > 0 and logic.weighted_layoff(comp["risk"], scale):
        return await _do_layoff(db, gid, uid, p, comp, cfg, today)

    blocks: list[dict] = []
    lines: list[str] = []
    # 本函数内所有流水先登记、save_player 成功后再统一落库（见 Txs 的类文档）：
    # 即写即落的话，中途任何异常（save_player 回滚、事件字段类型不对）都会留下
    # 「现金没变、流水多一笔」的账实不符。
    txs = Txs()
    h = _pay_rent(p, cfg, blocks, lines, txs)

    perf = logic.rf(
        float(logic.cfg_get(cfg, "checkin_perf_min", 0.85)),
        float(logic.cfg_get(cfg, "checkin_perf_max", 1.25)),
    )
    pay = logic.daily_pay(float(p["salary"]), perf, int(p["attend_streak"]), cfg=cfg)

    commute_mode, commute_paid, late = _apply_commute(p, txs)
    penalty_rate = float(logic.cfg_get(cfg, "late_pay_penalty_rate", 0.2))
    if late:
        pay = round(pay * (1 - penalty_rate), 2)
        p["mind"] = float(p["mind"]) - 3
        lines.append(logic.pick(gd.t("work", "commute_late")))

    insurance = round(pay * float(logic.cfg_get(cfg, "social_insurance_rate", 0.10)), 2)
    net_pay = round(pay - insurance, 2)
    p["fund_savings"] = round(float(p.get("fund_savings") or 0) + insurance, 2)
    p["cash"] = round(float(p["cash"]) + net_pay, 2)
    # 三套口径，别混：
    #   total_earned += 【税前】pay     —— 累计劳动所得（面板 #工资条 第一行）
    #   现金       += net_pay          —— 真的到手多少
    #   公积金     += insurance        —— 五险一金【不是支出】，是转进公积金
    #                                     （房子首付可抵扣，见面板的
    #                                     「-{insurance} → 公积金 {fund}」）
    # 所以 #工资条 上「累计总收入」「公积金」「现金+存款」是并排的三个数，
    # 本来就不该相等；该保持的不变式只有一条：
    #   流水合计 == 现金列变动额   （insurance 不进流水，因为它没动现金；
    #   若给它补一条支出流水，反而会把这条不变式打破）
    # 只记净额则会让「累计总收入」永远小于实际劳动所得，并把账实不符引到公积金上。
    p["total_earned"] = round(float(p.get("total_earned") or 0) + pay, 2)
    p["attend_streak"] = int(p["attend_streak"]) + 1
    p["work_day"] = today
    txs.add(
        _t("kind_salary"),
        net_pay,
        _t(
            "salary_note",
            {
                "pay": logic.fmt_money(pay),
                "insurance": logic.fmt_money(insurance),
            },
        ),
    )

    intensity = float(comp["intensity"])
    hp_down = round(intensity * float(logic.cfg_get(cfg, "work_intensity_health_factor", 0.4)), 1)
    mind_down = round(intensity * float(logic.cfg_get(cfg, "work_intensity_mind_factor", 0.25)), 1)
    p["health"] = round(float(p["health"]) - hp_down + h["recover"], 1)
    p["mind"] = round(float(p["mind"]) - mind_down + h["recover"] * 0.5, 1)

    ev, ev_exp = _apply_checkin_event(p, lines, txs)
    exp_gain = (
        logic.ri(
            int(logic.cfg_get(cfg, "checkin_exp_min", 2)),
            int(logic.cfg_get(cfg, "checkin_exp_max", 5)),
        )
        + ev_exp
    )
    p["exp"] = int(p["exp"]) + exp_gain
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await txs.flush(db, gid, uid)

    # 若为群友自建公司，员工打卡按配置比例为企业金库创造营收利润
    profit_rate = float(logic.cfg_get(cfg, "custom_company_profit_rate", 0.15))
    if comp.get("is_custom") and comp.get("custom_id") and profit_rate > 0:
        await asyncio.to_thread(
            db.add_custom_company_balance, comp["custom_id"], round(pay * profit_rate, 2)
        )

    if random.random() < float(logic.cfg_get(cfg, "layoff_safe_event_rate", 0.05)):
        lines.append(logic.pick(gd.t("work", "layoff_safe")))
    blocks.extend(
        _checkin_blocks(
            p,
            net_pay=net_pay,
            insurance=insurance,
            perf=perf,
            late=late,
            penalty_rate=penalty_rate,
            commute_mode=commute_mode,
            commute_paid=commute_paid,
            exp_gain=exp_gain,
        )
    )
    rate = f"{round((1 - penalty_rate) * 100):g}"
    return R(
        tmpl="panel",
        data={
            "icon": "💼",
            "title": (
                _title("title_checkin_late", "打卡成功（迟到了） · ")
                if late
                else _title("title_checkin", "打卡成功 · ")
            )
            + comp["name"],
            "accent": "#fc6262" if late else "#7fd1ff",
            "subtitle": _t("subtitle_news", {"news": gd.news_of_day()}),
            "lines": lines,
            "blocks": blocks,
            "foot": _t("foot_work_hint"),
        },
        text=(
            _t("text_checkin_head", {"net": logic.fmt_money(net_pay)})
            + (_t("text_checkin_late", {"rate": rate}) if late else "")
            + _t(
                "text_checkin_tail",
                {
                    "insurance": logic.fmt_money(insurance),
                    "health": f"{p['health']}",
                    "mind": f"{p['mind']}",
                    "ev": ev["text"],
                },
            )
        ),
    )


async def slack(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_slack_unemployed"))
    cd = float(logic.cfg_get(cfg, "slack_cooldown_minutes", 60)) * 60
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "slack") > 0:
        return R(
            err=_t("err_slack_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "slack"))})
        )
    logic.cd_set(p, "slack", cd)

    # 护盾仅在真正抵挡时消耗（get 探测，未触发保留）；拉屎卡必触发直接消耗
    cds = p.setdefault("_cds", {})
    shield = cds.get("shield_active")
    poop = cds.pop("poop_active", None)

    caught_prob = 0.0 if poop else float(logic.cfg_get(cfg, "slack_caught_rate", 0.15))
    if random.random() < caught_prob:
        if shield:
            cds.pop("shield_active", None)
            await asyncio.to_thread(db.save_player, p)
            shield_line = logic.pick(gd.t("work", "shield_slack"))
            return R(
                tmpl="panel",
                data={
                    "icon": "🛡️",
                    "title": _title("title_slack_shield", "摸鱼被抓 · 护盾生效！"),
                    "accent": "#ffd86f",
                    "lines": [shield_line],
                    "blocks": [
                        {
                            "label": _t("lbl_shield_status"),
                            "value": _t("val_shield_used"),
                        },
                        {
                            "label": _t("lbl_fine_waived"),
                            "value": _t("val_fine_waived_pct"),
                        },
                    ],
                },
                text=_t("text_slack_shield", {"line": shield_line}),
            )
        line = logic.pick(gd.t("work", "slack_caught"))
        fine = max(
            float(logic.cfg_get(cfg, "slack_fine_min", 10.0)),
            round(
                float(p["salary"])
                / logic.workdays(cfg)
                * float(logic.cfg_get(cfg, "slack_fine_rate", 0.5)),
                2,
            ),
        )
        # 记账必须用【实付】而不是名义罚款：余额不足时扣款被截断，直接渲染/记录
        # 名义值就会出现「面板写着罚 200、现金只掉 100、流水一行没有」的账实不符。
        # 口径与 write_report（paid = min(fine, cash)）和 _maybe_hospitalize 一致。
        paid = round(min(fine, float(p["cash"])), 2)
        p["cash"] = round(float(p["cash"]) - paid, 2)
        txs = Txs()
        if paid > 0:  # 一分钱都掏不出时不写 0 元流水
            txs.add(_t("kind_slack_fine"), -paid, line[:40])
        # 实付为 0 时不能沿用「-{fine} 元」模板：render 出的是「-0 元」，玩家看到
        # 一个罚款却一分没扣的负号数字，只能自己猜是不是 bug。换成说明性文案。
        fine_text = (
            _t("val_fine", {"fine": logic.fmt_money(paid)}) if paid > 0 else _t("val_fine_unpaid")
        )
        p["mind"] = float(p["mind"]) - 5
        logic.clamp_status(p)
        await asyncio.to_thread(db.save_player, p)
        await txs.flush(db, gid, uid)
        return R(
            tmpl="panel",
            data={
                "icon": "🚨",
                "title": _title("title_slack_caught", "摸鱼被抓！"),
                "accent": "#fc6262",
                "lines": [line],
                "blocks": [
                    {
                        "label": _t("lbl_fine"),
                        "value": fine_text,
                    },
                    {
                        "label": _t("lbl_mind"),
                        "value": _t("val_mind_down5", {"mind": f"{p['mind']}"}),
                    },
                    {
                        "label": _t("lbl_cash"),
                        "value": _t("val_cash", {"cash": logic.fmt_money(p["cash"])}),
                    },
                ],
            },
            text=_t("text_slack_caught", {"fine": logic.fmt_money(paid), "line": line}),
        )

    lo = int(logic.cfg_get(cfg, "slack_mind_min", 8))
    hi = int(logic.cfg_get(cfg, "slack_mind_max", 15))
    gain = (logic.ri(lo, hi) * 2) if poop else logic.ri(lo, hi)
    p["mind"] = round(float(p["mind"]) + gain, 1)
    p["exp"] = int(p["exp"]) + logic.ri(0, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("work", "slack_ok"))
    return R(
        tmpl="panel",
        data={
            "icon": "🐟",
            "title": _title("title_slack_ok", "摸鱼成功"),
            "accent": "#6fe08c",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_mind_recover"),
                    "value": _t("val_mind_recover", {"gain": gain, "mind": f"{p['mind']}"}),
                }
            ],
        },
        text=_t("text_slack_ok", {"gain": gain, "mind": f"{p['mind']}", "line": line}),
    )


def _maybe_hospitalize(p, cfg, extra_lines) -> float:
    """健康触底时按概率住院：扣医疗费、回血、记叙述行。返回【实付】医疗费。

    实付 ≠ 名义费用：hospital_cost_min 可能高于现金余额，扣款会被
    max(0.0, …) 截断，流水与面板都必须用实付额，否则账面虚高一笔。
    """
    threshold = int(logic.cfg_get(cfg, "hospital_threshold", 15))
    if float(p["health"]) >= threshold:
        return 0.0
    if random.random() >= float(logic.cfg_get(cfg, "hospital_rate", 0.35)):
        return 0.0
    medical = round(
        min(
            float(logic.cfg_get(cfg, "hospital_cost_max", 3000.0)),
            max(
                float(logic.cfg_get(cfg, "hospital_cost_min", 200.0)),
                float(p["cash"]) * float(logic.cfg_get(cfg, "hospital_cost_rate", 0.25)),
            ),
        ),
        2,
    )
    paid = min(medical, round(float(p["cash"]), 2))
    p["cash"] = round(max(0.0, float(p["cash"]) - medical), 2)
    p["health"] = 45.0
    p["mind"] = float(p["mind"]) - 10
    extra_lines.append(logic.pick(gd.t("work", "hospital_texts")))
    return paid


def _overtime_blocks(p, *, pay, exp_gain, ot_limit, got_comp_leave, medical_paid, ev_cash=0.0):
    """加班面板的 blocks（纯展示，无副作用）。"""
    blocks = [
        {
            "label": _t("lbl_ot_pay"),
            "value": _t("val_ot_pay", {"pay": logic.fmt_money(pay)}),
        },
        {
            "label": _t("lbl_exp"),
            "value": _t("val_exp_gain", {"gain": exp_gain, "exp": p["exp"]}),
        },
        {
            "label": _t("lbl_ot_today"),
            "value": _t("val_ot_today", {"done": p["ot_count"], "limit": ot_limit}),
        },
        {
            "label": _t("lbl_health"),
            "value": _t("val_health_100", {"health": f"{p['health']}"}),
        },
        {
            "label": _t("lbl_mind"),
            "value": _t("val_mind_100", {"mind": f"{p['mind']}"}),
        },
    ]
    if ev_cash:
        gain = ev_cash > 0
        blocks.append(
            {
                "label": _t("lbl_ev_cash"),
                "value": _t(
                    "val_gain_yuan" if gain else "val_loss_yuan",
                    {"amount": logic.fmt_money(abs(ev_cash))},
                ),
            }
        )
    if got_comp_leave:
        blocks.append(
            {
                "label": _t("lbl_comp_leave"),
                "value": _t("val_comp_leave_gain", {"count": p["comp_leave"]}),
            }
        )
    if medical_paid:
        blocks.insert(
            1,
            {
                "label": _t("lbl_medical"),
                "value": _t("val_medical", {"medical": logic.fmt_money(medical_paid)}),
            },
        )
    return blocks


async def overtime(db, gid, uid, nickname, cfg):
    """加班：次数/冷却闸门 → 随机事件与加班费 → 调休券 → 住院判定 → 出图。"""
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_ot_unemployed"))
    # 每日次数上限：加班是「花时间换现金」的主要来源，只靠冷却挡不住——
    # 咖啡续命包能缩短冷却，买够就能把冷却压到 0 反复刷。次数上限是硬闸门。
    today = logic.today_str()
    ot_limit = max(1, int(logic.cfg_get(cfg, "overtime_daily_limit", 4)))
    ot_done = int(p.get("ot_count") or 0) if str(p.get("ot_day") or "") == today else 0
    if not logic.is_exempt(cfg, uid) and ot_done >= ot_limit:
        return R(err=_t("err_ot_limit", {"ot_done": ot_done, "ot_limit": ot_limit}))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "ot") > 0:
        return R(err=_t("err_ot_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "ot"))}))
    logic.cd_set(p, "ot", float(logic.cfg_get(cfg, "overtime_cooldown_hours", 3)) * 3600)
    p["ot_day"] = today
    p["ot_count"] = ot_done + 1

    ev = logic.pick(gd.t("work", "overtime_events"))
    if not isinstance(ev, dict):
        ev = {"text": _t("ev_overtime_fallback"), "health": -8, "mind": -4, "exp": 4}
    ev["text"] = str(ev.get("text") or "")
    # 技能加成：每掌握一门技能，加班收益 +2%
    per_skill = float(logic.cfg_get(cfg, "skill_overtime_bonus_rate", 0.02))
    skill_bonus = 1 + per_skill * len(p.get("_skills", []) or [])
    pay = round(
        float(p["salary"])
        / logic.workdays(cfg)
        * logic.rf(
            float(logic.cfg_get(cfg, "overtime_pay_min_rate", 0.5)),
            float(logic.cfg_get(cfg, "overtime_pay_max_rate", 1.1)),
        )
        * skill_bonus,
        2,
    )
    # 流水先登记、save_player 成功后再落库（见 Txs 的类文档）
    txs = Txs()
    txs.income(p, pay, _t("kind_overtime_pay"), ev["text"][:40])
    # 加班事件自带的现金收支（救火补贴 +200、打车 -30、便利店 -15）。此前只读
    # text/health/mind/exp，这三条的钱既不进也不出，文案承诺的收支与结算脱节。
    # 口径与 _apply_checkin_event 完全一致：记【实际】变动额，正向计入总收入。
    cash_before = float(p["cash"])
    p["cash"] = round(max(0.0, cash_before + logic.num_of(ev, "cash", 0)), 2)
    ev_cash = round(float(p["cash"]) - cash_before, 2)
    if ev_cash:
        if ev_cash > 0:
            p["total_earned"] = round(float(p.get("total_earned") or 0) + ev_cash, 2)
        txs.add(_t("kind_work_event"), ev_cash, ev["text"][:40])
    p["health"] = float(p["health"]) + logic.num_of(ev, "health", -8)
    p["mind"] = float(p["mind"]) + logic.num_of(ev, "mind", -4)
    exp_gain = logic.int_of(ev, "exp", 4) + logic.ri(2, 6)
    p["exp"] = int(p["exp"]) + exp_gain

    got_comp_leave = random.random() < float(logic.cfg_get(cfg, "overtime_comp_leave_rate", 0.2))
    if got_comp_leave:
        p["comp_leave"] = int(p.get("comp_leave") or 0) + 1

    extra_lines: list[str] = []
    medical_paid = _maybe_hospitalize(p, cfg, extra_lines)
    hospitalized = bool(extra_lines)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await txs.flush(db, gid, uid)
    if hospitalized:
        money = logic.fmt_money(medical_paid)
        if medical_paid > 0:  # 一分钱都掏不出时不写 0 元流水
            await asyncio.to_thread(
                db.add_transaction,
                gid,
                uid,
                _t("kind_medical"),
                -medical_paid,
                _t("hospital_note"),
            )
        await asyncio.to_thread(
            db.add_event,
            gid,
            uid,
            _t("kind_hospital"),
            _t("ev_hospital", {"name": logic.name_of(p, uid), "medical": money}),
        )

    return R(
        tmpl="panel",
        data={
            "icon": "🏥" if hospitalized else "🌙",
            "title": _title("title_ot_hospital", "深夜医院见")
            if hospitalized
            else _title("title_ot_done", "加班完成"),
            "accent": "#fc6262" if hospitalized else "#ffd86f",
            "lines": [ev["text"], *extra_lines],
            "blocks": _overtime_blocks(
                p,
                pay=pay,
                exp_gain=exp_gain,
                ot_limit=ot_limit,
                got_comp_leave=got_comp_leave,
                medical_paid=medical_paid,
                ev_cash=ev_cash,
            ),
            "foot": _t("foot_doctor_advice") if hospitalized else _t("foot_health_warn"),
        },
        text=(
            (
                _t("text_ot_hospital", {"medical": logic.fmt_money(medical_paid)})
                if hospitalized
                else _t("text_ot_done")
            )
            + _t("text_ot_tail", {"pay": logic.fmt_money(pay), "ev": ev["text"]})
        ),
    )


async def take_leave(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_leave_unemployed"))
    week = logic.yearweek_str()
    if p["leave_week"] != week:
        p["leave_week"] = week
        p["leave_count"] = 0
    limit = int(logic.cfg_get(cfg, "weekly_leave_limit", 2))
    if int(p["leave_count"]) >= limit:
        return R(err=_t("err_leave_limit", {"limit": limit}))
    today = logic.today_str()
    if p["work_day"] == today:
        return R(err=_t("err_leave_after_checkin"))
    p["leave_count"] = int(p["leave_count"]) + 1
    p["attend_streak"] = 0
    # 请假必须占掉今天的出勤位（与带薪调休一致）：不写 work_day 的话，
    # 「#请假」拿完精神/健康恢复后再「#上班」照样能领全额工资 —— 面板上
    # 那句「今日薪资 0 元（请假无薪）」就是假的，每周还能白拿两次恢复。
    p["work_day"] = today
    mind_gain = float(logic.cfg_get(cfg, "leave_mind_gain", 15))
    health_gain = float(logic.cfg_get(cfg, "leave_health_gain", 8))
    p["mind"] = round(float(p["mind"]) + mind_gain, 1)
    p["health"] = round(float(p["health"]) + health_gain, 1)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    line = logic.pick(gd.t("work", "leave_texts"))
    mg = logic.fmt_money(mind_gain)
    hg = logic.fmt_money(health_gain)
    return R(
        tmpl="panel",
        data={
            "icon": "🌴",
            "title": _title("title_leave_ok", "请假成功"),
            "accent": "#6fe08c",
            "lines": [line],
            "blocks": [
                {
                    "label": _t("lbl_today_salary"),
                    "value": _t("val_leave_no_pay"),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_mind_gain", {"gain": mg, "mind": f"{p['mind']}"}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_health_gain", {"gain": hg, "health": f"{p['health']}"}),
                },
                {
                    "label": _t("lbl_week_left"),
                    "value": _t("val_week_left", {"left": limit - int(p["leave_count"])}),
                },
            ],
            "foot": _t("foot_reset_checkin"),
        },
        text=_t("text_leave_ok", {"mg": mg, "hg": hg, "line": line}),
    )


async def take_comp_leave(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_compleave_unemployed"))
    if int(p.get("comp_leave") or 0) <= 0:
        rate = float(logic.cfg_get(cfg, "overtime_comp_leave_rate", 0.2))
        return R(err=_t("err_no_compleave", {"rate": f"{rate * 100:g}"}))
    today = logic.today_str()
    if p["work_day"] == today:
        return R(err=_t("err_compleave_after_checkin"))
    p["comp_leave"] = int(p["comp_leave"]) - 1
    pay = logic.daily_pay(float(p["salary"]), 1.0, int(p["attend_streak"]), cfg=cfg)
    insurance = round(pay * float(logic.cfg_get(cfg, "social_insurance_rate", 0.10)), 2)
    net_pay = round(pay - insurance, 2)
    p["fund_savings"] = round(float(p.get("fund_savings") or 0) + insurance, 2)
    p["cash"] = round(float(p["cash"]) + net_pay, 2)
    p["total_earned"] = round(float(p.get("total_earned") or 0) + pay, 2)
    mind_gain = float(logic.cfg_get(cfg, "leave_mind_gain", 15))
    health_gain = float(logic.cfg_get(cfg, "leave_health_gain", 8))
    p["mind"] = round(float(p["mind"]) + mind_gain, 1)
    p["health"] = round(float(p["health"]) + health_gain, 1)
    p["work_day"] = today
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await asyncio.to_thread(
        db.add_transaction, gid, uid, _t("kind_comp_leave"), net_pay, _t("compleave_note")
    )
    mg = logic.fmt_money(mind_gain)
    hg = logic.fmt_money(health_gain)
    return R(
        tmpl="panel",
        data={
            "icon": "🎫",
            "title": _title("title_compleave_ok", "带薪调休成功！"),
            "accent": "#6fe08c",
            "lines": [_t("line_compleave")],
            "blocks": [
                {
                    "label": _t("lbl_today_income"),
                    "value": _t("val_compleave_income", {"net": logic.fmt_money(net_pay)}),
                },
                {
                    "label": _t("lbl_comp_leave_left"),
                    "value": _t("val_comp_leave_left", {"count": p["comp_leave"]}),
                },
                {
                    "label": _t("lbl_mind"),
                    "value": _t("val_mind_gain", {"gain": mg, "mind": f"{p['mind']}"}),
                },
                {
                    "label": _t("lbl_health"),
                    "value": _t("val_health_gain", {"gain": hg, "health": f"{p['health']}"}),
                },
            ],
        },
        text=_t(
            "text_compleave_ok",
            {"net": logic.fmt_money(net_pay), "count": p["comp_leave"]},
        ),
    )


async def write_report(db, gid, uid, nickname, cfg):
    p = await logic.load_player(db, gid, uid, nickname, cfg)
    if int(p["company"]) == -1:
        return R(err=_t("err_report_unemployed"))
    if not logic.is_exempt(cfg, uid) and logic.cd_left(p, "report") > 0:
        return R(
            err=_t("err_report_cd", {"remaining": logic.fmt_remaining(logic.cd_left(p, "report"))})
        )

    bonus_rate = random.random()
    exp_gain = logic.ri(3, 8)
    s_threshold = float(logic.cfg_get(cfg, "report_s_threshold", 0.85))
    bonus = round(
        float(p["salary"])
        / logic.workdays(cfg)
        * float(logic.cfg_get(cfg, "report_bonus_rate", 0.3))
        * (
            float(logic.cfg_get(cfg, "report_s_bonus_multi", 2.0))
            if bonus_rate > s_threshold
            else 1.0
        ),
        2,
    )
    caught = random.random() < float(logic.cfg_get(cfg, "report_fail_rate", 0.12))

    # 两个分支各自登记流水，统一在 save_player 之后落库（见 Txs 的类文档）
    txs = Txs()
    lines = []
    if caught:
        # 罚款金额只在这里取一次：原先扣款、流水、面板各写了一遍字面量 50，
        # 改一处就会和另外两处对不上
        fine = float(logic.cfg_get(cfg, "report_fail_fine", 50))
        mind_loss = float(logic.cfg_get(cfg, "report_fail_mind", 5))
        paid = min(fine, round(float(p["cash"]), 2))
        p["cash"] = round(max(0.0, float(p["cash"]) - fine), 2)
        p["mind"] = float(p["mind"]) - mind_loss
        # 与 #摸鱼 同一个失效模式：paid=min(fine, cash)，余额为 0 时
        # 「-{fine} 元（态度问题）」会渲染成「-0 元（态度问题）」。实付为 0 时
        # 换成说明性文案（复用同一个键）。
        fine_text = (
            _t("val_report_fine", {"fine": logic.fmt_money(paid)})
            if paid > 0
            else _t("val_fine_unpaid")
        )
        txs.add(_t("kind_report"), -paid, _t("report_note"))
        result_text = logic.pick(gd.t("work", "weeklyreport_fail"))
        title, accent, icon = _title("title_report_fail", "周报被打回"), "#fc6262", "📄"
        blocks = [
            {
                "label": _t("lbl_fine"),
                "value": fine_text,
            }
        ]
        lines.append(result_text)
    else:
        p["cash"] = round(float(p["cash"]) + bonus, 2)
        p["total_earned"] = round(float(p.get("total_earned") or 0) + bonus, 2)
        a_threshold = float(logic.cfg_get(cfg, "report_a_threshold", 0.5))
        grade = "S" if bonus_rate > s_threshold else ("A" if bonus_rate > a_threshold else "B")
        result_text = logic.pick(gd.t("work", "weeklyreport_ok"))
        txs.add(_t("kind_report_perf"), bonus, _t("tx_report_grade", {"grade": grade}))
        title, accent, icon = f"{_title('title_report_grade', '周报评级')} {grade}", "#6fe08c", "📝"
        blocks = [
            {
                "label": _t("lbl_report_bonus"),
                "value": _t("val_report_bonus", {"bonus": logic.fmt_money(bonus)}),
            },
            {
                "label": _t("lbl_exp"),
                "value": _t("val_exp_gain", {"gain": exp_gain, "exp": p["exp"]}),
            },
        ]
        lines.append(result_text)

    p["exp"] = int(p["exp"]) + exp_gain
    logic.cd_set(p, "report", float(logic.cfg_get(cfg, "report_cooldown_days", 7)) * 86400)
    logic.clamp_status(p)
    await asyncio.to_thread(db.save_player, p)
    await txs.flush(db, gid, uid)
    return R(
        tmpl="panel",
        data={
            "icon": icon,
            "title": title,
            "accent": accent,
            "lines": [_t("line_report_intro"), *lines],
            "blocks": blocks,
            "foot": _t("foot_next_report"),
        },
        text=_t("text_report", {"title": title, "result": result_text}),
    )
