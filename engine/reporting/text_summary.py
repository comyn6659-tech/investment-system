"""纯文本摘要。

只读 AnalysisResult，不重新计算任何数字；渲染前必须已通过契约校验。
"""

from __future__ import annotations

from typing import Any

from engine.contracts import reason_codes as rc
from engine.contracts.enums import AnalysisStatus, Decision, ValuationState

_STATUS_LABEL = {
    "complete": "分析完成",
    "partial": "部分完成",
    "insufficient_data": "资料不足",
    "unsupported": "不适用/未实现",
    "needs_clarification": "需要澄清",
    "engine_error": "引擎错误",
}

_DECISION_LABEL = {
    "candidate": "满足当前模型的买点条件（非收益保证）",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "不形成买点结论",
}

_VALUATION_LABEL = {
    "attractive": "价格位置偏低",
    "neutral": "价格位置中性",
    "expensive": "价格位置偏高",
    "unknown": "价格位置未知",
    "not_applicable": "价格位置不适用",
}

_ASSET_LABEL = {"equity": "A股个股", "index": "指数", "etf": "ETF", "unknown": "未知类型"}


def render_text(result: dict[str, Any]) -> str:
    """result 必须是 AnalysisResult.to_dict() 的结果。

    与 JSON 出口使用同一个校验器：校验不过就抛异常，绝不渲染一份没通过契约的摘要。
    """
    from engine.contracts.validation import enforce

    enforce(result)
    lines: list[str] = []
    instrument = result.get("instrument") or {}
    payload = result.get("payload") or {}
    status = result.get("analysis_status")
    decision = result.get("decision")

    # 1. 标的与目的
    if instrument:
        scope = (instrument.get("scope") or {}).get("status", "unverified")
        scope_label = {"in_scope": "范围内", "out_of_scope": "范围外", "unverified": "待核实"}.get(
            scope, scope
        )
        lines.append(
            f"【标的】{instrument.get('display_name')}（{instrument.get('instrument_id')}）"
            f"· {_ASSET_LABEL.get(instrument.get('asset_class'), instrument.get('asset_class'))}"
            f" · 市场 {instrument.get('market')}"
            f" · {'币种 ' + str(instrument.get('currency')) if instrument.get('currency') else '无币种（指数点位）'}"
            f" · {scope_label}"
        )
    else:
        lines.append("【标的】未识别")
    lines.append(f"【目的】{_intent_label(result.get('intent'))}")

    # 2. 结论与一句话原因
    lines.append("")
    lines.append(f"【结论】{_DECISION_LABEL.get(decision, decision)}")
    lines.append(
        f"  分析状态 {status}（{_STATUS_LABEL.get(status, status)}）"
        f" ｜ 模型状态 {result.get('model_status') or '无'}"
        f" ｜ 价格位置 {result.get('valuation_state')}"
        f"（{_VALUATION_LABEL.get(result.get('valuation_state'), '')}）"
        f" ｜ 决策 {decision}"
    )
    if result.get("reason_codes"):
        lines.append("  原因：")
        for code in result["reason_codes"]:
            lines.append(f"    - {code}：{rc.describe(code)}")

    # 3. 方法状态
    plan = result.get("model_plan") or {}
    lines.append("")
    if plan.get("primary_model"):
        lines.append(f"【方法】主模型 {plan['primary_model']}（{plan.get('model_status')}）")
    else:
        lines.append(f"【方法】未选主模型：{plan.get('selection_reason') or '无说明'}")
        candidates = plan.get("candidate_models") or []
        if candidates:
            lines.append("  本应评估的模型：")
            for model in candidates:
                model_id = model.get("model_id") or model.get("model") or "未命名模型"
                status = model.get("status") or model.get("reason_code") or "未知状态"
                implemented = model.get("implemented")
                note = model.get("notes") or model.get("reason") or ""
                implemented_text = (
                    f"，已实现={implemented}" if implemented is not None else ""
                )
                lines.append(f"    - {model_id}（{status}{implemented_text}）：{note}")
        category = plan.get("applicable_model_category")
        if category:
            lines.append(f"  适用模型类别：{category}")
        for item in plan.get("required_evidence_to_implement") or []:
            lines.append(f"    - 补齐后可实现：{item}")

    # 4. 业务载荷：个股情景 / 指数位置 / ETF 执行
    lines.extend(_render_payload(payload))

    # 5. 证据与未核实
    evidence = result.get("evidence_refs") or []
    if evidence:
        lines.append("")
        lines.append("【证据来源】")
        for ev in evidence[:8]:
            title = ev.get("publisher") or ev.get("evidence_id")
            fields = "、".join(ev.get("fields_cited") or []) or "—"
            url = f" {ev['url']}" if ev.get("url") else ""
            lines.append(f"  - {title}（{ev.get('kind')}，取于 {ev.get('retrieved_at')}）字段：{fields}{url}")
        if len(evidence) > 8:
            lines.append(f"  - ……共 {len(evidence)} 条，完整清单见 JSON 的 evidence_refs")

    missing = result.get("missing_data") or []
    if missing:
        lines.append("")
        lines.append("【尚未核实/缺失】")
        for item in missing[:10]:
            note = f"（{item['note']}）" if item.get("note") else ""
            lines.append(f"  - {item['fact_id']}：{item['status']}{note}")

    # 6. 失效条件
    invalidation = result.get("invalidation") or []
    if invalidation:
        lines.append("")
        lines.append("【哪些变化会使当前结论失效】")
        for item in invalidation:
            lines.append(f"  - {item}")

    # 7. 数据截至时间
    lines.append("")
    lines.append(
        f"【数据时点】截至 {result.get('as_of') or '未取得数据'}"
        f" ｜ 公开可得时间 {result.get('available_at') or '未知'}"
        f" ｜ 快照 {result.get('snapshot_id') or '无'}"
    )
    lines.append(
        f"【版本】schema {result.get('schema_version')} ｜ code {result.get('code_version')}"
        f" ｜ policy {result.get('policy_version')} ｜ model {result.get('model_version')}"
        f" ｜ analysis_id {result.get('analysis_id')}"
    )
    if result.get("clarification"):
        lines.append("")
        lines.append("【需要你确认】")
        lines.append(result["clarification"])
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("【提示】")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)


def _render_payload(payload: dict[str, Any]) -> list[str]:
    ptype = payload.get("payload_type")
    lines: list[str] = []
    if ptype == "equity":
        facts = payload.get("company_facts") or {}
        if facts:
            lines.append("")
            lines.append("【公司事实】")
            for metric, fact in facts.items():
                lines.append(f"  - {metric}：{_fact_text(fact)}")
        periods = payload.get("financial_periods") or []
        if periods:
            lines.append("")
            lines.append("【报告期（合并口径，累计数）】")
            for entry in periods[:5]:
                notice = entry.get("notice_date") or "公告时间未知"
                key_metrics = _period_highlights(entry.get("facts") or {})
                lines.append(f"  - {entry['period']}（公告 {notice}）：{key_metrics}")
            if len(periods) > 5:
                lines.append(f"  - ……共 {len(periods)} 个报告期，完整科目见 JSON")
        summary = payload.get("dividend_summary")
        if summary:
            lines.extend(_render_dividend_summary(summary))
        elif payload.get("dividend_records"):
            lines.append("")
            lines.append(f"【分红记录】{len(payload['dividend_records'])} 条明细见 JSON")
        research = payload.get("research_valuation")
        if research:
            lines.extend(_render_research_valuation(research, payload.get("model_applicability")))
        elif not payload.get("valuation"):
            lines.append("")
            lines.append(
                "【估值情景】无。本次没有产出研究估值"
                "（原因见上方结论与原因码），因此不存在内在价值、安全边际或买点条件。"
            )
    elif ptype == "index":
        level = payload.get("level")
        if level and level.get("value") is not None:
            lines.append("")
            lines.append(
                f"【指数位置】{level['value']} 点（{level.get('unit')}，"
                f"不带币种：指数点位不是人民币金额）"
            )
        valuation = payload.get("valuation_facts") or {}
        if valuation:
            lines.append("")
            lines.append("【估值事实】")
            for name, fact in valuation.items():
                lines.append(f"  - {name}：{_fact_text(fact)}")
        coverage = payload.get("history_coverage") or {}
        if coverage:
            lines.extend(_render_history_coverage(coverage))
        profile = payload.get("valuation_profile")
        if profile:
            lines.extend(_render_index_profile(profile))
        signal = payload.get("candidate_a_signal")
        if signal:
            lines.extend(_render_candidate_a_signal(signal))
        else:
            lines.append("")
            lines.append(
                "【策略状态】本轮未取得候选 A 研究信号所需的官方日频序列："
                "不输出入场/退出条件，也不给目标点位。"
            )
    elif ptype == "etf":
        underlying = payload.get("underlying_index") or {}
        lines.append("")
        if underlying:
            lines.append(
                f"【跟踪对象】{underlying.get('display_name')}（{underlying.get('instrument_id')}）"
            )
        lines.append("【ETF 事实】")
        lines.append(f"  - 价格：{_amount_text(payload.get('price'))}")
        lines.append(f"  - 净值：{_nav_text(payload.get('nav'))}")
        lines.append(f"  - 折溢价：{_amount_text(payload.get('premium_discount'))}")
        lines.append(f"  - 费用：{_fees_text(payload.get('fees'))}")
        lines.append(f"  - 交易状态：{_trading_text(payload.get('trading'))}")
        tracking = payload.get("tracking") or {}
        if tracking.get("note"):
            lines.append(f"  - 跟踪信息：{tracking['note']}")
        check = payload.get("product_check")
        if check:
            lines.extend(_render_product_check(check))
    return lines


_PERIOD_HIGHLIGHT_KEYS = (
    ("total_operating_revenue", "营业总收入"),
    ("net_profit_parent", "归母净利润"),
    ("operating_cash_flow", "经营现金流"),
    ("capex", "资本开支"),
)


def _period_highlights(facts: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in _PERIOD_HIGHLIGHT_KEYS:
        fact = facts.get(key)
        if not fact:
            continue
        if fact.get("status") in ("missing", "not_applicable", "conflict"):
            parts.append(f"{label} {fact.get('status')}")
            continue
        value = fact.get("value")
        if isinstance(value, (int, float)):
            parts.append(f"{label} {value / 1e8:,.2f} 亿元")
        else:
            parts.append(f"{label} {value}")
    return "、".join(parts) or "（无关键科目）"


def _render_dividend_summary(summary: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", "【分红（按所属财年归集，每 10 股已换算为每股）】"]
    years = summary.get("fiscal_years") or []
    for entry in years[:5]:
        text = (
            f"  - {entry['fiscal_year']} 财年：普通分配 {entry['ordinary_dps']:.4f} 元/股"
            f"（含年度末期分配：{'是' if entry.get('has_annual_final') else '否'}）"
        )
        if entry.get("special_dps"):
            text += f"；特别分配 {entry['special_dps']:.4f} 元/股（单列，不永续）"
        lines.append(text)
        for item in entry.get("components") or []:
            if item.get("bucket") != "ordinary":
                continue
            lines.append(
                f"      · {item.get('kind')} {item.get('per_share', 0):.4f} 元/股"
                f"，除权日 {item.get('ex_date') or '未知'}"
                f"，状态 {item.get('status') or '未知'}"
            )
    if len(years) > 5:
        lines.append(f"  - ……共 {len(years)} 个财年记录，完整明细见 JSON")
    special = summary.get("unlabeled_special") or []
    if special:
        lines.append(
            "  - 所属财年未标注的特别分配："
            + "、".join(f"{item['per_share']:.4f} 元/股" for item in special)
            + "（不并入任何财年，也不外推）"
        )
    if summary.get("ltm_paid_per_share") is not None:
        window = summary.get("ltm_window") or []
        lines.append(
            f"  - 过去十二个月实际支付 {summary['ltm_paid_per_share']:.4f} 元/股"
            + (f"（除权日 {window[0]} 之后至 {window[1]}）" if len(window) == 2 else "")
            + "：与「某财年对应分配」不是同一指标"
        )
    return lines


def _pct_text(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _ratio_text(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _render_index_profile(profile: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", "【5 年月度估值画像（描述性统计，不是交易信号）】"]
    rule = profile.get("window_rule") or {}
    lines.append(
        f"  窗口：分析时点所在月份之前 {rule.get('months')} 个完整自然月"
        f"（有效月份门槛 {rule.get('min_valid_months')} 个）"
    )
    for metric in ("pe_rolling", "pb"):
        block = (profile.get("metrics") or {}).get(metric)
        if not block:
            continue
        window = block.get("window") or {}
        comparison = window.get("comparison") or {}
        pool = window.get("pool") or {}
        lines.append(
            f"  - {block.get('label')}（{metric}）：当前 {_ratio_text(comparison.get('value'))}"
            f"（{comparison.get('date') or '日期未知'}）"
            f" ｜ 分位 {(str(block.get('percentile_display')) + '%') if block.get('percentile_display') is not None else '未计算'}"
            f" ｜ 有效样本 {pool.get('size')} 个（有效月份 {window.get('valid_months')}/{window.get('expected_months')}）"
            f" ｜ 参考区间 {_ratio_text(pool.get('min'))} – {_ratio_text(pool.get('max'))}"
            f" ｜ 中位数 {_ratio_text(pool.get('median'))}"
        )
        if block.get("status") != "computed":
            reason = window.get("insufficient_reason")
            if reason:
                lines.append(f"      不计算分位：{reason}")
        missing_months = window.get("missing_months") or []
        if missing_months:
            lines.append(f"      缺失月份：{'、'.join(missing_months[:6])}" + ("…" if len(missing_months) > 6 else ""))
        if window.get("comparison_removed_from_pool"):
            lines.append("      比较值落在参考池中，已从池内移除（不参与自己的排名）")
    official = profile.get("official_short_history") or {}
    for metric, block in official.items():
        latest = block.get("latest") or {}
        lines.append(
            f"  官方短历史（并列事实，不参与长历史排名）：{block.get('label')}"
            f" {_ratio_text(latest.get('value'))}（{latest.get('date')}），"
            f"仅 {block.get('observation_count')} 个交易日"
        )
    for item in profile.get("limitations") or []:
        lines.append(f"  · 限制：{item}")
    note = profile.get("decision_note")
    if note:
        lines.append(f"  · 用途：{note}")
    return lines


_SIGNAL_LABEL = {
    "entry": "入场条件成立",
    "exit": "退出条件成立",
    "none": "未触发",
    "not_evaluable": "不可评估",
}


def _render_candidate_a_signal(signal: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", f"【{signal.get('label') or '候选 A 研究信号（未批准）'}】"]
    rule = signal.get("rule") or {}
    lines.append(
        f"  规则（{rule.get('rule_version')}）：官方滚动市盈率周度分位 "
        f"≤ {float(rule.get('entry_percentile') or 0):.0f} 为入场条件、"
        f"≥ {float(rule.get('exit_percentile') or 0):.0f} 为退出条件；"
        f"持有满 {rule.get('max_hold_months')} 个日历月到期；{rule.get('execution')}"
    )
    latest = signal.get("latest")
    if latest:
        percentile = latest.get("percentile")
        lines.append(
            f"  - 最新观察点：{latest.get('date')}（{latest.get('week')}，"
            f"{'该周已结束' if latest.get('week_complete') else '该周尚未结束，信号为盘中临时值'}）"
        )
        lines.append(
            f"  - 官方滚动市盈率：{_ratio_text(latest.get('pe'))}"
            f" ｜ 5 年周度分位：{_pct_text(percentile) if percentile is not None else '未计算'}"
            f"（可用周 {latest.get('valid_weeks')}/{latest.get('weeks_in_window')}）"
        )
        condition = _SIGNAL_LABEL.get(signal.get("signal"), signal.get("signal"))
        lines.append(
            f"  - 条件：入场条件 {'成立' if signal.get('entry_condition_met') else '未成立'}"
            f"；退出条件 {'成立' if signal.get('exit_condition_met') else '未成立'}"
            f" → 信号：{condition}"
        )
        if latest.get("reason"):
            lines.append(f"  - 不可评估原因：{latest['reason']}")
    else:
        lines.append(f"  - 未计算：{signal.get('reason') or '缺少序列'}")
    backtest = signal.get("backtest") or {}
    if backtest.get("trades") is not None:
        lines.append(
            f"  - 回测记录（{'全收益' if backtest.get('return_basis') == 'total_return' else backtest.get('return_basis')}"
            f" / T+{backtest.get('execution_lag')} / {float(backtest.get('cost_bp') or 0):.0f}bp）："
            f"{backtest.get('start')}→{backtest.get('end')}，{backtest.get('trades')} 笔，"
            f"累计 {_signed_pct(backtest.get('cumulative_return'))}，"
            f"最大回撤 {_signed_pct(backtest.get('max_drawdown'))}；"
            f"同期全收益买入持有 {_signed_pct(backtest.get('benchmark_total_return'))}，"
            f"超额 {_signed_pct(backtest.get('excess_vs_total_return'))}"
        )
        lines.append(
            f"  - 记录位置：{backtest.get('report_path')}"
            f"（数据 {backtest.get('data_path')}，生成于 {backtest.get('generated_at')}）"
        )
    ledger = signal.get("ledger") or {}
    if ledger.get("status") == "ok":
        lines.append(
            f"  - 只追加账本：{ledger.get('path')}（{ledger.get('records')} 条记录，"
            f"最后 {ledger.get('last_week')} / {ledger.get('last_date')}，"
            f"信号 {_SIGNAL_LABEL.get(ledger.get('last_signal'), ledger.get('last_signal'))}）"
        )
    elif ledger.get("status") == "empty":
        lines.append(f"  - 只追加账本：{ledger.get('path')}（尚未写入观察点）")
    lines.append(
        "  · 说明：研究信号，规则与阈值未获批准；只描述条件是否成立，不跟踪持仓，"
        "不构成买卖建议，也不给目标点位。"
    )
    return lines


def _signed_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value) * 100.0
    except (TypeError, ValueError):
        return str(value)
    return f"{number:+.2f}%"


def _render_product_check(check: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", "【ETF 产品资料完整性检查】"]
    identity = check.get("identity") or {}
    underlying = identity.get("underlying_index") or {}
    if underlying:
        lines.append(
            f"  跟踪对象：{underlying.get('display_name')}（{underlying.get('instrument_id')}）"
            f" ｜ 产品性质：{identity.get('etf_strategy')}"
        )
    price_block = check.get("price") or {}
    price = price_block.get("price") or {}
    bar = price_block.get("bar_status") or {}
    lines.append(
        f"  市场价格：{_ratio_text(price.get('value'))} 元/份"
        f"（交易日 {price.get('period') or price.get('as_of')}，状态 {bar.get('value') or '未知'}）"
    )
    nav = check.get("nav") or {}
    nav_value = nav.get("value") or {}
    nav_date = nav.get("nav_date") or {}
    lines.append(
        f"  单位净值：{_ratio_text(nav_value.get('value'))}（净值日期 {nav_date.get('value') or '未知'}）"
        f" ｜ 披露时刻：{'已知' if nav.get('disclosure_time_known') else '未知'}"
    )
    premium = check.get("premium_discount_same_day") or {}
    if premium.get("value") is not None:
        lines.append(
            f"  事后同日折溢价：{_pct_text(premium.get('value'))}"
            "（价格与净值同一估值日期；披露时刻未知，不能证明收盘前可交易）"
        )
    else:
        lines.append(f"  事后同日折溢价：未计算（{premium.get('note') or '条件不满足'}）")
    fees = check.get("fees") or {}
    if fees:
        mgmt = (fees.get("management") or {}).get("value")
        custody = (fees.get("custody") or {}).get("value")
        lines.append(
            f"  费率：管理费 {_pct_text(mgmt)}/年，托管费 {_pct_text(custody)}/年"
            f"（{fees.get('note')}）"
        )
    turnover = check.get("turnover") or {}
    if turnover:
        latest = (turnover.get("latest") or {}).get("value")
        average = (turnover.get("average") or {}).get("value")
        lines.append(
            f"  成交：最新 {_fmt_cny(latest)} ｜ {turnover.get('average_label')} {_fmt_cny(average)}"
            f"（实际 {turnover.get('days_used')} 个交易日"
            f"{'，未标成完整 20 日均值' if not turnover.get('is_full_20d') else ''}）"
        )
    trading = check.get("trading") or {}
    status = (trading.get("secondary_market_status") or {}).get("value")
    spread = (trading.get("bid_ask_spread") or {}).get("value")
    lines.append(
        f"  交易状态：{'已知 ' + str(status) if status else '未取得停复牌字段'}"
        f" ｜ 买卖价差：{'已知 ' + str(spread) if spread else '未取得盘口数据'}"
    )
    subscription = (trading.get("subscription_status") or {}).get("value")
    redemption = (trading.get("redemption_status") or {}).get("value")
    if subscription or redemption:
        lines.append(
            f"  申购/赎回状态：{subscription or '未知'} / {redemption or '未知'}"
            "（来自基金净值表，不等于二级市场交易状态）"
        )
    profile = check.get("underlying_index_profile")
    if profile:
        lines.append("")
        lines.append("  【对应指数画像（与从指数进入时同源同算法）】")
        for line in _render_index_profile(profile):
            if line.strip():
                lines.append("  " + line)
    for item in check.get("missing") or []:
        lines.append(f"  · 缺失：{item.get('item')}（{item.get('status')}）{item.get('note') or ''}")
    if check.get("usage_note"):
        lines.append(f"  · 用途：{check['usage_note']}")
    return lines


def _fmt_cny(value: Any) -> str:
    if value is None:
        return "未取得"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(amount) >= 1e8:
        return f"{amount / 1e8:,.2f} 亿元"
    if abs(amount) >= 1e4:
        return f"{amount / 1e4:,.2f} 万元"
    return f"{amount:,.0f} 元"


def _render_research_valuation(
    research: dict[str, Any], applicability: dict[str, Any] | None
) -> list[str]:
    """研究估值报告：公司/模型选择、数据与缺口、价值范围与驱动、现价关系、失效条件。"""
    lines: list[str] = []
    # 研究估值里带的是完整适用性（含指标与政策），载荷里的 model_applicability 是精简版
    applicability = research.get("applicability") or applicability or {}

    # 1. 这是什么公司、为何选这个模型、哪些模型不适用
    lines.append("")
    lines.append("【模型选择】")
    lines.append(
        f"  企业族：{applicability.get('family_label') or applicability.get('family')}"
        f" ｜ 主模型：{research.get('model_id')}（{research.get('model_status')}）"
        f" ｜ 版本：{research.get('model_version')}"
    )
    indicators = applicability.get("indicators") or {}
    industry = indicators.get("industry")
    segments = indicators.get("top_segments") or []
    if industry or segments:
        mix = "、".join(
            f"{item.get('segment')} {item['revenue_share']:.1%}"
            for item in segments
            if item.get("revenue_share") is not None
        )
        lines.append(
            f"  业务：行业分类 {industry or '未取得'}"
            + (f" ｜ 主营构成：{mix}" if mix else " ｜ 主营构成未取得")
        )
    for reason in (applicability.get("reasons") or [])[:4]:
        note = reason.get("note")
        if note:
            lines.append(f"  - 依据（{reason.get('signal')}）：{note}")
    rejected = applicability.get("rejected_models") or []
    if rejected:
        lines.append("  不用于主模型的路线：")
        for item in rejected:
            lines.append(
                f"    · {item.get('model_id')}：{item.get('reason')}（{item.get('reason_code')}）"
            )
    if applicability.get("applicable_model_category"):
        lines.append(f"  适用模型类别：{applicability.get('applicable_model_category')}")
        for item in applicability.get("required_evidence_to_implement") or []:
            lines.append(f"    - 补齐后可实现：{item}")

    # 2. 用了哪些日期和口径的数据、缺什么
    data = research.get("data_used") or {}
    lines.append("")
    lines.append("【数据与口径】")
    lines.append(
        f"  快照 {data.get('snapshot_id')} ｜ 数据截至 {data.get('as_of')}"
        f" ｜ 现金流基准财年 {research.get('base_fiscal_year')}"
        f" ｜ 年报期数 {len(data.get('annual_years') or [])}"
    )
    bridge = data.get("fcff_bridge")
    if bridge:
        lines.append(
            f"  历史 FCFF 桥接（{bridge.get('period_end')}）：EBIT {_money(bridge.get('ebit'))}"
            f" ｜ 实际税率 {bridge.get('effective_tax_rate', 0):.2%}"
            f" ｜ 资本开支 {_money(bridge.get('capex'))}"
            f" ｜ 营运资本增加 {_money(bridge.get('change_in_nwc'))}"
            f" ｜ FCFF {_money(bridge.get('fcff'))}"
        )
        rel = bridge.get("reconciliation_relative")
        lines.append(
            f"  与经营现金流勾稽：差额 {_money(bridge.get('reconciliation_gap'))}"
            + (f"（占经营现金流 {rel:.1%}）" if rel is not None else "")
            + ("，在容差内" if bridge.get("reconciled") else "，超出容差（按部分完成处理）")
        )
    price = research.get("price")
    if price:
        lines.append(
            f"  价格：{price['value']} 元/股（{price.get('price_type')}，交易日 {price.get('as_of')}，"
            f"bar={price.get('bar_status')}）"
        )

    # 3. 研究假设下的价值范围与驱动变量
    lines.append("")
    lines.append("【研究估值（未获批准，不构成买点）】")
    scenarios = research.get("scenarios") or []
    for item in scenarios:
        value = item.get("value_per_share")
        terminal = item.get("terminal_share_of_value")
        lines.append(
            f"  - {item.get('name')}：{value:,.2f} 元/股"
            + (f"（终值占 {terminal:.1%}）" if terminal is not None else "")
            + (f"｜{item['note']}" if item.get("note") else "")
        )
    rng = research.get("valuation_range") or {}
    if rng:
        lines.append(
            f"  情景范围 {rng['low']:,.2f} – {rng['high']:,.2f} 元/股："
            "由输入情景定义，不是统计置信区间"
        )
    sensitivity = research.get("sensitivity") or []
    if sensitivity:
        lines.append("  主要驱动变量（单变量变动，其他假设不变）：")
        for row in sensitivity:
            if row.get("value_per_share") is None:
                lines.append(f"    · {row['variable']} {row['delta']:+.4f}：{row.get('note')}")
                continue
            change = row.get("change_pct")
            lines.append(
                f"    · {row['variable']} {row['delta']:+.4f} → {row['value_per_share']:,.2f} 元/股"
                + (f"（{change:+.1%}）" if change is not None else "")
            )
    cross = research.get("cross_check")
    if cross:
        lines.append("")
        lines.append("【交叉检查】")
        if cross.get("value_per_share") is None:
            lines.append(f"  {cross.get('model_id')}：{cross.get('note')}")
        else:
            lines.append(
                f"  {cross.get('model_id')}（{cross.get('model_version')}）："
                f"{cross['value_per_share']:,.2f} 元/股，相对主模型 "
                f"{cross['difference_pct']:+.1%}"
            )
            lines.append(f"  说明：{cross.get('note')}")

    # 4. 现价与研究情景的关系
    relationship = research.get("price_relationship")
    lines.append("")
    if relationship:
        label = {
            "research_value_above_price": "研究情景估值高于当前收盘价",
            "research_value_below_price": "研究情景估值低于当前收盘价",
            "research_value_in_line_with_price": "研究情景估值与当前收盘价大致相当",
        }.get(relationship.get("relation"), relationship.get("relation"))
        lines.append(
            f"【与现价的关系】{label}，差距 {relationship.get('difference_pct', 0):+.1%}"
            f"（展示带宽 ±{relationship.get('band', 0):.0%}）。"
        )
        lines.append(f"  {relationship.get('note')}")
    else:
        lines.append("【与现价的关系】没有可核实的收盘价，未做任何现价比较。")

    # 5. 假设与证据
    lines.append("")
    lines.append("【研究假设状态】")
    lines.append(
        f"  假设 {research.get('assumption_id')} ｜ 状态 {research.get('assumption_status')}"
        "：参数为研究候选，未经设计方批准，不得当作正式买入上限"
    )
    hypotheses = (data.get("research_hypothesis_parameters") or [])
    if hypotheses:
        lines.append("  属于研究假设（非观测值）的参数：" + "、".join(hypotheses))
    return lines


def _money(value: Any) -> str:
    if value is None:
        return "未取得"
    try:
        return f"{float(value):,.0f} 元"
    except (TypeError, ValueError):
        return str(value)


def _fact_text(fact: dict[str, Any]) -> str:
    status = fact.get("status")
    if status == "missing":
        return "缺失（不按 0 处理）"
    if status == "not_applicable":
        return "不适用"
    if status == "stale":
        return f"{fact.get('value')}（已过期，数据期 {fact.get('period')}）"
    if status == "conflict":
        return f"来源冲突 {fact.get('conflict_values')}"
    value = fact.get("value")
    unit = _UNIT_LABEL.get(fact.get("unit") or "", fact.get("unit") or "")
    currency = fact.get("currency")
    suffix = f" {currency}" if currency else ""
    period = f"（数据期 {fact.get('period')}）" if fact.get("period") else ""
    return f"{value} {unit}{suffix}{period}".strip()


def _compact(value: Any) -> str:
    if value is None:
        return "未取得"
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if k in {"source", "status_code"}:
                continue
            if isinstance(v, dict) and "value" in v:
                parts.append(f"{k}={v['value']}{v.get('unit') or ''}")
            else:
                parts.append(f"{k}={v}")
        return "，".join(parts)
    return str(value)


def _intent_label(intent: str | None) -> str:
    return {
        "buy_point_check": "判断当前是否满足买点条件",
    }.get(intent or "", intent or "未指定")


_ = (AnalysisStatus, Decision, ValuationState)

_UNIT_LABEL = {
    "CNY": "元",
    "CNY_per_share": "元/份",
    "index_point": "点",
    "percent": "%",
    "ratio": "倍",
    "shares": "股",
}


def _amount_text(value: Any) -> str:
    if value is None:
        return "未取得"
    if not isinstance(value, dict):
        return str(value)
    val = value.get("value")
    if val is None:
        return f"缺失（状态 {value.get('status')}）"
    unit = _UNIT_LABEL.get(value.get("unit"), value.get("unit"))
    currency = value.get("currency")
    suffix = f" {currency}" if currency else ""
    return f"{val} {unit}{suffix}"


def _nav_text(value: Any) -> str:
    if value is None:
        return "未取得"
    if value.get("status") and value.get("status") != "known":
        return f"缺失（状态 {value.get('status')}）"
    base = f"{value.get('value')} 元/份（净值日期 {value.get('nav_date') or '未知'}）"
    if not value.get("disclosed_at"):
        base += "；披露时间未知，不能断言收盘时已公布"
    return base


def _fees_text(value: Any) -> str:
    if value is None:
        return "未取得"
    parts = []
    for label, key in (("管理费", "management"), ("托管费", "custody")):
        item = value.get(key)
        parts.append(f"{label} {item['value']}%/年" if item and item.get("value") is not None else f"{label} 缺失")
    return "，".join(parts)


def _trading_text(value: Any) -> str:
    if value is None:
        return "未取得"
    if value.get("status") is None:
        return f"缺失（状态 {value.get('status_code')}）"
    return str(value.get("status"))


def _render_history_coverage(coverage: dict[str, Any]) -> list[str]:
    """历史覆盖必须区分「快照实时抓取」与「候选 A 序列库」，避免自相矛盾。"""
    lines = ["", "【历史覆盖】"]
    note = coverage.get("scope_note")
    if note:
        lines.append(f"  - 口径：{note}")
    snapshot_path = coverage.get("snapshot_path")
    if isinstance(snapshot_path, dict) and snapshot_path:
        lines.append("  - 快照实时抓取路径（不是候选 A 序列库）：")
        for key, value in snapshot_path.items():
            lines.append(f"    - {key}：{_coverage_text(value)}")
    library = coverage.get("series_library") or {}
    if isinstance(library, dict):
        lines.append("  - 序列库（候选 A 研究信号与回测使用）：")
        series = library.get("series") or {}
        for key, block in series.items():
            lines.append(
                f"    - {block.get('label') or key}：{block.get('start')} → {block.get('end')}，"
                f"{block.get('observations')} 个观测（{block.get('series_id')}，"
                f"{block.get('source_id')}）"
            )
        if library.get("status") == "available":
            lines.append(
                "    - 支持 5 年周度历史："
                f"{'是' if library.get('supports_weekly_5y_history') else '否'}"
            )
        else:
            lines.append(f"    - 不可用：{library.get('note')}")
    for key in ("monthly_reference_window", "profile_status", "profile_series_ids"):
        if key in coverage:
            lines.append(f"  - {key}：{_coverage_text(coverage[key])}")
    return lines


def _coverage_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value)
    parts = []
    for key, item in value.items():
        if key in {"source_id", "publisher"}:
            continue
        if isinstance(item, dict):
            parts.append(f"{key}={{" + _coverage_text(item) + "}")
        else:
            parts.append(f"{key}={item}")
    return "，".join(parts)
