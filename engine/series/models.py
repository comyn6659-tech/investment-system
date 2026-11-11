"""指数估值序列：带来源、口径说明与内容哈希的可重放数据。

设计书要求「不能只有无版本缓存或覆盖统计而没有可复算的观测值」。因此这里把
画像所需的每条序列单独存成文件，`series_id` = 内容哈希；分析结果引用该 id，
任何一次画像都能按 id 重新复算。口径未知的地方显式写 `unknown`，不猜。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from engine.data.snapshot import stable_digest
from engine.data.timeutil import parse_instant

SERIES_SCHEMA_VERSION = "index-series/1"

# 序列频率标记：画像用 monthly_reference；官方短历史用 daily_short；
# 事后诊断用的价格指数日线用 daily
FREQ_MONTHLY_REFERENCE = "monthly_reference"
FREQ_DAILY_SHORT = "daily_short"
FREQ_DAILY = "daily"

METRIC_PE_ROLLING = "pe_rolling"
METRIC_PB = "pb"
METRIC_OFFICIAL_PE_1 = "official_pe_ratio_1"
# 价格指数收盘（不含分红），只用于事后价格收益诊断
METRIC_PRICE_INDEX_CLOSE = "price_index_close"


class SeriesError(RuntimeError):
    """序列不可用（缺失、哈希不符、结构非法）。"""


@dataclass(frozen=True)
class SeriesSource:
    source_id: str
    publisher: str
    interface: str
    url: str | None
    retrieved_at: str
    definition_note: str = ""

    def __post_init__(self) -> None:
        if parse_instant(self.retrieved_at) is None:
            raise SeriesError(f"来源 {self.source_id} 的抓取时间无法解析: {self.retrieved_at!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "publisher": self.publisher,
            "interface": self.interface,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "definition_note": self.definition_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SeriesSource":
        return cls(
            source_id=str(data["source_id"]),
            publisher=str(data.get("publisher", "")),
            interface=str(data.get("interface", "")),
            url=data.get("url"),
            retrieved_at=str(data["retrieved_at"]),
            definition_note=str(data.get("definition_note", "")),
        )


@dataclass(frozen=True)
class IndexSeries:
    """一条同源同列的历史序列。不同列、不同来源不得拼进同一条序列。"""

    instrument_id: str
    metric: str
    field: str
    frequency: str
    source: SeriesSource
    observations: tuple[tuple[str, float], ...]
    unit: str = "ratio"
    transform: str = ""
    aggregation_method: str = "unknown"
    denominator_basis: str = "unknown"
    loss_sample_handling: str = "unknown"
    revision_availability: str = "unknown"
    notes: str = ""
    schema_version: str = SERIES_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.observations:
            raise SeriesError(f"{self.instrument_id} {self.metric} 序列为空")
        if self.frequency not in (FREQ_MONTHLY_REFERENCE, FREQ_DAILY_SHORT, FREQ_DAILY):
            raise SeriesError(f"未知的序列频率 {self.frequency!r}")
        seen: set[str] = set()
        cleaned: list[tuple[str, float]] = []
        for raw_date, raw_value in self.observations:
            day = str(raw_date)[:10]
            if parse_instant(day) is None:
                raise SeriesError(f"观测日期无法解析: {raw_date!r}")
            value = float(raw_value)
            if value != value or value in (float("inf"), float("-inf")):
                raise SeriesError(f"观测值非有限数值: {raw_value!r}")
            if (day, value) in seen:
                continue
            seen.add((day, value))
            cleaned.append((day, value))
        object.__setattr__(self, "observations", tuple(sorted(cleaned)))

    # --- 身份 -----------------------------------------------------------
    def _content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "metric": self.metric,
            "field": self.field,
            "frequency": self.frequency,
            "source": self.source.to_dict(),
            "observations": [{"date": d, "value": v} for d, v in self.observations],
            "unit": self.unit,
            "transform": self.transform,
            "aggregation_method": self.aggregation_method,
            "denominator_basis": self.denominator_basis,
            "loss_sample_handling": self.loss_sample_handling,
            "revision_availability": self.revision_availability,
            "notes": self.notes,
        }

    @property
    def series_id(self) -> str:
        return "ser-" + stable_digest(self._content_payload())

    @property
    def start_date(self) -> str:
        return self.observations[0][0]

    @property
    def end_date(self) -> str:
        return self.observations[-1][0]

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["series_id"] = self.series_id
        payload["observation_count"] = len(self.observations)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, expected_id: str | None = None) -> "IndexSeries":
        if data.get("schema_version") != SERIES_SCHEMA_VERSION:
            raise SeriesError(f"未知的序列 schema: {data.get('schema_version')!r}")
        series = cls(
            instrument_id=str(data["instrument_id"]),
            metric=str(data["metric"]),
            field=str(data["field"]),
            frequency=str(data["frequency"]),
            source=SeriesSource.from_dict(data["source"]),
            observations=tuple((str(o["date"]), float(o["value"])) for o in data["observations"]),
            unit=str(data.get("unit", "ratio")),
            transform=str(data.get("transform", "")),
            aggregation_method=str(data.get("aggregation_method", "unknown")),
            denominator_basis=str(data.get("denominator_basis", "unknown")),
            loss_sample_handling=str(data.get("loss_sample_handling", "unknown")),
            revision_availability=str(data.get("revision_availability", "unknown")),
            notes=str(data.get("notes", "")),
            schema_version=str(data["schema_version"]),
        )
        declared = data.get("series_id")
        if declared is not None and declared != series.series_id:
            raise SeriesError(
                f"序列声明 {declared}，内容哈希为 {series.series_id}：内容已被改动"
            )
        if expected_id is not None and expected_id != series.series_id:
            raise SeriesError(f"请求的序列 {expected_id} 与文件内容 {series.series_id} 不一致")
        return series


@dataclass
class SeriesStore:
    """序列目录 + manifest 的只读仓库（写入用 save/rebuild_manifest）。"""

    root: Path
    manifest_path: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.manifest_path is None:
            self.manifest_path = self.root / "manifest.json"

    def load_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))["series"]

    def save(self, series: IndexSeries) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{series.series_id}.json"
        path.write_text(
            json.dumps(series.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return path

    def load(self, series_id: str) -> IndexSeries:
        path = self.root / f"{series_id}.json"
        if not path.exists():
            raise SeriesError(f"找不到序列文件: {path.name}")
        return IndexSeries.from_dict(
            json.loads(path.read_text(encoding="utf-8")), expected_id=series_id
        )

    def find(self, instrument_id: str, metric: str, *, frequency: str) -> IndexSeries | None:
        entries = [
            e
            for e in self.load_manifest()
            if e["instrument_id"] == instrument_id
            and e["metric"] == metric
            and e.get("frequency") == frequency
        ]
        if not entries:
            return None
        entries.sort(key=lambda e: (str(e.get("retrieved_at") or ""), e["series_id"]))
        return self.load(entries[-1]["series_id"])

    def rebuild_manifest(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("ser-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            series = IndexSeries.from_dict(data, expected_id=data.get("series_id"))
            entries.append(
                {
                    "series_id": series.series_id,
                    "instrument_id": series.instrument_id,
                    "metric": series.metric,
                    "frequency": series.frequency,
                    "source_id": series.source.source_id,
                    "retrieved_at": series.source.retrieved_at,
                    "start_date": series.start_date,
                    "end_date": series.end_date,
                    "observation_count": len(series.observations),
                    "file": path.name,
                }
            )
        entries.sort(key=lambda e: (e["instrument_id"], e["metric"], e["frequency"], e["series_id"]))
        self.manifest_path.write_text(
            json.dumps({"series": entries}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return entries

    def check_consistency(self) -> list[str]:
        problems: list[str] = []
        entries = self.load_manifest()
        seen: set[str] = set()
        for entry in entries:
            sid = entry["series_id"]
            if sid in seen:
                problems.append(f"manifest 中 series_id 重复: {sid}")
            seen.add(sid)
            path = self.root / entry["file"]
            if not path.exists():
                problems.append(f"manifest 指向的序列文件不存在: {entry['file']}")
                continue
            try:
                self.load(sid)
            except SeriesError as exc:
                problems.append(f"{sid}: {exc}")
        files = {p.name for p in self.root.glob("ser-*.json")}
        for name in sorted(files - {e["file"] for e in entries}):
            problems.append(f"序列文件未登记在 manifest: {name}")
        return problems


def metric_label(metric: str) -> str:
    return {
        METRIC_PE_ROLLING: "滚动市盈率",
        METRIC_PB: "市净率",
        METRIC_OFFICIAL_PE_1: "官方市盈率1",
        METRIC_PRICE_INDEX_CLOSE: "价格指数收盘",
    }.get(metric, metric)


__all__ = [
    "FREQ_DAILY",
    "FREQ_DAILY_SHORT",
    "FREQ_MONTHLY_REFERENCE",
    "IndexSeries",
    "METRIC_OFFICIAL_PE_1",
    "METRIC_PB",
    "METRIC_PE_ROLLING",
    "METRIC_PRICE_INDEX_CLOSE",
    "SERIES_SCHEMA_VERSION",
    "SeriesError",
    "SeriesSource",
    "SeriesStore",
    "metric_label",
]
