"""本地页面的数据层：所有数字与结论都经 `AskService`（与 `./ask` 同一引擎）。

页面只做三件事：把 `ask` 的卡片原文交给浏览器渲染、把同一份排行/池/账本数据整理成表格、
把 `--recompute` 的复算结果原样返回。页面不新增规则、不新增结论、不自己算估值。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date
from typing import Any

from engine import ledger_paths
from engine.ask.company_type import classify as classify_company_type
from engine.ask.company_type import ordered_types
from engine.ask.intent import Intent
from engine.ask.plain import cycle_text
from engine.ask.service import (
    BANK_REPORT,
    CONCLUSION_LABELS,
    INCOME_REPORT,
    POOL_LABELS,
    QUALITY_REPORT,
    AskService,
)
from engine.policy.conclusion_ledger import ConclusionLedger
from engine.repro.recompute import recompute
from engine.web.index_trend import pe_trend

POOL_TITLES = {"quality": "好公司池", "income": "稳定收息池", "bank": "银行池"}
POOL_ORDER = ("quality", "income", "bank")
REPORT_PATHS = {"quality": QUALITY_REPORT, "income": INCOME_REPORT, "bank": BANK_REPORT}

INDICES = (
    {
        "instrument_id": "INDEX:000300.SH",
        "name": "沪深300",
        "question": "沪深300 现在什么位置",
    },
    {
        "instrument_id": "INDEX:000905.SH",
        "name": "中证500",
        "question": "中证500 现在什么位置",
    },
    {
        "instrument_id": "INDEX:000852.SH",
        "name": "中证1000",
        "question": "中证1000 现在什么位置",
    },
    {"instrument_id": "INDEX:HSTECH.HKEX", "name": "恒生科技", "question": "恒生科技"},
)


def _label(conclusion: Any) -> str:
    return CONCLUSION_LABELS.get(str(conclusion), "无法判断")


def _jsonable(value: Any) -> Any:
    """把服务层返回值转成 JSON 可序列化结构（不改变数值）。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class WebApp:
    """本地页面的数据层（线程安全：所有服务调用串行化）。"""

    def __init__(
        self,
        *,
        service: AskService | None = None,
        allow_update: bool = True,
        analysis_date: date | None = None,
        write_ledger: bool = True,
    ) -> None:
        self.service = service or AskService(
            allow_update=allow_update,
            analysis_date=analysis_date,
            write_ledger=write_ledger,
        )
        self._lock = threading.RLock()
        self._stock_cache: dict[str, dict[str, Any]] = {}
        self._overview_cache: dict[str, Any] | None = None
        self._pools_cache: dict[str, Any] | None = None
        self._indices_cache: dict[str, Any] | None = None

    # --- 基础 -----------------------------------------------------------
    def _stock_result(self, code: str) -> dict[str, Any]:
        key = str(code).zfill(6)
        if key not in self._stock_cache:
            self._stock_cache[key] = self.service._evaluate_stock(key, write_ledger=False)
        return self._stock_cache[key]

    def name_for(self, code: str) -> str:
        key = str(code).zfill(6)
        return self.service.batch.name_for(key) or key

    # --- 提问 -----------------------------------------------------------
    def ask(self, question: str, *, detail: bool = False) -> dict[str, Any]:
        """与 `./ask` 完全相同的一次提问：同一引擎、同一写账本规则。"""
        with self._lock:
            result = self.service.ask(question, detail=detail)
        return {
            "markdown": result.markdown,
            "detail": detail,
            "intent": result.intent.to_dict(),
            "target": result.target.to_dict() if result.target else None,
            "data": _jsonable(result.data),
        }

    # --- 买点总览 -------------------------------------------------------
    def _overview_row(self, row: dict[str, Any], *, bank: bool = False) -> dict[str, Any]:
        pools = ["银行池"] if bank else list(row.get("pools") or [])
        return {
            "code": row["code"],
            "name": row["name"],
            "type": row.get("type") or ("银行池" if bank else ""),
            "pools": pools,
            "industry": row.get("industry") or ("银行" if bank else ""),
            "ownership": row.get("ownership") or "",
            "cycle": row.get("cycle") or "",
            "price": row.get("price"),
            "price_date": row.get("price_date"),
            "entry": row.get("entry"),
            "distance": row.get("distance"),
            "conclusion": row.get("conclusion"),
            "conclusion_label": _label(row.get("conclusion")),
            "grade": row.get("grade"),
            "binding": row.get("binding"),
        }

    def overview(self) -> dict[str, Any]:
        """候选买点与「离买点最近」排行：数据来自排行卡片同一套评估。"""
        with self._lock:
            if self._overview_cache is None:
                result = self.service._ranking(Intent("ranking", pool="all"), detail=False)
                data = result["data"]
                rows = [self._overview_row(row) for row in data.get("ranking") or []]
                rows += [
                    self._overview_row(row, bank=True) for row in data.get("bank_ranking") or []
                ]
                self._overview_cache = {
                    "data_date": data.get("data_date"),
                    "evaluated": data.get("count"),
                    "rows": rows,
                    "types": ordered_types([row["type"] for row in rows]),
                    "industries": sorted({row["industry"] for row in rows if row["industry"]}),
                    "ownerships": sorted(
                        {
                            row["ownership"]
                            for row in rows
                            if row["ownership"] and row["ownership"] != "未标记"
                        }
                    ),
                    "pool_filters": ["好公司池", "稳定收息池", "银行池"],
                    "card_markdown": result["markdown"],
                    "note": (
                        "与终端「现在有哪些候选买点」同一次评估；按类型分组，组内按距门槛排序。"
                        "距门槛为负表示现价低于门槛。排序不代表公司质量或收益预期。"
                    ),
                }
        return self._overview_cache

    # --- 三个池 ---------------------------------------------------------
    def _ownership_for(self, pool: str, code: str, row: dict[str, Any]) -> str:
        if pool == "bank":
            return str(row.get("ownership") or self.service._bank_ownership(code) or "未标记")
        return str(self.service.ownership.get(code) or row.get("ownership") or "未标记")

    def _cycle_for(self, pool: str, code: str) -> str:
        if pool == "bank":
            return cycle_text("bank")
        try:
            return str(self._stock_result(code).get("cycle_label") or "—")
        except Exception:  # noqa: BLE001 - 周期标签缺失不影响表格其它列
            return "—"

    def _columns(self, rows: list[dict[str, Any]]) -> list[str]:
        present = {metric["label"] for row in rows for metric in row["metrics"]}
        order = [label for _key, label in AskService.SCORECARD_FIELDS]
        return [label for label in order if label in present]

    def pools(self) -> dict[str, Any]:
        """三池表格：名单与指标来自已提交的 report.json（与成绩单卡片同一取数）。"""
        with self._lock:
            if self._pools_cache is not None:
                return self._pools_cache
            payload: dict[str, Any] = {}
            for pool in POOL_ORDER:
                report = json.loads(REPORT_PATHS[pool].read_text(encoding="utf-8"))
                meta = report.get("meta") or {}
                rows: list[dict[str, Any]] = []
                for row in report.get("in_pool") or []:
                    code = str(row.get("code")).zfill(6)
                    metrics: list[tuple[str, str]] = []
                    try:
                        company_result = self.service._company_result(pool, code)
                        if company_result is not None:
                            metrics = self.service._scorecard_rows(pool, code, company_result)
                    except Exception:  # noqa: BLE001
                        metrics = []
                    rows.append(
                        {
                            "code": code,
                            "name": str(row.get("name") or code),
                            "industry": str(row.get("industry") or "—"),
                            "board": str(row.get("board") or "—"),
                            "ownership": self._ownership_for(pool, code, row),
                            "cycle": self._cycle_for(pool, code),
                            "metrics": [
                                {"label": label, "value": value} for label, value in metrics
                            ],
                        }
                    )
                payload[pool] = {
                    "title": POOL_TITLES[pool],
                    "policy_id": meta.get("policy_id"),
                    "policy_status": meta.get("policy_status"),
                    "snapshot_id": meta.get("snapshot_id"),
                    "as_of": meta.get("as_of"),
                    "count": len(rows),
                    "columns": self._columns(rows),
                    "rows": rows,
                }
            self._pools_cache = {"pools": payload, "order": list(POOL_ORDER)}
            return self._pools_cache

    # --- 单家公司：卡片 + 成绩单 ----------------------------------------
    def company(
        self, code: str, *, pool: str | None = None, detail: bool = False
    ) -> dict[str, Any]:
        key = str(code).zfill(6)
        name = self.service.batch.name_for(key)
        if not name:
            return {"ok": False, "error": f"全市场清单里没有代码 {key}"}
        with self._lock:
            card = self.service.ask(f"{name}怎么样", detail=detail)
            company_type = card.data.get("company_type") or classify_company_type(
                key, self.service.pool_sets
            )
            payload: dict[str, Any] = {
                "ok": True,
                "code": key,
                "name": name,
                "company_type": company_type,
                "card": {"markdown": card.markdown, "detail": detail},
                "pool": pool if pool in POOL_LABELS else None,
            }
            if pool in POOL_LABELS:
                company_result = self.service._company_result(pool, key)
                scorecard = self.service.ask(
                    f"{name}在不在{POOL_LABELS[pool]}", detail=True
                )
                payload["scorecard"] = {
                    "markdown": scorecard.markdown,
                    "pool": POOL_LABELS[pool],
                    "in_pool": bool(
                        company_result is not None
                        and getattr(company_result, "status", "") == "in_pool"
                    ),
                }
        return payload

    # --- 指数 -----------------------------------------------------------
    def indices(self) -> dict[str, Any]:
        with self._lock:
            if self._indices_cache is not None:
                return self._indices_cache
            items: list[dict[str, Any]] = []
            for meta in INDICES:
                card = self.service.ask(meta["question"], detail=False)
                items.append(
                    {
                        "name": meta["name"],
                        "instrument_id": meta["instrument_id"],
                        "question": meta["question"],
                        "markdown": card.markdown,
                        "data": _jsonable(card.data),
                        "trend": pe_trend(meta["instrument_id"]),
                    }
                )
            self._indices_cache = {
                "indices": items,
                "note": (
                    "指数只做位置说明：结论固定「无法判断」，不给买点或目标点位；"
                    "分位走势只描述历史位置。"
                ),
            }
            return self._indices_cache

    # --- 账本 -----------------------------------------------------------
    def _ledger_rows(self) -> list[dict[str, Any]]:
        path = ledger_paths.read_path("equity-conclusions.jsonl")
        rows: list[dict[str, Any]] = []
        for record in ConclusionLedger(path).load():
            code = str(record.get("code") or "").zfill(6)
            reversal = record.get("record_type") == "reversal"
            rows.append(
                {
                    "record_id": record.get("record_id"),
                    "analysis_date": record.get("analysis_date"),
                    "recorded_at": record.get("recorded_at"),
                    "code": code,
                    "name": self.name_for(code) if code else "—",
                    "record_type": "reversal" if reversal else "formal",
                    "conclusion": record.get("conclusion"),
                    "conclusion_label": "无法判断（改判）" if reversal else _label(record.get("conclusion")),
                    "reversal_of": record.get("reversal_of"),
                    "reason": record.get("reason"),
                    "price": record.get("price"),
                    "price_date": record.get("price_date"),
                    "values": record.get("values") or {},
                    "grade": record.get("grade"),
                    "policy_version": record.get("policy_version"),
                    "snapshot_id": record.get("snapshot_id"),
                    "trace_present": bool(record.get("trace")),
                }
            )
        rows.sort(
            key=lambda row: (str(row["analysis_date"]), str(row["recorded_at"])), reverse=True
        )
        return rows

    def _filter_ledger(
        self, rows: list[dict[str, Any]], query: str | None
    ) -> list[dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return rows
        if re.fullmatch(r"\d{6}", text):
            return [row for row in rows if row["code"] == text]
        target = self.service.target_index.match(text)
        if (
            target is not None
            and getattr(target, "code", None)
            and not getattr(target, "ambiguous", False)
        ):
            code = str(target.code).zfill(6)
            return [row for row in rows if row["code"] == code]
        lowered = text.lower()
        return [
            row
            for row in rows
            if lowered in str(row["name"]).lower() or text in str(row["code"])
        ]

    def ledger(self, query: str | None = None) -> dict[str, Any]:
        with self._lock:
            rows = self._ledger_rows()
            filtered = self._filter_ledger(rows, query)
            companies: dict[str, dict[str, Any]] = {}
            for row in rows:
                item = companies.setdefault(
                    row["code"],
                    {"code": row["code"], "name": row["name"], "count": 0},
                )
                item["count"] += 1
            return {
                "rows": filtered,
                "total": len(rows),
                "returned": len(filtered),
                "query": query or "",
                "companies": sorted(companies.values(), key=lambda item: item["code"]),
                "source": "data/ledger/equity-conclusions.jsonl（只追加；旧记录不改）",
            }

    def recompute(self, record_id: str) -> dict[str, Any]:
        """与 `./ask --recompute <record_id>` 完全相同的复算（只读）。"""
        with self._lock:
            records = self._ledger_rows()
            known = next((row for row in records if row.get("record_id") == record_id), None)
            if known is None:
                return {
                    "record_id": record_id,
                    "status": "not_found",
                    "message": f"账本中找不到记录：{record_id}",
                    "fields": [],
                    "mismatched_fields": [],
                }
            path = ledger_paths.read_path("equity-conclusions.jsonl")
            record = next(
                (row for row in ConclusionLedger(path).load() if row.get("record_id") == record_id),
                None,
            )
            if record is None:  # pragma: no cover - 已由上面的查找保证
                return {"record_id": record_id, "status": "not_found", "fields": []}
            return _jsonable(recompute(record))

    # --- 数据状态 -------------------------------------------------------
    def status(self) -> dict[str, Any]:
        with self._lock:
            data = self.service.status_data()
        reviews: list[dict[str, str]] = []
        equity = data["equity_policy"]
        bank = data["bank_valuation"]
        pools = data["pool_rules"]
        for name, value in (
            (f"个股政策 {equity['policy_id']}", equity.get("review_date")),
            ("银行专用估值 v2.1", bank.get("review_date")),
            ("三池规则", pools.get("next_review_date")),
        ):
            if value:
                reviews.append({"name": name, "date": str(value)})
        reviews.sort(key=lambda item: item["date"])
        return {
            "status": data,
            "next_reviews": reviews,
            "next_review_date": reviews[0]["date"] if reviews else None,
        }


__all__ = ["INDICES", "POOL_ORDER", "POOL_TITLES", "REPORT_PATHS", "WebApp"]
