#!/usr/bin/env python
"""好公司池补治理检验：对 252 家候选公司逐家抓取审计意见、处罚/立案、质押。

候选固定为第四批报告（`report-4d.json`）的入池 144 家 + 只差一条 108 家，共 252 家；
不随第五/第六批结果变化而扩大或缩小。

数据来源优先级：交易所/巨潮公告 > 东方财富 F10 > 其他；本脚本实际使用：
- 审计意见：东方财富 F10 个股资产负债表（`OPINION_TYPE`，2021–2025 年报）；
- 处罚/立案/公开谴责：东方财富公告检索（按标题关键词，最近 5 年），逐条读正文判定
  主体身份（batch4f K3）；人工核对结果保存在 `data/policy-inputs/governance-review-v1.json`；
- 控股股东/第一大股东质押：公司整体质押为 0 的，直接记 0；否则抓最新定期报告 PDF，
  从“前十名股东质押、标记或冻结情况”表读取；表内取不到的，用东方财富重要股东质押
  明细或年报释义补足（batch4f K1）；无控股股东时按第一大股东（含一致行动人合计）
  口径（batch4f K2）。

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
CANDIDATE_REPORT = ROOT / "data" / "quality-pool" / "report-4d.json"
REVIEW_PATH = ROOT / "data" / "policy-inputs" / "governance-review-v1.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CUTOFF = "2021-11-12"
_THREAD_LOCAL = threading.local()
AUDIT_DATES = "2021-12-31,2022-12-31,2023-12-31,2024-12-31,2025-12-31"
POOL = ROOT / "data" / "quality-pool"
SNAPSHOT_4D = POOL / "snapshot-qps-e6fd0031c30f1e01"


def _session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _THREAD_LOCAL.session = session
    return session


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


def _symbol(code: str) -> str:
    return ("SH" if code.startswith("6") else "SZ") + code


def _strip(value: str | None) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _norm_name(name: str | None) -> str:
    text = str(name or "").upper().replace(" ", "").replace("\u3000", "")
    return (
        text.replace("（", "(")
        .replace("）", ")")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )


def _review() -> dict:
    return json.loads(REVIEW_PATH.read_text(encoding="utf-8"))


def candidates() -> list[dict]:
    """固定第四批 252 家候选；不受后续批次名单变化影响。"""
    report = json.loads(CANDIDATE_REPORT.read_text(encoding="utf-8"))
    rows = [
        {"code": item["code"], "name": item["name"], "batch4d": "in_pool"}
        for item in report["in_pool"]
    ]
    rows += [
        {"code": item["code"], "name": item["name"], "batch4d": "near_miss"}
        for item in report["near_misses"]
    ]
    seen: set[str] = set()
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
        return {
            "names": [],
            "name": None,
            "available": False,
            "status": "missing",
            "source_id": "gw.f10.controller",
            "source_url": url,
            "fetched_at": now(),
        }
    section = text.split('<th colspan="2">控股股东</th>', 1)[1].split("</table>", 1)[0]
    names = [
        _strip(value)
        for value in re.findall(
            r"<td>\s*名(?:\s|&nbsp;)*称\s*</td><td>(.*?)</td>", section, re.S
        )
    ]
    names = [
        name
        for name in dict.fromkeys(names)
        if name and name not in ("无", "不适用", "—", "-")
    ]
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
    deduped = {}
    priority = {"立案": 3, "行政处罚": 2, "公开谴责": 1, "纪律处分": 0}
    for event in events:
        key = event.get("art_code") or event["title"]
        current = deduped.get(key)
        if current is None or priority.get(event["type"], 0) > priority.get(current["type"], 0):
            deduped[key] = event
    events = sorted(deduped.values(), key=lambda item: item.get("date") or "", reverse=True)

    for event in events:
        content = ""
        try:
            content = _strip(announcement_content(event["art_code"]).get("notice_content"))
        except Exception:  # noqa: BLE001
            content = ""
        blob = event["title"] + " " + content
        event["doc_no"] = _extract_doc_no(blob)
        event["reason"] = _reason_snippet(blob, ("信息披露", "内幕交易", "短线交易", "虚假记载", "重大遗漏", "行政处罚", "立案"))
    return {
        "events": events,
        "available": True,
        "searched": searched,
        "source_id": "eastmoney.announcement",
        "source_url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
        "fetched_at": now(),
    }


def _extract_doc_no(text: str) -> str | None:
    match = re.search(r"〔\d{4}〕\s*\d+\s*号|\[\d{4}\]\s*\d+\s*号|\（\d{4}\）\s*\d+\s*号", text)
    return match.group(0) if match else None


def _reason_snippet(text: str, words: tuple[str, ...], width: int = 140) -> str | None:
    compact = re.sub(r"\s+", " ", text)
    for word in words:
        index = compact.find(word)
        if index >= 0:
            start = max(0, index - width // 2)
            return compact[start : start + width]
    return compact[:width] if compact else None


# ---------------------------------------------------------------------------
# 处罚主体判定（batch4f K3）
# ---------------------------------------------------------------------------

COUNTED_ROLES = frozenset(
    {"company", "controller", "actual_controller", "concert_party", "director_officer"}
)
ROLE_LABELS = {
    "company": "公司本身",
    "controller": "控股股东",
    "actual_controller": "实际控制人",
    "concert_party": "一致行动人",
    "director_officer": "时任董监高",
    "other_shareholder": "其他股东（非控股、非一致行动人）",
    "subsidiary": "子公司/分公司",
    "other_entity": "其他主体",
    "unknown": "主体身份不明",
}
INFO_PATTERNS = (
    "信息披露违法违规",
    "涉嫌信息披露违法",
    "信息披露违法",
    "虚假记载",
    "重大遗漏",
    "未按规定披露",
    "未及时披露",
    "未准确披露",
    "信息披露不准确",
    "未真实披露",
    "违规披露",
)
INFO_BOILERPLATE = (
    "保证信息披露的内容真实",
    "保证公告内容的真实",
    "履行信息披露义务",
    "指定信息披露媒体",
    "信息披露媒体为",
    "及时履行信息披露义务",
    "按照有关法律法规的规定和要求，及时履行信息披露义务",
    "没有虚假记载",
    "不存在虚假记载",
    "误导性陈述或重大遗漏",
    "真实、准确、完整",
)
DUTY_PATTERN = re.compile(r"董事|监事|高级管理人员|高管|总经理|副总经理|董事会秘书|财务总监")
PUBLIC_CENSURE = ("公开谴责",)


def _info_disclosure(blob: str) -> bool:
    text = re.sub(r"\s+", "", blob)
    for phrase in INFO_BOILERPLATE:
        text = text.replace(re.sub(r"\s+", "", phrase), "")
    return any(pattern in text for pattern in INFO_PATTERNS)


def _penalty_role(title: str, company_name: str) -> tuple[str | None, str | None]:
    if "实际控制人" in title:
        return "actual_controller", "实际控制人（公告所述）"
    if DUTY_PATTERN.search(title):
        return "director_officer", _duty_subject(title)
    if "间接控股股东" in title:
        return "other_entity", "间接控股股东"
    if "控股股东" in title:
        return "controller", "控股股东"
    if "股东" in title:
        return "other_shareholder", _shareholder_subject(title)
    if "子公司" in title or "分公司" in title:
        return "subsidiary", "子公司/分公司"
    if title.strip():
        return "company", company_name
    return "unknown", None


def _duty_subject(title: str) -> str:
    match = re.search(r"董事|监事|高级管理人员|高管|总经理|副总经理|董事会秘书|财务总监", title)
    return f"时任{match.group(0)}（公告所述）" if match else "时任董监高（公告所述）"


def _shareholder_subject(title: str) -> str:
    match = re.search(r"股东[^，。；)）]{0,20}", title)
    return match.group(0) if match else "股东（公告所述）"


def _regulator(blob: str, event_type: str) -> bool:
    if event_type in ("立案", "行政处罚"):
        return any(word in blob for word in ("中国证券监督管理委员会", "证监会", "证监局"))
    return any(
        word in blob
        for word in ("上交所", "上海证券交易所", "深交所", "深圳证券交易所", "证券交易所")
    )


def classify_penalty_events(code: str, name: str, events: list[dict]) -> list[dict]:
    review = _review()["penalty_events"]
    output = []
    for event in events:
        blob = event["title"] + " " + (event.get("reason") or "")
        override = review.get(event.get("art_code")) or {}
        if override:
            subject = override.get("subject")
            role = override.get("role")
            info = bool(override.get("info_disclosure"))
            basis = override.get("basis")
            public_censure = bool(override.get("public_censure", "公开谴责" in blob))
        else:
            role, subject = _penalty_role(event["title"], name)
            info = _info_disclosure(blob)
            basis = "标题/正文关键词：" + ("信息披露类" if info else "非信息披露类")
            if subject is None:
                subject = name if role == "company" else None
            public_censure = any(word in blob for word in PUBLIC_CENSURE)
        regulator = override.get("regulator") if override and "regulator" in override else _regulator(blob, event["type"])
        if event["type"] in ("公开谴责", "纪律处分"):
            final = public_censure
        else:
            final = True
        counted = info and regulator and final and role in COUNTED_ROLES
        unknown = info and regulator and final and role == "unknown"
        output.append(
            dict(
                event,
                subject=subject,
                subject_role=role,
                subject_basis=basis,
                info_disclosure=info,
                regulator=regulator,
                counted=counted,
                subject_unknown=unknown,
            )
        )
    return output


# ---------------------------------------------------------------------------
# 质押解析（batch4f K1/K2）
# ---------------------------------------------------------------------------

REPORT_KINDS = (
    ("第三季度报告", 2026, "2026 年三季报"),
    ("半年度报告", 2026, "2026 年半年报"),
    ("年度报告", 2025, "2025 年年报"),
    ("第三季度报告", 2025, "2025 年三季报"),
)
REPORT_EXCLUDE = (
    "摘要",
    "英文",
    "取消",
    "提示性",
    "业绩",
    "说明会",
    "更正",
    "补充",
    "问询",
    "回复",
    "预案",
    "审计",
    "更新",
)


def _periodic_report(code: str, title: str, year: int) -> dict | None:
    try:
        rows = announcement_list(code, title)
    except Exception:  # noqa: BLE001
        return None
    pattern = re.compile(rf"{year}年(?:第三|三)季度报告" if title == "第三季度报告" else rf"{year}年{title}")
    for row in rows:
        text = _strip(row.get("title"))
        if not pattern.search(text):
            continue
        if any(word in text for word in REPORT_EXCLUDE):
            continue
        return {
            "art_code": row.get("art_code"),
            "title": text,
            "notice_date": str(row.get("notice_date") or "")[:10],
            "kind": title,
            "year": year,
        }
    return None


def _pdf_text(art_code: str) -> tuple[str, str]:
    url = f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf"
    pdf = _get(url, timeout=90).content
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join((page.extract_text() or "") for page in reader.pages), url


def _top10_region(text: str) -> str | None:
    flat = re.sub(r"[ \t]+", " ", text)
    start = re.search(r"前\s*10\s*名股东持股情况", flat)
    if not start:
        return None
    end = re.search(r"前\s*10\s*名无限售条件股东持股情况", flat[start.end() :])
    return flat[start.start() : start.end() + (end.start() if end else 3000)]


def _parse_pledge_row(region: str | None, holdings: float | int | None) -> dict:
    if not region or not holdings:
        return {"status": "missing", "error": "no_region_or_holdings"}
    anchor = f"{int(float(holdings)):,}"
    index = region.find(anchor)
    if index < 0:
        return {"status": "missing", "error": "holdings_not_in_region"}
    tail = re.sub(r"\s+", " ", region[index + len(anchor) : index + len(anchor) + 120])
    match = re.match(
        r"^[\s\d.,%\-]*(质押并冻结|质押|冻结|标记|未知|不适用|无|-)", tail
    )
    if not match:
        return {"status": "missing", "error": "status_not_parsed", "tail": tail[:80]}
    status = match.group(1)
    if status == "未知":
        return {"status": "unknown", "tail": tail[:80]}
    if status in ("无", "不适用", "-", "冻结"):
        return {"status": "none", "pledged_shares": 0.0, "status_text": status}
    rest = tail[match.end() :]
    number = re.search(r"\d+(?:,\d{3})*", rest)
    if not number:
        return {"status": "unknown", "error": "pledged_not_parsed", "tail": tail[:80]}
    return {
        "status": "pledged",
        "pledged_shares": float(number.group(0).replace(",", "")),
        "status_text": status,
    }


def _parse_pledge_by_name(region: str | None, name: str, holder_names: list[str]) -> dict:
    """按股东姓名定位行：适配 PDF 把持股数拆成“2,742,578, 825”的情况。"""
    if not region or not name:
        return {"status": "missing", "error": "no_region_or_name"}
    pattern = r"\s*".join(re.escape(char) for char in name)
    match = re.search(pattern, region)
    if not match:
        return {"status": "missing", "error": "name_not_in_region"}
    end = len(region)
    for other in holder_names:
        if not other or other == name:
            continue
        other_pattern = r"\s*".join(re.escape(char) for char in other)
        other_match = re.search(other_pattern, region[match.end() :])
        if other_match:
            end = min(end, match.end() + other_match.start())
    segment = region[match.end() : end]
    keywords = list(
        re.finditer(r"质押并冻结|质押|冻结|标记|未知|不适用|无|-", segment)
    )
    if not keywords:
        return {"status": "missing", "error": "status_not_parsed", "tail": segment[:80]}
    last = keywords[-1]
    status = last.group(0)
    if status in ("无", "不适用", "-", "冻结"):
        return {"status": "none", "pledged_shares": 0.0, "status_text": status}
    if status == "未知":
        return {"status": "unknown", "tail": segment[:80]}
    number = re.search(r"\d+(?:,\d{3})*", segment[last.end() :])
    if not number:
        return {"status": "unknown", "error": "pledged_not_parsed", "tail": segment[:80]}
    return {
        "status": "pledged",
        "pledged_shares": float(number.group(0).replace(",", "")),
        "status_text": status,
    }


def _latest_report(code: str) -> tuple[dict | None, str | None, str | None]:
    for title, year, label in REPORT_KINDS:
        report = _periodic_report(code, title, year)
        if report is None:
            continue
        try:
            text, url = _pdf_text(report["art_code"])
        except Exception:  # noqa: BLE001
            continue
        region = _top10_region(text)
        if region and "股东名称" in region:
            period = {
                "第三季度报告": f"{year}-09-30",
                "半年度报告": f"{year}-06-30",
                "年度报告": f"{year}-12-31",
            }[title]
            return report | {"label": label, "pdf_url": url, "period_end": period}, region, text
    return None, None, None


def _sdgd_rows(code: str) -> list[dict]:
    response = _get(
        "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax",
        params={"code": _symbol(code)},
    )
    payload = response.json()
    rows = payload.get("sdgd") or []
    if not rows:
        return []
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        day = str(row.get("END_DATE"))[:10]
        if day:
            grouped.setdefault(day, []).append(row)
    if not grouped:
        return []
    quarter = grouped.get("2026-09-30") or []
    latest = (
        "2026-09-30"
        if len(quarter) >= 5
        else max(grouped, key=lambda day: len(grouped[day]))
    )
    rows = grouped[latest]
    return [
        {
            "name": row.get("HOLDER_NAME"),
            "holdings": float(row.get("HOLD_NUM") or 0),
            "ratio": row.get("HOLD_NUM_RATIO"),
            "rank": row.get("HOLDER_RANK"),
            "end_date": latest,
        }
        for row in rows
    ]


def _total_shares(code: str) -> float | None:
    try:
        response = _get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax",
            params={"code": _symbol(code)},
        )
        rows = response.json().get("gbjg") or []
        if rows and rows[0].get("TOTAL_SHARES"):
            return float(rows[0]["TOTAL_SHARES"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _rpta_rows(code: str) -> list[dict]:
    try:
        response = _get(
            "https://datacenter-web.eastmoney.com/api/data/v1/get",
            params={
                "sortColumns": "NOTICE_DATE",
                "sortTypes": "-1",
                "pageSize": "100",
                "pageNumber": "1",
                "reportName": "RPTA_APP_ACCUMDETAILS",
                "columns": "ALL",
                "quoteColumns": "",
                "source": "WEB",
                "client": "WEB",
                "filter": f'(SECURITY_CODE="{code}")',
            },
        )
        return ((response.json().get("result") or {}).get("data")) or []
    except Exception:  # noqa: BLE001
        return []


def _match_holder(name: str, rows: list[dict], aliases: dict) -> dict | None:
    candidates = [name, *aliases.get(name, [])]
    for candidate in candidates:
        target = _norm_name(candidate)
        for row in rows:
            if _norm_name(row.get("name")) == target:
                return row
    for candidate in candidates:
        target = _norm_name(candidate)
        for row in rows:
            other = _norm_name(row.get("name"))
            if target and (target in other or other in target):
                return row
    return None


def _rpta_pledge(
    code: str, names: list[str], total_shares: float | None, aliases: dict
) -> dict | None:
    rows = _rpta_rows(code)
    if not rows:
        return None
    holder_rows = [{"name": row.get("HOLDER_NAME"), "row": row} for row in rows]
    selected = _match_holder(names[0], holder_rows, aliases)
    if selected is None:
        return None
    selected_row = selected["row"]
    selected_name = selected["name"]
    outstanding = [
        row for row in rows if _norm_name(row.get("HOLDER_NAME")) == _norm_name(selected_name)
    ]
    accum = selected_row.get("ACCUM_PLEDGE_TSR")
    holdings = float(selected_row.get("HOLD_NUM") or 0) * 10000
    if accum in (None, ""):
        if any(row.get("UNFREEZE_STATE") == "未解押" for row in outstanding):
            return None
        pledged = 0.0
    else:
        if total_shares is None:
            return None
        pledged = float(accum) / 100 * total_shares
    if holdings <= 0:
        return None
    return {
        "pledged_shares": pledged,
        "holdings": holdings,
        "pledge_ratio": pledged / holdings,
        "source_id": "eastmoney.pledge.detail",
        "source_url": "https://data.eastmoney.com/gpzy/pledgeDetail.aspx",
        "note": f"东方财富重要股东质押明细：累计质押 {accum}% 总股本",
    }


def _is_nominee(name: str) -> bool:
    text = _norm_name(name)
    return any(
        pattern in text
        for pattern in ("香港中央结算", "HKSCCNOMINEES", "香港中央结算(代理人)")
    )


def _largest_non_nominee(rows: list[dict]) -> dict | None:
    for row in sorted(rows, key=lambda item: item["holdings"], reverse=True):
        if row.get("name") and not _is_nominee(row["name"]):
            return row
    return None


def _pledge_row(
    code: str,
    *,
    name: str,
    rows: list[dict],
    region: str | None,
    aliases: dict,
    total_shares: float | None,
    report_url: str | None = None,
) -> dict:
    row = _match_holder(name, rows, aliases)
    if row is not None:
        parsed = _parse_pledge_row(region, row["holdings"])
        if parsed["status"] not in ("none", "pledged"):
            parsed = _parse_pledge_by_name(
                region, row["name"], [item.get("name") for item in rows if item.get("name")]
            )
        if parsed["status"] in ("none", "pledged"):
            return {
                "subject": row["name"],
                "holdings": row["holdings"],
                "pledged_shares": parsed["pledged_shares"],
                "pledge_ratio": parsed["pledged_shares"] / row["holdings"]
                if row["holdings"]
                else None,
                "status": parsed["status"],
                "source_id": "eastmoney.announcement.pdf",
                "source_url": report_url,
                "method": "定期报告前十名股东表",
            }
    fallback = _rpta_pledge(code, [name], total_shares, aliases)
    if fallback:
        if row and row.get("holdings"):
            fallback["holdings"] = row["holdings"]
            fallback["pledge_ratio"] = (
                fallback["pledged_shares"] / row["holdings"] if row["holdings"] else None
            )
        return {
            "subject": row["name"] if row else name,
            "status": "pledged" if fallback["pledge_ratio"] else "none",
            **fallback,
            "method": "东方财富重要股东质押明细（补足）",
        }
    return {
        "subject": row["name"] if row else name,
        "holdings": row["holdings"] if row else None,
        "pledged_shares": None,
        "pledge_ratio": None,
        "status": "unknown",
        "source_id": "mixed",
        "method": "定期报告与质押明细均未取得",
    }


def pledge_ratio(code: str, controllers: list[str], company_ratio: float | None) -> dict:
    """返回质押结果。无控股股东时按 K2 第一大股东（含一致行动人合计）口径。"""
    review = _review()
    aliases = (review.get("pledge_aliases") or {}).get(code, {})
    no_controller_override = review.get("pledge_no_controller") or {}
    concert_parties = review.get("pledge_concert_parties") or {}
    concert_basis = review.get("pledge_concert_basis") or {}
    notes = review.get("pledge_notes") or {}
    source_notes = review.get("pledge_source_notes") or {}

    override = no_controller_override.get(code)
    if override and not controllers:
        controllers = []
    no_controller = not controllers or override is not None

    if not no_controller and not company_ratio:
        return {
            "available": True,
            "pledge_ratio": 0.0,
            "pledged_shares": 0.0,
            "holdings": None,
            "report_date": None,
            "subject": "；".join(controllers),
            "subject_role": "控股股东",
            "source_id": "eastmoney.pledge.company_level",
            "source_url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "公司整体质押比例为 0（未出现在质押比例表），控股股东质押必为 0",
            "fetched_at": now(),
        }
    if no_controller and not company_ratio:
        return {
            "available": True,
            "pledge_ratio": 0.0,
            "pledged_shares": 0.0,
            "holdings": None,
            "report_date": None,
            "subject": override.get("reason") if override else "无控股股东",
            "subject_role": "第一大股东（无控股股东）" if override else "无控股股东",
            "source_id": "eastmoney.pledge.company_level",
            "note": "无控股股东且公司整体质押为 0",
            "fetched_at": now(),
        }

    report, region, _text = _latest_report(code)
    rows = _sdgd_rows(code)
    if report is None or not rows:
        if rows and controllers and not no_controller:
            total_shares = _total_shares(code)
            fallback_matches = []
            for controller in controllers:
                fallback = _rpta_pledge(code, [controller], total_shares, aliases)
                if fallback:
                    fallback_matches.append({"subject": controller, **fallback})
            if fallback_matches:
                worst = max(fallback_matches, key=lambda item: item["pledge_ratio"] or 0.0)
                return {
                    "available": True,
                    "pledge_ratio": worst["pledge_ratio"],
                    "pledged_shares": worst["pledged_shares"],
                    "holdings": worst["holdings"],
                    "subject": "；".join(item["subject"] for item in fallback_matches),
                    "subject_role": "控股股东",
                    "detail": "；".join(
                        f"{item['subject']} {item['holdings']:,.0f} 股，质押 {item['pledged_shares']:,.0f} 股（{item['pledge_ratio']:.2%}）"
                        for item in fallback_matches
                    ),
                    "report_date": None,
                    "source_id": "eastmoney.pledge.detail",
                    "source_url": "https://data.eastmoney.com/gpzy/pledgeDetail.aspx",
                    "note": notes.get(code) or "定期报告 PDF 为扫描件；改用东方财富重要股东质押明细",
                    "fetched_at": now(),
                }
        reason = "最新定期报告股东表未取得（PDF 接口或解析失败）"
        if source_notes.get(code):
            reason = source_notes[code]
        return {
            "available": False,
            "error": "no_periodic_pledge_table",
            "subject": "；".join(controllers) if controllers else None,
            "subject_role": "控股股东" if controllers else "第一大股东（无控股股东）",
            "note": reason,
            "source_id": "eastmoney.announcement.pdf",
            "fetched_at": now(),
        }
    total_shares = _total_shares(code)
    report_date = report.get("period_end") or str(report.get("notice_date") or "")[:10]

    if no_controller:
        subject_row = _largest_non_nominee(rows)
        if subject_row is None:
            return {
                "available": False,
                "error": "no_largest_shareholder",
                "note": "前十名股东中未找到非名义持有人",
                "source_id": "eastmoney.announcement.pdf",
                "fetched_at": now(),
            }
        party_names = list(concert_parties.get(code) or [])
        if not party_names and override:
            party_names = list(override.get("concert_parties") or [])
        subject = _pledge_row(
            code,
            name=subject_row["name"],
            rows=rows,
            region=region,
            aliases=aliases,
            total_shares=total_shares,
            report_url=report.get("pdf_url"),
        )
        if subject["status"] == "unknown":
            return {
                "available": False,
                "error": "largest_shareholder_pledge_unknown",
                "subject": subject_row["name"],
                "subject_role": "第一大股东（无控股股东）",
                "note": notes.get(code) or "第一大股东质押状态未知",
                "source_id": subject.get("source_id"),
                "fetched_at": now(),
            }
        holdings = subject["holdings"] or subject_row["holdings"]
        pledged = subject["pledged_shares"] or 0.0
        total_holdings = holdings
        total_pledged = pledged
        party_details = []
        for party in party_names:
            party_row = _pledge_row(
                code,
                name=party,
                rows=rows,
                region=region,
                aliases=aliases,
                total_shares=total_shares,
                report_url=report.get("pdf_url"),
            )
            if party_row["status"] == "unknown" or party_row["holdings"] is None:
                continue
            total_holdings += party_row["holdings"]
            total_pledged += party_row["pledged_shares"] or 0.0
            party_details.append(
                f"{party_row['subject']} {party_row['holdings']:,.0f} 股，质押 {party_row['pledged_shares']:,.0f} 股"
            )
        detail = (
            f"{subject_row['name']}（第一大股东）{holdings:,.0f} 股，质押 {pledged:,.0f} 股；"
            + ("；".join(party_details) + "；" if party_details else "")
            + "合计口径。"
            + (concert_basis.get(code) or "")
        )
        return {
            "available": True,
            "pledge_ratio": total_pledged / total_holdings if total_holdings else None,
            "pledged_shares": total_pledged,
            "holdings": total_holdings,
            "subject": subject_row["name"],
            "subject_role": "第一大股东（无控股股东）",
            "detail": detail,
            "concert_parties": party_names,
            "report_date": report_date,
            "source_id": "eastmoney.announcement.pdf",
            "source_url": report.get("pdf_url"),
            "note": notes.get(code) or "无控股股东，按第一大股东（含一致行动人合计）口径",
            "fetched_at": now(),
        }

    matches = []
    unknown = []
    for controller in controllers:
        row = _pledge_row(
            code,
            name=controller,
            rows=rows,
            region=region,
            aliases=aliases,
            total_shares=total_shares,
            report_url=report.get("pdf_url"),
        )
        if row["status"] == "unknown":
            unknown.append(row)
        else:
            matches.append(row)
    if not matches:
        return {
            "available": False,
            "error": "controller_pledge_unknown",
            "subject": "；".join(controllers),
            "subject_role": "控股股东",
            "note": notes.get(code) or "控股股东质押状态未知",
            "attempts": [item.get("method") for item in unknown],
            "source_id": unknown[0].get("source_id") if unknown else "mixed",
            "fetched_at": now(),
        }
    worst = max(matches, key=lambda item: item["pledge_ratio"] or 0.0)
    detail = "；".join(
        f"{item['subject']} {item['holdings']:,.0f} 股，质押 {item['pledged_shares']:,.0f} 股（{item['pledge_ratio']:.2%}）"
        for item in matches
    )
    return {
        "available": True,
        "pledge_ratio": worst["pledge_ratio"],
        "pledged_shares": worst["pledged_shares"],
        "holdings": worst["holdings"],
        "subject": "；".join(item["subject"] for item in matches),
        "subject_role": "控股股东",
        "detail": detail + ("（多个控股股东取最高比例）" if len(matches) > 1 else ""),
        "report_date": report_date,
        "source_id": worst.get("source_id"),
        "source_url": worst.get("source_url") or report.get("pdf_url"),
        "note": notes.get(code) or (review.get("pledge_notes") or {}).get(code) or "控股股东质押股数 ÷ 其持股数",
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
    controller = result.get("controller") or {}
    controllers = controller.get("names") or []
    if not controllers and controller.get("name"):
        controllers = [controller["name"]]
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


def _company_ratio_map() -> dict[str, float]:
    return {
        row["code"]: float(row["pledge_ratio"])
        for row in read_table(SNAPSHOT_4D / "pledge.csv.gz")
        if row.get("pledge_ratio")
    }


def _controllers_of(data: dict) -> list[str]:
    controller = data.get("controller") or {}
    names = controller.get("names") or []
    if not names and controller.get("name"):
        names = [controller["name"]]
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refetch-pledge", action="store_true")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    codes = candidates()
    if args.limit:
        codes = codes[: args.limit]
    company_ratios = _company_ratio_map()

    if args.refetch_pledge:
        targets = []
        for item in codes:
            path = CACHE / f"{item['code']}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if args.force or not (data.get("pledge") or {}).get("available"):
                targets.append(item)
        print(f"待补质押 {len(targets)} 家", flush=True)

        def work(item: dict) -> dict:
            path = CACHE / f"{item['code']}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["pledge"] = pledge_ratio(
                item["code"], _controllers_of(data), company_ratios.get(item["code"])
            )
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return data

        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(work, item): item for item in targets}
            for future in as_completed(futures):
                item = futures[future]
                done += 1
                try:
                    data = future.result()
                    pledge = data.get("pledge") or {}
                    print(
                        f"[{done}/{len(targets)}] {item['code']} {item['name']} "
                        f"pledge={pledge.get('available')} ratio={pledge.get('pledge_ratio')} "
                        f"source={pledge.get('source_id')}",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[{done}/{len(targets)}] {item['code']} FAILED {exc}", flush=True)
        print("补数完成", flush=True)
        return 0

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
        result = fetch_company(
            item["code"], item["name"], item["batch4d"], company_ratios.get(item["code"])
        )
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
                print(
                    f"[{done}/{len(pending)}] {item['code']} {item['name']} "
                    f"audit={audit} pledge={pledge} penalties={penalties}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[{done}/{len(pending)}] {item['code']} FAILED {exc}", flush=True)
    print("采集完成", flush=True)
    return 0


AUDIT_COLUMNS = ["code", "fiscal_year", "opinion_type", "source_id", "source_url", "fetched_at"]
PENALTY_COLUMNS = [
    "code", "event_date", "event_type", "title", "subject", "subject_role", "subject_basis",
    "reason", "doc_no", "url", "info_disclosure", "regulator", "counted", "subject_unknown",
    "source_id", "fetched_at",
]
PLEDGE_COLUMNS = [
    "code", "subject", "subject_role", "holdings", "pledged_shares", "pledge_ratio",
    "concert_parties", "detail", "report_date", "source_id", "source_url", "note", "fetched_at",
]
COVERAGE_COLUMNS = [
    "code", "audit_available", "penalty_available", "pledge_available",
    "penalty_subject_unknown", "audit_years", "notes",
]


def build_tables() -> list[dict]:
    audit_rows = []
    penalty_rows = []
    pledge_rows = []
    coverage_rows = []
    seen_penalty: set[tuple[str, str, str]] = set()
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
        classified = classify_penalty_events(code, data["name"], penalties.get("events") or [])
        for event in classified:
            key = (code, str(event.get("title")), str(event.get("doc_no") or ""))
            if key in seen_penalty:
                continue
            seen_penalty.add(key)
            penalty_rows.append(
                {
                    "code": code,
                    "event_date": event.get("date"),
                    "event_type": event.get("type"),
                    "title": event.get("title"),
                    "subject": event.get("subject"),
                    "subject_role": event.get("subject_role"),
                    "subject_basis": event.get("subject_basis"),
                    "reason": event.get("reason"),
                    "doc_no": event.get("doc_no"),
                    "url": event.get("url"),
                    "info_disclosure": event.get("info_disclosure"),
                    "regulator": event.get("regulator"),
                    "counted": event.get("counted"),
                    "subject_unknown": event.get("subject_unknown"),
                    "source_id": penalties.get("source_id"),
                    "fetched_at": penalties.get("fetched_at"),
                }
            )
        pledge = data.get("pledge") or {}
        controllers = _controllers_of(data)
        if controllers:
            fallback_role = "控股股东"
            fallback_subject = "；".join(controllers)
        elif pledge.get("available") and not pledge.get("pledge_ratio"):
            fallback_role = "无控股股东"
            fallback_subject = "无控股股东（公司整体质押 0）"
        else:
            fallback_role = "第一大股东（无控股股东）"
            fallback_subject = None
        pledge_rows.append(
            {
                "code": code,
                "subject": pledge.get("subject") or fallback_subject,
                "subject_role": pledge.get("subject_role") or fallback_role,
                "holdings": pledge.get("holdings"),
                "pledged_shares": pledge.get("pledged_shares"),
                "pledge_ratio": pledge.get("pledge_ratio"),
                "concert_parties": "；".join(pledge.get("concert_parties") or []) or None,
                "detail": pledge.get("detail") or pledge.get("note"),
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
                "penalty_subject_unknown": any(
                    bool(event.get("subject_unknown")) for event in classified
                ),
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
                  "publisher": "东方财富", "interface": "公告检索（立案/行政处罚/公开谴责/纪律处分）+ 正文核对",
                  "url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
                  "definition_note": "2021-11-12 至 2026-11-12；只计公司/控股股东/实控人及一致行动人/时任董监高的信息披露类事项；其他主体只记录；主体不明记待定。"},
    ))
    metas.append(write_table(
        TABLE_DIR / "governance-pledge.csv.gz", pledge_rows, PLEDGE_COLUMNS,
        metadata={"kind": "governance_pledge", "source_id": "mixed",
                  "publisher": "东方财富 / 国网 F10 / 定期报告 PDF",
                  "interface": "公司整体质押表 + 最新定期报告前十名股东表 + 重要股东质押明细",
                  "url": "https://data.eastmoney.com/gpzy/",
                  "definition_note": "控股股东质押股数 ÷ 其持股数；无控股股东时第一大股东（含一致行动人合计）；名义持有人排除；取不到记待定。"},
    ))
    metas.append(write_table(
        TABLE_DIR / "governance-coverage.csv.gz", coverage_rows, COVERAGE_COLUMNS,
        metadata={"kind": "governance_coverage", "source_id": "mixed",
                  "publisher": "本项目", "interface": "逐家采集结果汇总",
                  "url": "", "definition_note": "252 家候选的治理三项可得性与处罚主体待定标记。"},
    ))
    return metas


def build_snapshot() -> dict:
    expected = {item["code"] for item in candidates()}
    cached = {path.stem for path in CACHE.glob("*.json")}
    missing = sorted(expected - cached)
    if missing:
        raise RuntimeError(f"还有 {len(missing)} 家未采集：{missing[:10]}")
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
             "interface": "公告检索（立案/行政处罚/公开谴责/纪律处分）+ 正文核对", "url": "https://np-anotice-stock.eastmoney.com/api/security/ann",
             "note": "最近 5 年；逐条判定主体身份与信息披露类事项。"},
            {"id": "gw.f10.controller", "publisher": "国网 F10",
             "interface": "个股控股股东", "url": "http://webf10.gw.com.cn/",
             "note": "只用于控股股东名称；名称过期时按年报/第一大股东口径补正。"},
            {"id": "eastmoney.pledge.detail", "publisher": "东方财富",
             "interface": "重要股东股权质押明细", "url": "https://data.eastmoney.com/gpzy/pledgeDetail.aspx",
             "note": "定期报告股东表取不到时补足；累计质押比例 × 总股本 ÷ 持股数。"},
        ],
        untested_conditions=[],
        build_stats={"candidates": 252, "governance": True, "policy": "quality-pool-v1.2-draft"},
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
