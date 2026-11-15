"""测试夹具：全部离线，只读仓库内的注册表与固定快照。"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.identity.catalog import IdentityCatalog
from engine.pipeline import Analyzer, AnalyzerConfig

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def catalog() -> IdentityCatalog:
    cat = IdentityCatalog.load(ROOT / "data" / "registry" / "instruments.json")
    cat.merge_equity_list(ROOT / "data" / "registry" / "cn_a_equities.json")
    return cat


@pytest.fixture(scope="session")
def analyzer() -> Analyzer:
    return Analyzer(
        AnalyzerConfig(
            registry_path=ROOT / "data" / "registry" / "instruments.json",
            equity_list_path=ROOT / "data" / "registry" / "cn_a_equities.json",
            snapshot_dir=ROOT / "data" / "snapshots",
        )
    )


@pytest.fixture(scope="session")
def snapshot_dir() -> Path:
    return ROOT / "data" / "snapshots"


# --- 第五阶段收尾 D1/D2：测试隔离与日期无关 ---------------------------------
import os

from engine import clock, ledger_paths

BASELINE_TODAY = "2026-11-14"
TEST_ANALYSIS_DATE = "2026-11-14"


@pytest.fixture(scope="session", autouse=True)
def isolated_runtime(tmp_path_factory):
    """全部测试的默认设置：
    - 账本「写入」指向临时目录（读仍用仓库内已提交账本，历史查询行为不变）；
    - 「今天」默认固定为 BASELINE_TODAY；外部可用 INVESTMENT_SYSTEM_TODAY 覆盖
      （用于 2026-11-15 / 2026-12-31 / 2027-06-01 等模拟日期）；
    - 测试前后真实 data/ledger/ 的哈希必须相同（守护测试）。
    """
    ledger_dir = tmp_path_factory.mktemp("ledger-writes")
    old_ledger_dir = os.environ.get(ledger_paths.LEDGER_DIR_ENV)
    old_today = os.environ.get(clock.TODAY_ENV)
    os.environ[ledger_paths.LEDGER_DIR_ENV] = str(ledger_dir)
    if not old_today:
        os.environ[clock.TODAY_ENV] = BASELINE_TODAY
    before = ledger_paths.ledger_hashes()
    try:
        yield ledger_dir
    finally:
        after = ledger_paths.ledger_hashes()
        if old_ledger_dir is None:
            os.environ.pop(ledger_paths.LEDGER_DIR_ENV, None)
        else:
            os.environ[ledger_paths.LEDGER_DIR_ENV] = old_ledger_dir
        if old_today is None:
            os.environ.pop(clock.TODAY_ENV, None)
        else:
            os.environ[clock.TODAY_ENV] = old_today
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    assert not changed, f"测试写入了真实账本（前后哈希不同）：{changed}"
