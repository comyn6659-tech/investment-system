"""数值卫生：NaN / Infinity 不得进入正常计算与输出。

设计书 §5.2 明确要求。这里提供单点工具，所有数值字段都必须经过它。
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any


class NumericError(ValueError):
    """数值违反了契约（NaN / Infinity / 类型不符）。"""


def is_finite_number(value: Any) -> bool:
    """True 仅当值是可比较的有限实数（bool 不算数字）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, Decimal)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def require_finite(value: Any, *, field: str) -> float:
    """校验并返回有限浮点数；否则抛出 NumericError。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise NumericError(f"{field} 必须是数值，收到 {type(value).__name__}: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise NumericError(f"{field} 不能是 NaN/Infinity: {value!r}")
    return float(value)


def round_half_up(value: float, digits: int) -> float:
    """确定性四舍五入（避免 Python banker's rounding 影响重放一致）。"""
    if not math.isfinite(value):
        raise NumericError(f"无法对非有限值取整: {value!r}")
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(value)).quantize(quant))


def sanitize_for_json(value: Any) -> Any:
    """把结构里的 NaN/Inf 显式抛出，而不是让 json.dumps 写成 NaN 字面量。"""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NumericError(f"输出中出现非有限数值: {value!r}")
        return value
    if isinstance(value, dict):
        return {k: sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_for_json(v) for v in value]
    return value
