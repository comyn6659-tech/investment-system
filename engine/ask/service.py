"""对话入口的核心服务：规则化意图 → 取数 → 固定结构卡片。

系统内部不调用任何大模型；卡片由本模块生成，Claude 只负责原样呈现。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.ask.intent import SUPPORTED_QUESTIONS, Intent, parse
from engine.ask.index_bands import band_for
from engine.ask.plain import (
    BANNED_TERMS,
    cycle_text,
    reason_plain,
    record_reasons,
    route_reason,
    unmet_plain,
)
from engine.ask.resolve import Target, TargetIndex, board_only, overseas
from engine.contracts import reason_codes as rc
from engine.data.snapshot import SnapshotStore
from engine.evidence.models import BusinessEvidence
from engine.forward import quotes, update
from engine.bank import history as bank_history
from engine.bank.conclusion import append_bank_formal, valuation_for as bank_valuation_for
from engine.forward.calendar import is_trading_day
from engine.income.evaluator import evaluate_company as evaluate_income
from engine.bank.evaluator import evaluate_company as evaluate_bank
from engine.policy.conclusion import build_policy_conclusion
from engine.policy.conclusion_ledger import (
    ConclusionConflict,
    ConclusionLedger,
    ConclusionRecord,
)
from engine.policy.equity import load_policy
from engine.policy.freshness import check_freshness
from engine.policy.trial import evaluate_company
from engine.policy.valuation import load_macro_series
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence
from engine.quality.evaluator import evaluate_company as evaluate_quality
from engine.quality.policy import load_policy as load_pool_policy
from engine.quality.snapshot import load_snapshot_dir
from engine.reporting.labels import condition_labels, family_label
from engine.quality.tables import read_table

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
INCOME_REPORT = ROOT / "data" / "income-pool" / "report.json"
BANK_REPORT = ROOT / "data" / "bank-pool" / "report.json"
QUALITY_POLICY = ROOT / "data" / "policies" / "quality-pool-v1.2.json"
INCOME_POLICY = ROOT / "data" / "policies" / "income-pool-v1.json"
BANK_POLICY = ROOT / "data" / "policies" / "bank-pool-v1.json"
QUALITY_SNAPSHOT = ROOT / "data" / "quality-pool" / "snapshot-qps-3ebbd24be612bf7e"
INCOME_SNAPSHOT = ROOT / "data" / "income-pool" / "snapshot-qps-d8fb7e3718675b93"
BANK_SNAPSHOT = ROOT / "data" / "bank-pool" / "snapshot-qps-dc9c12160476db8b"
OWNERSHIP = INCOME_SNAPSHOT / "ownership.csv.gz"
BANK_INPUTS = ROOT / "data" / "bank-pool" / "valuation-inputs.csv.gz"
BANK_INDICATORS = BANK_SNAPSHOT / "bank-indicators.csv.gz"
BANK_OWNERSHIP = BANK_SNAPSHOT / "ownership.csv.gz"
OFFICIAL_LEDGER = ROOT / "data" / "ledger" / "official-valuation-daily.jsonl"
HSTECH_LEDGER = ROOT / "data" / "ledger" / "hstech-monthly-valuation.jsonl"

CONCLUSION_LABELS = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}
POOL_LABELS = {"quality": "好公司池", "income": "稳定收息池", "bank": "银行池"}


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _money(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}%}"
    except (TypeError, ValueError):
        return str(value)


def _pct_points(value: Any, digits: int = 2) -> str:
    """已经是百分数的值（例如费率 0.15 表示 0.15%），直接加 %。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def _pct_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def _label(conclusion: str | None) -> str:
    return CONCLUSION_LABELS.get(str(conclusion), "无法判断")


def _clean_reason(text: str | None) -> str:
    """把内部英文标记换成中文，卡片默认不出现内部条件名。"""
    value = str(text or "")
    replacements = {
        "（*_by_default）": "",
        "(*_by_default)": "",
        "*_by_default": "机制默认",
        "by_default": "机制默认",
        "partial": "部分完成",
        "undetermined": "无法判断",
        "research": "研究",
        "route_not_evaluable": "路线不可评估",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


@dataclass
class AskResult:
    markdown: str
    intent: Intent
    target: Target | None
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "intent": self.intent.to_dict(),
            "target": self.target.to_dict() if self.target else None,
            "data": self.data,
        }


class AskService:
    def __init__(
        self,
        *,
        root: Path = ROOT,
        allow_update: bool = True,
        analysis_date: date | None = None,
        now: datetime | None = None,
        write_ledger: bool = True,
        ledger_path: Path | None = None,
        use_live_quotes: bool = True,
        update_timeout: float = 10.0,
    ):
        self.root = root
        self.allow_update = allow_update
        self.clock = _now(now)
        self.analysis_date = analysis_date or self.clock.date()
        self.write_ledger = write_ledger
        self.ledger_path = Path(ledger_path or LEDGER)
        self.use_live_quotes = use_live_quotes
        self.update_timeout = float(update_timeout)
        self._batch: BatchStore | None = None
        self._catalog = None
        self._target_index: TargetIndex | None = None
        self._policy = None
        self._macro = None
        self._quotes: dict[str, dict] | None = None
        self._analyzer = None
        self._pool_sets: dict[str, set[str]] | None = None
        self._pool_rows: dict[str, dict[str, dict]] | None = None
        self._ownership: dict[str, str] | None = None
        self._update_note: dict[str, Any] | None = None
        self._companies: dict[str, dict[str, Any]] = {}
        self._policies = {}

    # --- 懒加载 -------------------------------------------------------------
    @property
    def batch(self) -> BatchStore:
        if self._batch is None:
            self._batch = BatchStore()
        return self._batch

    @property
    def catalog(self):
        if self._catalog is None:
            from engine.identity.catalog import IdentityCatalog

            catalog = IdentityCatalog.load(ROOT / "data" / "registry" / "instruments.json")
            catalog.merge_equity_list(ROOT / "data" / "registry" / "cn_a_equities.json")
            self._catalog = catalog
        return self._catalog

    @property
    def target_index(self) -> TargetIndex:
        if self._target_index is None:
            self._target_index = TargetIndex.load(self.batch, self.catalog)
        return self._target_index

    @property
    def policy(self):
        if self._policy is None:
            self._policy = load_policy(POLICY)
        return self._policy

    @property
    def macro(self):
        if self._macro is None:
            self._macro = load_macro_series(MACRO)
        return self._macro

    @property
    def quote_rows(self) -> dict[str, dict]:
        if self._quotes is None:
            try:
                self._quotes = quotes.latest_quotes()
            except Exception:  # noqa: BLE001 - 行情快照损坏时回退到批量快照
                self._quotes = {}
        return self._quotes

    @property
    def analyzer(self):
        if self._analyzer is None:
            from engine.pipeline import Analyzer, AnalyzerConfig

            self._analyzer = Analyzer(AnalyzerConfig())
        return self._analyzer

    @property
    def pool_sets(self) -> dict[str, set[str]]:
        if self._pool_sets is None:
            quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
            income = json.loads(INCOME_REPORT.read_text(encoding="utf-8"))
            bank = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
            self._pool_sets = {
                "quality": {str(row["code"]).zfill(6) for row in quality["in_pool"]},
                "income": {str(row["code"]).zfill(6) for row in income["in_pool"]},
                "bank": {str(row["code"]).zfill(6) for row in bank["in_pool"]},
            }
        return self._pool_sets

    @property
    def pool_rows(self) -> dict[str, dict[str, dict]]:
        if self._pool_rows is None:
            quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
            income = json.loads(INCOME_REPORT.read_text(encoding="utf-8"))
            bank = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
            self._pool_rows = {
                "quality": {str(row["code"]).zfill(6): row for row in quality["in_pool"]},
                "income": {str(row["code"]).zfill(6): row for row in income["in_pool"]},
                "bank": {str(row["code"]).zfill(6): row for row in bank["in_pool"]},
            }
        return self._pool_rows

    @property
    def ownership(self) -> dict[str, str]:
        if self._ownership is None:
            self._ownership = {
                str(row["code"]).zfill(6): str(row.get("ownership") or "未标记")
                for row in read_table(OWNERSHIP)
            }
        return self._ownership

    def _pool_policy(self, pool: str):
        if pool not in self._policies:
            path = {"quality": QUALITY_POLICY, "income": INCOME_POLICY, "bank": BANK_POLICY}[pool]
            self._policies[pool] = load_pool_policy(path)
        return self._policies[pool]

    def _companies_for(self, pool: str) -> dict[str, Any]:
        if pool not in self._companies:
            path = {"quality": QUALITY_SNAPSHOT, "income": INCOME_SNAPSHOT, "bank": BANK_SNAPSHOT}[pool]
            _snapshot, companies, _stats = load_snapshot_dir(path)
            self._companies[pool] = {company.code: company for company in companies}
        return self._companies[pool]

    # --- 更新 ---------------------------------------------------------------
    def _update(self) -> dict[str, Any] | None:
        if not self.allow_update:
            return None
        if self._update_note is not None:
            return self._update_note
        note: dict[str, Any] = {"attempted": False}
        try:
            if not is_trading_day(self.clock.date()):
                note = {
                    "attempted": False,
                    "status": "skipped",
                    "reason": "非交易日，跳过自动更新",
                }
            elif not update.should_attempt(self.clock):
                state = update.last_run()
                note = {
                    "attempted": False,
                    "status": state.get("status"),
                    "run_at": state.get("run_at"),
                    "reason": "今日已成功更新，跳过联网",
                }
            else:
                result: dict[str, Any] = {}

                def worker() -> None:
                    try:
                        result["payload"] = update.run(now=self.clock, light=True)
                    except Exception as exc:  # noqa: BLE001
                        result["error"] = f"{type(exc).__name__}: {exc}"

                thread = threading.Thread(target=worker, daemon=True)
                started = time.monotonic()
                thread.start()
                thread.join(self.update_timeout)
                if thread.is_alive():
                    note = {
                        "attempted": True,
                        "status": "timeout",
                        "seconds": round(time.monotonic() - started, 1),
                        "reason": f"自动更新超过 {self.update_timeout:g} 秒",
                    }
                elif "error" in result:
                    note = {
                        "attempted": True,
                        "status": "failed",
                        "error": result["error"],
                    }
                else:
                    payload = result.get("payload") or {}
                    note = {
                        "attempted": True,
                        "status": payload.get("status", "ok"),
                        "run_at": payload.get("run_at"),
                        "steps": {item["step"]: item["status"] for item in payload.get("steps", [])},
                    }
        except Exception as exc:  # noqa: BLE001 - 失败不阻断回答
            note = {"attempted": True, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        self._update_note = note
        return note

    def _data_date(self, markdown: str) -> str:
        match = re.search(r"20\d\d-\d\d-\d\d", markdown)
        if match:
            return match.group(0)
        try:
            payload = quotes.latest_snapshot()
            if payload:
                return str(payload["price_date"])
        except Exception:  # noqa: BLE001
            pass
        return self.analysis_date.isoformat()

    def _update_notice(self, markdown: str) -> str | None:
        note = self._update_note or {}
        status = note.get("status")
        data_date = self._data_date(markdown)
        if status == "timeout":
            return (
                f"数据更新：自动更新超过 {self.update_timeout:g} 秒，本次未能更新；"
                f"数据截至 {data_date}。"
            )
        if status == "failed":
            return (
                "数据更新：自动更新失败（"
                + str(note.get("error") or "网络不可用")
                + f"）；数据截至 {data_date}，本次未能更新。"
            )
        if status == "skipped" and "非交易日" in str(note.get("reason") or ""):
            return f"数据更新：非交易日，未抓取新行情；数据截至 {data_date}。"
        return None

    # --- 入口 ---------------------------------------------------------------
    def ask(self, question: str, *, detail: bool = False) -> AskResult:
        update_note = self._update()
        target = self.target_index.match(question)
        intent = parse(question, target)
        if intent.name == "stock" and self._is_bank(intent.target):
            intent = Intent("bank", target=intent.target)
        data: dict[str, Any] = {"update": update_note}
        if intent.name == "ambiguous":
            markdown = self._ambiguous_card(question, intent, target)
        elif intent.name == "out_of_scope":
            markdown = self._out_of_scope_card(question, intent)
        elif intent.name == "status":
            markdown = self._status_card(detail=detail)
        elif intent.name == "history":
            markdown = self._history_card(intent.target, detail=detail)
        elif intent.name == "ranking":
            result = self._ranking(intent, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "pool_list":
            result = self._pool_list(intent, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "membership":
            result = self._membership(intent, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "bank":
            result = self._bank_card(intent.target, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "index":
            result = self._index_card(intent.target, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "etf":
            result = self._etf_card(intent.target, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        elif intent.name == "stock":
            if self._is_bank(intent.target):
                result = self._bank_card(intent.target, detail=detail)
            else:
                result = self._stock_card(intent.target, detail=detail)
            markdown = result["markdown"]
            data.update(result["data"])
        else:
            markdown = self._help_card(intent)
        notice = self._update_notice(markdown)
        if notice:
            lines = markdown.splitlines()
            if lines and lines[-1] == "不构成投资建议。":
                lines.insert(len(lines) - 1, notice)
            else:
                lines.append(notice)
            markdown = "\n".join(lines)
        if not detail:
            markdown = "\n".join(self._clip(markdown.splitlines()))
        data["question"] = question
        return AskResult(markdown=markdown, intent=intent, target=target, data=data)

    # --- 卡片：帮助 / 歧义 / 范围外 -----------------------------------------
    def _help_card(self, intent: Intent) -> str:
        lines = [
            "**结论：无法判断** — 没有识别出问题里的标的或问法。",
            "",
            "支持的问法（示例）：",
        ]
        lines.extend(f"- {item}" for item in SUPPORTED_QUESTIONS)
        lines += [
            "",
            "把问题换成上面的说法再试一次，例如「茅台怎么样」「好公司池有哪些」。",
            "数据与来源：本地固定快照、政策文件与身份注册表（核实 2026-11-11）；"
            "个股政策 equity-policy-v1.1（已批准 2026-11-12）。",
        ]
        return "\n".join(lines)

    def _ambiguous_card(self, question: str, intent: Intent, target: Target | None) -> str:
        candidates = list(target.candidates) if target is not None else []
        lines = [
            "**结论：无法判断** — 问题有歧义，需要先确认标的或范围。",
            "",
            f"原因：{intent.reason or '同一名称或代码对应多个候选'}",
        ]
        if candidates:
            lines.append("候选：")
            for item in candidates[:6]:
                lines.append(f"- {item.get('name')}（{item.get('kind')}，{item.get('code')}）")
            lines.append("请补充 6 位代码或完整名称，例如「000001 平安银行」或「上证指数」。")
        else:
            lines.append("请给出 6 位代码或完整名称。")
        lines += [
            "",
            "数据与来源：标的身份来自 data/registry/instruments.json（核实 2026-11-11）；"
            "个股政策 equity-policy-v1.1（已批准 2026-11-12）。",
            "不构成投资建议。",
        ]
        return "\n".join(lines)

    def _out_of_scope_card(self, question: str, intent: Intent) -> str:
        target = intent.target
        reason = intent.reason or (
            rc.describe(target.scope.reason_code) if target and getattr(target, "scope", None) else None
        )
        if target is not None:
            payload = getattr(target, "instrument_id", None)
            name = target.name
            code = target.code
        else:
            payload = None
            name = "范围外标的"
            code = "—"
        lines = [
            "**结论：无法判断** — 该标的不在本版已接入的研究范围内。",
            "",
            f"标的：{name}（{code}）",
        ]
        if reason:
            lines.append(f"原因：{reason}")
        if question.strip() in ("创业板", "创业板板块"):
            lines.append("说明：创业板指（399006）已于 2026-11-13 移出（官方历史估值不可得）。")
        lines += [
            "",
            "本版覆盖：境内 A 股个股、沪深300/中证500/中证1000/恒生科技指数、"
            "跟踪这些指数的境内普通被动 ETF。",
            "范围外不代表对该公司或产品的投资价值判断。",
        ]
        if intent.name == "out_of_scope" and "境外" in (reason or ""):
            lines.append("境外市场个股/基金需要另一套数据与规则，本版不接入。")
        lines += [
            "",
            "数据与来源：标的身份来自 data/registry/instruments.json（核实 2026-11-11）；"
            "研究范围与移出记录见同一文件。",
            "不构成投资建议。",
        ]
        return "\n".join(lines)

    # --- 卡片：状态 ---------------------------------------------------------
    def _latest_text_series(self, instrument_id: str, metric: str) -> tuple[str | None, str | None]:
        from engine.series.models import FREQ_DAILY, SeriesStore

        store = SeriesStore(ROOT / "data" / "series")
        item = store.find(instrument_id, metric, frequency=FREQ_DAILY)
        if item is None:
            return None, None
        return item.end_date, item.source.publisher

    def _status_card(self, *, detail: bool = False) -> str:
        quote_payload = None
        try:
            quote_payload = quotes.latest_snapshot()
        except Exception:  # noqa: BLE001
            quote_payload = None
        quote_date = quote_payload["price_date"] if quote_payload else "未取得"
        official_rows = []
        if OFFICIAL_LEDGER.exists():
            official_rows = [
                json.loads(line) for line in OFFICIAL_LEDGER.read_text(encoding="utf-8").splitlines() if line
            ]
        official_latest = max((row.get("date") for row in official_rows), default="—")
        hstech_rows = []
        if HSTECH_LEDGER.exists():
            hstech_rows = [
                json.loads(line) for line in HSTECH_LEDGER.read_text(encoding="utf-8").splitlines() if line
            ]
        hstech_latest = hstech_rows[-1] if hstech_rows else None
        state = update.last_run()
        policy = self.policy
        approval = policy.data.get("approval") or {}
        q_policy = json.loads(QUALITY_POLICY.read_text(encoding="utf-8")).get("approval") or {}
        lines = [
            "**结论：数据状态如下** — 回答里每个数字都带日期，正式结论按最新可用数据重算。",
            "",
            f"- 行情：{quote_date}（腾讯财经全市场快照；收盘前取上一交易日收盘）",
            "- 财务：2025 年报 + 2026 三季报（东方财富合并报表，批量快照 2026-11-12）",
            f"- 指数官方序列：{self._latest_text_series('INDEX:000300.SH', 'official_close')[0] or '—'}（中证指数官网）",
            f"- 官方当日估值账本：{official_latest}（沪深300 / 中证500 / 中证1000）",
        ]
        if hstech_latest:
            lines.append(
                f"- 恒生科技官方月度估值：{hstech_latest.get('date')} "
                f"（PE {hstech_latest.get('pe')}，股息率 {hstech_latest.get('dividend_yield_pct')}%）"
            )
        lines += [
            f"- 个股政策：{policy.policy_id}（{policy.status}；批准 {approval.get('approved_at')}；"
            f"复核 {approval.get('review_date')}）",
            f"- 池规则：好公司池 quality-pool-v1.2 / 收息池 income-pool-v1 / 银行池 bank-pool-v1"
            f"（批准 {q_policy.get('approved_at')}；下次复核 {q_policy.get('next_review_date')}）",
            f"- 前向更新：{state.get('run_at') or '尚未运行'}（{state.get('status') or '—'}）；"
            "每日定时任务需股东手动安装一次（scripts/install-daily-update.sh）",
        ]
        if detail:
            lines += [
                "",
                "【详情】",
                f"- 官方估值账本记录：{len(official_rows)} 条；恒生科技月度记录：{len(hstech_rows)} 条",
                f"- 行情快照行数：{len(quote_payload['rows']) if quote_payload else 0}",
                f"- 政策文件：{POLICY.relative_to(ROOT)}",
                f"- 更新日志：data/ledger/update-log.jsonl",
            ]
        lines += ["", "不构成投资建议。"]
        return "\n".join(lines)

    # --- 卡片：历史 ---------------------------------------------------------
    def _history_card(self, target: Target | None, *, detail: bool = False) -> str:
        if target is None:
            return self._help_card(Intent("unrecognized", reason="历史结论需要标的"))
        records = []
        if LEDGER.exists():
            for line in LEDGER.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if str(row.get("code")).zfill(6) == target.code or row.get("instrument_id") == target.instrument_id:
                    records.append(row)
        records.sort(key=lambda row: (str(row.get("analysis_date")), str(row.get("recorded_at"))))
        lines = [
            f"**结论：{target.name}的历史结论** — 账本记录共 {len(records)} 条"
            "（正式结论只记录候选买点/等待/回避；无法判断仅在改判时追加）。",
            "",
        ]
        if not records:
            lines += [
                "账本里没有该标的的正式结论记录。",
                "可能原因：从未给出正式结论（例如无法判断或资料不足），或标的不在已估值范围内。",
            ]
        else:
            for row in records[-5:]:
                values = row.get("values") or {}
                label = _label(row.get("conclusion"))
                extra = ""
                if row.get("record_type") == "reversal":
                    label = "无法判断（改判）"
                    extra = f"｜改判原因：{row.get('reason') or '—'}"
                lines.append(
                    f"- {row.get('analysis_date')}：{label}"
                    f"｜现价 {_money(row.get('price'))}（{row.get('price_date') or '—'}）"
                    f"｜基准价值 {_money(values.get('base'))}"
                    f"｜等级 {row.get('grade') or '—'}"
                    f"｜政策 {row.get('policy_version')}"
                    f"{extra}"
                )
            if detail:
                lines += ["", "【详情】"]
                for row in records:
                    values = row.get("values") or {}
                    label = _label(row.get("conclusion"))
                    if row.get("record_type") == "reversal":
                        label = "无法判断（改判）"
                    lines.append(
                        f"- {row.get('analysis_date')}｜{row.get('instrument_id')}｜"
                        f"快照 {row.get('snapshot_id')}｜{label}｜"
                        f"悲观/基准/乐观 {_money(values.get('pessimistic'))} / "
                        f"{_money(values.get('base'))} / {_money(values.get('optimistic'))}"
                        + (
                            f"｜原因码 {'、'.join(row.get('reason_codes') or [])}"
                            f"｜{row.get('reason')}"
                            if row.get("record_type") == "reversal"
                            else ""
                        )
                    )
        policies = sorted({str(row.get("policy_version")) for row in records if row.get("policy_version")})
        lines += [
            "",
            f"数据与来源：账本 data/ledger/equity-conclusions.jsonl（只追加）；分析日 {self.analysis_date.isoformat()}；"
            f"政策 {'、'.join(policies) if policies else '—'}。",
            "账本只追加、不改旧记录；同一标的同分析日同快照内容不同会报冲突，不覆盖。",
            "不构成投资建议。",
        ]
        return "\n".join(lines)

    # --- 个股估值 -----------------------------------------------------------
    def _evaluate_stock(self, code: str, *, write_ledger: bool = True) -> dict[str, Any]:
        code = str(code).zfill(6)
        quote = (self.quote_rows.get(code) or {}) if self.use_live_quotes else {}
        price = quote.get("price")
        price_date = quote.get("price_date")
        snapshot = build_snapshot(
            self.batch,
            code,
            price_override=price if price is not None else None,
            price_date_override=price_date if price is not None else None,
        )
        evidence = load_evidence(
            code,
            name=self.batch.name_for(code),
            industry=self.batch.industry_for(code),
            snapshot=snapshot,
        )
        record = evaluate_company(
            self.policy, snapshot=snapshot, evidence=evidence, macro=self.macro, code=code
        )
        hits = check_freshness(self.policy, snapshot, analysis_date=self.analysis_date)
        if hits and record.get("values"):
            import copy

            record = copy.deepcopy(record)
            record["status"] = "partial"
            record["status_reason"] = "；".join(hit.detail for hit in hits)
            record["grade"] = {
                "grade": "C",
                "margin_of_safety": None,
                "unmet_conditions": [hit.code for hit in hits],
            }
            record["trial_conclusion"] = {"conclusion": "undetermined", "reason": "数据新鲜度护栏触发"}
            record["reason_codes"] = [hit.code for hit in hits]
        block = build_policy_conclusion(
            record, policy=self.policy, analysis_date=self.analysis_date.isoformat()
        )
        pools = [
            POOL_LABELS[name]
            for name in ("quality", "income", "bank")
            if code in self.pool_sets.get(name, set())
        ]
        ownership = self.ownership.get(code) or "未标记"
        family = (record.get("route") or {}).get("family")
        result = {
            "record": record,
            "block": block,
            "snapshot": snapshot,
            "price": record.get("price"),
            "price_date": record.get("price_as_of"),
            "pools": pools,
            "ownership": ownership,
            "family": family,
            "cycle_label": cycle_text(family),
            "industry": self.batch.industry_for(code) or (evidence.industry_clue or ""),
            "name": self.batch.name_for(code),
            "ledger": None,
        }
        if write_ledger and self.write_ledger:
            result["ledger"] = self._write_ledger(record, code)
        return result

    def _write_ledger(self, record: dict[str, Any], code: str) -> dict[str, Any]:
        from engine.policy.conclusion import append_evaluation

        try:
            outcome = append_evaluation(
                ConclusionLedger(self.ledger_path),
                record,
                policy=self.policy,
                analysis_date=self.analysis_date.isoformat(),
            )
        except ConclusionConflict as exc:
            return {"status": "conflict", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": f"{type(exc).__name__}: {exc}"}
        if outcome.get("type") == "reversal":
            return {
                "status": "reversal",
                "detail": "改判为无法判断（旧记录不改）",
                "added": outcome.get("added") or [],
                "reversal_of": outcome.get("reversal_of"),
            }
        return {
            "status": "added" if outcome.get("added") else "skipped",
            "added": outcome.get("added") or [],
            "skipped": outcome.get("skipped") or [],
        }

    def _stock_reason(self, result: dict[str, Any]) -> str:
        block = result["block"]
        conclusion = block.get("conclusion")
        price = block.get("price", {}).get("value")
        entry = block.get("candidate_entry_price", {}).get("value") if block.get("candidate_entry_price") else None
        pessimism = (block.get("values") or {}).get("pessimistic") or {}
        optimistic = (block.get("values") or {}).get("optimistic") or {}
        if conclusion == "candidate_buy_point":
            return (
                f"现价 {_money(price)} 元（{block.get('price_date')}）不高于候选门槛 "
                f"{_money(entry)} 元，也不高于悲观情景价值 {_money(pessimism.get('value'))} 元。"
            )
        if conclusion == "wait":
            distance = block.get("distance_to_candidate_pct")
            tail = f"，高出 {distance:.1f}%" if distance is not None else ""
            return (
                f"现价 {_money(price)} 元（{block.get('price_date')}）仍高于候选门槛 "
                f"{_money(entry)} 元{tail}；未同时满足安全边际与悲观价值约束。"
            )
        if conclusion == "avoid":
            return (
                f"现价 {_money(price)} 元（{block.get('price_date')}）高于乐观情景价值 "
                f"{_money(optimistic.get('value'))} 元，按政策判「回避」。"
            )
        return self._plain_headline(result)

    def _plain_headline(self, result: dict[str, Any]) -> str:
        record = result["record"]
        route = record.get("route") or {}
        if route.get("status") == "family_not_implemented":
            return route_reason(record)
        reasons = record_reasons(record, headline_only=True)
        if reasons:
            return reasons[0]
        status = record.get("status")
        if status == "insufficient_data":
            return "这家公司缺必要的资料，系统无法判断。"
        if status == "partial":
            return "部分数据没有对上，系统只给部分结果，不给正式结论。"
        return "按已批准政策，这次评估不给正式结论。"

    def _plain_extras(self, result: dict[str, Any]) -> list[str]:
        record = result["record"]
        route = record.get("route") or {}
        if route.get("status") == "family_not_implemented":
            return []
        headline = self._plain_headline(result)
        reasons = record_reasons(record)
        extras = [text for text in reasons if text != headline]
        if "五年以后" in headline:
            extras = [text for text in extras if "五年以后" not in text]
        return extras

    def _undetermined_reason(self, result: dict[str, Any]) -> str:
        record = result["record"]
        route = record.get("route") or {}
        if route.get("status") == "family_not_implemented":
            fam = family_label(route.get("family"))
            category = route.get("applicable_model_category") or "对应专用口径"
            return f"{fam}企业路线首版未实现（{category}）；按已批准政策不给正式结论。"
        status = record.get("status")
        if status == "insufficient_data":
            return (record.get("status_reason") or "资料不足").replace("：", "；")
        if status == "partial":
            return (record.get("status_reason") or "结果状态 partial").replace("研究价值仅供研究参考，不用于结论", "不给结论")
        grade = (record.get("grade") or {}).get("grade")
        if grade == "C":
            unmet = condition_labels((record.get("grade") or {}).get("unmet_conditions") or [])
            detail = "、".join(unmet) if unmet else "未满足 A/B 级条件"
            return f"确定性等级 C（{detail}）；按政策不给正式结论。"
        return record.get("status_reason") or "按政策无法给出正式结论。"

    # --- 卡片：排行 ---------------------------------------------------------
    def _pool_codes(self, pool: str | None) -> list[str]:
        if pool == "quality":
            return sorted(self.pool_sets["quality"] - self.pool_sets["bank"])
        if pool == "income":
            return sorted(self.pool_sets["income"] - self.pool_sets["bank"])
        return sorted((self.pool_sets["quality"] | self.pool_sets["income"]) - self.pool_sets["bank"])

    def _ranking(self, intent: Intent, *, detail: bool = False) -> dict[str, Any]:
        pool = intent.pool or "all"
        if pool == "bank":
            return self._bank_pool_ranking(detail=detail)
        codes = self._pool_codes(pool)
        rows = []
        for code in codes:
            result = self._evaluate_stock(code, write_ledger=False)
            block = result["block"]
            conclusion = block.get("conclusion")
            if conclusion not in ("candidate_buy_point", "wait", "avoid"):
                continue
            if (result["record"].get("grade") or {}).get("grade") not in ("A", "B"):
                continue
            if result["record"].get("status") != "complete":
                continue
            distance = block.get("distance_to_candidate_pct")
            if distance is None:
                continue
            rows.append(
                {
                    "code": code,
                    "name": result["name"],
                    "price": result["price"],
                    "price_date": result["price_date"],
                    "entry": (block.get("candidate_entry_price") or {}).get("value"),
                    "distance": distance,
                    "conclusion": conclusion,
                    "grade": (result["record"].get("grade") or {}).get("grade"),
                    "pools": result["pools"],
                    "binding": (block.get("binding_condition") or {}).get("label"),
                }
            )
        rows.sort(key=lambda item: item["distance"])
        candidates = [row for row in rows if row["conclusion"] == "candidate_buy_point"]
        pool_text = {"all": "两个池（去重、非银行）", "quality": "好公司池", "income": "稳定收息池"}.get(
            pool, "两个池（去重、非银行）"
        )
        price_dates = sorted({row["price_date"] for row in rows if row["price_date"]})
        data_date = price_dates[-1] if price_dates else self.analysis_date.isoformat()
        lines = [
            f"**结论：{'有 ' + str(len(candidates)) + ' 个候选买点' if candidates else '当前没有候选买点'}**"
            f" — 基于 {data_date} 行情，对{pool_text} {len(codes)} 家重算；只列状态完整、等级 A/B 的正式结论。",
            "",
        ]
        if candidates:
            lines.append("候选买点（现价不高于候选门槛，且不高于悲观价值）：")
            for row in (candidates if detail else candidates[:10]):
                lines.append(
                    f"- {row['code']} {row['name']}：现价 {_money(row['price'])} 元"
                    f"｜门槛 {_money(row['entry'])}｜距门槛 {_pct_number(row['distance'])}"
                    f"｜等级 {row['grade']}"
                )
        else:
            lines.append("当前没有同时满足安全边际与悲观价值约束的标的。")
        closest = [row for row in rows if row["conclusion"] != "candidate_buy_point"][: (20 if detail else 5)]
        if closest:
            lines += ["", "距候选门槛最近（尚未到买点）："]
            for row in closest:
                lines.append(
                    f"- {row['code']} {row['name']}：现价 {_money(row['price'])} 元"
                    f"｜门槛 {_money(row['entry'])}｜{_pct_number(row['distance'])}"
                    f"｜{_label(row['conclusion'])}"
                )
        if pool == "all":
            bank_rows = self._bank_ranking_rows()
            bank_candidates = [
                row for row in bank_rows if row["conclusion"] == "candidate_buy_point"
            ]
            lines += ["", "银行部分（银行专用估值）："]
            if bank_candidates:
                lines.append(f"银行候选买点（{len(bank_candidates)} 家）：")
                for row in bank_candidates:
                    lines.append(
                        f"- {row['code']} {row['name']}：现价 {_money(row['price'])} 元"
                        f"｜门槛 {_money(row['entry'])}｜距门槛 {_pct_number(row['distance'])}"
                        f"｜候选买点"
                    )
            else:
                lines.append("银行池当前没有候选买点。")
        lines += [
            "",
            "入池不等于买入：池名单只说明历史公开数据满足规则，不代表现在值得买。",
            "说明：排序只按「现价距候选门槛」，不代表公司质量或收益预期；无法判断的标的不参与排行。",
            f"来源与日期：价格 腾讯财经（{data_date}）；政策 equity-policy-v1.1（已批准 2026-11-12）；"
            "财务 东方财富批量快照 2026-11-12。",
            "不构成投资建议。",
        ]
        return {"markdown": "\n".join(lines), "data": {"pool": pool, "count": len(codes), "candidates": candidates, "ranking": rows}}

    def _bank_pool_ranking(self, *, detail: bool = False) -> dict[str, Any]:
        rows = self._bank_ranking_rows()
        candidates = [row for row in rows if row["conclusion"] == "candidate_buy_point"]
        lines = [
            f"**结论：银行池{'有 ' + str(len(candidates)) + ' 个候选买点' if candidates else '当前没有候选买点'}**"
            " — 按银行专用估值的现价距门槛排序。",
            "",
        ]
        if candidates:
            lines.append("候选买点（现价不高于门槛价，且不高于悲观价值）：")
            for row in candidates:
                lines.append(
                    f"- {row['code']} {row['name']}：现价 {_money(row['price'])} 元"
                    f"｜门槛 {_money(row['entry'])}｜距门槛 {_pct_number(row['distance'])}"
                )
        else:
            lines.append("银行池当前没有同时满足安全边际与悲观价值约束的标的。")
        closest = [row for row in rows if row["conclusion"] != "candidate_buy_point"][: (20 if detail else 5)]
        if closest:
            lines += ["", "距候选门槛最近（尚未到买点）："]
            for row in closest:
                lines.append(
                    f"- {row['code']} {row['name']}：现价 {_money(row['price'])} 元"
                    f"｜门槛 {_money(row['entry'])}｜{_pct_number(row['distance'])}"
                    f"｜{_label(row['conclusion'])}"
                )
        lines += [
            "",
            "入池不等于买入：银行池只说明通过质量筛选，不代表现在便宜。",
            "说明：排序只按「现价距候选门槛」，不代表公司质量或收益预期；无法判断的标的不参与排行。",
            "来源与日期：价格 腾讯财经（最新交易日）；银行专用估值政策（已批准 2026-11-14，复核 2027-05-31）。",
            "不构成投资建议。",
        ]
        return {
            "markdown": "\n".join(lines),
            "data": {"pool": "bank", "count": len(rows), "candidates": candidates, "ranking": rows},
        }

    # --- 卡片：池名单 -------------------------------------------------------
    def _pool_list(self, intent: Intent, *, detail: bool = False) -> dict[str, Any]:
        pool = intent.pool or "all"
        ownership = intent.ownership
        quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
        income = json.loads(INCOME_REPORT.read_text(encoding="utf-8"))
        bank = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
        sections = []
        if pool in ("quality", "all"):
            rows = list(quality["in_pool"])
            if ownership:
                rows = [row for row in rows if ownership in str(row.get("ownership") or "")]
            sections.append(("好公司池", "quality-pool-v1.2", quality, rows))
        if pool in ("income", "all"):
            rows = list(income["in_pool"])
            if ownership:
                rows = [row for row in rows if ownership in str(row.get("ownership") or "")]
            sections.append(("稳定收息池", "income-pool-v1", income, rows))
        if pool in ("bank", "all"):
            rows = list(bank["in_pool"])
            sections.append(("银行池", "bank-pool-v1", bank, rows))
        total = sum(len(item[3]) for item in sections)
        headline = (
            f"**结论：{sections[0][0]}共 {total} 家**"
            if len(sections) == 1
            else f"**结论：三池名单共 {total} 家**"
        )
        lines = [
            headline
            + f"{'（所有制筛选：' + ownership + '）' if ownership else ''}"
            + " — 名单来自已批准规则与固定快照，不构成买入建议。",
            "",
        ]
        for name, policy_id, report, rows in sections:
            meta = report.get("meta") or {}
            lines.append(
                f"{name}：{len(rows)} 家｜规则 {policy_id}（{meta.get('policy_status') or 'approved'}）"
                f"｜快照 {meta.get('snapshot_id')}（{meta.get('as_of')}）"
            )
            if ownership:
                lines.append("  " + ("、".join(f"{row['code']} {row['name']}" for row in rows[:30]) or "没有符合条件的公司"))
            elif name == "银行池":
                for row in rows:
                    lines.append(
                        f"  - {row['code']} {row['name']}：ROE {float(row.get('roe_recent_average') or 0):.2f}%"
                        f"｜不良 {row.get('npl_ratio')}%｜拨备 {row.get('provision_coverage')}%"
                        f"｜核心一级 {row.get('core_tier1_ratio')}%"
                    )
            else:
                sample = rows if detail else rows[:12]
                lines.append("  " + "、".join(f"{row['code']} {row['name']}" for row in sample))
                if len(rows) > len(sample):
                    lines.append(f"  ……共 {len(rows)} 家；加 --detail 查看全部名单")
        if detail:
            lines += ["", "【详情】完整名单："]
            for name, _policy_id, _report, rows in sections:
                lines.append(f"{name}（{len(rows)} 家）：" + "、".join(f"{row['code']} {row['name']}" for row in rows))
        for name, _policy_id, _report, _rows in sections:
            if name == "银行池":
                lines.append(
                    "银行池不排行：银行专用估值方法还没启用，只列指标；可问：平安银行怎么样。"
                )
            else:
                lines.append(
                    f"想看哪些现在离买点最近，可问：{name}里谁离买点最近"
                )
        lines += [
            "",
            "入池不等于买入：入池只说明历史公开数据满足规则，不说明现在便宜；名单每年复盘。",
            "不构成投资建议。",
        ]
        return {"markdown": "\n".join(lines), "data": {"pool": pool, "ownership": ownership, "counts": {item[0]: len(item[3]) for item in sections}}}

    # --- 卡片：是否在池 -----------------------------------------------------
    def _company_result(self, pool: str, code: str):
        companies = self._companies_for(pool)
        company = companies.get(code)
        if company is None:
            return None
        policy = self._pool_policy(pool)
        evaluator = {"quality": evaluate_quality, "income": evaluate_income, "bank": evaluate_bank}[pool]
        return evaluator(company, policy)

    def _membership(self, intent: Intent, *, detail: bool = False) -> dict[str, Any]:
        target = intent.target
        if target is None or target.kind != "stock":
            return {"markdown": self._out_of_scope_card("", Intent("out_of_scope", reason="池名单只针对 A 股个股")), "data": {}}
        pool = intent.pool or "quality"
        if pool == "all":
            pool = "quality"
        label = POOL_LABELS.get(pool, "好公司池")
        result = self._company_result(pool, target.code)
        if result is None:
            return {
                "markdown": "\n".join(
                    [
                        f"**结论：不在{label}** — 批量快照中没有该公司的池规则评估记录。",
                        "",
                        "说明：该公司可能不在池规则覆盖范围（例如金融业、ST、上市不足 7 年），"
                        "或代码不在全市场清单中。",
                        "",
                        "不构成投资建议。",
                    ]
                ),
                "data": {},
            }
        in_pool = result.status == "in_pool"
        failing = result.failing
        if in_pool:
            headline = f"**结论：在{label}** — 全部条件通过。"
        elif result.status == "out_of_scope":
            headline = f"**结论：不在{label}** — 范围外：{result.reason}"
        else:
            headline = f"**结论：不在{label}** — 未通过 {len(failing)} 条条件。"
        lines = [headline, "", f"标的：{result.name}（{target.code}）｜行业 {result.industry or '—'}｜板块 {result.board or '—'}"]
        if failing:
            lines.append("成绩单（未通过条件）：")
            for item in failing[: (10 if detail else 5)]:
                lines.append(f"- {item.label}：{item.reason}")
        if result.reason and not in_pool and not failing:
            lines.append(f"原因：{result.reason}")
        if in_pool:
            metrics = self.pool_rows[pool].get(target.code) or result.metrics
            bits = []
            for key, label_text in (
                ("roe_average", "平均净资产收益率"),
                ("roe_recent_average", "近 3 年净资产收益率"),
                ("cash_conversion", "经营现金流与利润的匹配"),
                ("dividend_payout", "分红率"),
                ("net_debt_to_ebitda", "有息负债水平"),
                ("npl_ratio", "不良率"),
                ("provision_coverage", "拨备覆盖率"),
                ("core_tier1_ratio", "核心一级"),
            ):
                value = self.pool_rows[pool].get(target.code, {}).get(key)
                if value is None and isinstance(result.metrics, dict):
                    raw = result.metrics.get(key)
                    value = raw.get("value") if isinstance(raw, dict) else raw
                if value is None:
                    continue
                if pool == "bank" and key in (
                    "npl_ratio",
                    "provision_coverage",
                    "core_tier1_ratio",
                    "roe_average",
                    "roe_recent_average",
                ):
                    bits.append(f"{label_text} {float(value):.2f}%")
                elif key == "net_debt_to_ebitda":
                    bits.append(f"{label_text} {float(value):.2f} 倍")
                else:
                    bits.append(f"{label_text} {_pct(value)}")
            if bits:
                lines.append("成绩单：" + "｜".join(bits))
        lines += [
            "",
            f"规则：{label} {self._pool_policy(pool).policy_id}（已批准）；快照 2026-11-12；"
            "入池结果见 data/quality-pool/report.json 等固定文件。",
            "说明：未入池只说明历史公开数据未满足规则，不代表公司质量差；入池不等于买入。",
            "不构成投资建议。",
        ]
        if detail:
            lines += ["", "【详情】逐条条件："]
            for item in result.conditions:
                lines.append(f"- {item.label}：{item.status}（{item.reason}）")
        return {"markdown": "\n".join(lines), "data": {"pool": pool, "in_pool": in_pool, "status": result.status, "failing": [item.condition_id for item in failing]}}

    # --- 卡片：银行 ---------------------------------------------------------
    def _is_bank(self, target: Target | None) -> bool:
        if target is None:
            return False
        if target.code in self.pool_sets["bank"]:
            return True
        if target.code in self._bank_inputs():
            return True
        industry = self.batch.industry_for(target.code)
        return "银行" in (industry or "")

    def _bank_equity(self, code: str) -> float | None:
        if not hasattr(self, "_bank_balance"):
            self._bank_balance = {
                str(row["SECURITY_CODE"]).zfill(6): row
                for row in read_table(BANK_SNAPSHOT / "balance-2025.csv.gz")
            }
        row = self._bank_balance.get(code) or {}
        value = row.get("TOTAL_PARENT_EQUITY")
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _bank_indicator(self, code: str) -> dict[str, Any]:
        if not hasattr(self, "_bank_indicator_rows"):
            grouped: dict[str, list[dict]] = {}
            for row in read_table(BANK_INDICATORS):
                grouped.setdefault(str(row.get("code")).zfill(6), []).append(row)
            latest: dict[str, dict] = {}
            fill_fields = ("npl_ratio", "provision_coverage", "core_tier1_ratio")
            for key, items in grouped.items():
                items.sort(key=lambda row: str(row.get("report_date") or ""))
                current = dict(items[-1])
                for field in fill_fields:
                    if current.get(field) not in (None, ""):
                        continue
                    for previous in reversed(items[:-1]):
                        if previous.get(field) not in (None, ""):
                            current[field] = previous[field]
                            current[f"{field}_fallback_date"] = previous.get("report_date")
                            break
                latest[key] = current
            self._bank_indicator_rows = latest
        return self._bank_indicator_rows.get(code) or {}

    def _bank_ownership(self, code: str) -> str | None:
        if not hasattr(self, "_bank_ownership_map"):
            self._bank_ownership_map = {
                str(row["code"]).zfill(6): str(row.get("ownership") or "")
                for row in read_table(BANK_OWNERSHIP)
            }
        return self._bank_ownership_map.get(code)

    def _bank_inputs(self) -> dict[str, dict]:
        import csv
        import gzip

        if not hasattr(self, "_bank_rows"):
            with gzip.open(BANK_INPUTS, "rt", encoding="utf-8") as handle:
                self._bank_rows = {str(row["code"]).zfill(6): row for row in csv.DictReader(handle)}
        return self._bank_rows

    def _bank_formal_card(
        self,
        target: Target,
        valuation: dict[str, Any],
        *,
        detail: bool = False,
        pb: float | None = None,
        dividend_yield: float | None = None,
    ) -> dict[str, Any]:
        code = target.code
        price = valuation.get("price")
        price_date = valuation.get("price_date")
        threshold = valuation.get("threshold_price")
        values = valuation.get("values") or {}
        roe = valuation.get("roe") or {}
        binding = (
            "安全边际（基准价值打折后的价格）"
            if valuation.get("binding_condition") == "margin_of_safety"
            else "悲观价值"
        )
        distance = (
            (float(price) / float(threshold) - 1.0) * 100.0
            if price is not None and threshold not in (None, 0)
            else None
        )
        conclusion = valuation.get("conclusion")
        headline = {
            "candidate_buy_point": "候选买点",
            "wait": "等待",
            "avoid": "回避",
        }.get(conclusion, "无法判断")
        if conclusion == "candidate_buy_point":
            reason = "现价不高于门槛价，也不高于悲观价值。"
        elif conclusion == "wait":
            reason = "现价仍高于门槛价，未同时满足安全边际与悲观价值约束。"
        elif conclusion == "avoid":
            reason = "现价高于乐观价值，按规则判回避。"
        else:
            reason = "银行数据不完整，暂不给正式结论。"
        indicator = self._bank_indicator(code) or {}
        ownership = self._bank_ownership(code) or "未标记"
        implicit = valuation.get("implicit_return")
        lines = [
            f"**结论：{headline}** — {reason}",
            "",
            f"现价 {_money(price)} 元（{price_date or '日期未知'}）｜门槛 {_money(threshold)} 元"
            f"｜距门槛 {_pct_number(distance)}",
            "正式结论（银行专用估值政策，已批准 2026-11-14）",
            "关键指标："
            f"市净率 {pb:.2f} 倍｜股息率 {_pct(dividend_yield)}｜"
            f"不良率 {indicator.get('npl_ratio') or '—'}%｜拨备覆盖率 "
            f"{indicator.get('provision_coverage') or '—'}%｜核心一级 "
            f"{indicator.get('core_tier1_ratio') or '—'}%",
            "三档价值（元/股）："
            f"悲观 {_money(values.get('pessimistic'))}（最悲观假设下每股值多少）"
            f"｜基准 {_money(values.get('base'))}（安全边际的计算基准）"
            f"｜乐观 {_money(values.get('optimistic'))}（高于它就判回避）",
            f"为什么：起约束条件 {binding}；{reason}",
            "三档净资产收益率："
            f"基准 {_pct(roe.get('base'))}（假设停在最近一年）"
            f"｜悲观 {_pct(roe.get('pessimistic'))}（再按近 3 年速度下滑 3 年）"
            f"｜乐观 {_pct(roe.get('optimistic'))}（近 3 年平均）",
            bank_history.one_line(code),
            *(
                [f"隐含长期回报率：约 {implicit:.1%}（假设净资产收益率保持最新水平、按现价买入；描述性，不参与结论）。"]
                if implicit is not None
                else []
            ),
            f"它是什么：银行池成员｜所有制 {ownership}｜周期属性 {cycle_text('bank')}｜等级 B（银行池最高等级）",
            "局限：净资产收益率仍在下滑；报表坏账可能低估；只能前向验证。",
            "来源与日期：行情 腾讯财经（最新交易日）；财务 东方财富 2025 年报 / 2026 三季报；"
            "银行专用估值政策（已批准 2026-11-14，复核 2027-05-31）。",
            "不构成投资建议。",
        ]
        if detail:
            lines += [
                "",
                "【详情】",
                f"- 每股净资产：{_money(valuation.get('bvps'))} 元",
                f"- 银行股权成本 k：{_pct(valuation.get('cost_of_equity'), 2)}；银行溢价：{_pct(valuation.get('bank_premium'), 1)}",
                f"- 基准价值对应合理市净率：{_money((valuation.get('multiples') or {}).get('base'), 3)}",
                f"- 资产质量扣减：{'是' if valuation.get('asset_quality_deduction') else '否'}",
            ]
        ledger = None
        if self.write_ledger:
            ledger = append_bank_formal(
                valuation,
                ledger_path=self.ledger_path,
                analysis_date=self.analysis_date.isoformat(),
            )
        return {
            "markdown": "\n".join(lines),
            "data": {
                "code": code,
                "conclusion": conclusion,
                "in_bank_pool": True,
                "price": price,
                "price_date": price_date,
                "threshold_price": threshold,
                "values": values,
                "implicit_return": implicit,
                "ledger": ledger,
            },
        }

    def _bank_ranking_rows(self) -> list[dict[str, Any]]:
        rows = []
        for code in sorted(self.pool_sets["bank"]):
            quote = (self.quote_rows.get(code) or {}) if self.use_live_quotes else {}
            price = quote.get("price")
            price_date = quote.get("price_date")
            valuation = bank_valuation_for(code, price=price, price_date=price_date)
            if valuation is None or valuation.get("conclusion") not in (
                "candidate_buy_point",
                "wait",
                "avoid",
            ):
                continue
            threshold = valuation.get("threshold_price")
            price = valuation.get("price")
            distance = (
                (float(price) / float(threshold) - 1.0) * 100.0
                if price is not None and threshold not in (None, 0)
                else None
            )
            if distance is None:
                continue
            rows.append(
                {
                    "code": code,
                    "name": valuation["name"],
                    "price": price,
                    "price_date": valuation.get("price_date"),
                    "entry": threshold,
                    "distance": distance,
                    "conclusion": valuation["conclusion"],
                    "values": valuation.get("values") or {},
                }
            )
        rows.sort(key=lambda item: item["distance"])
        return rows

    def _bank_card(self, target: Target | None, *, detail: bool = False) -> dict[str, Any]:
        if target is None:
            return {"markdown": self._help_card(Intent("unrecognized")), "data": {}}
        code = target.code
        inputs = self._bank_inputs().get(code)
        in_pool = code in self.pool_sets["bank"]
        if inputs is None and not in_pool:
            return self._stock_card(target, detail=detail)
        quote = (self.quote_rows.get(code) or {}) if self.use_live_quotes else {}
        price = quote.get("price") or (float(inputs["price"]) if inputs and inputs.get("price") else None)
        price_date = quote.get("price_date") or (inputs.get("price_date") if inputs else None)
        shares = float(inputs["shares"]) if inputs and inputs.get("shares") else self.batch.shares_for(code)
        equity = self._bank_equity(code)
        equity_basis = "2025 年报归母净资产"
        if equity is None and inputs:
            try:
                net_profit_for_equity = float(inputs["base_net_profit"])
                roe_for_equity = float(inputs["roe_latest"])
                if roe_for_equity > 0:
                    equity = net_profit_for_equity / roe_for_equity
                    equity_basis = "2025 归母净利润 ÷ 加权 ROE 推算"
            except (TypeError, ValueError, KeyError):
                equity = None
        pb = price * shares / equity if price and shares and equity else None
        dividend_yield = None
        roe = None
        if inputs:
            try:
                net_profit = float(inputs["base_net_profit"])
                payout = float(inputs["payout_3y"])
                roe = float(inputs["roe_latest"])
                if price and shares and net_profit:
                    dividend_yield = (net_profit / shares) * payout / price
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                pass
        bank_row = self.pool_rows["bank"].get(code) or {}
        indicator = self._bank_indicator(code)
        npl = indicator.get("npl_ratio") or bank_row.get("npl_ratio")
        provision = indicator.get("provision_coverage") or bank_row.get("provision_coverage")
        cet1 = indicator.get("core_tier1_ratio") or bank_row.get("core_tier1_ratio")
        ownership = (
            self._bank_ownership(code)
            or bank_row.get("ownership")
            or self.ownership.get(code)
            or "未标记"
        )
        valuation = bank_valuation_for(code, price=price, price_date=price_date)
        if in_pool and valuation and valuation.get("conclusion") in (
            "candidate_buy_point",
            "wait",
            "avoid",
        ):
            return self._bank_formal_card(
                target, valuation, detail=detail, pb=pb, dividend_yield=dividend_yield
            )
        implicit_line = bank_history.implicit_return_line(code, price)
        if in_pool:
            conclusion_text = "银行数据不完整，暂不给正式结论。"
        else:
            conclusion_text = "不在银行池（未通过：银行池规则）；只给价值数字，不给买卖结论。"
        lines = [
            f"**结论：无法判断** — {conclusion_text}",
            "",
            f"是否在银行池：{'是' if in_pool else '否'}（银行池共 {len(self.pool_sets['bank'])} 家）"
            f"｜现价 {_money(price)} 元（{price_date or '日期未知'}）",
            f"关键指标：市净率 {pb:.2f} 倍" if pb is not None else "关键指标：市净率 —",
            f"股息率 {_pct(dividend_yield)}｜净资产收益率 {_pct(roe)}"
            f"｜不良率 {npl if npl is not None else '—'}%"
            f"｜拨备覆盖率 {provision if provision is not None else '—'}%"
            f"｜核心一级 {cet1 if cet1 is not None else '—'}%",
            *(
                [
                    "三档价值（元/股）："
                    f"悲观 {_money(valuation['values'].get('pessimistic'))}"
                    f"｜基准 {_money(valuation['values'].get('base'))}"
                    f"｜乐观 {_money(valuation['values'].get('optimistic'))}"
                    "（v2.1 试算，只给数字）"
                ]
                if valuation and valuation.get("values")
                else []
            ),
            bank_history.one_line(code),
            *([implicit_line] if implicit_line else []),
            "为什么：非银行池未通过银行池规则，只展示公开指标与 v2.1 试算价值，不给买卖结论。",
            f"它是什么：{'银行池成员' if in_pool else '银行（不在银行池）'}｜所有制 {ownership}"
            f"｜周期属性 {cycle_text('bank')}｜等级 —（非银行池不评等级）",
            "局限：净资产收益率仍在下滑；报表坏账可能低估；只能前向验证。",
            f"来源与日期：行情 腾讯财经（{price_date or '—'}）；财务 东方财富 2025 年报 / 2026 三季报；"
            "银行专用估值政策（已批准 2026-11-14，复核 2027-05-31）。",
            "不构成投资建议。",
        ]
        if detail:
            lines += [
                "",
                "【详情】",
                f"- 总股本：{shares:,.0f}" if shares else "- 总股本：—",
                f"- 归母净资产：{equity:,.0f} 元（{equity_basis}）" if equity else "- 归母净资产：—",
                f"- 近 3 年派息率：{float(inputs['payout_3y']):.1%}" if inputs and inputs.get("payout_3y") else "- 近 3 年派息率：—",
                f"- 数据完整：{inputs.get('data_complete') if inputs else '—'}",
            ]
        return {
            "markdown": "\n".join(lines),
            "data": {
                "code": code,
                "conclusion": "undetermined",
                "in_bank_pool": in_pool,
                "pb": pb,
                "dividend_yield": dividend_yield,
                "roe": roe,
                "npl": npl,
                "provision": provision,
                "cet1": cet1,
                "ownership": ownership,
            },
        }

    @staticmethod
    def _percentile_sentence(percentile: float) -> str:
        if percentile >= 50:
            return f"比过去 5 年里 {percentile:.0f}% 的时间都贵"
        return f"比过去 5 年里 {100 - percentile:.0f}% 的时间都便宜"

    # --- 卡片：指数 ---------------------------------------------------------
    def _index_card(self, target: Target | None, *, detail: bool = False) -> dict[str, Any]:
        if target is None:
            return {"markdown": self._help_card(Intent("unrecognized")), "data": {}}
        scope = target.scope or {}
        if scope.get("status") == "out_of_scope":
            return {
                "markdown": self._out_of_scope_card(target.name, Intent("out_of_scope", target=target, reason=scope.get("detail"))),
                "data": {},
            }
        try:
            result = self.analyzer.analyze(target.name or target.code)
        except Exception as exc:  # noqa: BLE001
            return {"markdown": f"**结论：无法判断** — 指数数据读取失败：{type(exc).__name__}: {exc}", "data": {}}
        payload = result.to_dict().get("payload") or {}
        profile = payload.get("valuation_profile") or {}
        signal = payload.get("candidate_a_signal") or {}
        if not profile:
            reason = "；".join(rc.describe(code) for code in result.reason_codes[:3])
            return {
                "markdown": "\n".join(
                    [
                        f"**结论：无法判断** — {target.name}没有可用的官方估值历史，不能计算分位。",
                        "",
                        f"原因：{reason or '官方估值历史不足'}",
                        "本版只对已接入的沪深300、中证500、中证1000、恒生科技给出可核实的数据。",
                        "不构成投资建议。",
                    ]
                ),
                "data": {},
            }
        metrics = profile.get("metrics") or {}
        pe = metrics.get("pe_rolling") or {}
        pb = metrics.get("pb") or {}
        pe_percentile = (
            float(pe["percentile"])
            if pe.get("status") == "computed" and pe.get("percentile") is not None
            else None
        )
        pb_percentile = (
            float(pb["percentile"])
            if pb.get("status") == "computed" and pb.get("percentile") is not None
            else None
        )
        band = band_for(pe_percentile)
        if band:
            headline = band["summary"]
        elif not metrics:
            headline = "官方月度估值历史不足，无法给出近 5 年位置"
        else:
            headline = "暂无可用的近 5 年位置"
        latest = (signal.get("latest") or {}) if signal else {}
        lines = [
            f"**结论：无法判断** — {headline}。",
            "说明：指数只做位置说明，规则未批准，不给买点或目标点位。",
            "",
            f"标的：{target.name}（{target.instrument_id or target.code}）｜画像日期 {profile.get('as_of') or '—'}",
        ]
        monthly_bits = []
        for item, label in ((pe, "PE"), (pb, "PB")):
            comparison = ((item.get("window") or {}).get("comparison") or {})
            if item.get("status") == "computed" and item.get("percentile") is not None:
                publisher = (item.get("source") or {}).get("publisher") or "第三方月度参考"
                monthly_bits.append(
                    f"{label} {comparison.get('value')}（{publisher}，{comparison.get('date')}；用于近 5 年分位）"
                )
        if monthly_bits:
            lines.append("月度参考：" + "｜".join(monthly_bits))
        else:
            lines.append("月度参考：没有足够的同源历史，未计算分位。")

        if latest:
            lines.append(
                f"官方周度：PE {latest.get('pe')}（中证指数官网，{latest.get('date')}；用于研究信号）"
            )
            lines.append(
                "两个 PE 为什么不同：来源不同（第三方月度参考 vs 中证指数官网日频）、时点不同、"
                "加权与样本口径未公开，不能直接对比。"
            )
            signal_text = {
                "entry": "按研究规则处于偏便宜区间（买入一侧）",
                "exit": "按研究规则处于偏贵区间（卖出/不买入一侧）",
                "hold": "按研究规则继续持有（中间区域）",
                "none": "按研究规则没有触发（中间区域）",
                "not_evaluable": "数据不足，无法判断",
            }.get(str(signal.get("signal")), "暂无可评估的研究信号")
            lines.append(f"研究信号：{signal_text}；研究信号，规则未批准。")
        else:
            lines.append("研究信号：暂无可评估的周度观察点。")

        sentences = []
        if pe_percentile is not None:
            sentences.append(f"PE {self._percentile_sentence(pe_percentile)}")
        if pb_percentile is not None:
            sentences.append(f"PB {self._percentile_sentence(pb_percentile)}")
        if sentences:
            lines.append("直白说：" + "；".join(sentences) + "。")

        if not metrics and profile.get("official_short_history"):
            short = profile["official_short_history"]
            official = {}
            for key in ("official_pe_ratio_1", "official_pe_1"):
                official = ((short.get(key) or {}).get("latest") or {})
                if official:
                    break
            if official:
                dividend = self._hstech_dividend_yield()
                dy_text = f"、股息率 {dividend}%" if dividend is not None else ""
                lines.append(
                    f"官方月度：PE {official.get('value')}（恒生指数公司，{official.get('date')}；"
                    f"股息率 {dividend}%；只作月度快照，不用于分位或研究信号）。"
                    if dividend is not None
                    else f"官方月度：PE {official.get('value')}（恒生指数公司，{official.get('date')}；"
                    "只作月度快照，不用于分位或研究信号）。"
                )
        lines += [
            "为什么：分位只描述历史位置，不是买卖判断；研究信号的规则与阈值还没有批准。",
            "局限：分位依赖供应商历史序列口径；官方最新值不参与第三方历史排名；指数没有盈利预测，无法给内在价值。",
            f"来源与日期：{self._index_sources(metrics, signal) if metrics else self._index_snapshot_source(target)}",
            "不构成投资建议。",
        ]
        if detail:
            lines += ["", "【详情】"]
            for key, item in metrics.items():
                lines.append(
                    f"- {item.get('label') or key}：状态 {item.get('status')}，分位 {item.get('percentile')}，"
                    f"有效月 {((item.get('window') or {}).get('valid_months'))}/"
                    f"{((item.get('window') or {}).get('expected_months'))}"
                )
            if signal:
                lines.append(
                    f"- 原始研究信号：{signal.get('signal')}（候选 A；规则未批准）"
                    f"，最新观察点 {latest.get('date') if latest else '—'}"
                )
            backtest = signal.get("backtest") or {}
            if backtest:
                lines.append(
                    f"- 候选 A 回测：{backtest.get('start')}→{backtest.get('end')}，{backtest.get('trades')} 笔，"
                    f"累计 {_pct(backtest.get('cumulative_return'))}，最大回撤 {_pct(backtest.get('max_drawdown'))}"
                )
        return {
            "markdown": "\n".join(lines),
            "data": {
                "instrument_id": target.instrument_id,
                "conclusion": "undetermined",
                "signal": signal.get("signal"),
                "profile_status": profile.get("status"),
                "band": band["label"] if band else None,
                "pe_percentile": pe_percentile,
            },
        }

    @staticmethod
    def _hstech_dividend_yield() -> float | None:
        if not HSTECH_LEDGER.exists():
            return None
        rows = [
            json.loads(line)
            for line in HSTECH_LEDGER.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not rows:
            return None
        latest = max(rows, key=lambda row: str(row.get("date") or ""))
        value = latest.get("dividend_yield_pct")
        return float(value) if value is not None else None

    @staticmethod
    def _index_snapshot_source(target: Target) -> str:
        return (
            f"恒生指数公司官方月度指数单张（2026-10-30）；"
            f"指数快照 {target.instrument_id or target.code}。"
        )

    @staticmethod
    def _index_sources(metrics: dict[str, Any], signal: dict[str, Any]) -> str:
        publishers = []
        for item in metrics.values():
            publisher = ((item.get("source") or {}).get("publisher"))
            if publisher and publisher not in publishers:
                publishers.append(publisher)
        return (
            f"{'、'.join(publishers) or '指数数据供应商'}（月度画像）｜"
            f"研究信号：中证指数官网官方日频序列（{signal.get('as_of') or '—'}）"
        )

    # --- 卡片：ETF ----------------------------------------------------------
    def _etf_card(self, target: Target | None, *, detail: bool = False) -> dict[str, Any]:
        if target is None:
            return {"markdown": self._help_card(Intent("unrecognized")), "data": {}}
        scope = target.scope or {}
        if scope.get("status") == "out_of_scope":
            return {
                "markdown": self._out_of_scope_card(target.name, Intent("out_of_scope", target=target, reason=scope.get("detail"))),
                "data": {},
            }
        try:
            result = self.analyzer.analyze(target.code or target.name)
        except Exception as exc:  # noqa: BLE001
            return {"markdown": f"**结论：无法判断** — ETF 数据读取失败：{type(exc).__name__}: {exc}", "data": {}}
        payload = result.to_dict().get("payload") or {}
        product = payload.get("product_check") or {}
        if not product:
            return {"markdown": f"**结论：无法判断** — {target.name} 没有可用的产品资料检查结果。", "data": {}}
        price = ((product.get("price") or {}).get("price") or {})
        nav = ((product.get("nav") or {}).get("value") or {})
        premium = (product.get("premium_discount_same_day") or {}).get("value")
        fees = product.get("fees") or {}
        underlying = (product.get("identity") or {}).get("underlying_index") or {}
        profile = product.get("underlying_index_profile") or {}
        metrics = profile.get("metrics") or {}
        lines = [
            "**结论：无法判断** — ETF 只做产品资料检查与跟踪指数研究信号，不给买卖判断。",
            "",
            f"标的：{target.name}（{target.instrument_id or target.code}）｜跟踪 {underlying.get('display_name') or '—'}"
            f"｜现价 {_money(price.get('value'))} 元（{price.get('period') or '—'}）",
            f"产品资料：单位净值 {_money(nav.get('value'))}（{nav.get('period') or '—'}）"
            f"｜同日折溢价 {_pct(premium) if premium is not None else '—'}"
            f"｜管理费 {_pct_points(((fees.get('management') or {}).get('value')))}"
            f"｜托管费 {_pct_points(((fees.get('custody') or {}).get('value')))}",
        ]
        bits = []
        for key, label in (("pe_rolling", "PE（滚动）"), ("pb", "PB")):
            item = metrics.get(key) or {}
            comparison = ((item.get("window") or {}).get("comparison") or {})
            if item.get("percentile") is not None:
                bits.append(f"{label} 分位 {item['percentile']:.0f}%（{comparison.get('date')}）")
        lines.append("跟踪指数位置：" + ("｜".join(bits) if bits else "指数历史不足，未计算分位"))
        lines += [
            "为什么：ETF 的工作是「跟踪指数 + 产品执行」；资料齐备不等于满足买入条件。",
            "局限：同日折溢价是事后计算，不能证明收盘前可交易；本轮不制定溢价/流动性/费率买卖门槛。",
            "来源与日期：产品资料 天天基金等（2026-11-11）；行情 东方财富（2026-11-10）；"
            "指数画像见 data/series；规则未批准。",
            "不构成投资建议。",
        ]
        if detail:
            lines += ["", "【详情】"]
            for item in product.get("missing") or []:
                lines.append(f"- 缺失：{item}")
        return {
            "markdown": "\n".join(lines),
            "data": {"instrument_id": target.instrument_id, "conclusion": "undetermined"},
        }


    def _stock_card(self, target: Target | None, *, detail: bool = False) -> dict[str, Any]:
        if target is None:
            return {"markdown": self._help_card(Intent("unrecognized")), "data": {}}
        result = self._evaluate_stock(target.code)
        block = result["block"]
        conclusion = block.get("conclusion") or "undetermined"
        pools = result["pools"]
        pool_text = "、".join(pools) if pools else "不在任何池中"
        values = block.get("values") or {}
        usage = values.get("usage") or {}
        price = block.get("price") or {}
        entry = block.get("candidate_entry_price") or {}
        grade = block.get("grade") or {}
        grade_value = grade.get("grade") or "—"
        margin = grade.get("margin_of_safety")
        label = (
            "正式结论（政策 equity-policy-v1.1，已批准）"
            if conclusion in ("candidate_buy_point", "wait", "avoid")
            else "政策结果（政策 equity-policy-v1.1，已批准）：无法判断"
        )
        lines = [
            f"**结论：{_label(conclusion)}** — {self._stock_reason(result)}",
            f"{label}",
            "",
            f"现价 {_money(price.get('value'))} 元（{block.get('price_date') or '日期未知'}）"
            f"｜候选门槛 {_money(entry.get('value'))} 元"
            f"｜距门槛 {_pct_number(block.get('distance_to_candidate_pct'))}",
        ]
        formal = conclusion in ("candidate_buy_point", "wait", "avoid")
        if formal and (values.get("pessimistic") or values.get("base") or values.get("optimistic")):
            lines.append(
                "三档价值（元/股）："
                f"悲观 {_money((values.get('pessimistic') or {}).get('value'))}（最悲观假设下每股值多少）"
                f"｜基准 {_money((values.get('base') or {}).get('value'))}（安全边际的计算基准）"
                f"｜乐观 {_money((values.get('optimistic') or {}).get('value'))}（高于它就判回避）"
            )
        else:
            lines.append("三档价值：无（本次不给三档价值）")
        binding = block.get("binding_condition") or {}
        unmet_names = grade.get("unmet_conditions") or []
        unmet = list(dict.fromkeys(unmet_plain(name) for name in unmet_names))
        binding_name = str(binding.get("name") or "")
        binding_label = {
            "margin_of_safety": "安全边际（现价需低于基准价值打折后的价格）",
            "pessimistic_value": "悲观情景价值",
        }.get(binding_name, str(binding.get("label") or "—"))
        binding_text = (
            f"{binding_label}：{_money((binding.get('price') or {}).get('value'))} 元"
            if formal
            else "不适用（无法判断）"
        )
        if formal:
            why_text = (
                f"起约束条件 {binding_text}"
                f"｜主要未满足条件：{'、'.join(unmet) if unmet else '无'}"
            )
        else:
            extras = [text.rstrip("。") for text in self._plain_extras(result)]
            why_text = (
                "；".join(extras) + "。"
                if extras
                else "没有更多原因；加 --detail 可看原始原因码与条件。"
            )
        lines += [
            f"为什么：{why_text}",
            f"它是什么：{pool_text}｜所有制 {result['ownership']}｜周期属性 {result['cycle_label']}"
            f"｜等级 {grade_value}（{grade.get('meaning') or '数据完整度与模型稳定性，不是公司质量'}）",
            f"局限：本结果只按已批准政策与固定输入计算，不预测股价，也不保证收益。{self._limitation(result)}",
            self._sources_line(result),
            "不构成投资建议。",
        ]
        if detail:
            lines = lines[:-1] + self._stock_detail(result) + [lines[-1]]
        return {"markdown": "\n".join(lines), "data": {**self._stock_data(result), "detail": detail}}

    def _limitation(self, result: dict[str, Any]) -> str:
        record = result["record"]
        unmet = set((record.get("grade") or {}).get("unmet_conditions") or [])
        parts = []
        terminal = (record.get("terminal_share") or {}).get("base")
        if terminal is not None and terminal >= 0.5 and "terminal_continuity_guardrail" not in unmet:
            parts.append(f"价值中有 {terminal:.1%} 来自五年以后的假设，对长期假设敏感")
        approximations = record.get("approximations") or {}
        impact = approximations.get("total_impact_per_share")
        if impact and "approximation_impact" not in unmet:
            parts.append(f"少量近似处理影响了等级（合计 {impact:.2f} 元/股）")
        cross = record.get("cross_check") or {}
        divergence = cross.get("divergence")
        if (
            divergence is not None
            and abs(divergence) >= 0.25
            and "cross_check_divergence" not in unmet
        ):
            parts.append(f"两种算法结果差异 {divergence:+.1%}")
        if not parts:
            parts.append("更多原始数字与条件见 --detail")
        return "；".join(parts) + "。"

    def _sources_line(self, result: dict[str, Any]) -> str:
        record = result["record"]
        block = result["block"]
        policy = block.get("policy") or {}
        snapshot = record.get("snapshot") or {}
        years = snapshot.get("annual_years") or []
        year_text = f"{max(years)} 年报" if years else "年报"
        interim = (
            " + 2026 三季报"
            if any(
                str(fact.period or "") == "2026-09-30"
                for fact in result["snapshot"].facts
                if fact.metric == "revenue"
            )
            else ""
        )
        price_date = block.get("price_date") or record.get("price_as_of") or "日期未知"
        return (
            f"来源与日期：行情 腾讯财经（{price_date}）；财务 东方财富（{year_text}{interim}）；"
            f"政策 {policy.get('policy_id')}（已批准 {policy.get('approved_at')}，复核 {policy.get('review_date')}）；"
            f"批量快照 {record.get('snapshot_id')}（数据截至 {record.get('as_of')}）。"
        )

    def _stock_detail(self, result: dict[str, Any]) -> list[str]:
        record = result["record"]
        block = result["block"]
        lines = ["", "【详情】", f"- 状态：{record.get('status')}；等级：{(record.get('grade') or {}).get('grade')}"]
        if record.get("status_reason"):
            lines.append(f"- 状态原因：{record['status_reason']}")
        if block.get("reason_codes") or record.get("reason_codes"):
            codes = record.get("reason_codes") or []
            lines.append(f"- 原因码：{'、'.join(codes) if codes else '无'}")
        conditions = (record.get("grade") or {}).get("conditions_A") or []
        if conditions:
            lines.append("- A 级条件：")
            for item in conditions:
                mark = "通过" if item.get("passed") else "未通过"
                lines.append(
                    f"  - {condition_labels([item.get('name')])[0]}：{mark}"
                    f"（要求 {item.get('required')}，观测 {item.get('observed')}）"
                )
        sensitivity = record.get("sensitivity") or []
        if sensitivity:
            lines.append("- 敏感性（折现率/终值增长 ±0.5pp）：")
            for item in sensitivity:
                lines.append(
                    f"  - {item.get('variable')} {item.get('delta')}: 每股价值 {_money(item.get('value_per_share'))}"
                )
        approximations = record.get("approximations") or {}
        items = approximations.get("items") or []
        if items:
            lines.append("- 近似项：")
            for item in items:
                lines.append(
                    f"  - {item.get('name') or item.get('label')}：{_money(item.get('impact_per_share'))} 元/股"
                )
        ledger = result.get("ledger")
        if ledger:
            lines.append(
                f"- 结论账本：{ledger.get('status')} {ledger.get('detail') or ''}".rstrip()
            )
        return lines

    def _stock_data(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": result["record"].get("code"),
            "name": result["name"],
            "conclusion": (result["block"] or {}).get("conclusion"),
            "price": result["price"],
            "price_date": result["price_date"],
            "pools": result["pools"],
            "ownership": result["ownership"],
            "cycle_label": result["cycle_label"],
            "grade": (result["record"].get("grade") or {}).get("grade"),
            "status": result["record"].get("status"),
            "values": result["record"].get("values"),
            "ledger": result.get("ledger"),
            "snapshot_id": result["record"].get("snapshot_id"),
            "reason_codes": list(result["record"].get("reason_codes") or []),
            "reason_explanations": [
                reason_plain(code) for code in (result["record"].get("reason_codes") or [])
            ],
        }

    @staticmethod
    def _clip(lines: list[str]) -> list[str]:
        """默认卡片不超过 25 行；超出时截断并提示用 --detail。"""
        if len(lines) <= 25:
            return lines
        clipped = lines[:24]
        clipped.append("（内容较长已截断；加 --detail 查看全部）")
        return clipped
