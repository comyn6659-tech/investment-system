"""好公司池（quality-pool-v1-draft）的研究实现。

本包只服务好公司池试运行：范围判定、指标、规则评估与报告素材；
不进入 CLI 的个股结论路径，不改个股政策、指数/ETF 结果或既有账本。
"""

from engine.quality.evaluator import evaluate_company, evaluate_pool
from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    CONDITION_UNTESTED,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    STATUS_PENDING,
    Company,
    CompanyResult,
    ConditionResult,
    FinancialYear,
    PoolRun,
)
from engine.quality.policy import (
    POLICY_ID,
    POLICY_SCHEMA_VERSION,
    QualityPolicy,
    PolicyError,
    load_policy,
)
from engine.quality.snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    SnapshotError,
    build_snapshot_dir,
    load_snapshot_dir,
)

__all__ = [
    "CONDITION_FAIL",
    "CONDITION_PASS",
    "CONDITION_PENDING",
    "CONDITION_UNTESTED",
    "STATUS_IN_POOL",
    "STATUS_NOT_IN_POOL",
    "STATUS_OUT_OF_SCOPE",
    "STATUS_PENDING",
    "Company",
    "CompanyResult",
    "ConditionResult",
    "FinancialYear",
    "POLICY_ID",
    "POLICY_SCHEMA_VERSION",
    "PolicyError",
    "PoolRun",
    "QualityPolicy",
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotError",
    "build_snapshot_dir",
    "evaluate_company",
    "evaluate_pool",
    "load_policy",
    "load_snapshot_dir",
]
