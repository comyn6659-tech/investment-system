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
        lines.append(f"【方法】未选主模型：{plan.get('selection_reason', '无说明')}")
        candidates = plan.get("candidate_models") or []
        if candidates:
            lines.append("  本应评估的模型：")
            for model in candidates:
                lines.append(
                    f"    - {model['model_id']}（{model['status']}，已实现={model['implemented']}）："
                    f"{model['notes']}"
                )

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
            lines.append("【报告期】")
            for entry in periods[:6]:
                notice = entry.get("notice_date") or "公告时间未知"
                metrics = "、".join(sorted(entry.get("facts", {}).keys()))
                lines.append(f"  - {entry['period']}（公告 {notice}）：{metrics}")
            if len(periods) > 6:
                lines.append(f"  - ……共 {len(periods)} 个报告期，完整数据见 JSON")
        dividends = payload.get("dividend_records") or []
        if dividends:
            lines.append("")
            lines.append(f"【分红记录】{len(dividends)} 条（按所属财年与实施状态归集，明细见 JSON）")
            for fact in dividends[:5]:
                lines.append(f"  - {fact.get('period') or '财年未标注'}：{_fact_text(fact)}")
        if not payload.get("valuation"):
            lines.append("")
            lines.append("【估值情景】无。阶段一未实现个股模型，因此没有内在价值、安全边际或买点条件。")
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
            lines.append("")
            lines.append("【历史覆盖】")
            for key, value in coverage.items():
                lines.append(f"  - {key}：{_coverage_text(value)}")
        lines.append("")
        lines.append(
            "【策略状态】候选 A/B 均为 research 且未实现；"
            "阶段一不计算分位、不产生任何入场/退出信号。"
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
    return lines


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
