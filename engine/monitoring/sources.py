"""红线监测的真实取数源（东方财富公开接口），每次直接取数，不读也不写缓存。

只用 Python 标准库（urllib）；接口失败不抛到全表：对应项记入 `GovernanceFacts.failures`，
由扫描记「本周未完成」。所有判定与建池共用 `engine.monitoring.classify`。
"""

from __future__ import annotations

import gzip
import csv
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from engine.monitoring.classify import (
    ROLE_LABELS,
    classify_event,
    extract_doc_no,
    load_review,
    strip_tags,
)
from engine.monitoring.models import GovernanceFacts, PenaltyEvent
from engine.monitoring.redline import audit_years

TZ = ZoneInfo("Asia/Singapore")


class SourceIncomplete(RuntimeError):
    """备用源没有覆盖完整区间：记未完成，不能当成「没有事件」。"""


ROOT = Path(__file__).resolve().parent.parent.parent
POOL_DIRS = {
    "quality": ROOT / "data" / "quality-pool",
    "income": ROOT / "data" / "income-pool",
    "bank": ROOT / "data" / "bank-pool",
}
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ANNOUNCE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
AUDIT_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew"
SINA_MAX_PAGES = 6
SINA_LIST = "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletin.php"
SINA_DETAIL = "https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php"
DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CONTROLLER_URL = "http://webf10.gw.com.cn/{market}/B10/{symbol}_B10.html"
TOTAL_SHARES_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax"


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _symbol(code: str) -> str:
    return ("SH" if code.startswith("6") else "SZ") + code


def _truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "是")


def _norm_name(name: str | None) -> str:
    text = str(name or "").upper().replace(" ", "").replace("\u3000", "")
    return (
        text.replace("（", "(")
        .replace("）", ")")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )


_THROTTLE_LOCK = threading.Lock()
_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 0.18


def _pledge_base_day(
    report_date: str,
    *,
    source_url: str = "",
    snapshot_date: str = "",
    as_of: date | None = None,
) -> date | None:
    """已提交质押表的基准日。

    report_date 是完整日期时直接使用；只有年份时按公告日期推断报告期
    （10 月后公告视为三季报 09-30，4 月前后公告视为上年年报 12-31，其余按半年报 06-30）。
    report_date 缺失时用该池快照的取数日期；仍无则退回扫描日。
    """
    text = str(report_date or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        match = re.search(r"AN(\d{4})(\d{2})(\d{2})", str(source_url or ""))
        if match:
            published = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            if published.month >= 10:
                return date(year, 9, 30)
            if published.month <= 4:
                return date(year, 12, 31)
            return date(year, 6, 30)
        return date(year, 1, 1)
    for fallback in (snapshot_date, as_of.isoformat() if as_of else ""):
        if fallback:
            try:
                return date.fromisoformat(str(fallback)[:10])
            except ValueError:
                continue
    return None


def committed_snapshot_dates() -> dict[str, str]:
    """每个已提交治理表所属池快照的取数日期（as_of）。"""
    output: dict[str, str] = {}
    for pool in POOL_DIRS:
        snapshot = current_snapshot(pool)
        if snapshot is None:
            continue
        manifest_path = snapshot / "manifest.json"
        as_of = ""
        if manifest_path.exists():
            try:
                as_of = str(json.loads(manifest_path.read_text(encoding="utf-8")).get("as_of") or "")
            except (OSError, json.JSONDecodeError):
                as_of = ""
        for row in _read_gzip_rows(snapshot / "governance-pledge.csv.gz"):
            code = str(row.get("code") or "").zfill(6)
            if code:
                output.setdefault(code, as_of)
    return output


def _throttle() -> None:
    with _THROTTLE_LOCK:
        wait = _MIN_INTERVAL - (time.monotonic() - _LAST_REQUEST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST[0] = time.monotonic()


def _get_json(
    url: str,
    params: dict[str, str],
    *,
    timeout: int = 30,
    tries: int = 3,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    last: Exception | None = None
    for attempt in range(tries):
        _throttle()
        try:
            request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
            raise RuntimeError("接口返回不是对象")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "567" in str(exc):
                raise
            if "429" in str(exc):
                time.sleep(4.0 * (attempt + 1))
            else:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"请求失败（重试 {tries} 次）：{url}：{last}")


def _get_text(url: str, *, timeout: int = 45, tries: int = 3, encoding: str = "utf-8") -> str:
    last: Exception | None = None
    for attempt in range(tries):
        _throttle()
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode(encoding, errors="ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"请求失败（重试 {tries} 次）：{url}：{last}")


def _parse_day(value: Any, *, as_of: date | None = None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}", text):
        day = date(int(text), 12, 31)
        return min(day, as_of) if as_of else day
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _read_gzip_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def current_snapshot(pool: str) -> Path | None:
    directory = POOL_DIRS.get(pool)
    if directory is None:
        return None
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current = str(manifest.get("current") or "")
    return directory / current if current else None


def committed_governance() -> tuple[dict[str, dict], dict[str, dict[int, str]]]:
    """已提交快照里的控股股东质押与审计意见（按代码合并，取最新报告期）。"""
    pledge: dict[str, dict] = {}
    audit: dict[str, dict[int, str]] = {}
    for pool in POOL_DIRS:
        snapshot = current_snapshot(pool)
        if snapshot is None:
            continue
        for row in _read_gzip_rows(snapshot / "governance-pledge.csv.gz"):
            code = str(row.get("code") or "").zfill(6)
            if not code:
                continue
            current = pledge.get(code)
            if current is None or str(row.get("report_date") or "") > str(current.get("report_date") or ""):
                pledge[code] = row
        for row in _read_gzip_rows(snapshot / "governance-audit.csv.gz"):
            code = str(row.get("code") or "").zfill(6)
            year = str(row.get("fiscal_year") or "")
            if code and year.isdigit():
                audit.setdefault(code, {})[int(year)] = str(row.get("opinion_type") or "")
    return pledge, audit


def committed_penalties_loader() -> dict[str, dict]:
    """已提交建池快照里的处罚判定，按公告 art_code 索引；同一公告以先读到的为准。"""
    output: dict[str, dict] = {}
    for pool in POOL_DIRS:
        snapshot = current_snapshot(pool)
        if snapshot is None:
            continue
        for row in _read_gzip_rows(snapshot / "governance-penalty.csv.gz"):
            url = str(row.get("url") or "")
            match = re.search(r"(AN\d+)\.html", url)
            keys = [match.group(1)] if match else []
            if row.get("doc_no"):
                keys.append(str(row["doc_no"]))
            keys.append(f"{row.get('code')}|{row.get('event_date')}|{row.get('title')}")
            for key in keys:
                if key:
                    output.setdefault(key, row)
    return output


def current_shares() -> dict[str, float]:
    """当前批量财务快照里的总股本（用于质押比例换算）。"""
    manifest_path = ROOT / "data" / "pool-valuation" / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current = str(manifest.get("current") or "")
    path = ROOT / "data" / "pool-valuation" / current / "shares.csv.gz"
    output: dict[str, float] = {}
    for row in _read_gzip_rows(path):
        code = str(row.get("code") or "").zfill(6)
        try:
            output[code] = float(row.get("total_shares"))
        except (TypeError, ValueError):
            continue
    return output


class EastmoneySource:
    """按公司取三项监测事实；每次直接取公开接口，不读也不写任何缓存。"""

    def __init__(
        self,
        *,
        now: datetime | None = None,
        review: dict[str, Any] | None = None,
        committed: tuple[dict[str, dict], dict[str, dict[int, str]]] | None = None,
        committed_penalty_rows: dict[str, dict] | None = None,
        committed_dates: dict[str, str] | None = None,
        shares: dict[str, float] | None = None,
    ):
        self.clock = _now(now)
        self.as_of = self.clock.date()
        self.review = load_review() if review is None else review
        self.committed_pledge, self.committed_audit = committed or committed_governance()
        self.committed_penalty_rows = (
            committed_penalties_loader() if committed_penalty_rows is None else committed_penalty_rows
        )
        self.committed_dates = committed_snapshot_dates() if committed_dates is None else committed_dates
        self.shares = current_shares() if shares is None else shares
        self._total_shares_cache: dict[str, float | None] = {}

    # --- 三项取数 -------------------------------------------------------
    def fetch(self, code: str, name: str = "") -> GovernanceFacts:
        code = str(code).zfill(6)
        failures: dict[str, str] = {}
        penalties, penalty_source = self._penalties(code, name, failures)
        opinions, audit_sources = self._audit(code, failures)
        (
            pledge_ratio,
            subject,
            pledge_date,
            source,
            detail,
            pledge_live_checked,
            pledge_base_date,
            pledge_new_announcements,
        ) = self._pledge(code, name, failures)
        cutoff = self.as_of.replace(year=self.as_of.year - 5)
        facts = GovernanceFacts(
            code=code,
            name=name,
            penalties=tuple(penalties),
            audit_opinions=opinions,
            pledge_ratio=pledge_ratio,
            pledge_subject=subject,
            pledge_date=pledge_date,
            pledge_source=source,
            pledge_detail=detail,
            pledge_live_checked=pledge_live_checked,
            pledge_base_date=pledge_base_date,
            pledge_new_announcements=pledge_new_announcements,
            failures=failures,
            penalty_source=penalty_source,
            penalty_window=f"{cutoff.isoformat()} 至 {self.as_of.isoformat()}",
            audit_sources=audit_sources,
        )
        return facts

    def _announcements(self, code: str, keyword: str) -> list[dict]:
        payload = _get_json(
            ANNOUNCE_URL,
            {
                "sr": "-1",
                "page_size": "50",
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": code,
                "title": keyword,
            },
        )
        return ((payload.get("data") or {}).get("list")) or []

    def _sina_events(self, code: str, name: str, *, since: str) -> list[PenaltyEvent]:
        """东方财富公告接口被限流时的备用源：新浪公告列表（必须覆盖 since 之后）。"""
        events: dict[str, dict] = {}
        keywords = ("立案", "行政处罚", "公开谴责", "纪律处分")
        covered = False
        for page in range(1, SINA_MAX_PAGES + 1):
            text = _get_text(f"{SINA_LIST}?stockid={code}&Page={page}", encoding="gbk")
            rows = re.findall(
                r"(\d{4}-\d{2}-\d{2})&nbsp;<a[^>]+href='([^']+)'>([^<]+)</a>", text
            )
            if not rows:
                if page == 1:
                    raise SourceIncomplete("新浪公告列表首页解析不到任何行")
                raise SourceIncomplete(f"新浪公告列表第 {page} 页为空，未覆盖到 {since}")
            if min(day for day, _href, _title in rows) < since:
                covered = True
            for day, href, title in rows:
                if day < since:
                    continue
                clean = strip_tags(title)
                if not any(keyword in clean for keyword in keywords):
                    continue
                if "纪律处分" in clean:
                    event_type = "纪律处分"
                elif "公开谴责" in clean:
                    event_type = "公开谴责"
                elif "行政处罚" in clean:
                    event_type = "行政处罚"
                else:
                    event_type = "立案"
                match = re.search(r"id=(\d+)", href)
                art_code = f"sina-{match.group(1)}" if match else href
                events[art_code] = {
                    "date": day,
                    "event_type": event_type,
                    "title": clean,
                    "art_code": art_code,
                    "url": href if href.startswith("http") else "https://vip.stock.finance.sina.com.cn" + href,
                }
            if covered:
                break
        if not covered:
            raise SourceIncomplete(
                f"新浪公告列表翻页上限 {SINA_MAX_PAGES} 页仍未覆盖起始日 {since}"
            )
        output: list[PenaltyEvent] = []
        for event in sorted(events.values(), key=lambda item: item["date"], reverse=True):
            content = ""
            try:
                content = strip_tags(
                    _get_text(event["url"], encoding="gbk", timeout=30)
                )
            except Exception:  # noqa: BLE001 - 正文取不到时记待定
                content = ""
            classified = classify_event(
                code=code,
                company_name=name,
                event_type=event["event_type"],
                title=event["title"],
                date=event["date"],
                url=event["url"],
                content=content,
                art_code=event["art_code"],
                review=self.review,
                committed=self.committed_penalty_rows.get(event["art_code"]),
            )
            output.append(PenaltyEvent.from_dict(classified))
        return output

    def _committed_events(self, code: str, cutoff: date) -> list[PenaltyEvent]:
        """已提交建池快照里的同公司事件；备用源只覆盖 120 天时用来补齐旧区间。"""
        output: list[PenaltyEvent] = []
        seen: set[str] = set()
        for row in self.committed_penalty_rows.values():
            if str(row.get("code") or "").zfill(6) != code:
                continue
            event_day = str(row.get("event_date") or "")[:10]
            if not event_day or event_day < cutoff.isoformat():
                continue
            key = str(row.get("url") or "") or f"{event_day}|{row.get('title')}"
            if key in seen:
                continue
            seen.add(key)
            output.append(
                PenaltyEvent(
                    date=event_day,
                    event_type=str(row.get("event_type") or ""),
                    title=str(row.get("title") or ""),
                    subject=str(row.get("subject") or ""),
                    role=str(row.get("subject_role") or ""),
                    counted=_truth(row.get("counted")),
                    subject_unknown=_truth(row.get("subject_unknown")),
                    info_disclosure=_truth(row.get("info_disclosure")),
                    regulator=_truth(row.get("regulator")),
                    doc_no=str(row.get("doc_no") or ""),
                    url=str(row.get("url") or ""),
                    evidence=str(row.get("reason") or row.get("title") or ""),
                    basis=str(row.get("subject_basis") or "沿用建池快照判定"),
                    decision_source="snapshot",
                )
            )
        return sorted(output, key=lambda item: item.date, reverse=True)

    def _penalties(
        self, code: str, name: str, failures: dict[str, str]
    ) -> tuple[list[PenaltyEvent], str]:
        cutoff = self.as_of.replace(year=self.as_of.year - 5)
        events: dict[str, dict] = {}
        priority = {"立案": 3, "行政处罚": 2, "公开谴责": 1, "纪律处分": 0}
        failures_count = 0
        for keyword in ("立案", "行政处罚", "公开谴责", "纪律处分"):
            try:
                rows = self._announcements(code, keyword)
            except Exception as exc:  # noqa: BLE001
                failures["penalty"] = f"{keyword}: {type(exc).__name__}"
                failures_count += 1
                continue
            for row in rows:
                notice = str(row.get("notice_date") or "")[:10]
                if not notice or notice < cutoff.isoformat():
                    continue
                title = strip_tags(row.get("title"))
                if keyword == "公开谴责" and "公开谴责" not in title:
                    continue
                if keyword == "纪律处分" and "纪律处分" not in title and "公开谴责" not in title:
                    continue
                if keyword == "立案" and "立案" not in title:
                    continue
                if keyword == "行政处罚" and "行政处罚" not in title:
                    continue
                art_code = str(row.get("art_code") or "")
                current = events.get(art_code or title)
                if current is None or priority.get(keyword, 0) > priority.get(current["event_type"], 0):
                    events[art_code or title] = {
                        "date": notice,
                        "event_type": keyword,
                        "title": title,
                        "art_code": art_code,
                        "url": f"https://data.eastmoney.com/notices/detail/{code}/{art_code}.html",
                    }
        if failures_count == 4 and not events:
            # 四个关键词都被限流：改用新浪公告列表（只取最近一段，旧公告由已提交治理表覆盖）
            try:
                fallback = self._sina_events(
                    code, name, since=(self.as_of - timedelta(days=120)).isoformat()
                )
            except Exception as exc:  # noqa: BLE001
                failures["penalty"] = f"东方财富/新浪均失败：{exc}"
                return [], "sina"
            failures.pop("penalty", None)
            merged = {event.url or event.title: event for event in fallback}
            for event in self._committed_events(code, cutoff):
                merged.setdefault(event.url or event.title, event)
            return sorted(merged.values(), key=lambda item: item.date, reverse=True), "sina+snapshot"
        output: list[PenaltyEvent] = []
        for event in sorted(events.values(), key=lambda item: item["date"], reverse=True):
            content = ""
            if event["art_code"]:
                try:
                    content = strip_tags(
                        (_get_json(CONTENT_URL, {"art_code": event["art_code"], "client_source": "web", "page_index": "1"}).get("data") or {}).get("notice_content")
                    )
                except Exception:  # noqa: BLE001 - 正文取不到时记待定
                    content = ""
            classified = classify_event(
                code=code,
                company_name=name,
                event_type=event["event_type"],
                title=event["title"],
                date=event["date"],
                url=event["url"],
                content=content,
                art_code=event["art_code"],
                review=self.review,
                committed=self.committed_penalty_rows.get(event["art_code"]),
            )
            output.append(PenaltyEvent.from_dict(classified))
        return output, "eastmoney"

    def _audit(
        self, code: str, failures: dict[str, str]
    ) -> tuple[dict[int, str], dict[int, str]]:
        years = audit_years(self.as_of)
        committed = self.committed_audit.get(code) or {}
        if all(committed.get(year) for year in years):
            return (
                {year: str(committed[year]) for year in years},
                {year: "governance.snapshot" for year in years},
            )
        dates = ",".join(f"{year}-12-31" for year in years)
        rows: list[dict] = []
        error = ""
        for company_type in ("4", "3"):
            try:
                payload = _get_json(
                    AUDIT_URL,
                    {
                        "companyType": company_type,
                        "reportDateType": "0",
                        "reportType": "1",
                        "dates": dates,
                        "code": _symbol(code),
                    },
                )
                rows = payload.get("data") or []
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                rows = []
            if rows:
                break
        opinions: dict[int, str] = {}
        sources: dict[int, str] = {}
        for row in rows:
            year = str(row.get("REPORT_DATE") or "")[:4]
            if year.isdigit() and row.get("OPINION_TYPE"):
                opinions[int(year)] = str(row["OPINION_TYPE"])
                sources[int(year)] = "eastmoney.f10.audit_opinion"
        # 已提交快照是同一来源的固定数据：网络缺某年时用它补齐，并逐年纪录来源。
        for year, value in committed.items():
            if year not in opinions:
                opinions[year] = value
                sources[year] = "governance.snapshot"
        if not rows and not opinions:
            failures["audit"] = error or "F10 审计意见为空"
        return opinions, sources

    def _pledge(
        self, code: str, name: str, failures: dict[str, str]
    ) -> tuple[float | None, str, str, str, str, bool, str, int]:
        """每周都查明细；基准日后无新公告才沿用已提交比例。

        返回：(比例, 主体, 日期, 来源, 说明, live_checked, 基准日, 基准日后新公告条数)
        """
        committed = self.committed_pledge.get(code) or {}
        subject = str(committed.get("subject") or "")
        committed_ratio = None
        try:
            if committed.get("pledge_ratio") not in (None, ""):
                committed_ratio = float(committed["pledge_ratio"])
        except (TypeError, ValueError):
            committed_ratio = None
        report_date = str(committed.get("report_date") or "")
        base_day = _pledge_base_day(
            report_date,
            source_url=str(committed.get("source_url") or ""),
            snapshot_date=self.committed_dates.get(code, ""),
            as_of=self.as_of,
        )
        base_text = base_day.isoformat() if base_day else (report_date or self.committed_dates.get(code, ""))
        source_id = str(committed.get("source_id") or "governance.snapshot")
        if committed_ratio is not None and not 0 <= committed_ratio <= 1:
            failures["pledge"] = f"已提交质押比例 {committed_ratio:.2%} 超过 0–100%，不采用"
            return None, subject, report_date, source_id, "", False, base_text, 0

        rows, live_checked = self._pledge_rows_checked(code, failures)
        if not live_checked:
            return None, subject, report_date, "eastmoney.pledge.detail", "", False, base_text, 0

        new_rows: list[tuple[date, dict]] = []
        for row in rows:
            day = _parse_day(row.get("NOTICE_DATE"), as_of=self.as_of)
            if day is not None and base_day is not None and day > base_day:
                new_rows.append((day, row))
        new_count = len(new_rows)

        if not new_rows:
            if committed_ratio is None:
                if rows:
                    failures["pledge"] = (
                        f"质押明细均早于最新定期报告（基准日 {base_text}），且无已提交数据"
                    )
                else:
                    failures["pledge"] = "质押明细为空且无已提交数据"
                return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, 0
            return (
                committed_ratio,
                subject,
                report_date,
                source_id,
                f"明细已查，基准日后无新公告（基准日 {base_text}；明细 {len(rows)} 条）",
                True,
                base_text,
                0,
            )

        holder_new = [
            (day, row)
            for day, row in new_rows
            if subject and _norm_name(row.get("HOLDER_NAME")) == _norm_name(subject)
        ]
        if not holder_new:
            if committed_ratio is not None:
                return (
                    committed_ratio,
                    subject,
                    report_date,
                    source_id,
                    f"明细已查，基准日后无控股股东新公告（基准日 {base_text}；新公告 {new_count} 条，均非控股股东）",
                    True,
                    base_text,
                    new_count,
                )
            failures["pledge"] = "基准日后无控股股东公告且无已提交数据"
            return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, new_count

        latest_day, latest = max(holder_new, key=lambda item: item[0])
        accum = latest.get("ACCUM_PLEDGE_TSR")
        holdings = None
        try:
            holdings = float(latest.get("HOLD_NUM")) * 10000.0
        except (TypeError, ValueError):
            holdings = None
        if holdings is None and committed.get("holdings") not in (None, ""):
            try:
                holdings = float(committed["holdings"])
            except (TypeError, ValueError):
                holdings = None
        if holdings is None or holdings <= 0:
            failures["pledge"] = "基准日后新公告的持股数缺失"
            return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, new_count
        if accum in (None, ""):
            if str(latest.get("UNFREEZE_STATE") or "") == "未解押":
                failures["pledge"] = "基准日后新公告缺累计质押比例"
                return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, new_count
            accum = 0.0
        total_shares = self.shares.get(code)
        if total_shares is None:
            total_shares = self._total_shares(code)
        if not total_shares:
            failures["pledge"] = "总股本缺失"
            return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, new_count
        pledged = float(accum) / 100.0 * float(total_shares)
        ratio = pledged / holdings
        if not 0 <= ratio <= 1:
            failures["pledge"] = f"质押明细计算比例 {ratio:.2%} 超过 0–100%，不采用"
            return None, subject, report_date, "eastmoney.pledge.detail", "", True, base_text, new_count
        return (
            ratio,
            subject,
            latest_day.isoformat(),
            "eastmoney.pledge.detail",
            f"基准日后新公告 {new_count} 条；最新累计质押 {accum}% 总股本，持股 {holdings:,.0f} 股",
            True,
            base_text,
            new_count,
        )

    def _pledge_rows_checked(self, code: str, failures: dict[str, str]) -> tuple[list[dict], bool]:
        """本次扫描内重试 3 次；仍失败记未完成，不用旧数据补成通过。"""
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self._pledge_rows(code), True
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
        failures["pledge"] = (
            f"质押明细请求连续失败（重试 3 次）：{type(last).__name__ if last else 'RuntimeError'}: {last}"
        )
        return [], False

    def _pledge_rows(self, code: str) -> list[dict]:
        payload = _get_json(
            DATA_URL,
            {
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
            timeout=20,
            tries=1,
        )
        return ((payload.get("result") or {}).get("data")) or []

    def _total_shares(self, code: str) -> float | None:
        if code in self._total_shares_cache:
            return self._total_shares_cache[code]
        value: float | None = None
        try:
            payload = _get_json(TOTAL_SHARES_URL, {"code": _symbol(code)})
            rows = payload.get("gbjg") or []
            if rows and rows[0].get("TOTAL_SHARES"):
                value = float(rows[0]["TOTAL_SHARES"])
        except Exception:  # noqa: BLE001
            value = None
        self._total_shares_cache[code] = value
        return value


__all__ = [
    "EastmoneySource",
    "SourceIncomplete",
    "committed_governance",
    "committed_penalties_loader",
    "committed_snapshot_dates",
    "current_shares",
    "current_snapshot",
]
