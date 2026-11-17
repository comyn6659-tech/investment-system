"""第七阶段（batch7）监测测试：政策口径、红线五种情形、财报季、变动提醒。

全部写入临时账本/临时目录；真实 `data/ledger` 与 `data/policies` 由 conftest 守护。
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.ask.service import AskService
from engine.monitoring import scanner
from engine.monitoring.ledger import MonitoringStore
from engine.monitoring.models import GovernanceFacts, Member, PenaltyEvent
from engine.monitoring.policy import load_policy
from engine.monitoring.redline import audit_years, evaluate_member, scan_members
from engine.monitoring.sources import EastmoneySource
from engine.quality.tables import read_table, write_table

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Singapore")
TODAY = datetime(2026, 11, 17, 18, 30, tzinfo=TZ)
STANDARD = {2021: "标准无保留意见", 2022: "标准无保留意见", 2023: "标准无保留意见", 2024: "标准无保留意见", 2025: "标准无保留意见"}


# ---------------------------------------------------------------------------
# M1 政策版本化与口径
# ---------------------------------------------------------------------------
def test_m1_policy_approved_and_red_lines_match_quality_pool():
    policy = load_policy()
    qp = json.loads((ROOT / "data" / "policies" / "quality-pool-v1.2.json").read_text(encoding="utf-8"))
    assert policy.status == "approved" and policy.approved is True
    assert policy.data["approval"]["approved_at"] == "2026-11-17"
    assert policy.data["next_review_date"] == "2027-11-13"
    assert "⑤" in policy.data["approval"]["decision"]
    penalty = policy.red_line("penalty")
    pledge = policy.red_line("pledge_ratio")
    audit = policy.red_line("audit_opinion")
    definitions = qp["definitions"]["penalty"]
    assert penalty["subject_scope"] == definitions["subject_scope"]
    assert penalty["matter_scope"] == definitions["matter_scope"]
    assert penalty["other_subjects"] == definitions["other_subjects"]
    assert penalty["unknown_subject"] == definitions["unknown_subject"]
    assert pledge["max"] == qp["parameters"]["pledge_ratio_max"]["value"] == 0.5
    assert pledge["no_controller"] == qp["definitions"]["pledge_ratio"]["no_controller"]
    assert pledge["nominee_exclusion"] == qp["definitions"]["pledge_ratio"]["nominee_exclusion"]
    assert audit["window_years"] == qp["parameters"]["audit_lookback_years"]["value"] == 5
    assert audit["nonstandard_max"] == qp["parameters"]["audit_nonstandard_years_max"]["value"] == 0
    assert penalty["window_years"] == qp["parameters"]["governance_lookback_years"]["value"] == 5
    assert penalty["count_max"] == qp["parameters"]["penalty_count_max"]["value"] == 0
    assert audit_years(TODAY.date()) == [2021, 2022, 2023, 2024, 2025]


def test_m1_policy_is_versioned_under_data_policies():
    path = ROOT / "data" / "policies" / "pool-monitoring-v1.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pool-monitoring-policy/1.0"
    assert payload["policy_id"] == "pool-monitoring-v1"
    assert payload["approved"] is True
    assert payload["actions"]["no_midyear_reentry"] is True
    assert "年度重跑" in payload["actions"]["reentry_rule"]


# ---------------------------------------------------------------------------
# M2 五种情形
# ---------------------------------------------------------------------------
class FakeSource:
    def __init__(self, facts: dict[str, GovernanceFacts]):
        self.facts = facts

    def fetch(self, code: str, name: str = "") -> GovernanceFacts:
        return self.facts[code]


def _members() -> list[Member]:
    return [
        Member("600001", "立案公司", ("quality",)),
        Member("600002", "非标公司", ("quality",)),
        Member("600003", "质押51", ("quality", "income")),
        Member("600004", "质押50", ("quality",)),
        Member("600005", "失败公司", ("income",)),
    ]


def _facts() -> dict[str, GovernanceFacts]:
    return {
        "600001": GovernanceFacts(
            "600001",
            "立案公司",
            penalties=(
                PenaltyEvent(
                    date="2026-03-01",
                    event_type="立案",
                    title="关于收到中国证监会立案告知书的公告",
                    subject="立案公司",
                    role="company",
                    counted=True,
                    info_disclosure=True,
                    regulator=True,
                    doc_no="证监立案字〔2026〕1 号",
                    url="https://data.eastmoney.com/notices/detail/600001/A1.html",
                ),
            ),
            audit_opinions=STANDARD,
            pledge_ratio=0.1,
            pledge_date="2026-09-30",
        ),
        "600002": GovernanceFacts(
            "600002", "非标公司", audit_opinions={**STANDARD, 2024: "保留意见"}, pledge_ratio=0.1
        ),
        "600003": GovernanceFacts(
            "600003", "质押51", audit_opinions=STANDARD, pledge_ratio=0.51, pledge_date="2026-09-30",
            pledge_subject="控股股东甲", pledge_source="https://example.com/pledge/600003",
        ),
        "600004": GovernanceFacts(
            "600004", "质押50", audit_opinions=STANDARD, pledge_ratio=0.50, pledge_date="2026-09-30"
        ),
        "600005": GovernanceFacts(
            "600005", "失败公司", audit_opinions=STANDARD, failures={"pledge": "接口超时"}
        ),
    }


def _ledger_dir(tmp_path: Path, codes: list[str], *, income_codes: list[str] | None = None) -> Path:
    directory = tmp_path / "ledger"
    directory.mkdir()
    for pool in ("quality", "income", "bank"):
        pool_codes = codes if pool == "quality" else (income_codes if income_codes is not None else codes)
        record = {
            "as_of": "2026-11-12",
            "policy_id": f"{pool}-pool-v1",
            "policy_digest": "digest",
            "snapshot_id": "qps-test",
            "counts": {"in_pool": len(pool_codes)},
            "pool_codes": pool_codes if pool != "bank" else [],
            "pool_hash": "test",
            "record_id": f"qpr-{pool}",
            "recorded_at": "2026-11-13T00:00:00+08:00",
        }
        (directory / f"{pool}-pool.jsonl").write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return directory


def test_m2_scan_five_cases_and_append_only(tmp_path):
    directory = _ledger_dir(
        tmp_path,
        ["600001", "600002", "600003", "600004"],
        income_codes=["600003", "600005"],
    )
    before = (directory / "quality-pool.jsonl").read_bytes()
    store = MonitoringStore(directory)
    notifications = []

    def notifier(title: str, message: str) -> dict:
        notifications.append((title, message))
        return {"status": "sent"}

    payload = scanner.run_scan(
        store=store,
        source=FakeSource(_facts()),
        policy=load_policy(),
        now=TODAY,
        members=_members(),
        notifier=notifier,
    )
    assert payload["counts"] == {"members": 5, "covered": 4, "hit": 3, "incomplete": 1, "ok": 1}
    assert payload["coverage"] == pytest.approx(0.8)
    status = {item["code"]: item["status"] for item in payload["hits"] + payload["incomplete"]}
    assert status["600001"] == status["600002"] == status["600003"] == "hit"
    assert status["600005"] == "incomplete"
    assert payload["incomplete"][0]["missing"] == ["pledge"]
    assert "本周未完成" in payload["incomplete"][0]["status_text"]
    assert len(notifications) == 3

    # 旧名单记录逐字节不变；只在其后追加剔除记录
    rows = [json.loads(line) for line in (directory / "quality-pool.jsonl").read_text(encoding="utf-8").splitlines()]
    assert (directory / "quality-pool.jsonl").read_bytes().startswith(before)
    assert len(rows) == 4
    assert [row.get("record_type") for row in rows[1:]] == ["exclusion", "exclusion", "exclusion"]
    assert {row["code"] for row in rows[1:]} == {"600001", "600002", "600003"}
    assert all("出池（红线：" in row["status"] for row in rows[1:])
    # 600003 同时属于两个池：两个池账本都追加
    income_rows = [
        json.loads(line)
        for line in (directory / "income-pool.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["code"] for row in income_rows[1:]] == ["600003"]

    # 有效名单：3 家出池；50% 的一家仍在
    effective = store.effective_sets({"quality": {"600001", "600002", "600003", "600004", "600005"}, "income": {"600005", "600003"}, "bank": set()})
    assert effective["quality"] == {"600004"}
    assert effective["income"] == {"600005"}

    # 扫描记录与变动记录
    scan = store.last_scan()
    assert scan["counts"]["hit"] == 3 and scan["coverage"] == 0.8
    changes = store.changes()
    assert any(item["type"] == "出池" for item in changes)
    assert any(item["type"] == "扫描失败" for item in changes)
    # 已剔除的公司再次扫描：不重复追加
    again = scanner.run_scan(
        store=store,
        source=FakeSource(_facts()),
        policy=load_policy(),
        now=datetime(2026, 11, 18, 18, 30, tzinfo=TZ),
        members=_members(),
    )
    assert again["added_exclusions"] == []
    assert len([line for line in (directory / "quality-pool.jsonl").read_text(encoding="utf-8").splitlines() if line]) == 4


def test_m2_pledge_threshold_is_le_50_percent():
    policy = load_policy()
    member = Member("600004", "质押50", ("quality",))
    enough = GovernanceFacts("600004", "质押50", audit_opinions=STANDARD, pledge_ratio=0.5)
    over = GovernanceFacts("600004", "质押50", audit_opinions=STANDARD, pledge_ratio=0.5001)
    assert evaluate_member(member, enough, policy, as_of=TODAY.date()).status == "ok"
    assert evaluate_member(member, over, policy, as_of=TODAY.date()).status == "hit"


def test_m2_failed_fetch_is_not_pass_and_not_removed():
    policy = load_policy()
    member = Member("600005", "失败公司", ("income",))
    facts = GovernanceFacts("600005", "失败公司", failures={"penalty": "接口超时", "audit": "接口超时"})
    scan = evaluate_member(member, facts, policy, as_of=TODAY.date())
    assert scan.status == "incomplete"
    assert scan.hits == ()
    assert set(scan.missing) == {"penalty", "audit", "pledge"}


def test_m2_notification_failure_does_not_break_scan(tmp_path):
    directory = _ledger_dir(tmp_path, ["600001"])
    store = MonitoringStore(directory)

    def broken(title: str, message: str) -> None:
        raise RuntimeError("osascript 不可用")

    payload = scanner.run_scan(
        store=store,
        source=FakeSource(_facts()),
        policy=load_policy(),
        now=TODAY,
        members=[Member("600001", "立案公司", ("quality",))],
        notifier=broken,
    )
    assert payload["status"] == "hit"
    assert payload["added_exclusions"] == ["600001"]


# ---------------------------------------------------------------------------
# M3 财报季：新快照、旧快照保留、估值切换、缺报表注明、结论变化
# ---------------------------------------------------------------------------
from engine.monitoring.batch import (  # noqa: E402
    BALANCE_COLUMNS,
    CASHFLOW_COLUMNS,
    INCOME_COLUMNS,
    build_earnings_snapshot,
)
from engine.pool_valuation.adapter import BatchStore, build_snapshot  # noqa: E402
from engine.quality.snapshot import build_snapshot_dir  # noqa: E402
from engine.policy.conclusion_ledger import ConclusionLedger, ConclusionRecord  # noqa: E402
from engine.monitoring.conclusions import refresh_conclusions  # noqa: E402
from engine.monitoring.bank_inputs import refresh_inputs  # noqa: E402
from engine.bank.inputs import current_inputs  # noqa: E402
from engine.bank.valuation_v2 import BankValuationInputs  # noqa: E402
from engine.web.app import WebApp  # noqa: E402


def _write_statement_table(tables: Path, name: str, kind: str, period: str, columns, rows) -> Path:
    if name in ("universe", "price", "shares", "industry"):
        path = tables / f"{name}.csv.gz"
    else:
        path = tables / f"{name}-{period}.csv.gz"
    write_table(
        path,
        rows,
        columns,
        metadata={
            "kind": kind,
            "report_date": period,
            "source_id": "test",
            "publisher": "test",
            "interface": "test",
            "url": "test",
            "retrieved_at": "2026-11-13T00:00:00+08:00",
            "definition_note": "fixture",
        },
    )
    return path


def _make_pool(tmp_path: Path, codes: list[str]) -> tuple[Path, str]:
    pool = tmp_path / "pool-valuation"
    tables = tmp_path / "cache"
    pool.mkdir()
    tables.mkdir()
    paths = []
    paths.append(_write_statement_table(tables, "universe", "universe", "", ["code", "name", "exchange"], [{"code": c, "name": c, "exchange": "SH"} for c in codes]))
    for period in ("2024-12-31", "2025-12-31"):
        paths.append(_write_statement_table(tables, "balance", "balance_full", period, BALANCE_COLUMNS, [{"SECURITY_CODE": c, "TOTAL_PARENT_EQUITY": 900 if period.startswith("2024") else 1000, "REPORT_DATE": period} for c in codes]))
        paths.append(_write_statement_table(tables, "income", "income_full", period, INCOME_COLUMNS, [{"SECURITY_CODE": c, "PARENT_NETPROFIT": 90 if period.startswith("2024") else 100, "REPORT_DATE": period} for c in codes]))
        paths.append(_write_statement_table(tables, "cashflow", "cashflow_full", period, CASHFLOW_COLUMNS, [{"SECURITY_CODE": c, "NETCASH_OPERATE": 120, "REPORT_DATE": period} for c in codes]))
    paths.append(_write_statement_table(tables, "price", "price", "2026-11-13", ["code", "name", "price", "price_date"], [{"code": c, "name": c, "price": 10.0, "price_date": "2026-11-13"} for c in codes]))
    paths.append(_write_statement_table(tables, "shares", "shares", "2025-12-31", ["code", "name", "end_date", "total_shares"], [{"code": c, "name": c, "end_date": "2025-12-31", "total_shares": 100.0} for c in codes]))
    paths.append(_write_statement_table(tables, "industry", "industry", "2025-12-31", ["code", "industry"], [{"code": c, "industry": "测试"} for c in codes]))
    directory, manifest = build_snapshot_dir(
        paths,
        parent=pool,
        as_of="2026-11-13",
        window={"start_fy": 2016, "end_fy": 2025, "min_years": 7},
        scope={},
        sources=[],
        untested_conditions=[],
        build_stats={},
    )
    (pool / "manifest.json").write_text(
        json.dumps(
            {
                "current": directory.name,
                "snapshot_id": manifest["snapshot_id"],
                "content_hash": manifest["content_hash"],
                "as_of": "2026-11-13",
                "tables": len(manifest["tables"]),
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return pool, manifest["snapshot_id"]


class FakeEarningsSource:
    def __init__(self, codes: list[str]):
        self.codes = codes

    def fetch_statement(self, statement: str, period: str) -> list[dict]:
        rows = []
        for code in self.codes:
            if statement == "balance":
                rows.append({"SECURITY_CODE": code, "TOTAL_PARENT_EQUITY": 1100, "REPORT_DATE": period})
            elif statement == "income":
                rows.append({"SECURITY_CODE": code, "PARENT_NETPROFIT": 110, "REPORT_DATE": period})
            else:
                rows.append({"SECURITY_CODE": code, "NETCASH_OPERATE": 150, "REPORT_DATE": period})
        return rows


def test_m3_earnings_builds_new_snapshot_keeps_old_and_switches(tmp_path, monkeypatch):
    pool, old_id = _make_pool(tmp_path, ["600519", "000333", "600036"])
    old_dir = pool / json.loads((pool / "manifest.json").read_text(encoding="utf-8"))["current"]
    source = FakeEarningsSource(["600519", "600036"])
    build = build_earnings_snapshot(
        season="2027-annual",
        period="2026-12-31",
        kind="annual",
        source=source,
        pool_dir=pool,
        cache_tables=tmp_path / "cache",
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
    )
    pointer = json.loads((pool / "manifest.json").read_text(encoding="utf-8"))
    assert build.snapshot_id != old_id
    assert old_dir.exists() and build.directory.exists()
    assert pointer["current"] == build.directory.name
    assert pointer["snapshot_id"] == build.snapshot_id
    assert pointer["earnings_latest_period"] == "2026-12-31"
    assert pointer["earnings_applied"][-1]["period"] == "2026-12-31"
    assert build.missing_companies == 1

    store = BatchStore(build.directory)
    assert store.has_period("600519", "2026-12-31")
    assert not store.has_period("000333", "2026-12-31")
    assert "财报未更新" in (store.report_note("000333") or "")
    assert "2026 年报" in (store.report_note("000333") or "")
    assert store.report_note("600519") is None

    snapshot = build_snapshot(store, "600519")
    facts = {fact.metric: fact.value for fact in snapshot.facts}
    assert facts["net_profit_parent"] == 110
    assert facts["equity_parent"] == 1100
    import engine.repro.trace as trace_module

    monkeypatch.setattr(trace_module, "ROOT", tmp_path)
    assert trace_module.batch_reference(store)["snapshot_id"] == build.snapshot_id


def _formal_record() -> ConclusionRecord:
    return ConclusionRecord(
        instrument_id="EQUITY:600519.SH",
        code="600519",
        analysis_date="2026-11-14",
        policy_version="equity-policy-v1.1",
        policy_digest="digest",
        snapshot_id="snap-old",
        price=100.0,
        price_date="2026-11-13",
        values={"pessimistic": 80.0, "base": 100.0, "optimistic": 120.0},
        grade="A",
        margin_of_safety=0.2,
        conclusion="wait",
        binding_condition="安全边际",
        recorded_at="2026-11-14T12:00:00+08:00",
    )


def _fake_result(conclusion: str) -> dict:
    return {
        "record": {
            "instrument_id": "EQUITY:600519.SH",
            "code": "600519",
            "snapshot_id": "snap-new",
            "price": 90.0,
            "price_as_of": "2026-11-17",
            "values": {"pessimistic": 85.0, "base": 110.0, "optimistic": 130.0},
            "grade": {"grade": "A", "margin_of_safety": 0.2},
            "status": "complete",
            "trial_conclusion": {
                "conclusion": conclusion,
                "thresholds": {"binding_condition_text": "安全边际"},
            },
            "reason_codes": [],
        },
        "block": {"conclusion": conclusion},
    }


def test_m3_conclusion_change_appends_formal_then_reversal(tmp_path, monkeypatch):
    pool, _old_id = _make_pool(tmp_path, ["600519", "000333", "600036"])
    build = build_earnings_snapshot(
        season="2027-annual",
        period="2026-12-31",
        kind="annual",
        source=FakeEarningsSource(["600519", "600036"]),
        pool_dir=pool,
        cache_tables=tmp_path / "cache",
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
    )
    ledger_path = tmp_path / "equity-conclusions.jsonl"
    ledger = ConclusionLedger(ledger_path)
    ledger.append([_formal_record()])
    store = MonitoringStore(tmp_path)
    service = AskService(
        allow_update=False,
        use_live_quotes=False,
        analysis_date=date(2027, 5, 6),
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
        ledger_path=ledger_path,
        monitoring=store,
    )
    service._batch = BatchStore(build.directory)

    monkeypatch.setattr(
        service,
        "_evaluate_stock",
        lambda code, write_ledger=True: _fake_result("candidate_buy_point"),
    )
    first = refresh_conclusions(
        service=service, store=store, now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ)
    )
    assert first["changed"] == 1
    rows = ledger.load()
    assert len(rows) == 2
    assert rows[-1]["conclusion"] == "candidate_buy_point"
    assert rows[-1]["snapshot_id"] == "snap-new"
    assert any(item["type"] == "结论变化" for item in store.changes())

    monkeypatch.setattr(
        service,
        "_evaluate_stock",
        lambda code, write_ledger=True: _fake_result("undetermined"),
    )
    second = refresh_conclusions(
        service=service, store=store, now=datetime(2027, 5, 7, 18, 30, tzinfo=TZ)
    )
    assert second["changed"] == 1
    rows = ledger.load()
    assert len(rows) == 3
    assert rows[-1]["record_type"] == "reversal"
    assert rows[-1]["reversal_of"] == rows[1]["record_id"]
    assert any("无法判断（改判）" in item["detail"] for item in store.changes())


def test_m3_missing_report_note_on_card(tmp_path, monkeypatch):
    pool, _old_id = _make_pool(tmp_path, ["600519", "000333", "600036"])
    build = build_earnings_snapshot(
        season="2027-annual",
        period="2026-12-31",
        kind="annual",
        source=FakeEarningsSource(["600519", "600036"]),
        pool_dir=pool,
        cache_tables=tmp_path / "cache",
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
    )
    service = AskService(
        allow_update=False,
        use_live_quotes=False,
        write_ledger=False,
        analysis_date=date(2027, 5, 6),
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
        monitoring=MonitoringStore(tmp_path),
    )
    service._batch = BatchStore(build.directory)
    import engine.ask.service as ask_module

    monkeypatch.setattr(
        ask_module.policy_trace,
        "batch_reference",
        lambda batch: {
            "kind": "batch_snapshot",
            "snapshot_id": str(batch.manifest.get("snapshot_id") or ""),
            "path": "data/pool-valuation/test",
            "manifest_sha256": "0" * 64,
        },
    )
    monkeypatch.setattr(
        ask_module.policy_trace,
        "batch_price_reference",
        lambda batch: {
            "kind": "batch_price",
            "snapshot_id": str(batch.manifest.get("snapshot_id") or ""),
            "price_date": "2026-11-13",
            "path": "data/pool-valuation/price.csv.gz",
            "sha256": "0" * 64,
        },
    )
    card = service._stock_card(service.target_index.match("000333"))
    assert "财报未更新" in card["markdown"]


def test_m3_bank_inputs_refresh_uses_new_report(tmp_path):
    pool, _old_id = _make_pool(tmp_path, ["600519", "000333", "600036"])
    build = build_earnings_snapshot(
        season="2027-annual",
        period="2026-12-31",
        kind="annual",
        source=FakeEarningsSource(["600519", "600036"]),
        pool_dir=pool,
        cache_tables=tmp_path / "cache",
        now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ),
    )
    bank_dir = tmp_path / "bank-pool"
    valuation = bank_dir / "valuation-v2"
    valuation.mkdir(parents=True)
    columns = [
        "code", "name", "in_scope", "report_date", "parent_equity", "other_equity_tool",
        "bvps", "shares", "price", "price_date", "roe_2021", "roe_2022", "roe_2023",
        "roe_2024", "roe_2025", "roe_3y_avg", "roe_5y_avg", "roe_5y_min", "npl_latest",
        "npl_5y_ago", "npl_change_5y", "provision_coverage", "loss_years_7",
        "data_complete", "pb_current",
    ]
    old_rows = [{
        "code": "600036", "name": "招商银行", "in_scope": "True", "report_date": "2025-12-31",
        "parent_equity": "1000", "other_equity_tool": "0", "bvps": "10", "shares": "100",
        "price": "9", "price_date": "2026-11-13", "roe_2021": "0.1", "roe_2022": "0.1",
        "roe_2023": "0.1", "roe_2024": "0.1", "roe_2025": "0.1", "roe_3y_avg": "0.1",
        "roe_5y_avg": "0.1", "roe_5y_min": "0.1", "npl_latest": "1.0", "npl_5y_ago": "1.0",
        "npl_change_5y": "0", "provision_coverage": "300", "loss_years_7": "0",
        "data_complete": "True", "pb_current": "0.9",
    }]
    old_path = valuation / "inputs.csv.gz"
    write_table(old_path, old_rows, columns, metadata={"table_id": "inputs", "kind": "bank_valuation_inputs"})
    old_bytes = old_path.read_bytes()
    (valuation / "manifest.json").write_text(
        json.dumps({"current_inputs": "inputs.csv.gz", "current_history": "history.csv.gz"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = refresh_inputs(batch=BatchStore(build.directory), bank_dir=bank_dir, now=datetime(2027, 5, 6, 18, 30, tzinfo=TZ))
    assert result.period == "2026-12-31"
    assert result.rows_updated == 1
    assert old_path.read_bytes() == old_bytes
    new_path = current_inputs(valuation)
    assert new_path.name == "inputs-2026-12-31.csv.gz"
    row = read_table(new_path)[0]
    assert row["report_date"] == "2026-12-31"
    assert float(row["parent_equity"]) == 1100.0
    assert float(row["roe_latest"]) == pytest.approx(110 / ((1000 + 1100) / 2))
    inputs = BankValuationInputs.from_row(row, in_bank_pool=True)
    assert inputs.roe_latest == pytest.approx(110 / ((1000 + 1100) / 2))


# ---------------------------------------------------------------------------
# M4 最近变动：页面与 ask 一致；通知可被替身捕获
# ---------------------------------------------------------------------------
def test_m4_changes_page_and_ask_use_same_list(tmp_path):
    store = MonitoringStore(tmp_path)
    store.append_changes(
        [
            {"date": "2026-11-17", "type": "出池", "code": "600001", "name": "立案公司", "detail": "出池（红线：立案，2026-11-15，文号）"},
            {"date": "2026-11-16", "type": "财报更新", "detail": "2026 年报批量快照 qps-test"},
            {"date": "2026-11-15", "type": "扫描失败", "detail": "1 家本周未完成"},
        ],
        now=TODAY,
    )
    service = AskService(allow_update=False, use_live_quotes=False, monitoring=store)
    card = service.ask("最近有什么变动").markdown
    assert "出池" in card and "财报更新" in card and "扫描失败" in card
    web = WebApp(service=service, allow_update=False)
    payload = web.status()
    items = payload["status"]["monitoring"]["changes"]["items"]
    assert [item["detail"] for item in items] == [
        "出池（红线：立案，2026-11-15，文号）",
        "2026 年报批量快照 qps-test",
        "1 家本周未完成",
    ]
    for item in items:
        assert item["detail"] in card


def test_m4_notifier_receives_exclusion_and_failure_is_tolerated():
    from engine.monitoring.notify import build_command, notify

    captured = []

    def runner(command, **kwargs):
        captured.append(command)
        return None

    result = notify("池成员出池", '600001 立案公司：出池（红线："立案"，2026-11-15）', runner=runner, platform="darwin")
    assert result.status == "sent"
    assert captured and captured[0][0] == "osascript"
    assert "立案" in captured[0][2]

    def broken(command, **kwargs):
        raise RuntimeError("no osascript")

    failed = notify("池成员出池", "x", runner=broken, platform="darwin")
    assert failed.status == "failed"
    skipped = notify("x", "y", runner=runner, platform="linux")
    assert skipped.status == "skipped"
    assert build_command("t", 'a"b')[2].count('\\"') == 1


def test_m3_pledge_uses_latest_periodic_report_over_stale_detail(monkeypatch):
    from engine.monitoring.sources import EastmoneySource

    now = datetime(2026, 11, 17, 18, 30, tzinfo=TZ)
    committed = {
        "002057": {
            "subject": "中钢资本控股有限公司",
            "holdings": "133245380",
            "pledge_ratio": "0.4367",
            "report_date": "2026-09-30",
            "source_id": "governance.snapshot",
        }
    }
    source = EastmoneySource(
        now=now,
        committed=(committed, {"002057": STANDARD}),
        shares={"002057": 1_000_000_000},
        review={},
    )
    monkeypatch.setattr(
        source,
        "_pledge_rows",
        lambda code: [
            {
                "HOLDER_NAME": "中钢资本控股有限公司",
                "HOLD_NUM": "13324.538",
                "ACCUM_PLEDGE_TSR": "10.11",
                "UNFREEZE_STATE": "未解押",
                "NOTICE_DATE": "2020-09-05 00:00:00",
            }
        ],
    )
    failures = {}
    ratio, *_ = source._pledge("002057", "中钢天源", failures)
    assert ratio == pytest.approx(0.4367)
    assert failures == {}

    # 明细比定期报告更新时，才采用明细
    monkeypatch.setattr(
        source,
        "_pledge_rows",
        lambda code: [
            {
                "HOLDER_NAME": "中钢资本控股有限公司",
                "HOLD_NUM": "10000",
                "ACCUM_PLEDGE_TSR": "6",
                "UNFREEZE_STATE": "未解押",
                "NOTICE_DATE": "2026-11-10 00:00:00",
            }
        ],
    )
    ratio, *_ = source._pledge("002057", "中钢天源", failures)
    assert ratio == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# H5 财报季真实缺陷：顶层 manifest 的 tables 是数量，不能当表清单迭代
# ---------------------------------------------------------------------------
def test_h5_applied_seasons_reads_current_snapshot_manifest(tmp_path):
    from engine.monitoring.batch import annual_periods
    from engine.monitoring.earnings import applied_seasons, run_due_earnings

    pool, _old_id = _make_pool(tmp_path, ["600519", "000333"])
    pointer = json.loads((pool / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(pointer["tables"], int)

    with pytest.raises(TypeError, match="tables"):
        annual_periods(pointer)

    applied = applied_seasons(pointer, day=date(2026, 11, 17), pool_dir=pool)
    assert "2026-annual" in applied
    assert "2026-interim" not in applied and "2026-q3" not in applied

    result = run_due_earnings(
        pool_dir=pool,
        source=FakeEarningsSource(["600519", "000333"]),
        now=TODAY,
        dry_run=True,
    )
    assert result["status"] == "applied"
    # 夹具只有年报、没有 2025 半年报/三季报，所以 2025 两个季度也算到期；
    # 关键是顶层指针的 int 不再被迭代，2026 半年报/三季报能正常进入刷新。
    seasons = [item["season"] for item in result["seasons"]]
    assert "2026-interim" in seasons and "2026-q3" in seasons
    assert seasons == sorted(seasons, key=lambda value: (value[:4], {"interim": 0, "q3": 1}[value[5:]]))
    assert all(item["snapshot_id"] == "(dry-run)" for item in result["snapshots"])
