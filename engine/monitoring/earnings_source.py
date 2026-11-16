"""财报季批量取数：东方财富按报告期的三张合并报表（分页）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from engine.monitoring.batch import STATEMENT_KINDS
from engine.monitoring.sources import _get_json

TZ = ZoneInfo("Asia/Singapore")
DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
CODE_RANGES = (
    ("SZ", '(SECURITY_CODE<"400000")'),
    ("SH", '(SECURITY_CODE>="600000")(SECURITY_CODE<"700000")'),
)


class EastmoneyEarningsSource:
    def __init__(self, *, now: datetime | None = None, timeout: int = 60):
        self.clock = now or datetime.now(TZ)
        self.timeout = int(timeout)

    def fetch_statement(self, statement: str, period: str) -> list[dict[str, Any]]:
        if statement not in STATEMENT_KINDS:
            raise ValueError(f"未知报表：{statement}")
        _kind, columns, report_name = STATEMENT_KINDS[statement]
        rows: list[dict[str, Any]] = []
        for _venue, code_filter in CODE_RANGES:
            page = 1
            while page <= 30:
                payload = _get_json(
                    DATA_URL,
                    {
                        "reportName": report_name,
                        "columns": ",".join(columns),
                        "pageSize": "500",
                        "pageNumber": str(page),
                        "source": "WEB",
                        "client": "WEB",
                        "filter": f"(REPORT_DATE='{period}'){code_filter}",
                        "sortColumns": "SECURITY_CODE",
                        "sortTypes": "1",
                    },
                    timeout=self.timeout,
                )
                data = (payload.get("result") or {}).get("data") or []
                if not data:
                    break
                rows.extend(data)
                if len(data) < 500:
                    break
                page += 1
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            secucode = str(row.get("SECUCODE") or "")
            if not (secucode.endswith(".SH") or secucode.endswith(".SZ")):
                continue
            code = str(row.get("SECURITY_CODE") or "").zfill(6)
            item = {column: row.get(column) for column in columns}
            item["SECURITY_CODE"] = code
            by_code[code] = item
        return [by_code[code] for code in sorted(by_code)]


__all__ = ["EastmoneyEarningsSource"]
