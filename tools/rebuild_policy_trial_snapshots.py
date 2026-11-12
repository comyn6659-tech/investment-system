#!/usr/bin/env python
"""按 R3 / R11 重建政策试运行快照（冻结原始事实，只修字段映射与时点标记）。

背景：v1 快照把东方财富 `END_CASH_EQUIVALENTS`（现金等价物分项）当作
「期末现金及现金等价物余额」。审查 F2a 以茅台为例：该字段 2021–2025 为
0 / 0 / 30.0 / 56.7 / 84.7 亿元，而真正的余额是 `END_CCE` = 1,264.3 亿元。
R3 要求只用 END_CCE，并把 END_CASH_EQUIVALENTS 还原为分项。

R11 要求 000858 的 2025 年中期数据（2025-03-31 / 06-30 / 09-30）在快照
记录的是更正后数值时，标记 restated=true，available_at 改为更正公告日
（2025 年年报披露日 2026-04-30）。

做法：以已有快照的原始事实为输入，原地重算事实 id、标签、时点与内容哈希；
不重新联网抓取，因此 v1 → v1.1 的差异只来自政策修订，不掺入新数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.facts import Fact  # noqa: E402
from engine.data.snapshot import Snapshot, SnapshotStore  # noqa: E402

SNAPSHOT_DIR = ROOT / "data" / "policy-trial" / "snapshots"

# 000858：2025 年三季报数据在 2025 年年报中被前期差错更正。
_RESTATED = {
    "EQUITY:000858.SZ": {
        "periods": ("2025-03-31", "2025-06-30", "2025-09-30"),
        "available_at": "2026-04-30",
        "note": (
            "2025 年年报对 2025 年前三季度做前期差错更正；快照记录的是更正后数值，"
            "故标记 restated=true，可获得时间改为更正公告日（2025 年年报披露日 2026-04-30）"
        ),
    }
}
_ORIGINAL_Q3_REVENUE_NOTE = (
    "更正前 2025 年三季报累计营业收入 609.45 亿元（2025-10-31 披露）；"
    "2025 年年报更正后为 306.38 亿元"
)


def _fix_cash(fact: Fact, existing_ids: set[str]) -> list[Fact]:
    """把 cash_equivalents_end（原为 END_CASH_EQUIVALENTS 分项）改为分项，
    并从 END_CCE 生成真正的期末现金余额。"""
    if fact.fact_id.endswith(".cash_equivalents_end") and "END_CASH_EQUIVALENTS" in fact.transform:
        component = Fact(
            fact_id=fact.fact_id.replace(".cash_equivalents_end", ".cash_equivalents_component"),
            metric="cash_and_equivalents_component",
            status=fact.status,
            value=fact.value,
            unit=fact.unit,
            currency=fact.currency,
            period=fact.period,
            available_at=fact.available_at,
            fetched_at=fact.fetched_at,
            source=fact.source,
            transform=fact.transform + "；R3：该科目是「现金等价物」分项，不是期末余额",
            restated=fact.restated,
            notes=(fact.notes + "；" if fact.notes else "")
            + "R3 字段改正：不得当作期末现金及现金等价物余额使用",
        )
        return [component]
    if fact.fact_id.endswith(".cash_flow_end_cce"):
        target = fact.fact_id.replace(".cash_flow_end_cce", ".cash_equivalents_end")
        if target in existing_ids:
            return [fact]
        balance = Fact(
            fact_id=target,
            metric="cash_and_equivalents_end",
            status=fact.status,
            value=fact.value,
            unit=fact.unit,
            currency=fact.currency,
            period=fact.period,
            available_at=fact.available_at,
            fetched_at=fact.fetched_at,
            source=fact.source,
            transform=fact.transform + "；R3：期末现金余额只用该口径（END_CCE）",
            restated=fact.restated,
            notes=fact.notes,
        )
        return [fact, balance]
    return [fact]


def _apply_restatement(instrument_id: str, facts: list[Fact]) -> list[Fact]:
    rule = _RESTATED.get(instrument_id)
    if not rule:
        return facts
    out: list[Fact] = []
    for fact in facts:
        if fact.period in rule["periods"] and not fact.restated:
            notes = rule["note"]
            if (
                fact.period == "2025-09-30"
                and fact.fact_id.endswith(".revenue")
                and fact.value is not None
            ):
                notes = notes + "；" + _ORIGINAL_Q3_REVENUE_NOTE
            fact = Fact(
                fact_id=fact.fact_id,
                metric=fact.metric,
                status=fact.status,
                value=fact.value,
                unit=fact.unit,
                currency=fact.currency,
                period=fact.period,
                available_at=rule["available_at"],
                fetched_at=fact.fetched_at,
                source=fact.source,
                transform=fact.transform,
                restated=True,
                notes=(fact.notes + "；" if fact.notes else "") + notes,
            )
        out.append(fact)
    return out


def rebuild_one(snapshot: Snapshot) -> Snapshot:
    facts: list[Fact] = []
    existing_ids = {fact.fact_id for fact in snapshot.facts}
    for fact in snapshot.facts:
        facts.extend(_fix_cash(fact, existing_ids))
    facts = _apply_restatement(snapshot.instrument_id, facts)

    coverage = json.loads(json.dumps(snapshot.coverage))
    coverage["cash_mapping"] = {
        "rule": "END_CCE_only",
        "rebuilt_by": "tools/rebuild_policy_trial_snapshots.py",
        "only_field": "END_CCE",
        "note": (
            "R3：期末现金余额只用 END_CCE；END_CASH_EQUIVALENTS 保留为"
            " cash_equivalents_component（现金等价物分项），不参与估值"
        ),
    }
    if snapshot.instrument_id in _RESTATED:
        coverage["restatement"] = {
            "periods": list(_RESTATED[snapshot.instrument_id]["periods"]),
            "available_at": _RESTATED[snapshot.instrument_id]["available_at"],
            "note": _RESTATED[snapshot.instrument_id]["note"],
        }

    return Snapshot(
        instrument_id=snapshot.instrument_id,
        asset_class=snapshot.asset_class,
        as_of=snapshot.as_of,
        available_at=snapshot.available_at,
        fetched_at=snapshot.fetched_at,
        facts=facts,
        coverage=coverage,
        producers=list(snapshot.producers),
        notes=(
            snapshot.notes
            if "R3/R11 重建" in snapshot.notes
            else snapshot.notes + "；R3/R11 重建：期末现金只用 END_CCE，000858 更正后数据标记 restated"
        ),
        schema_version=snapshot.schema_version,
    )


def main() -> int:
    store = SnapshotStore(SNAPSHOT_DIR)
    old_files = sorted(SNAPSHOT_DIR.glob("snap-*.json"))
    rebuilt = []
    for path in old_files:
        snapshot = Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
        new_snapshot = rebuild_one(snapshot)
        new_path = store.save(new_snapshot)
        rebuilt.append(
            {
                "instrument_id": snapshot.instrument_id,
                "old": snapshot.snapshot_id,
                "new": new_snapshot.snapshot_id,
                "old_file": path.name,
                "new_file": new_path.name,
            }
        )
    # 删除旧版本文件（保留新文件），再按最终目录重建 manifest
    keep = {item["new_file"] for item in rebuilt}
    removed = []
    for path in old_files:
        if path.name not in keep:
            resolved = path.resolve()
            if resolved.parent != SNAPSHOT_DIR.resolve():  # 防御：只删本目录
                raise RuntimeError(f"拒绝删除目录外文件: {resolved}")
            path.unlink()
            removed.append(path.name)
    store.rebuild_manifest()

    problems = store.check_consistency()
    print(
        json.dumps(
            {
                "rebuilt": len(rebuilt),
                "removed": len(removed),
                "consistency": problems,
                "changes": rebuilt,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
