"""引擎版本与政策版本。

版本号必须随契约或政策变化而递增；结果里记录这些版本以便审计与重放。
"""

from __future__ import annotations

# 代码版本：契约结构或判定逻辑任何语义变化都要升版本
CODE_VERSION = "phase4-0.2.0"

# 政策版本：投资政策（范围、模型适用性、门槛）变化才升版本。
# 阶段一尚未批准任何新的投资门槛，因此这里只标记"未批准"状态。
POLICY_VERSION = "unapproved-phase2"

# 模型版本：个股 3 个 + 指数画像 1 个 + 候选 A 研究信号 1 个，全部 research，均未获正式使用批准
MODEL_VERSION = "research-0.4.0"

# 结果契约版本
RESULT_SCHEMA_VERSION = "analysis-result/1"

# 快照契约版本
SNAPSHOT_SCHEMA_VERSION = "snapshot/1"

# 身份注册表契约版本
REGISTRY_SCHEMA_VERSION = "identity-registry/1"
