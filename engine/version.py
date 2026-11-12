"""引擎版本与政策版本。

版本号必须随契约或政策变化而递增；结果里记录这些版本以便审计与重放。
"""

from __future__ import annotations

# 代码版本：契约结构或判定逻辑任何语义变化都要升版本
CODE_VERSION = "phase4-1.0.0"

# 政策版本：投资政策（范围、模型适用性、门槛）变化才升版本。
# 2026-11-12 股东决策②批准 equity-policy-v1.1；个股正式结论使用该标识。
POLICY_VERSION = "equity-policy-v1.1"
RESEARCH_POLICY_VERSION = "unapproved-phase2"

# 模型版本：默认给未批准/研究路径；个股政策正式结果在 pipeline 中改用 VALIDATED_MODEL_VERSION
MODEL_VERSION = "research-0.4.0"
VALIDATED_MODEL_VERSION = "validated-1.0.0"

# 结果契约版本
RESULT_SCHEMA_VERSION = "analysis-result/1"

# 快照契约版本
SNAPSHOT_SCHEMA_VERSION = "snapshot/1"

# 身份注册表契约版本
REGISTRY_SCHEMA_VERSION = "identity-registry/1"
