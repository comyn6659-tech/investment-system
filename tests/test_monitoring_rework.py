"""第七阶段返工 R1–R4：判定一致、待定、备用源与扫描证据。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.monitoring import sources
from engine.monitoring.classify import classify_event, info_disclosure
from engine.monitoring.ledger import MonitoringStore
from engine.monitoring.models import GovernanceFacts, Member, PenaltyEvent
from engine.monitoring.policy import load_policy
from engine.monitoring.redline import evaluate_member
from engine.monitoring.scanner import run_scan
from engine.monitoring.sources import EastmoneySource, SourceIncomplete

ROOT = Path(__file__).resolve().parent.parent
TODAY = datetime(2026, 11, 17, 18, 30, tzinfo=ZoneInfo("Asia/Singapore"))
GUOYAO_TITLE = "国药股份:国药股份关于公司高管收到立案调查告知书的公告"
GUOYAO_URL = "https://data.eastmoney.com/notices/detail/600511/AN202401091616875360.html"
STANDARD = {2021: "标准无保留意见", 2022: "标准无保留意见", 2023: "标准无保留意见", 2024: "标准无保留意见", 2025: "标准无保留意见"}


def _guoyao_text() -> str:
    return (ROOT / "tests" / "fixtures" / "600511-2024-01-10.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# R1 判定口径统一
# ---------------------------------------------------------------------------
def test_r1_guoyao_is_insider_trading_not_info_disclosure():
    text = _guoyao_text()
    assert info_disclosure(text) is False
    result = classify_event(
        code="600511",
        company_name="国药股份",
        event_type="立案",
        title=GUOYAO_TITLE,
        date="2024-01-10",
        url=GUOYAO_URL,
        content=text,
        art_code="AN202401091616875360",
    )
    assert result["counted"] is False
    assert result["pending"] is False
    assert result["matter"] == "内幕交易"
    assert result["basis"] == "内幕交易（他公司股票），非信息披露类"


@pytest.mark.parametrize(
    "text",
    [
        "本公司董事会保证本公告内容不存在任何虚假记载、误导性陈述或者重大遗漏。",
        "本公司保证公告内容不存在任何虚假记载、误导性陈述或重大遗漏。",
        "本公司保证信息披露的内容真实、准确、完整，没有虚假记载、误导性陈述或重大遗漏。",
    ],
)
def test_r1_boilerplate_variants_are_removed(text):
    assert info_disclosure(text) is False


@pytest.mark.parametrize(
    ("text", "matter"),
    [
        ("公司高管因涉嫌内幕交易被证监会立案。", "内幕交易"),
        ("控股股东因短线交易被交易所处罚。", "短线交易"),
        ("子公司因环保处罚被生态环境部门罚款。", "环保"),
        ("子公司发生安全生产事故被处罚。", "安全生产"),
        ("公司被国家金融监督管理总局处罚。", "金融监管"),
    ],
)
def test_r1_excluded_matters_are_not_counted(text, matter):
    result = classify_event(
        code="600001",
        company_name="测试公司",
        event_type="行政处罚",
        title="关于收到处罚决定书的公告",
        date="2026-01-01",
        url="https://example.com/a",
        content=text,
    )
    assert result["matter"] == matter
    assert result["info_disclosure"] is False
    assert result["counted"] is False
    assert "非信息披露类" in result["basis"]


def test_r1_committed_snapshot_decision_is_reused():
    committed = {
        "subject": "时任高管（公告所述）",
        "subject_role": "director_officer",
        "info_disclosure": "False",
        "regulator": "False",
        "counted": "False",
        "subject_unknown": "False",
        "subject_basis": "建池时按正文判为非信息披露类",
        "doc_no": "",
        "url": GUOYAO_URL,
    }
    result = classify_event(
        code="600511",
        company_name="国药股份",
        event_type="立案",
        title=GUOYAO_TITLE,
        date="2024-01-10",
        url=GUOYAO_URL,
        content=_guoyao_text(),
        art_code="AN202401091616875360",
        committed=committed,
    )
    assert result["decision_source"] == "snapshot"
    assert result["counted"] is False
    assert result["role"] == "director_officer"


def test_r1_build_and_monitoring_share_the_same_classifier():
    from tools import build_governance_data

    event = {
        "date": "2024-01-10",
        "type": "立案",
        "title": GUOYAO_TITLE,
        "art_code": "AN202401091616875360",
        "url": GUOYAO_URL,
        "content": _guoyao_text(),
        "reason": _guoyao_text(),
    }
    built = build_governance_data.classify_penalty_events("600511", "国药股份", [event])[0]
    direct = classify_event(
        code="600511",
        company_name="国药股份",
        event_type="立案",
        title=GUOYAO_TITLE,
        date="2024-01-10",
        url=GUOYAO_URL,
        content=_guoyao_text(),
        art_code="AN202401091616875360",
    )
    assert built["counted"] is False
    assert built["subject_role"] == direct["role"] == "director_officer"
    assert built["subject_basis"] == direct["basis"] == "内幕交易（他公司股票），非信息披露类"


# ---------------------------------------------------------------------------
# R2 不确定就待定
# ---------------------------------------------------------------------------
def test_r2_missing_content_is_pending_and_not_a_pass():
    result = classify_event(
        code="600001",
        company_name="测试公司",
        event_type="立案",
        title="关于收到中国证监会立案告知书的公告",
        date="2026-03-01",
        url="https://example.com/a",
        content="",
    )
    assert result["pending"] is True
    assert result["counted"] is False
    assert "正文未取得" in result["basis"]

    facts = GovernanceFacts(
        "600001",
        "测试公司",
        penalties=(PenaltyEvent.from_dict(result),),
        audit_opinions=STANDARD,
        pledge_ratio=0.1,
    )
    scan = evaluate_member(Member("600001", "测试公司", ("quality",)), facts, load_policy(), as_of=TODAY.date())
    assert scan.status == "incomplete"
    assert scan.hits == ()
    assert "待定" in "；".join(scan.missing_notes())


def test_r2_content_without_matter_is_pending():
    result = classify_event(
        code="600001",
        company_name="测试公司",
        event_type="立案",
        title="关于收到立案告知书的公告",
        date="2026-03-01",
        url="https://example.com/a",
        content="公司于近日收到中国证监会立案告知书。",
    )
    assert result["pending"] is True
    assert result["counted"] is False


# ---------------------------------------------------------------------------
# R3 备用源失败要报；质押明细不可用要报
# ---------------------------------------------------------------------------
def test_r3_sina_homepage_without_rows_is_incomplete(monkeypatch):
    source = EastmoneySource(now=TODAY, committed=({}, {}), review={})
    monkeypatch.setattr(sources, "_get_text", lambda *args, **kwargs: "<html>没有公告行</html>")
    with pytest.raises(SourceIncomplete, match="首页解析不到任何行"):
        source._sina_events("600511", "国药股份", since="2026-07-20")


def test_r3_sina_pagination_must_cover_start_date(monkeypatch):
    source = EastmoneySource(now=TODAY, committed=({}, {}), review={})
    row = "2026-11-17&nbsp;<a href='/corp/view/vCB_AllBulletinDetail.php?id=1'>立案公告</a>"
    monkeypatch.setattr(sources, "SINA_MAX_PAGES", 2)
    monkeypatch.setattr(sources, "_get_text", lambda *args, **kwargs: row)
    with pytest.raises(SourceIncomplete, match="仍未覆盖起始日"):
        source._sina_events("600511", "国药股份", since="2026-07-20")


def test_r3_fallback_failure_is_recorded_not_silently_passed(monkeypatch):
    source = EastmoneySource(now=TODAY, committed=({}, {}), committed_penalty_rows={}, review={})

    def broken(code: str, keyword: str) -> list[dict]:
        raise RuntimeError("限流")

    source._announcements = broken  # type: ignore[assignment]
    monkeypatch.setattr(
        source,
        "_sina_events",
        lambda *args, **kwargs: (_ for _ in ()).throw(SourceIncomplete("首页解析不到任何行")),
    )
    failures: dict[str, str] = {}
    events, origin = source._penalties("600511", "国药股份", failures)
    assert events == [] and origin == "sina"
    assert "首页解析不到任何行" in failures["penalty"]


def test_r3_pledge_ratio_over_100_is_rejected():
    source = EastmoneySource(
        now=TODAY,
        committed=(
            {"600001": {"subject": "控股股东", "pledge_ratio": "1.53", "report_date": "2026-09-30"}},
            {},
        ),
        committed_penalty_rows={},
        shares={"600001": 1_000_000_000},
        review={},
    )
    failures: dict[str, str] = {}
    ratio, *_ = source._pledge("600001", "测试公司", failures)
    assert ratio is None
    assert "超过 0–100%" in failures["pledge"]


def test_r3_stale_pledge_detail_is_not_used():
    source = EastmoneySource(
        now=TODAY,
        committed=(
            {"600001": {"subject": "控股股东", "report_date": "2026-09-30"}},
            {},
        ),
        committed_penalty_rows={},
        shares={"600001": 1_000_000_000},
        review={},
    )
    monkeypatch_row = {
        "HOLDER_NAME": "控股股东",
        "HOLD_NUM": "10000",
        "ACCUM_PLEDGE_TSR": "10",
        "UNFREEZE_STATE": "未解押",
        "NOTICE_DATE": "2015-09-30 00:00:00",
    }
    original = source._pledge_rows
    source._pledge_rows = lambda code: [monkeypatch_row]  # type: ignore[assignment]
    failures: dict[str, str] = {}
    ratio, *_ = source._pledge("600001", "测试公司", failures)
    assert ratio is None
    assert "早于最新定期报告" in failures["pledge"]
    source._pledge_rows = original  # type: ignore[assignment]


def test_r3_no_usable_pledge_data_is_incomplete():
    source = EastmoneySource(
        now=TODAY,
        committed=({}, {}),
        committed_penalty_rows={},
        shares={},
        review={},
    )
    source._pledge_rows = lambda code: []  # type: ignore[assignment]
    failures: dict[str, str] = {}
    ratio, *_ = source._pledge("600001", "测试公司", failures)
    assert ratio is None
    assert failures["pledge"]


def test_r3_computed_pledge_ratio_over_100_is_rejected():
    source = EastmoneySource(
        now=TODAY,
        committed=(
            {"600001": {"subject": "控股股东", "report_date": "2026-09-30"}},
            {},
        ),
        committed_penalty_rows={},
        shares={"600001": 100_000_000},
        review={},
    )
    source._pledge_rows = lambda code: [  # type: ignore[assignment]
        {
            "HOLDER_NAME": "控股股东",
            "HOLD_NUM": "100",
            "ACCUM_PLEDGE_TSR": "153",
            "UNFREEZE_STATE": "未解押",
            "NOTICE_DATE": "2026-11-10 00:00:00",
        }
    ]
    failures: dict[str, str] = {}
    ratio, *_ = source._pledge("600001", "测试公司", failures)
    assert ratio is None
    assert "计算比例" in failures["pledge"]


# ---------------------------------------------------------------------------
# R4 扫描记录写证据
# ---------------------------------------------------------------------------
class FakeSource:
    def __init__(self, facts: GovernanceFacts):
        self.facts = facts

    def fetch(self, code: str, name: str = "") -> GovernanceFacts:
        return self.facts


def test_r4_member_dict_contains_pledge_audit_penalty_evidence():
    facts = GovernanceFacts(
        "600001",
        "测试公司",
        penalties=(
            PenaltyEvent(
                date="2026-03-01",
                event_type="立案",
                title="关于收到中国证监会立案告知书的公告",
                subject="测试公司",
                role="company",
                counted=True,
                info_disclosure=True,
                regulator=True,
                basis="正文关键词：信息披露类",
            ),
        ),
        audit_opinions=STANDARD,
        audit_sources={year: "eastmoney.f10.audit_opinion" for year in STANDARD},
        pledge_ratio=0.2,
        pledge_subject="控股股东甲",
        pledge_date="2026-09-30",
        pledge_source="eastmoney.announcement.pdf",
        penalty_source="eastmoney",
        penalty_window="2021-11-17 至 2026-11-17",
    )
    scan = evaluate_member(Member("600001", "测试公司", ("quality",)), facts, load_policy(), as_of=TODAY.date())
    data = scan.to_dict()
    evidence = data["evidence"]
    assert evidence["pledge"]["ratio"] == 0.2
    assert evidence["pledge"]["subject"] == "控股股东甲"
    assert evidence["pledge"]["as_of"] == "2026-09-30"
    assert evidence["pledge"]["source"] == "eastmoney.announcement.pdf"
    assert evidence["audit"]["2025"]["opinion"] == "标准无保留意见"
    assert evidence["audit"]["2025"]["source"] == "eastmoney.f10.audit_opinion"
    assert evidence["penalty"]["source"] == "eastmoney"
    assert evidence["penalty"]["window"] == "2021-11-17 至 2026-11-17"
    assert evidence["penalty"]["events"][0]["counted"] is True
    assert evidence["penalty"]["events"][0]["basis"] == "正文关键词：信息披露类"


def test_r4_scan_record_appends_evidence_and_keeps_old_records(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir()
    for pool in ("quality", "income", "bank"):
        (directory / f"{pool}-pool.jsonl").write_text(
            json.dumps(
                {
                    "as_of": "2026-11-12",
                    "policy_id": f"{pool}-pool-v1",
                    "pool_codes": ["600001"] if pool == "quality" else [],
                    "record_id": f"qpr-{pool}",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    scans = directory / "monitoring-scans.jsonl"
    old = {"schema_version": "monitoring-scan/1", "scan_date": "2026-11-10", "counts": {}, "members": []}
    scans.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
    before = scans.read_bytes()
    store = MonitoringStore(directory)
    facts = GovernanceFacts(
        "600001",
        "测试公司",
        audit_opinions=STANDARD,
        audit_sources={year: "eastmoney.f10.audit_opinion" for year in STANDARD},
        pledge_ratio=0.1,
        pledge_subject="控股股东甲",
        pledge_date="2026-09-30",
        pledge_source="eastmoney.pledge.detail",
        penalty_source="eastmoney",
        penalty_window="2021-11-17 至 2026-11-17",
    )
    payload = run_scan(
        store=store,
        source=FakeSource(facts),
        now=TODAY,
        members=[Member("600001", "测试公司", ("quality",))],
    )
    assert payload["status"] == "ok"
    assert scans.read_bytes().startswith(before)
    record = store.last_scan() or {}
    assert record["schema_version"] == "monitoring-scan/2"
    member = record["members"][0]
    assert member["evidence"]["pledge"]["ratio"] == 0.1
    assert member["evidence"]["audit"]["2024"]["opinion"] == "标准无保留意见"
    assert member["evidence"]["penalty"]["source"] == "eastmoney"
