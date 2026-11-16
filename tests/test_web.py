"""第六阶段 G2–G5：本地交互页面。

覆盖：一条命令启动且只监听本机；六个部分齐全；三池表格字段可搜索/排序/筛选；
对问题集全部问题页面结果与 `./ask` 逐项一致；页面提问写账本规则与复算结果与 CLI 相同。
所有写入都走测试的临时账本（tests/conftest.py），真实账本前后哈希必须相同。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pytest

from engine import ledger_paths
from engine.ask.company_type import TYPE_ORDER
from engine.ask.service import AskService
from engine.web.app import WebApp
from engine.web.server import DEFAULT_PORT, HOST, STATIC_DIR, create_server, start_in_thread

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "data" / "ask" / "questions.json"
REPORTS = {
    "quality": ROOT / "data" / "quality-pool" / "report.json",
    "income": ROOT / "data" / "income-pool" / "report.json",
    "bank": ROOT / "data" / "bank-pool" / "report.json",
}
EQUITY_LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
FORBIDDEN = ("建议买入", "建议卖出", "推荐买入", "推荐卖出", "强烈推荐", "建议加仓", "建议减仓")
CLI_SAMPLE = (
    "贵州茅台怎么样",
    "招商银行怎么样",
    "平安银行怎么样",
    "沪深300 现在什么位置",
    "510300 能买吗",
    "好公司池有哪些",
    "格力在不在好公司池",
    "现在有哪些候选买点",
    "数据是哪天的",
    "星河科技怎么样",
)


# --- HTTP 辅助 --------------------------------------------------------------
def _request(url: str, *, method: str = "GET", payload: dict | None = None, timeout: int = 180):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"content-type": "application/json"} if body else {}
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.headers.get("Content-Type") or "",
                response.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type") or "", exc.read().decode("utf-8")


def get_json(url: str):
    status, _ctype, body = _request(url)
    return status, json.loads(body) if body else {}


def post_json(url: str, payload: dict):
    status, _ctype, body = _request(url, method="POST", payload=payload)
    return status, json.loads(body) if body else {}


def get_text(url: str):
    status, ctype, body = _request(url)
    return status, ctype, body


def _api(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


# --- 夹具 -------------------------------------------------------------------
@pytest.fixture(scope="module")
def web():
    app = WebApp(allow_update=False, analysis_date=date(2026, 11, 14), write_ledger=False)
    server, url, _thread = start_in_thread(app)
    try:
        yield {"app": app, "server": server, "url": url}
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def reference() -> AskService:
    return AskService(
        allow_update=False,
        write_ledger=False,
        use_live_quotes=True,
        analysis_date=date(2026, 11, 14),
    )


def _ledger_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in EQUITY_LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- G2 一条命令启动、只监听本机 --------------------------------------------
def test_web_entrypoint_and_defaults():
    path = ROOT / "web"
    assert path.is_file()
    assert os.access(path, os.X_OK)
    assert "engine.web" in path.read_text(encoding="utf-8")
    assert HOST == "127.0.0.1"
    assert DEFAULT_PORT == 8765


def test_server_refuses_non_loopback_host(web):
    with pytest.raises(ValueError):
        create_server(web["app"], host="0.0.0.0", port=0)


def test_server_listens_on_loopback_only(web):
    host, port = web["server"].server_address[0], web["server"].server_address[1]
    assert host == "127.0.0.1"
    assert web["url"] == f"http://127.0.0.1:{port}/"
    status, payload = get_json(_api(web["url"], "api/health"))
    assert status == 200
    assert payload == {"status": "ok", "host": "127.0.0.1"}


def _web_entry_command() -> list[str]:
    """干净归档不含 .venv：有本机解释器时直接跑 ./web，否则用同一模块入口。"""
    venv_python = ROOT / ".venv" / "bin" / "python"
    args = ["--no-browser", "--no-update", "--port", "0"]
    if venv_python.exists():
        return [str(ROOT / "web")] + args
    return [sys.executable, "-m", "engine.web"] + args


def test_web_command_starts_and_prints_local_url():
    """`./web` 一条命令启动：打印本机地址、只监听 127.0.0.1、无需其它参数。"""
    env = os.environ.copy()
    env.setdefault("INVESTMENT_SYSTEM_TODAY", "2026-11-14")
    process = subprocess.Popen(
        _web_entry_command(),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    holder: dict[str, str] = {}

    def _read() -> None:
        holder["line"] = process.stdout.readline() if process.stdout else ""

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(60)
    try:
        assert not reader.is_alive(), "启动超时（./web 没有打印地址）"
        line = holder.get("line", "")
        match = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        assert match, f"没有打印本机地址：{line!r}"
        status, payload = get_json(f"http://127.0.0.1:{match.group(1)}/api/health")
        assert status == 200
        assert payload["host"] == "127.0.0.1"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            process.wait(timeout=15)


# --- G3 页面六个部分 --------------------------------------------------------
def test_page_has_six_sections(web):
    status, ctype, body = get_text(web["url"])
    assert status == 200
    assert "text/html" in ctype
    for label in ("提问", "买点总览", "三个池", "指数", "账本", "数据状态"):
        assert label in body
    for panel in ("ask", "overview", "pools", "indices", "ledger", "status"):
        assert f'id="panel-{panel}"' in body


def test_page_does_not_load_external_resources(web):
    status, _ctype, body = get_text(web["url"])
    assert status == 200
    for text in (body,):
        assert "http://" not in text
        assert "https://" not in text
    for name in ("app.js", "style.css"):
        status, _ctype, asset = get_text(_api(web["url"], f"static/{name}"))
        assert status == 200
        assert "http://" not in asset
        assert "https://" not in asset


def test_static_assets_served_with_content_types(web):
    status, ctype, body = get_text(_api(web["url"], "static/app.js"))
    assert status == 200 and "javascript" in ctype and body
    status, ctype, body = get_text(_api(web["url"], "static/style.css"))
    assert status == 200 and "text/css" in ctype and body


def test_page_has_search_sort_and_filter_controls(web):
    _status, _ctype, body = get_text(web["url"])
    for element in (
        'id="pool-search"',
        'id="pool-industry"',
        'id="pool-ownership"',
        'id="pool-cycle"',
        'id="overview-pool"',
        'id="overview-industry"',
        'id="overview-ownership"',
        'id="ledger-input"',
        'id="modal"',
    ):
        assert element in body, element
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for token in (
        "pool-search",
        "data-key",
        "data-code",
        "ledger-companies",
        "/api/recompute",
        "/api/company",
    ):
        assert token in script, token


def test_overview_payload_groups_by_type_and_carries_filters(web):
    status, payload = get_json(_api(web["url"], "api/overview"))
    assert status == 200
    rows = payload["rows"]
    assert rows
    types = payload["types"]
    assert types == [item for item in TYPE_ORDER if item in types]
    assert {"好公司型", "好公司型＋收息型", "收息型", "银行池"} <= set(types)
    for row in rows:
        assert re.fullmatch(r"\d{6}", row["code"])
        assert row["conclusion"] in ("candidate_buy_point", "wait", "avoid")
        assert row["conclusion_label"]
        assert "industry" in row and "ownership" in row and "cycle" in row
        assert row["type"] in TYPE_ORDER
    assert [row for row in rows if row["conclusion"] == "candidate_buy_point"]
    assert payload["data_date"]
    assert payload["card_markdown"].splitlines()[0].startswith("**结论：")


def test_pools_payload_matches_reports_and_has_columns(web):
    status, payload = get_json(_api(web["url"], "api/pools"))
    assert status == 200
    expected_counts = {
        name: len(json.loads(path.read_text(encoding="utf-8"))["in_pool"])
        for name, path in REPORTS.items()
    }
    for key in ("quality", "income", "bank"):
        pool = payload["pools"][key]
        assert pool["count"] == expected_counts[key]
        assert len(pool["rows"]) == pool["count"]
        assert pool["columns"]
        labels = {metric["label"] for row in pool["rows"] for metric in row["metrics"]}
        for column in pool["columns"]:
            assert column in labels
        for row in pool["rows"]:
            assert re.fullmatch(r"\d{6}", row["code"])
            assert row["name"] and row["industry"] and row["ownership"] and row["cycle"]
            assert set(row) >= {"code", "name", "industry", "ownership", "cycle", "metrics"}
    bank_labels = {
        metric["label"]
        for row in payload["pools"]["bank"]["rows"]
        for metric in row["metrics"]
    }
    assert {"不良率", "拨备覆盖率", "核心一级"} <= bank_labels


def test_company_endpoint_returns_card_and_scorecard(web, reference):
    status, payload = get_json(_api(web["url"], "api/company?code=002594&pool=quality"))
    assert status == 200
    assert payload["ok"] is True
    assert payload["company_type"] == "池外"
    assert payload["card"]["markdown"] == reference.ask("比亚迪怎么样").markdown
    assert payload["scorecard"]["markdown"] == reference.ask(
        "比亚迪在不在好公司池", detail=True
    ).markdown
    assert payload["scorecard"]["in_pool"] is False
    assert "不在好公司池" in payload["scorecard"]["markdown"]
    assert "成绩单" in payload["scorecard"]["markdown"]

    status, in_pool = get_json(_api(web["url"], "api/company?code=600519&pool=quality"))
    assert status == 200
    assert in_pool["company_type"] == "好公司型＋收息型"
    assert in_pool["scorecard"]["in_pool"] is True
    assert in_pool["scorecard"]["markdown"] == reference.ask(
        "贵州茅台在不在好公司池", detail=True
    ).markdown


def test_indices_payload_has_four_indices_and_percentile_trends(web):
    status, payload = get_json(_api(web["url"], "api/indices"))
    assert status == 200
    assert [item["name"] for item in payload["indices"]] == [
        "沪深300",
        "中证500",
        "中证1000",
        "恒生科技",
    ]
    for item in payload["indices"][:3]:
        trend = item["trend"]
        assert trend["status"] == "ok", item["name"]
        assert len(trend["points"]) >= 2
        assert trend["series_id"].startswith("ser-")
        for point in trend["points"]:
            assert 0 <= point["percentile"] <= 100
            assert re.fullmatch(r"\d{4}-\d{2}", point["month"])
        assert "近 5 年" in item["markdown"]
    hstech = payload["indices"][3]
    assert hstech["trend"]["status"] == "no_series"
    assert hstech["trend"]["points"] == []
    assert "恒生指数公司" in hstech["markdown"]


def test_ledger_endpoint_lists_and_filters_with_reversals(web):
    status, payload = get_json(_api(web["url"], "api/ledger"))
    assert status == 200
    assert payload["total"] >= 110
    assert payload["returned"] == payload["total"]
    assert payload["companies"]

    status, filtered = get_json(_api(web["url"], "api/ledger?q=600486"))
    assert status == 200
    assert filtered["returned"] >= 2
    assert all(row["code"] == "600486" for row in filtered["rows"])
    reversals = [row for row in filtered["rows"] if row["record_type"] == "reversal"]
    assert reversals
    assert all(row["conclusion_label"] == "无法判断（改判）" for row in reversals)

    status, by_name = get_json(_api(web["url"], "api/ledger?q=" + quote("贵州茅台")))
    assert status == 200
    assert by_name["returned"] >= 1
    assert all(row["name"] == "贵州茅台" for row in by_name["rows"])


def test_status_endpoint_reports_data_date_update_and_reviews(web):
    status, payload = get_json(_api(web["url"], "api/status"))
    assert status == 200
    data = payload["status"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["quote_date"])
    assert data["equity_policy"]["policy_id"] == "equity-policy-v1.1"
    assert data["equity_policy"]["approved_at"] == "2026-11-12"
    assert data["pool_rules"]["next_review_date"] == "2027-11-13"
    assert data["bank_valuation"]["review_date"] == "2027-05-31"
    assert payload["next_review_date"] == "2027-05-31"
    assert data["update"]["run_at"]


# --- G4 对问题集全部问题，页面结果与 ./ask 逐项一致 -------------------------
def test_every_question_matches_ask_item_by_item(web, reference):
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
    problems: list[str] = []
    for item in questions:
        status, payload = post_json(
            _api(web["url"], "api/ask"), {"question": item["question"]}
        )
        if status != 200:
            problems.append(f"{item['id']} HTTP {status}")
            continue
        expected = reference.ask(item["question"])
        if payload["markdown"] != expected.markdown:
            problems.append(f"{item['id']} 卡片文字不一致")
        expected_data = json.loads(json.dumps(expected.data, ensure_ascii=False, default=str))
        for key in ("conclusion", "price", "price_date", "grade", "pools"):
            if payload["data"].get(key) != expected_data.get(key):
                problems.append(
                    f"{item['id']} data.{key} 不一致："
                    f"{payload['data'].get(key)!r} ≠ {expected_data.get(key)!r}"
                )
        if payload["intent"]["intent"] != expected.intent.name:
            problems.append(f"{item['id']} 意图不一致")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("question", CLI_SAMPLE)
def test_page_cards_match_cli_output(web, question):
    status, payload = post_json(_api(web["url"], "api/ask"), {"question": question})
    assert status == 200
    run = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--no-update", question],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stderr[-400:]
    assert payload["markdown"] == run.stdout.rstrip("\n"), question


def test_detail_parameter_expands_card(web):
    status, payload = post_json(
        _api(web["url"], "api/ask"), {"question": "贵州茅台怎么样", "detail": True}
    )
    assert status == 200
    assert payload["detail"] is True
    assert "【详情】" in payload["markdown"]
    assert payload["markdown"].rstrip().endswith("不构成投资建议。")


# --- G5 写账本规则与 ask 相同；复算按钮与 --recompute 相同 -------------------
def test_page_ask_writes_ledger_like_ask_and_skips_repeat():
    app = WebApp(
        allow_update=False,
        analysis_date=date(2026, 11, 14),
        write_ledger=True,
    )
    server, url, _thread = start_in_thread(app)
    try:
        real_before = ledger_paths.ledger_hashes()
        status, first = post_json(_api(url, "api/ask"), {"question": "贵州茅台怎么样"})
        status2, second = post_json(_api(url, "api/ask"), {"question": "贵州茅台怎么样"})
        assert (status, status2) == (200, 200)
        assert first["data"]["ledger"]["status"] in ("added", "skipped")
        assert second["data"]["ledger"]["status"] == "skipped"
        # 真实账本一个字节都不动（测试写入只到临时目录）
        assert ledger_paths.ledger_hashes() == real_before

        ledger = ledger_paths.ledger_dir() / "equity-conclusions.jsonl"
        rows = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        matching = [
            row
            for row in rows
            if str(row.get("code")) == "600519" and row.get("analysis_date") == "2026-11-14"
        ]
        assert matching, "页面提问没有写账本"
        assert len({row["snapshot_id"] for row in matching}) == len(matching), (
            "同一标的/同一分析日/同一快照应只写一条"
        )
        record = matching[-1]
        assert record["policy_version"] == "equity-policy-v1.1"
        assert record["snapshot_id"]
        assert record["values"]["base"]
        trace = record.get("trace") or {}
        assert trace.get("policy", {}).get("sha256")
        assert trace.get("quote", {}).get("price_date") == record["price_date"]
    finally:
        server.shutdown()
        server.server_close()


def test_recompute_button_result_matches_cli(web):
    rows = _ledger_rows()
    record = next((row for row in rows if row.get("trace")), None) or rows[0]
    status, payload = post_json(
        _api(web["url"], "api/recompute"), {"record_id": record["record_id"]}
    )
    assert status == 200
    run = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--recompute", record["record_id"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert run.returncode in (0, 1, 2), run.stderr[-400:]
    assert json.loads(run.stdout) == payload
    assert payload["status"] in ("consistent", "inconsistent", "not_recomputable")


def test_recompute_of_reversal_record_is_not_recomputable(web):
    rows = _ledger_rows()
    reversal = next((row for row in rows if row.get("record_type") == "reversal"), None)
    if reversal is None:  # pragma: no cover - 账本里已有改判记录
        pytest.skip("账本里没有改判记录")
    status, payload = post_json(
        _api(web["url"], "api/recompute"), {"record_id": reversal["record_id"]}
    )
    assert status == 200
    assert payload["status"] == "not_recomputable"
    run = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--recompute", reversal["record_id"]],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert run.returncode == 2
    assert json.loads(run.stdout) == payload


def test_recompute_unknown_record_is_not_found(web):
    status, payload = post_json(
        _api(web["url"], "api/recompute"), {"record_id": "concl-doesnotexist"}
    )
    assert status == 200
    assert payload["status"] == "not_found"


# --- 禁用词扫描同样适用于页面 ----------------------------------------------
@pytest.mark.parametrize("name", ["index.html", "app.js", "style.css"])
def test_static_assets_have_no_forbidden_words(name):
    text = (STATIC_DIR / name).read_text(encoding="utf-8")
    for word in FORBIDDEN:
        assert word not in text, (name, word)


def test_page_answers_have_no_forbidden_words(web):
    for question in ("招商银行怎么样", "平安银行怎么样", "现在有哪些候选买点", "星河科技怎么样"):
        status, payload = post_json(_api(web["url"], "api/ask"), {"question": question})
        assert status == 200
        for word in FORBIDDEN:
            assert word not in payload["markdown"], (question, word)


# --- 请求校验 ---------------------------------------------------------------
def test_bad_requests_are_rejected(web):
    status, payload = post_json(_api(web["url"], "api/ask"), {"question": ""})
    assert status == 400
    assert payload["error"]
    status, payload = get_json(_api(web["url"], "api/company"))
    assert status == 400
    status, payload = get_json(_api(web["url"], "api/not-exist"))
    assert status == 404
