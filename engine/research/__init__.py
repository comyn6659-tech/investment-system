"""研究与验证（阶段三实现）。

研究模块不能自行批准上线：任何模型要变成 validated，
必须由设计方根据完整报告决定。这里的常量与函数只用于表达这条边界。
"""

from __future__ import annotations

ALLOWED_MODEL_STATUS = ("planned", "research", "validated", "deprecated")


def may_emit_candidate(model_status: str | None) -> bool:
    """只有 validated 模型才可能输出正式 candidate。"""
    return model_status == "validated"


def research_cannot_publish(model_status: str | None) -> bool:
    return model_status in ("planned", "research")
