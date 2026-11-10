"""JSON 出口。

与文本出口共用同一个校验器：先校验对象，再校验序列化后的字典，
最后做一次「序列化 → 反序列化 → 再校验」，确保没有任何出口绕过契约。
"""

from __future__ import annotations

import json
from typing import Any

from engine.contracts.result import AnalysisResult
from engine.contracts.validation import enforce
from engine.data.numeric import sanitize_for_json


def to_payload(result: AnalysisResult) -> dict[str, Any]:
    enforce(result)
    data = result.to_dict()
    enforce(data)
    return sanitize_for_json(data)


def export_json(result: AnalysisResult, *, indent: int = 2) -> str:
    data = to_payload(result)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    round_tripped = AnalysisResult.from_dict(json.loads(text))
    enforce(round_tripped)
    return text


def parse_and_validate(text: str) -> AnalysisResult:
    result = AnalysisResult.from_dict(json.loads(text))
    enforce(result)
    return result
