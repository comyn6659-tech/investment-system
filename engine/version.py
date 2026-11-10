"""引擎版本与政策版本。

版本号必须随契约或政策变化而递增；结果里记录这些版本以便审计与重放。
"""

from __future__ import annotations

# 代码版本：契约结构或判定逻辑任何语义变化都要升版本
CODE_VERSION = "phase1-0.1.0"

# 政策版本：投资政策（范围、模型适用性、门槛）变化才升版本。
# 阶段一尚未批准任何新的投资门槛，因此这里只标记"未批准"状态。
POLICY_VERSION = "unapproved-phase1"

# 模型版本：阶段一没有任何已验证模型
MODEL_VERSION = "none"

# 结果契约版本
RESULT_SCHEMA_VERSION = "analysis-result/1"

# 快照契约版本
SNAPSHOT_SCHEMA_VERSION = "snapshot/1"

# 身份注册表契约版本
REGISTRY_SCHEMA_VERSION = "identity-registry/1"
