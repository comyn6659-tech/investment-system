"""输入规范化。

只做格式层处理，不做类型推断：不根据「六位数字」或「带字母」猜市场。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_CODE_PATTERN = re.compile(r"^(?:(SH|SZ|BJ))?(\d{6})(?:\.(SH|SZ|BJ|CSI))?$")
_LATIN_OR_DIGIT = re.compile(r"^[0-9A-Za-z\u4e00-\u9fff]+$")


@dataclass(frozen=True)
class NormalizedInput:
    raw: str
    canonical: str  # 去空白、全角转半角、拉丁字母大写
    code: str | None  # 仅当整体形如证券代码
    venue_hint: str | None  # SH / SZ / BJ / CSI
    is_plausible_name: bool
    prefix_venue: str | None = None
    suffix_venue: str | None = None

    @property
    def venue_conflict(self) -> bool:
        """前缀与后缀指向不同交易所（如 SZ601398.SH）。"""
        return bool(
            self.prefix_venue
            and self.suffix_venue
            and self.prefix_venue != self.suffix_venue
        )

    @property
    def is_empty(self) -> bool:
        return not self.canonical

    @property
    def looks_like_code(self) -> bool:
        return self.code is not None


def normalize(text: str | None) -> NormalizedInput:
    raw = "" if text is None else str(text)
    # NFKC：全角数字/字母转半角，避免「６００５１９」被当成不同输入
    folded = unicodedata.normalize("NFKC", raw)
    canonical = re.sub(r"\s+", "", folded).upper()

    code: str | None = None
    venue: str | None = None
    match = _CODE_PATTERN.match(canonical)
    if match:
        prefix, digits, suffix = match.groups()
        code = digits
        venue = prefix or suffix

    # 代码允许带交易所前后缀（如 601398.SH），名称只允许字母/数字/汉字
    plausible = True if code is not None else bool(
        canonical and _LATIN_OR_DIGIT.match(canonical)
    )
    return NormalizedInput(
        raw=raw,
        canonical=canonical,
        code=code,
        venue_hint=venue,
        is_plausible_name=plausible,
        prefix_venue=match.group(1) if match else None,
        suffix_venue=match.group(3) if match else None,
    )
