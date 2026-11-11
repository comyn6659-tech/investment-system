#!/usr/bin/env python
"""清理快照目录：同一个 (instrument_id, as_of) 只保留抓取时间最新的一版。

重建快照时会生成新版本；旧版本如果与新版代表同一时点，就属于被取代的中间产物，
留在目录里会让「哪一份才是当前有效快照」变得含糊。本脚本按
`(as_of, fetched_at, snapshot_id)` 排序后删除每组里除最后一版以外的文件，并重建 manifest。

用法：
    .venv/bin/python tools/prune_snapshots.py --dry-run
    .venv/bin/python tools/prune_snapshots.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.snapshot import SnapshotStore  # noqa: E402

SNAPSHOT_DIR = ROOT / "data" / "snapshots"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理同标的同 as_of 的旧快照版本")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不删除")
    args = parser.parse_args(argv)

    store = SnapshotStore(SNAPSHOT_DIR)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry in store.load_manifest():
        groups[(entry["instrument_id"], entry["as_of"])].append(entry)

    removed: list[str] = []
    for key, entries in sorted(groups.items()):
        if len(entries) < 2:
            continue
        entries.sort(key=store._version_key)
        keep = entries[-1]
        for entry in entries[:-1]:
            removed.append(f"{entry['snapshot_id']}（{key[0]} {key[1]}）被 {keep['snapshot_id']} 取代")
            if not args.dry_run:
                (SNAPSHOT_DIR / entry["file"]).unlink(missing_ok=True)

    if not args.dry_run:
        entries = store.rebuild_manifest()
        problems = store.check_consistency()
        print(json.dumps({"removed": removed, "entries": len(entries), "consistency": problems},
                         ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    print(json.dumps({"would_remove": removed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
