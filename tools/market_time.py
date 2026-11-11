"""行情日期与收盘状态的公共判定。

阶段一已在 build_snapshots.py 内实现；阶段二抽出来共用，避免两套「市场日期」
口径。规则不变：

- 市场日期来自行情序列本身，不从「今天」倒推；
- 指定 --as-of 时按日期选行，找不到就失败，绝不回填相邻日；
- 当日未收盘的行情不得标为收盘价。
"""

from __future__ import annotations

from datetime import date, datetime

MARKET_CLOSE = (15, 0)


class BuildError(RuntimeError):
    """采集结果与预期时点不一致，或数据不足以构建可审计快照。"""


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def bar_status(market_date: date, now: datetime) -> str:
    today = now.date()
    if market_date > today:
        raise BuildError(f"行情日期 {market_date} 晚于当前日期 {today}：数据异常，拒绝构建快照")
    if market_date < today:
        return "closed"
    close_hour, close_minute = MARKET_CLOSE
    if (now.hour, now.minute) >= (close_hour + 1, close_minute):
        return "closed"
    return "intraday"


def assert_expected(market_date: str, expected: str | None, label: str) -> None:
    if expected is not None and market_date != expected:
        raise BuildError(
            f"{label} 实际市场日期为 {market_date}，与预期 {expected} 不一致："
            "不做倒填，请确认 --as-of 或数据源"
        )


def select_bar(df, date_column: str, expected: str | None, label: str):
    """选出要使用的那一根行情，返回 (截断后的 DataFrame, 市场日期, 丢弃行数)。"""
    dates = [as_date(v) for v in df[date_column]]
    if expected is not None:
        targets = [i for i, day in enumerate(dates) if day.isoformat() == expected]
        if not targets:
            raise BuildError(f"{label} 中没有 {expected} 这一行：不做倒填，请确认 --as-of 或数据源")
        index = targets[0]
    else:
        index = len(dates) - 1
    dropped = len(dates) - index - 1
    return df.iloc[: index + 1].reset_index(drop=True), dates[index], dropped


def normalize_date_text(value) -> str:
    text = str(value)[:10] if value is not None else "未知"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text
