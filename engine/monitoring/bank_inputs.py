"""财报季刷新银行估值输入表（v2.1）。

只更新由财报/行情决定的字段：报告期、归母净资产、每股净资产、市净率、股本、价格，
以及年报季的 ROE 序列（净利润 ÷ 平均归母净资产）。不良率、拨备覆盖率仍来自银行池
年度快照，不在财报季内改动。

新表写成 `inputs-<报告期>.csv.gz`，旧 `inputs.csv.gz` 不动；清单 `current_inputs`
指向新表，历史账本记录仍按旧路径与哈希复算。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.bank.inputs import BANK_VALUATION_DIR, current_inputs
from engine.pool_valuation.adapter import BatchStore
from engine.quality.tables import read_table, write_table

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _num(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=path.name + "."
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


@dataclass(frozen=True)
class RefreshResult:
    period: str
    file: Path
    rows_updated: int
    roe_years: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "file": str(self.file),
            "rows_updated": self.rows_updated,
            "roe_years": list(self.roe_years),
        }


def _roe_series(annual: dict[int, dict[str, Any]]) -> dict[int, float]:
    output: dict[int, float] = {}
    years = sorted(annual)
    for index, year in enumerate(years):
        row = annual[year]
        profit = _num(row.get("PARENT_NETPROFIT"))
        equity = _num(row.get("TOTAL_PARENT_EQUITY"))
        if profit is None or equity is None:
            continue
        previous = None
        if index:
            previous = _num(annual[years[index - 1]].get("TOTAL_PARENT_EQUITY"))
        if previous is None:
            continue
        average = (previous + equity) / 2.0
        if average <= 0:
            continue
        output[year] = profit / average
    return output


def refresh_inputs(
    *,
    batch: BatchStore | None = None,
    bank_dir: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> RefreshResult:
    clock = _now(now)
    valuation_dir = Path(bank_dir or ROOT / "data" / "bank-pool") / "valuation-v2"
    inputs_path = current_inputs(valuation_dir)
    rows = read_table(inputs_path)
    if not rows:
        raise RuntimeError(f"银行估值输入为空：{inputs_path}")
    batch = batch or BatchStore()
    period = str(batch.earnings_latest_period or (batch.report_periods[-1] if batch.report_periods else ""))
    if not period:
        raise RuntimeError("批量快照没有可用的报告期")
    old_columns = list(rows[0].keys())
    new_columns = old_columns[:]
    for extra in ("roe_latest", "financial_period"):
        if extra not in new_columns:
            new_columns.append(extra)

    updated = 0
    roe_years: set[int] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "").zfill(6)
        if not batch.has_period(code, period):
            output.append(item)
            continue
        balance = batch.balance.get(period, {}).get(code) or {}
        equity = _num(balance.get("TOTAL_PARENT_EQUITY"))
        shares = batch.shares_for(code) or _num(item.get("shares"))
        other = _num(item.get("other_equity_tool")) or 0.0
        price, price_date = batch.price_for(code)
        if equity is None or not shares or shares <= 0:
            output.append(item)
            continue
        item["report_date"] = period
        item["parent_equity"] = equity
        item["shares"] = shares
        if price is not None:
            item["price"] = price
        item["price_date"] = price_date or str(item.get("price_date") or "")
        item["bvps"] = (equity - other) / shares
        if price is not None and item["bvps"]:
            item["pb_current"] = price / float(item["bvps"])
        annual = batch.annual_rows(code)
        series = _roe_series(annual)
        if series:
            latest_year = max(series)
            for year, value in series.items():
                key = f"roe_{year}"
                if key not in new_columns:
                    new_columns.append(key)
                item[key] = round(value, 6)
                roe_years.add(year)
            latest = series[latest_year]
            recent = [series[year] for year in sorted(series)[-3:]]
            five = [series[year] for year in sorted(series)[-5:]]
            item["roe_latest"] = round(latest, 6)
            item["roe_3y_avg"] = round(_mean(recent) or latest, 6)
            item["roe_5y_avg"] = round(_mean(five) or latest, 6)
            item["roe_5y_min"] = round(min(five), 6)
            item["loss_years_7"] = sum(
                1
                for year in sorted(series)[-7:]
                if (_num((annual.get(year) or {}).get("PARENT_NETPROFIT")) or 0) < 0
            )
        item["financial_period"] = period
        output.append(item)
        updated += 1

    target = valuation_dir / f"inputs-{period}.csv.gz"
    if not dry_run:
        meta = write_table(
            target,
            output,
            new_columns,
            metadata={
                "table_id": f"inputs-{period}",
                "kind": "bank_valuation_inputs",
                "report_date": period,
                "source_id": "bank.valuation.inputs",
                "publisher": "东方财富",
                "definition_note": "银行 v2.1 估值输入；财报季刷新资产负债表口径与 ROE 序列。",
                "generated_at": clock.isoformat(timespec="seconds"),
            },
        )
        manifest_path = valuation_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        periods = list(manifest.get("periods") or [])
        periods = [entry for entry in periods if str(entry.get("period")) != period]
        periods.append({"period": period, "file": target.name, "generated_at": meta["generated_at"]})
        tables = [entry for entry in manifest.get("tables") or [] if entry.get("file") != target.name]
        tables.append(dict(meta))
        manifest.update(
            {
                "generated_at": meta["generated_at"],
                "current_inputs": target.name,
                "current_history": manifest.get("current_history") or "history.csv.gz",
                "financial_period": period,
                "inputs_rows": len(output),
                "in_scope": sum(1 for row in output if str(row.get("in_scope")) == "True"),
                "periods": periods,
                "tables": tables,
            }
        )
        _write_json(manifest_path, manifest)
    return RefreshResult(period=period, file=target, rows_updated=updated, roe_years=sorted(roe_years))


__all__ = ["RefreshResult", "refresh_inputs"]
