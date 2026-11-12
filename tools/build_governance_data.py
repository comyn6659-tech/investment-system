#!/usr/bin/env python
"""好公司池补治理检验：对 252 家候选公司逐家抓取审计意见、处罚/立案、控股股东质押。

候选 = 第四批入池 144 家 + 只差一条 108 家，共 252 家；不扩到 252 家以外。
数据来源优先级：交易所/巨潮公告 > 东方财富 F10 > 其他；本脚本实际使用：
- 审计意见：东方财富 F10 个股资产负债表（`OPINION_TYPE`，2021–2025 年报）；
- 处罚/立案/公开谴责：东方财富公告检索（按标题关键词，最近 5 年）；
- 控股股东名称：国网 F10 个股页（东方财富 F10 不提供控股股东字段）；
- 控股股东质押：公司整体质押为 0 的，直接记 0；否则抓最新定期报告 PDF，
  从“前十名股东质押、标记或冻结情况”表读取控股股东质押股数 ÷ 持股数。

缓存逐家 JSON，断点续跑；PDF 解析后即删除，原始缓存不提交。
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from pypdf import PdfReader
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.snapshot import build_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table, write_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
CACHE = ROOT / "data" / "cache" / "governance"
TABLE_DIR = ROOT / "data" / "cache" / "quality-pool" / "tables"
REPORT = ROOT / "data" / "quality-pool" / "report.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CUTOFF = "2021-11-12"
_THREAD_LOCAL = threading.local()


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _THREAD_LOCAL.session = session
    return session
AUDIT_DATES = "2021-12-31,2022-12-31,2023-12-31,2024-12-31,2025-12-31"
POOL = ROOT / "data" / "quality-pool"
SNAPSHOT_4D = POOL / "snapshot-qps-e6fd0031c30f1e01"


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _get(url: str, *, params: dict | None = None, timeout: int = 30) -> requests.Response:
    last = None
    for attempt in range(4):
        try:
            response = _session().get(url, params=params, timeout=timeout, headers=UA)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"GET 失败：{url}（{last}）")


def _post(url: str, *, data: dict, timeout: int = 30) -> requests.Response:
    last = None
    for attempt in range(4):
        try:
            response = _session().post(url, data=data, timeout=timeout, headers=UA)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"POST 失败：{url}（{last}）")


def _symbol(code: str) -> str:
    return ("SH" if code.startswith("6") else "SZ") + code


def _strip(value: str | None) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def candidates() -> list[dict]:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = []
    for item in report["in_pool"]:
        rows.append({"code": item["code"], "name": item["name"], "batch4d": "in_pool"})
    for item in report["near_misses"]:
        rows.append({"code": item["code"], "name": item["name"], "batch4d": "near_miss"})
    seen = set()
    unique = []
    for row in rows:
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        unique.append(row)
    return unique


def audit_opinions(code: str) -> dict:
    response = _get(
        "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
        params={
            "companyType": "4",
            "reportDateType": "0",
            "reportType": "1",
            "dates": AUDIT_DATES,
            "code": _symbol(code),
        },
    )
    rows = response.json().get("data") or []
    opinions = {}
    for row in rows:
        year = str(row.get("REPORT_DATE") or "")[:4]
        if year:
            opinions[year] = row.get("OPINION_TYPE")
    return {
        "opinions": opinions,
        "available": all(opinions.get(str(year)) for year in range(2021, 2026)),
        "source_id": "eastmoney.f10.audit_opinion",
        "source_url": "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
        "fetched_at": now(),
    }


def controlling_shareholder(code: str) -> dict:
    symbol = _symbol(code)
    url = f"http://webf10.gw.com.cn/{symbol[:2]}/B10/{symbol}_B10.html"
    text = _get(url, timeout=45).content.decode("utf-8", errors="ignore")
    if '<th colspan="2">控股股东</th>' not in text:
        return {"names": [], "name": None, "available": False, "status": "missing",
                "source_id": "gw.f10.controller", "source_url": url, "fetched_at": now()}
    section = text.split('<th colspan="2">控股股东</th>', 1)[1].split("</table>", 1)[0]
    names = [
        _strip(value)
        for value in re.findall(r"<td>\s*名(?:\s|&nbsp;)*称\s*</td><td>(.*?)</td>", section, re.S)
    ]
    names = [name for name in dict.fromkeys(names) if name and name not in ("无", "不适用", "—", "-")]
    return {
        "names": names,
        "name": "；".join(names) if names else None,
        "available": True,
        "status": "named" if names else "none",
        "source_id": "gw.f10.controller",
        "source_url": url,
        "fetched_at": now(),
    }


def announcement_list(code: str, title: str, page: int = 1) -> list[dict]:
    response = _get(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        params={
            "sr": "-1",
            "page_size": "50",
            "page_index": str(page),
            "ann_type": "A",
            "client_source": "web",
            "stock_list": code,
            "title": title,
        },
    )
    return ((response.json().get("data") or {}).get("list")) or []


def announcement_content(art_code: str) -> dict:
    response = _get(
        "https://np-cnotice-stock.eastmoney.com/api/content/ann",
        params={"art_code": art_code, "client_source": "web", "page_index": "1"},
    )
    return response.json().get("data") or {}


def penalty_events(code: str) -> dict:
    cutoff = date.fromisoformat(CUTOFF)
    events = []
    searched = []
    for keyword in ("立案", "行政处罚", "公开谴责", "纪律处分"):
        try:
            rows = announcement_list(code, keyword)
            searched.append(keyword)
        except Exception as exc:  # noqa: BLE001
            return {
                "events": events,
                "available": False,
                "error": f"{keyword}: {exc}",
                "source_id": "eastmoney.announcement",
                "fetched_at": now(),
            }
        for row in rows:
            notice = str(row.get("notice_date") or "")[:10]
            if notice and notice < CUTOFF:
                continue
            title = _strip(row.get("title"))
            if keyword == "公开谴责" and "公开谴责" not in title:
                continue
            if keyword == "纪律处分" and "纪律处分" not in title and "公开谴责" not in title:
                continue
            if keyword == "立案" and "立案" not in title:
                continue
            if keyword == "行政处罚" and "行政处罚" not in title:
                continue
            events.append(
                {
                    "date": notice,
                    "type": keyword,
                    "title": title,
                    "art_code": row.get("art_code"),
                    "url": f"https://data.eastmoney.com/notices/detail/{code}/{row.get('art_code')}.html",
                }
            )
    # 同一事件可能同时命中多个关键词；按 art_code 去重，保留最具体类型。
    deduped = {}
    priority = {"立案": 3, "行政处罚": 2, "公开谴责": 1, "纪律处分": 0}
    for event in events:
        key = event.get("art_code") or event["title"]
        current = deduped.get(key)
        if current is None or priority.get(event["type"], 0) > priority.get(current["type"], 0):
            deduped[key] = event
    events = sorted(deduped.values(), key=lambda item: item.get("date") or "", reverse=True)

    info_words = ("信息披露", "虚假记载", "重大遗漏", "未按规定披露", "未及时披露", "信息披露违法", "财务造假", "定期报告")
    for event in events:
        content = ""
        try:
            content = _strip(announcement_content(event["art_code"]).get("notice_content"))
        except Exception:  # noqa: BLE001
            content = ""
        blob = event["title"] + " " + content
        event["doc_no"] = _extract_doc_no(blob)
        event["info_disclosure"] = any(word in blob for word in info_words)
        event["reason"] = _reason_snippet(blob, info_words)
        regulator = any(
            word in blob for word in ("中国证券监督管理委员会", "证监会", "证监局")
        )
        event["regulator"] = regulator
        if event["type"] == "立案":
            event["counted"] = event["info_disclosure"] and regulator
        elif event["type"] == "行政处罚":
            event["counted"] = event["info_disclosure"] and regulator
        elif event["type"] == "公开谴责":
            event["counted"] = True
        else:
            event["counted"] = "公开谴责" in blob
    return {
        "events": events,
        "available": True,
        "searched": searched,
        "source_id": "eastmoney.announcement",
        "source_url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
        "fetched_at": now(),
    }


def _extract_doc_no(text: str) -> str | None:
    match = re.search(r"〔\d{4}〕\s*\d+\s*号|\[\d{4}\]\s*\d+\s*号", text)
    return match.group(0) if match else None


def _reason_snippet(text: str, words: tuple[str, ...], width: int = 120) -> str | None:
    compact = re.sub(r"\s+", " ", text)
    for word in words:
        index = compact.find(word)
        if index >= 0:
            start = max(0, index - width // 2)
            return compact[start : start + width]
    return compact[:width] if compact else None


def _periodic_report(code: str, title: str, year: str) -> dict | None:
    try:
        rows = announcement_list(code, title)
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        notice = str(row.get("notice_date") or "")[:10]
        text = _strip(row.get("title"))
        if year not in notice and year not in text:
            continue
        if "摘要" in text or "英文" in text or "取消" in text:
            continue
        if title == "第三季度报告" and "三季度报告" not in text:
            continue
        if title == "半年度报告" and "半年度报告" not in text:
            continue
        if title == "年度报告" and ("年度报告" not in text or "半年度" in text or "季度" in text):
            continue
        return {"art_code": row.get("art_code"), "title": text, "notice_date": notice}
    return None


def _parse_pledge_table(text: str, controller: str) -> dict:
    compact = re.sub(r"\s+", "", text)
    name = re.sub(r"\s+", "", controller)
    index = compact.find(name)
    if index < 0:
        return {"error": "controller_not_in_top10"}
    segment = compact[index : index + 500]
    status_match = re.search(r"(质押|不适用|未知|无|冻结)", segment)
    if not status_match:
        return {"error": "row_not_parsed", "segment": segment}
    status = status_match.group(1)
    before = segment[: status_match.start()]
    after = segment[status_match.end() :]
    number_pattern = r"\d+(?:,\d{3})*"
    if "%" in before:
        # 65.80% 1,286,211,613：比例在前，持股在后
        pct_match = re.search(r"(\d{1,3}\.\d{2})%", before)
        if not pct_match:
            return {"error": "pct_not_parsed", "segment": segment}
        ratio_pct = pct_match.group(1)
        numbers = re.findall(number_pattern, before[pct_match.end() :])
    else:
        # 115,470,000 34.21 0：数量在前，比例在后；取最大数为持股
        numbers = re.findall(number_pattern, before)
        pct_matches = re.findall(r"(\d{1,3}\.\d{2})", before)
        if not pct_matches:
            return {"error": "pct_not_parsed", "segment": segment}
        ratio_pct = pct_matches[-1].lstrip("0") or "0"
        if not ratio_pct.startswith("0"):
            ratio_pct = ratio_pct if "." in ratio_pct else ratio_pct
    if not numbers:
        return {"error": "holdings_not_parsed", "segment": segment}
    holdings = max(numbers, key=lambda value: float(value.replace(",", "")))
    pledged_match = re.search(number_pattern, after)
    pledged = pledged_match.group(0) if pledged_match else "0"
    holdings_value = float(holdings.replace(",", ""))
    pledged_value = float(pledged.replace(",", "")) if pledged else 0.0
    if status == "未知":
        return {"error": "pledge_unknown", "segment": segment}
    if status in ("无", "不适用", "冻结"):
        pledged_value = 0.0
    return {
        "holdings": holdings_value,
        "pledged_shares": pledged_value,
        "pledge_ratio": pledged_value / holdings_value if holdings_value else None,
        "status": status,
        "holdings_pct": float(ratio_pct),
        "segment": segment,
    }


def pledge_ratio(code: str, controllers: list[str], company_ratio: float | None) -> dict:
    if not controllers:
        if not company_ratio:
            return {
                "available": True,
                "pledge_ratio": 0.0,
                "pledged_shares": 0.0,
                "holdings": None,
                "report_date": None,
                "source_id": "eastmoney.pledge.company_level",
                "note": "无控股股东且公司整体质押为 0",
                "fetched_at": now(),
            }
        return {
            "available": False,
            "error": "no_controller_with_company_pledge",
            "source_id": "gw.f10.controller",
            "fetched_at": now(),
        }
    if not company_ratio:
        return {
            "available": True,
            "pledge_ratio": 0.0,
            "pledged_shares": 0.0,
            "holdings": None,
            "report_date": None,
            "source_id": "eastmoney.pledge.company_level",
            "source_url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "公司整体质押比例为 0（未出现在质押比例表），控股股东质押必为 0",
            "fetched_at": now(),
        }
    attempts = []
    for title, year in (("第三季度报告", "2026"), ("半年度报告", "2026"), ("年度报告", "2025")):
        report = _periodic_report(code, title, year)
        if report is None:
            attempts.append({"report": f"{title} {year}", "error": "not_found"})
            continue
        try:
            pdf_url = f"https://pdf.dfcfw.com/pdf/H2_{report['art_code']}_1.pdf"
            pdf = _get(pdf_url, timeout=60).content
            reader = PdfReader(io.BytesIO(pdf))
        except Exception as exc:  # noqa: BLE001
            attempts.append({"report": report.get("title"), "error": str(exc)[:120]})
            continue
        for page in reader.pages:
            text = page.extract_text() or ""
            if "质押" not in text:
                continue
            if "前十名股东" not in text and "前10 名股东" not in text and "前10名股东" not in text:
                continue
            matches = []
            for controller in controllers:
                parsed = _parse_pledge_table(text, controller)
                if "error" not in parsed:
                    matches.append({"controller": controller, **parsed})
            if matches:
                worst = max(matches, key=lambda item: item.get("pledge_ratio") or 0.0)
                return {
                    "available": True,
                    **worst,
                    "controllers": controllers,
                    "matched": [item["controller"] for item in matches],
                    "report_date": str(year),
                    "report_title": report["title"],
                    "source_id": "eastmoney.announcement.pdf",
                    "source_url": pdf_url,
                    "note": "最新定期报告前十名股东表「质押、标记或冻结情况」；多个控股股东取最高比例",
                    "fetched_at": now(),
                    "attempts": attempts,
                }
    return {
        "available": False,
        "error": "no_periodic_pledge_table",
        "attempts": attempts,
        "source_id": "eastmoney.announcement.pdf",
        "fetched_at": now(),
    }


def fetch_company(code: str, name: str, batch4d: str, company_ratio: float | None) -> dict:
    result = {"code": code, "name": name, "batch4d": batch4d, "fetched_at": now()}
    try:
        result["audit"] = audit_opinions(code)
    except Exception as exc:  # noqa: BLE001
        result["audit"] = {"available": False, "error": str(exc), "fetched_at": now()}
    try:
        result["controller"] = controlling_shareholder(code)
    except Exception as exc:  # noqa: BLE001
        result["controller"] = {"available": False, "error": str(exc), "fetched_at": now()}
    controllers = (result.get("controller") or {}).get("names") or []
    try:
        result["pledge"] = pledge_ratio(code, controllers, company_ratio)
    except Exception as exc:  # noqa: BLE001
        result["pledge"] = {"available": False, "error": str(exc), "fetched_at": now()}
    try:
        result["penalties"] = penalty_events(code)
    except Exception as exc:  # noqa: BLE001
        result["penalties"] = {"available": False, "error": str(exc), "fetched_at": now()}
    result["complete"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    codes = candidates()
    if args.limit:
        codes = codes[: args.limit]
    pledge_rows = {
        row["code"]: float(row["pledge_ratio"])
        for row in read_table(ROOT / "data" / "quality-pool" / "snapshot-qps-e6fd0031c30f1e01" / "pledge.csv.gz")
        if row.get("pledge_ratio")
    }
    pending = []
    for item in codes:
        path = CACHE / f"{item['code']}.json"
        if path.exists() and not args.force:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("complete"):
                    continue
            except Exception:  # noqa: BLE001
                pass
        pending.append(item)
    print(f"候选 {len(codes)} 家，待抓 {len(pending)} 家", flush=True)

    def work(item: dict) -> dict:
        result = fetch_company(item["code"], item["name"], item["batch4d"], pledge_rows.get(item["code"]))
        path = CACHE / f"{item['code']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(work, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            try:
                result = future.result()
                audit = (result.get("audit") or {}).get("available")
                pledge = (result.get("pledge") or {}).get("available")
                penalties = len((result.get("penalties") or {}).get("events") or [])
                print(f"[{done}/{len(pending)}] {item['code']} {item['name']} audit={audit} pledge={pledge} penalties={penalties}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[{done}/{len(pending)}] {item['code']} FAILED {exc}", flush=True)
    print("采集完成", flush=True)
    return 0


AUDIT_COLUMNS = ["code", "fiscal_year", "opinion_type", "source_id", "source_url", "fetched_at"]
PENALTY_COLUMNS = [
    "code", "event_date", "event_type", "title", "reason", "doc_no", "url",
    "info_disclosure", "counted", "source_id", "fetched_at",
]
PLEDGE_COLUMNS = [
    "code", "controller", "holdings", "pledged_shares", "pledge_ratio",
    "report_date", "source_id", "source_url", "note", "fetched_at",
]
COVERAGE_COLUMNS = [
    "code", "audit_available", "penalty_available", "pledge_available",
    "audit_years", "notes",
]


def build_tables() -> list[dict]:
    audit_rows = []
    penalty_rows = []
    pledge_rows = []
    coverage_rows = []
    for path in sorted(CACHE.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        code = data["code"]
        audit = data.get("audit") or {}
        for year, opinion in sorted((audit.get("opinions") or {}).items()):
            audit_rows.append(
                {
                    "code": code,
                    "fiscal_year": year,
                    "opinion_type": opinion,
                    "source_id": audit.get("source_id"),
                    "source_url": audit.get("source_url"),
                    "fetched_at": audit.get("fetched_at"),
                }
            )
        penalties = data.get("penalties") or {}
        for event in penalties.get("events") or []:
            penalty_rows.append(
                {
                    "code": code,
                    "event_date": event.get("date"),
                    "event_type": event.get("type"),
                    "title": event.get("title"),
                    "reason": event.get("reason"),
                    "doc_no": event.get("doc_no"),
                    "url": event.get("url"),
                    "info_disclosure": event.get("info_disclosure"),
                    "counted": (
                        bool(event.get("info_disclosure"))
                        and any(
                            word in (str(event.get("title")) + " " + str(event.get("reason")))
                            for word in ("中国证券监督管理委员会", "证监会", "证监局")
                        )
                        if event.get("type") in ("立案", "行政处罚")
                        else event.get("counted")
                    ),
                    "source_id": penalties.get("source_id"),
                    "fetched_at": penalties.get("fetched_at"),
                }
            )
        pledge = data.get("pledge") or {}
        pledge_rows.append(
            {
                "code": code,
                "controller": (data.get("controller") or {}).get("name"),
                "holdings": pledge.get("holdings"),
                "pledged_shares": pledge.get("pledged_shares"),
                "pledge_ratio": pledge.get("pledge_ratio"),
                "report_date": pledge.get("report_date"),
                "source_id": pledge.get("source_id"),
                "source_url": pledge.get("source_url"),
                "note": pledge.get("note") or pledge.get("error"),
                "fetched_at": pledge.get("fetched_at"),
            }
        )
        coverage_rows.append(
            {
                "code": code,
                "audit_available": bool(audit.get("available")),
                "penalty_available": bool(penalties.get("available")),
                "pledge_available": bool(pledge.get("available")),
                "audit_years": len(audit.get("opinions") or {}),
                "notes": pledge.get("error") or "",
            }
        )
    metas = []
    metas.append(write_table(
        TABLE_DIR / "governance-audit.csv.gz", audit_rows, AUDIT_COLUMNS,
        metadata={"kind": "governance_audit", "source_id": "eastmoney.f10.audit_opinion",
                  "publisher": "东方财富", "interface": "PC_HSF10 zcfzbAjaxNew OPINION_TYPE",
                  "url": "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
                  "definition_note": "2021–2025 年报审计意见类型；逐家取，缺年份即不可得。"},
    ))
    metas.append(write_table(
        TABLE_DIR / "governance-penalty.csv.gz", penalty_rows, PENALTY_COLUMNS,
        metadata={"kind": "governance_penalty", "source_id": "eastmoney.announcement",
                  "publisher": "东方财富", "interface": "公告检索（立案/行政处罚/公开谴责/纪律处分）",
                  "url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
                  "definition_note": "2021-11-12 至 2026-11-12；counted 为规则实际计件。"},
    ))
    metas.append(write_table(
        TABLE_DIR / "governance-pledge.csv.gz", pledge_rows, PLEDGE_COLUMNS,
        metadata={"kind": "governance_pledge", "source_id": "mixed",
                  "publisher": "东方财富 / 国网 F10 / 定期报告 PDF",
                  "interface": "公司整体质押表 + 最新定期报告前十名股东表",
                  "url": "https://data.eastmoney.com/gpzy/",
                  "definition_note": "控股股东质押股数 ÷ 其持股数；无控股股东且公司整体质押 >0 记不可得。"},
    ))
    metas.append(write_table(
        TABLE_DIR / "governance-coverage.csv.gz", coverage_rows, COVERAGE_COLUMNS,
        metadata={"kind": "governance_coverage", "source_id": "mixed",
                  "publisher": "本项目", "interface": "逐家采集结果汇总",
                  "url": "", "definition_note": "252 家候选的治理三项可得性。"},
    ))
    return metas


def build_snapshot() -> dict:
    expected = {item["code"] for item in candidates()}
    cached = {path.stem for path in CACHE.glob("*.json")}
    missing = sorted(expected - cached)
    if missing:
        raise RuntimeError(f"还有 {len(missing)} 家未采集：{missing[:10]}")
    manifest_4d = json.loads((POOL / "manifest.json").read_text(encoding="utf-8"))
    # 4d 清单可能已指向 4e；固定用 4d 目录。
    old_manifest = json.loads((SNAPSHOT_4D / "manifest.json").read_text(encoding="utf-8"))
    table_paths = [p for p in SNAPSHOT_4D.glob("*.csv.gz")]
    table_paths += [
        TABLE_DIR / "governance-audit.csv.gz",
        TABLE_DIR / "governance-penalty.csv.gz",
        TABLE_DIR / "governance-pledge.csv.gz",
        TABLE_DIR / "governance-coverage.csv.gz",
    ]
    target, manifest = build_snapshot_dir(
        table_paths,
        parent=POOL,
        as_of="2026-11-12",
        window=old_manifest["window"],
        scope=old_manifest["scope"],
        sources=list(old_manifest.get("sources") or []) + [
            {"id": "eastmoney.f10.audit_opinion", "publisher": "东方财富",
             "interface": "F10 个股资产负债表 OPINION_TYPE", "url": "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
             "note": "2021–2025 年报审计意见；逐家取。"},
            {"id": "eastmoney.announcement", "publisher": "东方财富",
             "interface": "公告检索（立案/行政处罚/公开谴责/纪律处分）", "url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
             "note": "最近 5 年；正文核对信息披露类。"},
            {"id": "gw.f10.controller", "publisher": "国网 F10",
             "interface": "个股控股股东", "url": "http://webf10.gw.com.cn/",
             "note": "只用于控股股东名称；质押比例取定期报告 PDF。"},
        ],
        untested_conditions=[],
        build_stats={"candidates": 252, "governance": True},
    )
    (POOL / "manifest.json").write_text(
        json.dumps(
            {
                "current": target.name,
                "snapshot_id": manifest["snapshot_id"],
                "content_hash": manifest["content_hash"],
                "as_of": manifest["as_of"],
                "tables": len(manifest["tables"]),
                "total_bytes": manifest["total_bytes"],
                "written_at": now(),
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"新快照 {target.name}，{len(manifest['tables'])} 张表，{manifest['total_bytes']/1024/1024:.2f} MB", flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-tables", action="store_true")
    parser.add_argument("--build-snapshot", action="store_true")
    args, _ = parser.parse_known_args()
    if args.build_tables:
        for meta in build_tables():
            print(meta["file"], meta["rows"], meta["size_bytes"], flush=True)
        raise SystemExit(0)
    if args.build_snapshot:
        build_snapshot()
        raise SystemExit(0)
    raise SystemExit(main())
