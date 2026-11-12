#!/usr/bin/env python
"""稳定收息型数据补齐、治理补查、所有制标记与固定快照构建。

步骤（可断点续跑）：
    .venv/bin/python tools/build_income_pool_data.py --build-preliminary
    .venv/bin/python tools/build_income_pool_data.py --financial-candidates
    .venv/bin/python tools/build_income_pool_data.py --fetch-governance --workers 6
    .venv/bin/python tools/build_income_pool_data.py --fetch-ownership
    .venv/bin/python tools/build_income_pool_data.py --build-tables
    .venv/bin/python tools/build_income_pool_data.py --build-snapshot

- 财务表沿用第六批快照（`qps-3ebbd24be612bf7e`）的 44 张表；
- 分红补 2016–2020 财年（2016–2025 共 20 个报告期）；
- 治理复用第五、六批工具与口径，只对收息型候选（财务全过或只差一条）补查；
- 所有制表只覆盖两个池的公司与 30 只测试集，只标记、不参与判定。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.income.evaluator import evaluate_company, evaluate_pool  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import build_snapshot_dir, load_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table, write_table  # noqa: E402
from engine.quality.test_set import TEST_CODES  # noqa: E402
from tools.build_governance_data import (  # noqa: E402
    CACHE as GOVERNANCE_CACHE,
    TABLE_DIR as GOVERNANCE_TABLE_DIR,
    _get,
    _symbol,
    build_tables as build_governance_tables,
    fetch_company,
    now,
)

TZ = ZoneInfo("Asia/Singapore")
POOL_4F = ROOT / "data" / "quality-pool" / "snapshot-qps-3ebbd24be612bf7e"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
INCOME_DIR = ROOT / "data" / "income-pool"
DIVIDEND_DIR = ROOT / "data" / "cache" / "quality-pool" / "tables" / "dividends"
POLICY_PATH = ROOT / "data" / "policies" / "income-pool-v1-draft.json"
CANDIDATES_PATH = INCOME_DIR / "candidates.json"
MANIFEST_PATH = INCOME_DIR / "manifest.json"
OWNERSHIP_TABLE = GOVERNANCE_TABLE_DIR / "ownership.csv.gz"
OWNERSHIP_REVIEW = ROOT / "data" / "policy-inputs" / "ownership-review-v1.json"

OWNERSHIP_COLUMNS = [
    "code", "name", "ownership", "holder", "holder_role", "basis", "source_id", "source_url", "fetched_at",
]
CENTRAL_MARKERS = (
    "国务院国有资产监督管理委员会",
    "国务院国资委",
    "国务院",
    "财政部",
    "中央汇金",
    "中国国新",
    "中国诚通",
)
LOCAL_MARKERS = (
    "人民政府国有资产监督管理委员会",
    "国有资产监督管理委员会",
    "国资委",
    "财政局",
    "财政厅",
    "人民政府",
    "国有资产",
    "国有资本",
)
FOREIGN_MARKERS = (
    "香港",
    "澳门",
    "台湾",
    "新加坡",
    "日本",
    "美国",
    "英国",
    "法国",
    "德国",
    "荷兰",
    "瑞士",
    "瑞典",
    "韩国",
    "开曼",
    "维尔京",
    "百慕大",
    "国际",
    "海外",
    "株式会社",
)
OTHER_MARKERS = ("中国科学院", "研究院", "研究所", "社团", "联合会", "职工持股会", "工会")
PERSON_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}$")


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _financial_tables() -> list[Path]:
    return sorted(
        path
        for path in POOL_4F.glob("*.csv.gz")
        if not path.name.startswith("governance-")
    )


def _dividend_tables() -> list[Path]:
    return sorted(DIVIDEND_DIR.glob("dividends-*.csv.gz"))


def _load_manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def _write_pointer(directory: Path, target: Path, manifest: dict) -> None:
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "current": target.name,
                "snapshot_id": manifest["snapshot_id"],
                "content_hash": manifest["content_hash"],
                "as_of": manifest["as_of"],
                "tables": len(manifest["tables"]),
                "total_bytes": manifest["total_bytes"],
                "written_at": _now(),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _snapshot_sources() -> list[dict]:
    source = _load_manifest(POOL_4F).get("sources") or []
    return list(source) + [
        {
            "id": "eastmoney.dividend",
            "publisher": "东方财富",
            "interface": "RPT_SHAREBONUS_DET 按报告期",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "现金分红 2016–2025 财年，中期与年度方案；每 10 股派现（含税）× 总股本 ÷ 10。",
        },
        {
            "id": "eastmoney.f10.ownership",
            "publisher": "东方财富 F10",
            "interface": "RPT_F10_BASIC_ORGINFO ACTUAL_HOLDER；无实际控制人时用第一大股东",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "所有制只标记，不参与任何条件判定；逐家记录实际控制人/第一大股东名称与判定依据。",
        },
    ]


def build_preliminary() -> None:
    INCOME_DIR.mkdir(parents=True, exist_ok=True)
    window = dict(_load_manifest(POOL_4F).get("window") or {})
    window["dividend_start_fy"] = 2016
    target, manifest = build_snapshot_dir(
        _financial_tables() + _dividend_tables(),
        parent=INCOME_DIR,
        as_of=_load_manifest(POOL_4F)["as_of"],
        window=window,
        scope=_load_manifest(POOL_4F)["scope"],
        sources=_snapshot_sources(),
        untested_conditions=[],
        build_stats={"kind": "income_preliminary", "dividend_periods": len(_dividend_tables())},
    )
    _write_pointer(INCOME_DIR, target, manifest)
    print(f"预快照 {target.name}，{len(manifest['tables'])} 张表，{manifest['total_bytes']/1024/1024:.2f} MB")


def _current_snapshot_dir() -> Path:
    pointer = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return INCOME_DIR / pointer["current"]


def financial_candidates() -> list[dict]:
    snapshot_dir = _current_snapshot_dir()
    snapshot, companies, _stats = load_snapshot_dir(snapshot_dir)
    policy = load_policy(POLICY_PATH)
    rows = []
    for company in companies:
        result = evaluate_company(company, policy)
        if result.status == "out_of_scope":
            continue
        financial = [item for item in result.conditions if item.group_id != "governance"]
        fails = [item for item in financial if item.status == "fail"]
        pendings = [item for item in financial if item.status == "pending"]
        if pendings or len(fails) > 1:
            continue
        rows.append(
            {
                "code": company.code,
                "name": company.name,
                "industry": company.industry,
                "financial_fail_count": len(fails),
                "financial_fails": [item.condition_id for item in fails],
            }
        )
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_PATH.write_text(
        json.dumps(
            {
                "generated_at": _now(),
                "policy_id": policy.policy_id,
                "snapshot_id": snapshot["snapshot_id"],
                "count": len(rows),
                "candidates": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"收息型财务候选 {len(rows)} 家 → {CANDIDATES_PATH}")
    return rows


def _candidate_codes() -> list[dict]:
    payload = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    return payload["candidates"]


def _company_ratio_map() -> dict[str, float]:
    return {
        str(row["code"]).zfill(6): float(row["pledge_ratio"])
        for row in read_table(POOL_4F / "pledge.csv.gz")
        if row.get("pledge_ratio")
    }


def fetch_governance(workers: int) -> None:
    candidates = _candidate_codes()
    ratios = _company_ratio_map()
    pending = []
    for item in candidates:
        path = GOVERNANCE_CACHE / f"{item['code']}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("complete"):
                    continue
            except Exception:  # noqa: BLE001
                pass
        pending.append(item)
    print(f"候选 {len(candidates)} 家，待补治理 {len(pending)} 家", flush=True)

    def work(item: dict) -> dict:
        result = fetch_company(
            item["code"], item["name"], "income_candidate", ratios.get(item["code"])
        )
        path = GOVERNANCE_CACHE / f"{item['code']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(work, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            try:
                result = future.result()
                audit = (result.get("audit") or {}).get("available")
                pledge = (result.get("pledge") or {}).get("available")
                penalties = len((result.get("penalties") or {}).get("events") or [])
                print(
                    f"[{done}/{len(pending)}] {item['code']} {item['name']} "
                    f"audit={audit} pledge={pledge} penalties={penalties}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[{done}/{len(pending)}] {item['code']} FAILED {exc}", flush=True)
    print("治理补查完成", flush=True)


def _orginfo(code: str) -> dict:
    response = _get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_F10_BASIC_ORGINFO",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,ACTUAL_HOLDER,PROVINCE",
            "pageSize": "1",
            "pageNumber": "1",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{code}")',
        },
    )
    rows = ((response.json().get("result") or {}).get("data")) or []
    return rows[0] if rows else {}


def _fallback_holder(code: str) -> str | None:
    path = GOVERNANCE_CACHE / f"{code}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    pledge = data.get("pledge") or {}
    if pledge.get("subject"):
        return str(pledge["subject"])
    controller = data.get("controller") or {}
    names = controller.get("names") or []
    if not names and controller.get("name"):
        names = [controller["name"]]
    return "；".join(names) if names else None


CORPORATE_WORDS = re.compile(
    r"(plc|ltd|limited|inc|corp|company|group|s\.a|gmbh|foundation|fund|bank|holdings|capital|international)",
    re.IGNORECASE,
)


def _is_person_token(token: str) -> bool:
    compact = re.sub(r"\s+", "", token)
    if PERSON_RE.match(compact):
        return True
    # 例：Ge Li(李革)、Boliang Lou（楼柏良）
    if re.match(r"^[A-Za-z][A-Za-z .·'\-]*[\(（][\u4e00-\u9fa5]{2,4}[\)）]$", compact):
        return True
    # 例：LI-MOU ZHENG、Liu Chang
    if (
        re.match(r"^[A-Za-z][A-Za-z .·'\-]{2,30}$", compact)
        and not CORPORATE_WORDS.search(compact)
    ):
        return True
    return False


def classify_ownership(holder: str | None) -> tuple[str, str]:
    """返回 (所有制类别, 判定依据)。规则只标记，不参与池判定。"""
    if not holder or holder.strip() in ("", "无", "—", "-"):
        return "其他", "无实际控制人/第一大股东名称"
    text = holder.strip()
    compact = re.sub(r"\s+", "", text)
    if any(marker in compact for marker in CENTRAL_MARKERS):
        return "央企", "实际控制人含国务院/财政部/中央汇金等中央国有标记"
    if any(marker in compact for marker in OTHER_MARKERS):
        return "其他", "实际控制人为科研院所/社团/职工组织等"
    tokens = [item for item in re.split(r"[,，、;；]", compact) if item]
    if tokens and all(_is_person_token(token) for token in tokens):
        return "民企", "实际控制人为境内自然人"
    if any(marker in compact for marker in FOREIGN_MARKERS) or re.search(r"[A-Za-z]", compact):
        return "外资", "实际控制人含境外地区/国际/海外/外文名称"
    if any(marker in compact for marker in LOCAL_MARKERS):
        return "地方国企", "实际控制人含国资/政府/财政标记"
    if (
        any(marker in compact for marker in ("省", "市", "自治区", "县", "区"))
        and any(
            word in compact
            for word in ("投资控股", "发展集团", "金融控股", "投资集团", "建设投资", "控股集团")
        )
    ):
        return "地方国企", "实际控制人为地方国有投资/控股平台"
    if compact.startswith(("中国", "国家")) and any(
        word in compact for word in ("集团有限公司", "有限公司", "总公司")
    ):
        return "央企", "实际控制人为中央企业集团（中国/国家字头）"
    return "民企", "实际控制人为境内非国有主体（名称未命中国有/外资标记）"


def _manual_ownership_review() -> dict:
    if not OWNERSHIP_REVIEW.exists():
        return {}
    payload = json.loads(OWNERSHIP_REVIEW.read_text(encoding="utf-8"))
    return payload.get("overrides") or {}


def _required_ownership_codes() -> list[dict]:
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
    rows = [{"code": item["code"], "name": item["name"]} for item in quality.get("in_pool") or []]
    rows += [{"code": item["code"], "name": item["name"]} for item in _candidate_codes()]
    for code in TEST_CODES:
        rows.append({"code": code, "name": ""})
    seen = {}
    for row in rows:
        seen.setdefault(row["code"], row)
    return [seen[code] for code in sorted(seen)]


def fetch_ownership() -> list[dict]:
    required = _required_ownership_codes()
    overrides = _manual_ownership_review()
    rows = []
    for item in required:
        code = item["code"]
        try:
            info = _orginfo(code)
        except Exception:  # noqa: BLE001
            info = {}
        holder = str(info.get("ACTUAL_HOLDER") or "").strip()
        role = "实际控制人"
        source_url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO"
        if not holder or holder in ("无", "—", "-"):
            fallback = _fallback_holder(code)
            if fallback:
                holder = fallback
                role = "第一大股东（无实际控制人）"
                source_url = "东方财富 F10 股东表/治理质押主体"
        if code in overrides:
            node = overrides[code]
            ownership = str(node["ownership"])
            basis = str(node.get("basis") or "人工复核")
        else:
            ownership, basis = classify_ownership(holder)
        rows.append(
            {
                "code": code,
                "name": item["name"] or str(info.get("SECURITY_NAME_ABBR") or ""),
                "ownership": ownership,
                "holder": holder or None,
                "holder_role": role,
                "basis": basis,
                "source_id": "eastmoney.f10.ownership",
                "source_url": source_url,
                "fetched_at": _now(),
            }
        )
        time.sleep(0.1)
    OWNERSHIP_TABLE.parent.mkdir(parents=True, exist_ok=True)
    write_table(
        OWNERSHIP_TABLE,
        rows,
        OWNERSHIP_COLUMNS,
        metadata={
            "kind": "ownership",
            "source_id": "eastmoney.f10.ownership",
            "publisher": "东方财富 F10",
            "interface": "RPT_F10_BASIC_ORGINFO ACTUAL_HOLDER；无实际控制人时用第一大股东",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "definition_note": "央企/地方国企/民企/外资/其他；只标记，不参与判定；逐家记录实控人名称与依据。",
        },
    )
    print(f"所有制表 {len(rows)} 行 → {OWNERSHIP_TABLE}")
    counts = {}
    for row in rows:
        counts[row["ownership"]] = counts.get(row["ownership"], 0) + 1
    print("所有制分布（标记范围）", counts)
    return rows


def build_tables() -> None:
    for meta in build_governance_tables():
        print(meta["file"], meta["rows"], meta["size_bytes"])
    if not OWNERSHIP_TABLE.exists():
        raise RuntimeError("缺少所有制表；先运行 --fetch-ownership")


def build_snapshot() -> None:
    INCOME_DIR.mkdir(parents=True, exist_ok=True)
    window = dict(_load_manifest(POOL_4F).get("window") or {})
    window["dividend_start_fy"] = 2016
    table_paths = _financial_tables() + _dividend_tables()
    table_paths += [
        GOVERNANCE_TABLE_DIR / "governance-audit.csv.gz",
        GOVERNANCE_TABLE_DIR / "governance-penalty.csv.gz",
        GOVERNANCE_TABLE_DIR / "governance-pledge.csv.gz",
        GOVERNANCE_TABLE_DIR / "governance-coverage.csv.gz",
        OWNERSHIP_TABLE,
    ]
    candidates = _candidate_codes()
    target, manifest = build_snapshot_dir(
        table_paths,
        parent=INCOME_DIR,
        as_of=_load_manifest(POOL_4F)["as_of"],
        window=window,
        scope=_load_manifest(POOL_4F)["scope"],
        sources=_snapshot_sources(),
        untested_conditions=[],
        build_stats={
            "kind": "income_pool",
            "income_candidates": len(candidates),
            "dividend_periods": len(_dividend_tables()),
        },
    )
    _write_pointer(INCOME_DIR, target, manifest)
    print(f"收息型快照 {target.name}，{len(manifest['tables'])} 张表，{manifest['total_bytes']/1024/1024:.2f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-preliminary", action="store_true")
    parser.add_argument("--financial-candidates", action="store_true")
    parser.add_argument("--fetch-governance", action="store_true")
    parser.add_argument("--fetch-ownership", action="store_true")
    parser.add_argument("--build-tables", action="store_true")
    parser.add_argument("--build-snapshot", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.build_preliminary:
        build_preliminary()
    if args.financial_candidates:
        financial_candidates()
    if args.fetch_governance:
        fetch_governance(args.workers)
    if args.fetch_ownership:
        fetch_ownership()
    if args.build_tables:
        build_tables()
    if args.build_snapshot:
        build_snapshot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
