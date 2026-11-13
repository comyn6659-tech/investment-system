"""指数官方日频序列与候选 A 周度信号的前向刷新。

- 官方序列：中证指数官网历史行情接口（`index-perf`，与第三批同源），直接带超时抓取，
  不依赖 AKShare 的默认重试；只有出现新交易日时才保存新序列，内容未变则跳过。
- 周度信号：只追加已结束自然周的观察点；同一标的同一周重复运行内容一致跳过。
"""

from __future__ import annotations

import contextlib
import io
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.series.models import (
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
SERIES_DIR = ROOT / "data" / "series"
SIGNAL_LEDGER = ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"
ATTEMPT_STATE = ROOT / "data" / "series" / ".last_series_attempt.json"
PERF_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
INDICES = {
    "000300": {"name": "沪深300", "tr": "H00300", "launch": "2005-04-08", "start": "20040101"},
    "000905": {"name": "中证500", "tr": "H00905", "launch": "2007-01-15", "start": "20040101"},
    "000852": {"name": "中证1000", "tr": "H00852", "launch": "2014-10-17", "start": "20100101"},
}
CODE_ORDER = ("000300", "000905", "000852")


class SeriesRefreshError(RuntimeError):
    pass


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _source(kind: str, code: str, stamp: str) -> SeriesSource:
    return SeriesSource(
        source_id="csindex.indicators",
        publisher="中证指数有限公司",
        interface="中证指数官网历史行情接口 index-perf（与 AKShare stock_zh_index_hist_csindex 同源）",
        url=PERF_URL,
        retrieved_at=stamp,
        definition_note=(
            "官方历史行情文件。文件不带公布时刻，也不提供修订版本；"
            "滚动市盈率的加权方式与亏损样本处理未在文件内说明。"
            + ("全收益指数：把样本股分红再投资后的指数，主收益口径。" if kind == "tr" else "")
        ),
    )


def _get_rows(code: str, start: str, end: str, *, timeout: int = 40) -> list[dict[str, Any]]:
    params = {"indexCode": code, "startDate": start, "endDate": end}
    request = urllib.request.Request(
        f"{PERF_URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data") or []
    if not rows:
        raise SeriesRefreshError(f"中证指数官网没有返回 {code} 的历史行情")
    return rows


def _observations(rows: list[dict[str, Any]], value_key: str, launch: str) -> tuple[tuple[str, float], ...]:
    output: dict[str, float] = {}
    for row in rows:
        raw_date = str(row.get("tradeDate") or "")
        if len(raw_date) != 8:
            continue
        day = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        if day < launch:
            continue
        raw = row.get(value_key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        output[day] = value
    return tuple(sorted(output.items()))


def build_frames(code: str, *, now: datetime | None = None) -> list[IndexSeries]:
    """抓取并组装一个指数的三条官方序列（收盘、滚动 PE、全收益收盘）。"""
    clock = _now(now)
    stamp = clock.isoformat(timespec="seconds")
    meta = INDICES[code]
    launch = meta["launch"]
    rows = _get_rows(code, meta["start"], "20261231")
    tr_rows = _get_rows(meta["tr"], meta["start"], "20261231")
    close_obs = _observations(rows, "close", launch)
    pe_obs = _observations(rows, "peg", launch)
    tr_obs = _observations(tr_rows, "close", launch)
    if not close_obs or not pe_obs or not tr_obs:
        raise SeriesRefreshError(f"{code} 的官方序列为空")
    return [
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_OFFICIAL_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            source=_source("price", code, stamp),
            observations=close_obs,
            unit="index_point",
            transform="官方历史行情接口『收盘』原值；价格指数，不含分红",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            revision_availability="unknown",
            notes=f"只保留发布日 {launch} 及之后的记录。价格指数，不含分红。",
        ),
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_OFFICIAL_PE_ROLLING,
            field="滚动市盈率",
            frequency=FREQ_DAILY,
            source=_source("pe", code, stamp),
            observations=pe_obs,
            unit="ratio",
            transform="官方历史行情接口『peg』原列（与既有序列的滚动市盈率同源同列），未做换算",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            revision_availability="unknown",
            notes=(
                f"只保留发布日 {launch} 及之后的记录。"
                "非正值不参与分位；文件不带公布时刻，回测按 T+1 收盘执行并作 T+2 敏感性。"
            ),
        ),
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_TOTAL_RETURN_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            source=_source("tr", meta["tr"], stamp),
            observations=tr_obs,
            unit="index_point",
            transform=f"全收益指数 {meta['tr']} 官方历史行情接口『收盘』原值",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            revision_availability="unknown",
            notes=f"全收益口径（分红再投资），发布日 {launch} 之后的记录；用于主收益口径。",
        ),
    ]


def _latest_observations(store: SeriesStore, instrument_id: str, metric: str) -> tuple:
    item = store.find(instrument_id, metric, frequency=FREQ_DAILY)
    return item.observations if item is not None else ()


def _last_completed_week_end(now: datetime) -> date:
    day = now.date()
    weekday = day.weekday()
    if weekday >= 5:  # 周六 / 周日
        return day - timedelta(days=weekday - 4)
    if weekday == 4 and now.hour >= 16:  # 周五收盘后
        return day
    return day - timedelta(days=weekday + 3)


def _attempted_today(now: datetime) -> bool:
    if not ATTEMPT_STATE.exists():
        return False
    try:
        data = json.loads(ATTEMPT_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return data.get("date") == now.date().isoformat()


def _mark_attempt(now: datetime) -> None:
    ATTEMPT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ATTEMPT_STATE.write_text(
        json.dumps({"date": now.date().isoformat(), "attempted_at": now.isoformat(timespec="seconds")})
        + "\n",
        encoding="utf-8",
    )


def refresh_official_series(
    *,
    now: datetime | None = None,
    skip_network: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    if skip_network:
        return {"status": "skipped", "reason": "skip-network", "indices": []}
    clock = _now(now)
    store = SeriesStore(SERIES_DIR)
    expected_week_end = _last_completed_week_end(clock).isoformat()
    latest_dates = {}
    for code in CODE_ORDER:
        observations = _latest_observations(store, f"INDEX:{code}.SH", METRIC_OFFICIAL_CLOSE)
        latest_dates[code] = observations[-1][0] if observations else None
    if not force and all(value and value >= expected_week_end for value in latest_dates.values()):
        return {
            "status": "skipped",
            "reason": f"官方序列已覆盖最近已结束自然周（{expected_week_end}）",
            "expected_week_end": expected_week_end,
            "latest_dates": latest_dates,
        }
    if not force and _attempted_today(clock):
        return {
            "status": "skipped",
            "reason": "今日已尝试过官方序列刷新；不重复联网",
            "expected_week_end": expected_week_end,
            "latest_dates": latest_dates,
        }
    _mark_attempt(clock)
    summary = []
    changed = []
    for code in CODE_ORDER:
        frames = build_frames(code, now=clock)
        instrument_id = f"INDEX:{code}.SH"
        metrics = {
            METRIC_OFFICIAL_CLOSE: frames[0],
            METRIC_OFFICIAL_PE_ROLLING: frames[1],
            METRIC_TOTAL_RETURN_CLOSE: frames[2],
        }
        unchanged = all(
            _latest_observations(store, instrument_id, metric) == series.observations
            for metric, series in metrics.items()
        )
        summary.append(
            {
                "index_code": code,
                "status": "skipped" if unchanged else "updated",
                "latest_date": frames[0].end_date,
                "observations": {metric: len(item.observations) for metric, item in metrics.items()},
            }
        )
        if not unchanged:
            changed.extend(frames)
    if changed:
        for item in changed:
            store.save(item)
        store.rebuild_manifest()
    return {
        "status": "ok",
        "expected_week_end": expected_week_end,
        "indices": summary,
        "series_saved": len(changed),
        "series_dir": str(SERIES_DIR.relative_to(ROOT)),
    }


def refresh_signal_ledger(*, now: datetime | None = None, skip_network: bool = False) -> dict[str, Any]:
    """追加候选 A 周度信号；不依赖网络（只读已保存的官方序列）。"""
    if skip_network:
        return {"status": "skipped", "reason": "skip-network"}
    try:
        import tools.update_signal_ledger as updater
    except Exception as exc:  # noqa: BLE001
        raise SeriesRefreshError(f"无法加载信号账本更新器：{type(exc).__name__}: {exc}") from exc
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = updater.main([])
    except Exception as exc:  # noqa: BLE001
        raise SeriesRefreshError(f"信号账本更新失败：{type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(buffer.getvalue())
    except json.JSONDecodeError:
        payload = {}
    return {
        "status": "ok" if code == 0 else "conflict",
        "exit_code": code,
        "added": payload.get("added"),
        "skipped": payload.get("skipped"),
        "ledger_records_after": payload.get("ledger_records_after"),
        "indices": payload.get("indices"),
    }


__all__ = [
    "ATTEMPT_STATE",
    "CODE_ORDER",
    "INDICES",
    "SERIES_DIR",
    "SIGNAL_LEDGER",
    "SeriesRefreshError",
    "build_frames",
    "refresh_official_series",
    "refresh_signal_ledger",
]
