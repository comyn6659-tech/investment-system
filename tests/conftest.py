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
