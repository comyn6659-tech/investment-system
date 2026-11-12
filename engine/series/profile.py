"""指数估值画像：把同源同列序列变成「当前值 + 5 年月度分位」的描述性块。

本轮只做描述性统计：
- 不输出目标点位、不把分位乘点位、不给 candidate；
- PE 与 PB 分别计算，不拼成综合分数；
- 官方短历史与第三方长历史并列展示，但**不**用官方最新值去排第三方历史池；
- 数据不足时明确不给分位，并说明缺口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from engine.series.models import (
    FREQ_DAILY_SHORT,
    METRIC_OFFICIAL_PB_1,
    METRIC_OFFICIAL_PE_1,
    METRIC_PB,
    METRIC_PE_ROLLING,
    IndexSeries,
    metric_label,
)
from engine.series.window import MIN_VALID_MONTHS, WINDOW_MONTHS, build_window

PROFILE_PAYLOAD_VERSION = "index-profile/1"

# 官方来源前缀：候选 A 的固定序列与「只允许官方估值」的标的都按此校验。
OFFICIAL_SOURCE_PREFIXES = ("csindex.", "cnindex.")

# 需要计入参考池的指标（顺序决定报告顺序）
POOL_METRICS = (METRIC_PE_ROLLING, METRIC_PB)
_SHORT_METRICS = (METRIC_OFFICIAL_PE_1, METRIC_OFFICIAL_PB_1)

_LIMITATIONS = (
    "PE/PB 为供应商同一列的月度参考样本；聚合方法、分母口径与亏损样本处理未在来源中说明，记为 unknown。",
    "分位是该供应商历史样本中的相对位置，不是未来上涨概率，也不是内在价值折扣。",
    "历史发布时间与修订版本未知，因此这些数字不能当作严格可交易信号，也不能用于回测的当时可得数据。",
    "官方指标文件只覆盖最近若干交易日，仅作并列事实，不参与长历史排名。",
)

_SHORT_ONLY_LIMITATIONS = (
    "该标的只取得官方当前估值快照（单日），没有可用的官方日度或月度历史序列。",
    "单日快照只能说明「此刻的官方值是多少」，不能计算分位，也不能当作估值高低判断。",
    "没有同源历史样本，因此本报告不输出分位、研究信号或回测；不用第三方估值或板块数据拼接。",
)

_DECISION_NOTE = (
    "本轮只回答「当前值相对该历史口径处于什么位置」；尚无经过验证的波段交易结论，"
    "不输出目标点位，也不据此给出买点。"
)

_SHORT_ONLY_DECISION_NOTE = (
    "本轮只展示官方当前估值快照；因缺少同源历史，不计算分位，也不据此给出买点。"
)


def is_official_source(source: object) -> bool:
    """来源是否为官方指数机构（中证/国证）。"""
    source_id = getattr(source, "source_id", None)
    return isinstance(source_id, str) and source_id.startswith(OFFICIAL_SOURCE_PREFIXES)


@dataclass(frozen=True)
class MetricProfile:
    metric: str
    label: str
    field: str
    series_id: str
    source: dict[str, Any]
    window: dict[str, Any]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "label": self.label,
            "field": self.field,
            "series_id": self.series_id,
            "source": self.source,
            "status": self.status,
            "window": self.window,
            "percentile": self.window.get("percentile"),
            "percentile_display": self.window.get("percentile_display"),
        }


def build_index_profile(
    *,
    instrument_id: str,
    as_of: str,
    series: Mapping[str, IndexSeries],
    short_series: Mapping[str, IndexSeries] | None = None,
    window_months: int = WINDOW_MONTHS,
    min_valid_months: int = MIN_VALID_MONTHS,
    official_only: bool = False,
) -> dict[str, Any]:
    """组装可直接进 payload 的指数画像块。

    `official_only=True` 时，任何非中证/国证来源的序列都不进入分位计算；
    当前用于创业板指（施工单要求估值、信号、回测只用官方同源数据）。
    """
    metrics: dict[str, Any] = {}
    series_ids: dict[str, str] = {}
    sources: dict[str, dict[str, Any]] = {}
    missing: list[dict[str, Any]] = []

    for metric in POOL_METRICS:
        item = series.get(metric)
        if item is None:
            missing.append(
                {
                    "metric": metric,
                    "label": metric_label(metric),
                    "reason": "没有该指标的同源同列历史序列",
                }
            )
            continue
        if official_only and not is_official_source(item.source):
            missing.append(
                {
                    "metric": metric,
                    "label": item.field,
                    "reason": "非官方来源，按该标的的估值来源政策不进入分位",
                }
            )
            continue
        window = build_window(
            item.observations,
            metric=metric,
            as_of=as_of,
            require_positive=(metric == METRIC_PE_ROLLING),
            window_months=window_months,
            min_valid_months=min_valid_months,
        )
        block = MetricProfile(
            metric=metric,
            label=item.field,
            field=item.field,
            series_id=item.series_id,
            source=item.source.to_dict(),
            window=window.to_dict(),
            status="computed" if window.sufficient else "insufficient_coverage",
        ).to_dict()
        metrics[metric] = block
        series_ids[metric] = item.series_id
        sources[item.source.source_id] = item.source.to_dict()
        if not window.sufficient:
            missing.append(
                {
                    "metric": metric,
                    "label": item.field,
                    "reason": window.insufficient_reason,
                }
            )

    official: dict[str, Any] = {}
    for metric, item in (short_series or {}).items():
        if item.frequency != FREQ_DAILY_SHORT:
            continue
        if official_only and not is_official_source(item.source):
            missing.append(
                {
                    "metric": metric,
                    "label": item.field,
                    "reason": "非官方来源，按该标的的估值来源政策不进入画像",
                }
            )
            continue
        # 官方短序列同样受画像 as_of 限制：截至该日没有可用记录时只报缺口，
        # 不能把未来观测塞进「截至某日」的报告。
        visible = tuple(o for o in item.observations if o[0] <= as_of)
        excluded_future = len(item.observations) - len(visible)
        block: dict[str, Any] = {
            "metric": metric,
            "label": item.field,
            "series_id": item.series_id,
            "field": item.field,
            "source": item.source.to_dict(),
            "usage": "仅作并列事实；样本过短，不参与长历史排名",
            "excluded_future_observations": excluded_future,
        }
        if visible:
            latest_date, latest_value = visible[-1]
            block.update(
                {
                    "status": "computed",
                    "latest": {"date": latest_date, "value": latest_value},
                    "observation_count": len(visible),
                    "coverage_start": visible[0][0],
                    "coverage_end": latest_date,
                }
            )
        else:
            block.update(
                {
                    "status": "no_observation_by_as_of",
                    "latest": None,
                    "observation_count": 0,
                    "coverage_start": None,
                    "coverage_end": None,
                    "note": f"官方序列没有不晚于 {as_of} 的观测，因此不展示数值",
                }
            )
            missing.append(
                {
                    "metric": metric,
                    "label": item.field,
                    "reason": f"官方短历史没有不晚于 {as_of} 的观测",
                }
            )
        official[metric] = block
        series_ids[metric] = item.series_id
        sources[item.source.source_id] = item.source.to_dict()

    computed = [m for m in metrics.values() if m["status"] == "computed"]
    if len(computed) == len(POOL_METRICS):
        status = "complete"
    elif computed:
        status = "partial"
    else:
        status = "insufficient_data"

    short_only = status == "insufficient_data" and bool(official)
    limitations = list(_SHORT_ONLY_LIMITATIONS if short_only else _LIMITATIONS)
    decision_note = _SHORT_ONLY_DECISION_NOTE if short_only else _DECISION_NOTE

    return {
        "payload_version": PROFILE_PAYLOAD_VERSION,
        "instrument_id": instrument_id,
        "as_of": as_of,
        "status": status,
        "window_rule": {
            "months": window_months,
            "min_valid_months": min_valid_months,
            "rule": (
                "分析时点所在月份之前的 N 个完整自然月；每月至多一个观测，"
                "取该月内最后一个有效且有实际日期的同口径记录；"
                "比较值为该来源最新可用观测，若落在参考池中则移除"
            ),
        },
        "metrics": metrics,
        "official_short_history": official,
        "missing": missing,
        "sources": list(sources.values()),
        "series_ids": series_ids,
        "limitations": limitations,
        "decision_note": decision_note,
        "is_trading_signal": False,
    }


__all__ = [
    "POOL_METRICS",
    "PROFILE_PAYLOAD_VERSION",
    "MetricProfile",
    "build_index_profile",
]
