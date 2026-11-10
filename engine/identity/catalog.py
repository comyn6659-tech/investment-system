"""身份注册表：从 data/registry/instruments.json 载入已核实标的。

注册表是「有来源」的唯一权威入口。任何进入宽基指数/ETF 流程的标的，
都必须在这里有条目与证据；运行时不做网络查询，也不读旧项目。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.contracts.enums import AssetClass, EtfStrategy, IndexClass, Region, ScopeStatus
from engine.identity.models import EvidenceRef, InstrumentRef, ScopeDecision


_VENUE_SUFFIX = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}


class RegistryError(RuntimeError):
    """注册表缺失或格式错误。"""


class IdentityCatalog:
    """已核实标的集合，按多种键索引。"""

    def __init__(self, instruments: list[InstrumentRef]) -> None:
        self.instruments: list[InstrumentRef] = []
        self.by_id: dict[str, InstrumentRef] = {}
        self._by_name: dict[str, list[InstrumentRef]] = {}
        self._by_code: dict[str, list[InstrumentRef]] = {}
        for inst in instruments:
            self._add(inst)

    def _add(self, inst: InstrumentRef) -> None:
        if inst.instrument_id in self.by_id:
            return
        self.instruments.append(inst)
        self.by_id[inst.instrument_id] = inst
        for key in {inst.display_name, *inst.aliases}:
            if key:
                self._by_name.setdefault(key.upper(), []).append(inst)
        if inst.code:
            self._by_code.setdefault(inst.code, []).append(inst)

    def merge_equity_list(self, path: Path) -> int:
        """合并交易所A股清单（代码、名称、板块、上市日期）。

        这些条目只能作为「个股」进入个股流程，不会被当成指数或 ETF；
        它们不参与宽基指数/ETF 流程，也不改变任何投资政策。
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != "equity-list/1":
            raise RegistryError(f"未知的A股清单 schema: {data.get('schema_version')!r}")
        source = data["source"]
        columns = data["columns"]
        evidence = EvidenceRef(
            evidence_id=source["evidence_id"],
            kind=source.get("kind", "exchange_list"),
            publisher=source["publisher"],
            url=source.get("url"),
            retrieved_at=source["retrieved_at"],
            fields_cited=("证券代码", "证券简称", "板块", "上市日期"),
            note=source.get("note", ""),
        )
        idx = {name: i for i, name in enumerate(columns)}
        added = 0
        for row in data["rows"]:
            code = str(row[idx["code"]])
            venue = str(row[idx["venue"]])
            instrument_id = f"EQUITY:{code}.{_VENUE_SUFFIX.get(venue, venue)}"
            if instrument_id in self.by_id:
                continue
            self._add(
                InstrumentRef(
                    instrument_id=instrument_id,
                    display_name=str(row[idx["name"]]),
                    asset_class=AssetClass.EQUITY,
                    code=code,
                    market="CN",
                    venue=venue,
                    venue_type="exchange",
                    currency="CNY",
                    scope=ScopeDecision(
                        status=ScopeStatus.IN_SCOPE,
                        reason_code="DOMESTIC_A_SHARE",
                        detail="境内A股个股（交易所清单）",
                    ),
                    data_coverage_verified=False,
                    evidence=(evidence,),
                    valid_from=_row_value(row, idx, "listing_date"),
                )
            )
            added += 1
        return added

    # --- 查询 -----------------------------------------------------------
    def get(self, instrument_id: str) -> InstrumentRef | None:
        return self.by_id.get(instrument_id)

    def by_name(self, name: str) -> list[InstrumentRef]:
        return list(self._by_name.get(name.upper(), []))

    def by_code(self, code: str) -> list[InstrumentRef]:
        return list(self._by_code.get(code, []))

    def name_contains(self, fragment: str, *, limit: int = 20) -> list[InstrumentRef]:
        frag = fragment.upper()
        hits = [i for i in self.instruments if frag and frag in i.display_name.upper()]
        hits.sort(key=lambda i: (i.asset_class.value, i.instrument_id))
        return hits[:limit]

    def underlying(self, etf: InstrumentRef) -> InstrumentRef | None:
        if not etf.underlying_index_id:
            return None
        return self.by_id.get(etf.underlying_index_id)

    # --- 载入 -----------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "IdentityCatalog":
        if not Path(path).exists():
            raise RegistryError(f"身份注册表不存在: {path}")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != "identity-registry/1":
            raise RegistryError(f"未知的注册表 schema: {data.get('schema_version')!r}")
        instruments = [_parse_instrument(entry) for entry in data["instruments"]]
        return cls(instruments)


def _parse_instrument(entry: dict[str, Any]) -> InstrumentRef:
    scope_entry = entry["scope"]
    scope = ScopeDecision(
        status=ScopeStatus(scope_entry["status"]),
        reason_code=scope_entry["reason_code"],
        detail=scope_entry.get("detail", ""),
    )
    evidence = tuple(
        EvidenceRef(
            evidence_id=e["evidence_id"],
            kind=e["kind"],
            publisher=e["publisher"],
            url=e.get("url"),
            retrieved_at=e["retrieved_at"],
            fields_cited=tuple(e.get("fields_cited", ())),
            note=e.get("note", ""),
        )
        for e in entry.get("evidence", [])
    )
    return InstrumentRef(
        instrument_id=entry["instrument_id"],
        display_name=entry["display_name"],
        asset_class=AssetClass(entry["asset_class"]),
        code=entry["code"],
        market=entry.get("market", "CN"),
        venue=entry["venue"],
        venue_type=entry.get("venue_type", "exchange"),
        currency=entry.get("currency"),
        aliases=tuple(entry.get("aliases", ())),
        vendor_codes=tuple(entry.get("vendor_codes", ())),
        index_class=IndexClass(entry["index_class"]) if entry.get("index_class") else None,
        etf_strategy=EtfStrategy(entry["etf_strategy"]) if entry.get("etf_strategy") else None,
        region=Region(entry.get("region", "domestic")),
        underlying_index_id=entry.get("underlying_index_id"),
        scope=scope,
        in_first_batch=bool(entry.get("in_first_batch", False)),
        data_coverage_verified=bool(entry.get("data_coverage_verified", False)),
        evidence=evidence,
        valid_from=entry.get("valid_from"),
        valid_to=entry.get("valid_to"),
    )


def _row_value(row: list, idx: dict[str, int], name: str):
    """按列名安全取值（缺失列返回 None）。"""
    position = idx.get(name)
    if position is None or position >= len(row):
        return None
    value = row[position]
    return str(value) if value is not None else None
