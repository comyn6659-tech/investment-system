"""快照与重放测试：内容哈希、时点过滤、离线可复现。"""

from __future__ import annotations

import json

import pytest

from engine.data.snapshot import Snapshot, SnapshotError, SnapshotStore, facts_available_by


def test_every_manifest_snapshot_verifies(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    entries = store.load_manifest()
    assert len(entries) >= 5
    for entry in entries:
        snap = store.load(entry["snapshot_id"])
        assert snap.snapshot_id == entry["snapshot_id"]
        assert snap.validate() == []


def test_tampered_snapshot_is_rejected(tmp_path, snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    entry = store.load_manifest()[0]
    data = json.loads((snapshot_dir / entry["file"]).read_text(encoding="utf-8"))
    data["facts"][0]["value"] = 999999

    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SnapshotError):
        Snapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def test_snapshot_id_is_content_addressed(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    snap = store.load(store.load_manifest()[0]["snapshot_id"])
    other = Snapshot.from_dict(json.loads(json.dumps(snap.to_dict(), ensure_ascii=False)))
    assert other.snapshot_id == snap.snapshot_id


def test_point_in_time_filter_hides_later_disclosures(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    snap = store.load(store.latest_for("EQUITY:601398.SH"))

    # 2026-06-30 时点：2026-09-30 的报告（2026-10-31 才公告）不可见
    visible = facts_available_by(snap, "2026-06-30")
    visible_ids = {f.fact_id for f in visible}
    assert "fin.2026-09-30.ocf" not in visible_ids
    assert all(f.available_at and f.available_at <= "2026-06-30" for f in visible)

    # 报告公告之后的时点才可见
    later = {f.fact_id for f in facts_available_by(snap, "2026-11-01")}
    assert "fin.2026-09-30.ocf" in later


def test_facts_without_availability_are_never_released(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    snap = store.load(store.latest_for("INDEX:000300.SH"))
    visible = facts_available_by(snap, "2099-12-31")
    visible_ids = {f.fact_id for f in visible}
    # 可获得时间未知的估值事实不能被当作历史时点已知
    assert "index.pe_ttm" not in visible_ids
    assert "index.level" in visible_ids


def test_store_returns_latest_per_instrument(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    sid = store.latest_for("INDEX:000300.SH")
    assert sid is not None
    assert store.load(sid).instrument_id == "INDEX:000300.SH"
    assert store.latest_for("EQUITY:000000.SH") is None
