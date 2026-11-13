"""明显周期企业的行业线索清单（版本化）。

背景：已批准政策 equity-policy-v1.1 把「强周期企业」列为路线不支持、不给正式结论。
原实现把行业线索硬编码在 `engine.evidence.applicability.SELECTION_POLICY`；本模块把
线索改为版本化清单 `data/policies/cyclical-industries-v1.json`（依据申万行业分类
2021 版），只做名称匹配，不改变任何阈值。

判定仍保持原规则：行业线索成立只是必要条件，还须收入波动或收入回撤超过既有阈值
（`SELECTION_POLICY.revenue_volatility_cyclical` / `revenue_drawdown_cyclical`）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LIST_PATH = ROOT / "data" / "policies" / "cyclical-industries-v1.json"


class CyclicalListError(RuntimeError):
    """清单文件缺失或结构不合法。"""


def _normalize_name(value: str) -> str:
    text = str(value or "").strip()
    for suffix in ("Ⅲ", "Ⅱ"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    node = Path(path)
    if not node.exists():
        raise CyclicalListError(f"周期行业清单不存在：{node}")
    payload = json.loads(node.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "cyclical-industry-list/1":
        raise CyclicalListError(f"周期行业清单 schema 不合法：{payload.get('schema_version')!r}")
    if not payload.get("groups"):
        raise CyclicalListError("周期行业清单缺少 groups")
    return payload


def load_list(path: Path | None = None) -> dict[str, Any]:
    """读取并校验清单；返回原始对象（只读使用）。"""
    return _load(str(Path(path or DEFAULT_LIST_PATH)))


def list_summary(path: Path | None = None) -> dict[str, Any]:
    """报告用的清单摘要：版本、依据、条目数。"""
    payload = load_list(path)
    exact = []
    legacy = []
    for group in payload["groups"]:
        for name in group.get("listed_names") or []:
            exact.append({"name": name, "group": group["group"], "level1": group.get("shenwan_level1")})
        for clue in group.get("legacy_clues") or []:
            legacy.append({"clue": clue, "group": group["group"]})
    return {
        "list_id": payload["list_id"],
        "version": payload["version"],
        "status": payload["status"],
        "effective_date": payload["effective_date"],
        "basis": payload["basis"],
        "matching_rule": payload["matching_rule"],
        "groups": [
            {
                "group": group["group"],
                "level1": group.get("shenwan_level1"),
                "listed_names": list(group.get("listed_names") or []),
                "legacy_clues": list(group.get("legacy_clues") or []),
                "basis": group.get("basis"),
            }
            for group in payload["groups"]
        ],
        "exact_names": exact,
        "legacy_clues": legacy,
        "notes": list(payload.get("notes") or []),
    }


def match_industry(industry_clue: str | None, *, path: Path | None = None) -> dict[str, Any] | None:
    """判断行业线索是否命中清单；命中返回清单条目，未命中返回 None。

    先按「/」拆分做精确名称比较（允许末尾Ⅱ/Ⅲ），再用旧关键词做包含匹配。
    这样「化学制药」不会因为包含「化学」被误判，而「化学原料」可以精确命中。
    """
    text = str(industry_clue or "").strip()
    if not text:
        return None
    payload = load_list(path)
    exact_index: dict[str, tuple[str, str]] = {}
    for group in payload["groups"]:
        for name in group.get("listed_names") or []:
            exact_index[_normalize_name(name)] = (group["group"], name)
    for segment in text.replace("、", "/").split("/"):
        normalized = _normalize_name(segment)
        if normalized in exact_index:
            group, matched = exact_index[normalized]
            return {
                "list_id": payload["list_id"],
                "version": payload["version"],
                "group": group,
                "matched_name": matched,
                "matched_by": "exact_name",
                "industry_clue": text,
            }
    for group in payload["groups"]:
        for clue in group.get("legacy_clues") or []:
            if clue and clue in text:
                return {
                    "list_id": payload["list_id"],
                    "version": payload["version"],
                    "group": group["group"],
                    "matched_name": clue,
                    "matched_by": "legacy_keyword",
                    "industry_clue": text,
                }
    return None


def is_cyclical_industry(industry_clue: str | None, *, path: Path | None = None) -> bool:
    return match_industry(industry_clue, path=path) is not None


__all__ = [
    "DEFAULT_LIST_PATH",
    "CyclicalListError",
    "is_cyclical_industry",
    "list_summary",
    "load_list",
    "match_industry",
]
