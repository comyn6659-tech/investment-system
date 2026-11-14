"""指数位置分档（版本化配置，只做文字概括，不参与估值/信号计算）。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "data" / "policies" / "index-position-bands-v1.json"


class BandError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load(path: str) -> dict[str, Any]:
    node = Path(path)
    if not node.exists():
        raise BandError(f"指数位置分档配置不存在：{node}")
    payload = json.loads(node.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "index-position-bands/1":
        raise BandError(f"指数位置分档 schema 非法：{payload.get('schema_version')!r}")
    return payload


def load(path: Path | None = None) -> dict[str, Any]:
    return _load(str(Path(path or DEFAULT_PATH)))


def band_for(percentile: float | None, *, path: Path | None = None) -> dict[str, Any] | None:
    if percentile is None:
        return None
    value = max(0.0, min(100.0, float(percentile)))
    payload = load(path)
    for band in payload["bands"]:
        lower = float(band["lower"])
        upper = float(band["upper"])
        last = band is payload["bands"][-1]
        if value >= lower and (value < upper or (last and value <= upper)):
            return {
                "label": band["label"],
                "summary": band["summary"],
                "lower": lower,
                "upper": upper,
                "percentile": value,
                "band_id": payload["band_id"],
            }
    return None


__all__ = ["BandError", "DEFAULT_PATH", "band_for", "load"]
