#!/usr/bin/env python
"""账本引用快照校验：账本引用的池快照与单标的快照必须存在、内容哈希必须对得上。

用法：
    .venv/bin/python scripts/verify-ledger-snapshots.py

校验规则：
- `qps-*`：池快照目录（data/<池>/snapshot-qps-*/），逐表核对 content_hash 与行数；
- `snap-*`：单标的快照 JSON（data/snapshots/ 或 data/policy-trial/snapshots/），重算内容哈希；
- 形如 `snap-*` 但在上述目录中没有文件的 id：属于批量路径生成的逐公司派生 id
  （例如 `data/pool-valuation/pool-member-valuation.json` 里的 snapshot_id），
  校验它们能否在已保留的批量结果文件中追溯到来源；无法追溯的列出但不删除任何文件；
- `bank-valuation-v2.1:*` 这类政策派生 id 不是文件引用，只登记不校验。

退出码 0 表示：所有持久化快照存在且哈希通过。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.snapshot import Snapshot  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402

DERIVED_REPORT_FILES = (
    ROOT / "data" / "pool-valuation" / "pool-member-valuation.json",
    ROOT / "data" / "pool-valuation" / "cyclical-rerun.json",
    ROOT / "data" / "policy-trial" / "trial-report.json",
)


def _ledger_refs() -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    for path in sorted((ROOT / "data" / "ledger").glob("*.jsonl")):
        found: set[str] = set()

        def walk(node) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "snapshot_id" and isinstance(value, str):
                        found.add(value)
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                walk(json.loads(line))
        refs[str(path.relative_to(ROOT))] = found
    return refs


def _qps_dirs() -> dict[str, Path]:
    output = {}
    for manifest_path in (ROOT / "data").glob("*/snapshot-qps-*/manifest.json"):
        directory = manifest_path.parent
        output[directory.name.removeprefix("snapshot-")] = directory
    return output


def _snap_files() -> dict[str, Path]:
    output = {}
    for base in (ROOT / "data" / "snapshots", ROOT / "data" / "policy-trial" / "snapshots"):
        for path in base.glob("snap-*.json"):
            output[path.stem] = path
    return output


def verify() -> dict:
    """返回校验结果；不打印，供命令行与测试共用。"""
    refs = _ledger_refs()
    qps_dirs = _qps_dirs()
    snap_files = _snap_files()
    derived_text = {
        path.name: path.read_text(encoding="utf-8") for path in DERIVED_REPORT_FILES if path.exists()
    }
    problems: list[str] = []
    checked = Counter()
    derived: list[str] = []
    untraceable: list[str] = []
    for ledger, ids in refs.items():
        for snapshot_id in sorted(ids):
            if snapshot_id.startswith("qps-"):
                directory = qps_dirs.get(snapshot_id)
                if directory is None:
                    problems.append(f"{ledger}: 快照目录不存在 {snapshot_id}")
                    continue
                try:
                    manifest, _companies, _stats = load_snapshot_dir(directory)
                except Exception as exc:  # noqa: BLE001 - 失败就是问题
                    problems.append(f"{ledger}: {snapshot_id} 校验失败：{type(exc).__name__}: {exc}")
                    continue
                if manifest.get("snapshot_id") != snapshot_id:
                    problems.append(f"{ledger}: {snapshot_id} 清单 id 不一致")
                checked["qps"] += 1
            elif snapshot_id.startswith("snap-"):
                path = snap_files.get(snapshot_id)
                if path is not None:
                    try:
                        snapshot = Snapshot.from_dict(
                            json.loads(path.read_text(encoding="utf-8")), expected_id=snapshot_id
                        )
                    except Exception as exc:  # noqa: BLE001
                        problems.append(
                            f"{ledger}: {snapshot_id} 校验失败：{type(exc).__name__}: {exc}"
                        )
                        continue
                    if snapshot.snapshot_id != snapshot_id:
                        problems.append(f"{ledger}: {snapshot_id} 内容哈希不一致")
                    checked["snap"] += 1
                    continue
                derived.append(snapshot_id)
                if not any(snapshot_id in text for text in derived_text.values()):
                    untraceable.append(snapshot_id)
            else:
                checked["policy"] += 1

    return {
        "ledgers": len(refs),
        "qps": checked["qps"],
        "snap": checked["snap"],
        "policy": checked["policy"],
        "derived": sorted(set(derived)),
        "untraceable": sorted(set(untraceable)),
        "problems": problems,
    }


def main() -> int:
    result = verify()
    print(
        f"账本 {result['ledgers']} 个；已校验池快照 {result['qps']} 个、单标的快照 {result['snap']} 个；"
        f"政策派生 id {result['policy']} 个（不是文件引用）"
    )
    print(
        f"批量路径逐公司派生 id {len(result['derived'])} 个（逐公司 JSON 未落盘，属既有设计；"
        f"其中 {len(result['derived']) - len(result['untraceable'])} 个可在已保留的批量结果文件中追溯）"
    )
    if result["untraceable"]:
        print("以下派生 id 只能追溯到账本记录本身（不删除任何文件）：")
        for item in result["untraceable"]:
            print(f"  - {item}")
    if result["problems"]:
        print("校验失败：", file=sys.stderr)
        for item in result["problems"]:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("所有持久化快照（池快照 + 单标的快照）存在且内容哈希校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
