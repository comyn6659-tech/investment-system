"""规则化标的识别：从中文问题里找出股票 / 指数 / ETF，并给出歧义候选。

不调用大模型。优先级：
1. 6 位代码（股票与指数同码时列候选，不猜）；
2. 常用简称表（data/ask/aliases.json）；
3. 身份注册表里的指数/ETF 全名与别名；
4. 全市场 A 股名称（允许简称包含关系）。

名称包含关系只在唯一命中时使用；多个同长命中时一律列候选，交给用户澄清。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
ALIASES = ROOT / "data" / "ask" / "aliases.json"
CODE_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
OVERSEAS_MARKERS = (
    "苹果",
    "特斯拉",
    "微软",
    "谷歌",
    "亚马逊",
    "英伟达",
    "美股",
    "港股",
    "纳斯达克",
    "纳指",
    "标普",
    "道琼斯",
    "日经",
)
BOARD_MARKERS = ("创业板", "科创板", "北交所")


@dataclass(frozen=True)
class Target:
    kind: str  # stock / index / etf
    code: str
    name: str
    instrument_id: str | None = None
    matched_text: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    ambiguous: bool = False
    scope: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "code": self.code,
            "name": self.name,
            "instrument_id": self.instrument_id,
            "matched_text": self.matched_text,
            "candidates": list(self.candidates),
            "ambiguous": self.ambiguous,
            "scope": self.scope,
        }


@dataclass
class TargetIndex:
    stocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stock_names: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    catalog_entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, batch: Any, catalog: Any) -> "TargetIndex":
        """batch: BatchStore；catalog: IdentityCatalog。"""
        stocks = {
            code: {"code": code, "name": str(row.get("name") or code)}
            for code, row in batch.universe.items()
        }
        names = {code: [row["name"]] for code, row in stocks.items()}
        for code, variants in names.items():
            for variant in list(variants):
                if variant.endswith(("Ａ", "A")) and len(variant) > 1:
                    variants.append(variant[:-1])
        aliases_payload = json.loads(ALIASES.read_text(encoding="utf-8"))
        aliases = {str(key): str(value) for key, value in (aliases_payload.get("aliases") or {}).items()}
        entries = []
        for instrument in catalog.instruments:
            payload = instrument.to_dict()
            scope = payload.get("scope") or {}
            entries.append(
                {
                    "instrument_id": payload["instrument_id"],
                    "code": str(payload["code"]),
                    "name": payload["display_name"],
                    "kind": "index" if payload["asset_class"] == "index" else ("etf" if payload["asset_class"] == "etf" else "stock"),
                    "names": [payload["display_name"], *list(payload.get("aliases") or [])],
                    "scope": scope,
                }
            )
        return cls(stocks=stocks, stock_names=names, aliases=aliases, catalog_entries=entries)

    def match(self, question: str) -> Target | None:
        text = unicodedata.normalize("NFKC", question or "")
        code_match = CODE_PATTERN.search(text)
        if code_match:
            return self._match_code(code_match.group(1))
        candidates = self._match_names(text)
        if not candidates and len(text) <= 4:
            # 很短的输入（例如「平安」）按包含关系找候选，只用于列候选，不直接下结论
            for code, variants in self.stock_names.items():
                for variant in variants:
                    if len(variant) > len(text) and text and text in variant:
                        candidates.append(
                            {
                                "kind": "stock",
                                "code": code,
                                "name": self.stocks[code]["name"],
                                "matched_text": text,
                                "length": len(text),
                                "start": 0,
                            }
                        )
                        break
        if not candidates:
            return None
        # 先按出现位置：句首的标的优先（避免把「值得买」「美的」这类词当成标的名）
        min_start = min(item["start"] for item in candidates)
        top = [item for item in candidates if item["start"] == min_start]
        max_len = max(item["length"] for item in top)
        top = [item for item in top if item["length"] == max_len]
        unique = {(item["kind"], item["code"]): item for item in top}
        if len(unique) > 1:
            return Target(
                kind="ambiguous",
                code="",
                name="、".join(item["name"] for item in unique.values()),
                candidates=tuple(
                    {
                        "kind": item["kind"],
                        "code": item["code"],
                        "name": item["name"],
                        "instrument_id": item.get("instrument_id"),
                    }
                    for item in unique.values()
                ),
                ambiguous=True,
                matched_text=top[0]["matched_text"],
            )
        item = next(iter(unique.values()))
        return Target(
            kind=item["kind"],
            code=item["code"],
            name=item["name"],
            instrument_id=item.get("instrument_id"),
            matched_text=item["matched_text"],
            candidates=(
                {
                    "kind": item["kind"],
                    "code": item["code"],
                    "name": item["name"],
                    "instrument_id": item.get("instrument_id"),
                },
            ),
            scope=item.get("scope"),
        )

    # --- 内部 ---------------------------------------------------------------
    def _match_code(self, code: str) -> Target | None:
        candidates: list[dict[str, Any]] = []
        stock = self.stocks.get(code)
        if stock is not None:
            candidates.append({"kind": "stock", "code": code, "name": stock["name"]})
        for entry in self.catalog_entries:
            if entry["code"] == code:
                candidates.append(
                    {
                        "kind": entry["kind"],
                        "code": code,
                        "name": entry["name"],
                        "instrument_id": entry["instrument_id"],
                    }
                )
        if not candidates:
            return None
        # 同一代码既是批量表个股、又在注册表（交易所清单）里出现时，只保留个股条目
        if any(item["kind"] == "stock" for item in candidates):
            candidates = [item for item in candidates if item["kind"] != "stock" or "instrument_id" not in item]
        unique = {(item["kind"], item["code"], item.get("instrument_id")): item for item in candidates}
        if len(unique) > 1:
            return Target(
                kind="ambiguous",
                code=code,
                name="、".join(item["name"] for item in unique.values()),
                candidates=tuple(unique.values()),
                ambiguous=True,
                matched_text=code,
            )
        item = next(iter(unique.values()))
        return Target(
            kind=item["kind"],
            code=code,
            name=item["name"],
            instrument_id=item.get("instrument_id"),
            matched_text=code,
            candidates=(item,),
            scope=next(
                (entry["scope"] for entry in self.catalog_entries if entry["code"] == code and entry["kind"] == item["kind"]),
                None,
            ),
        )

    def _match_names(self, text: str) -> list[dict[str, Any]]:
        lowered = text.lower()
        found: list[dict[str, Any]] = []
        for alias, code in self.aliases.items():
            if alias and alias in text and code in self.stocks:
                found.append(
                    {
                        "kind": "stock",
                        "code": code,
                        "name": self.stocks[code]["name"],
                        "matched_text": alias,
                        "length": len(alias),
                        "start": text.find(alias),
                    }
                )
        for code, variants in self.stock_names.items():
            for variant in variants:
                if len(variant) >= 2 and variant in text:
                    found.append(
                        {
                            "kind": "stock",
                            "code": code,
                            "name": self.stocks[code]["name"],
                            "matched_text": variant,
                            "length": len(variant),
                            "start": text.find(variant),
                        }
                    )
        for entry in self.catalog_entries:
            for name in entry["names"]:
                needle = str(name or "")
                if len(needle) < 2:
                    continue
                if needle in text or needle.lower() in lowered:
                    found.append(
                        {
                            "kind": entry["kind"],
                            "code": entry["code"],
                            "name": entry["name"],
                            "instrument_id": entry["instrument_id"],
                            "scope": entry.get("scope"),
                            "matched_text": needle,
                            "length": len(needle),
                            "start": text.find(needle) if needle in text else lowered.find(needle.lower()),
                        }
                    )
        # 去重：同一标的保留最长匹配
        best: dict[tuple[str, str], dict[str, Any]] = {}
        for item in found:
            key = (item["kind"], item["code"])
            if key not in best or item["length"] > best[key]["length"]:
                best[key] = item
        return list(best.values())


def overseas(question: str) -> bool:
    return any(marker in (question or "") for marker in OVERSEAS_MARKERS)


def board_only(question: str) -> str | None:
    text = unicodedata.normalize("NFKC", question or "")
    for marker in BOARD_MARKERS:
        if marker in text and not any(
            suffix in text for suffix in ("指", "指数", "50", "100", "200", "综")
        ):
            return marker
    return None


__all__ = ["BOARD_MARKERS", "OVERSEAS_MARKERS", "Target", "TargetIndex", "board_only", "overseas"]
