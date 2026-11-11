# 固定快照目录说明

## 当前快照（7 个）

| snapshot_id | 标的 | as_of | 构建方式 |
|---|---|---|---|
| `snap-6e6d5c31a0371ee0` | INDEX:000300.SH | 2026-11-10 | 按 `--as-of` 选行，closed bar |
| `snap-1a5ed1b1f7206c14` | INDEX:000905.SH | 2026-11-10 | 同上 |
| `snap-5d60fe1e7fb6ed3b` | INDEX:000852.SH | 2026-11-10 | 同上 |
| `snap-6938a7a7b2c868c4` | ETF:510300.SH | 2026-11-10 | ETF 日线按日期选行 |
| `snap-ff2df2705d7e880f` | ETF:510500.SH | 2026-11-10 | 同上 |
| `snap-4c1d2bfec2a65678` | ETF:512100.SH | 2026-11-10 | 同上 |
| `snap-fac0481bd5f7d1b5` | EQUITY:601398.SH | 2026-09-30 | 最近报告期 |

本目录**只放当前有效快照**。作废样例已移出（见 `docs/phase1-rework-f1f3.md` 的迁移说明），
不再用备注文字控制数据准入。

## 规则

1. **内容寻址**：`snapshot_id` = 文件内容（除 `snapshot_id` 自身外的全部字段）规范化后的
   SHA-256 前 16 位。`fetched_at`、`notes`、`producers`、`coverage` 都在哈希范围内，
   改动其中任何一项都会改变身份。
2. **绑定**：加载必须提供请求 ID，且声明 ID、内容哈希、请求 ID 三者一致；分析入口还核对
   `instrument_id` 与 `asset_class`。
3. **版本排序**：`latest_for` 按 `(as_of, fetched_at, snapshot_id)` 取最新；`fetched_at`
   受内容哈希保护，不能在不换 ID 的情况下修改。
4. **manifest 一致性**：`SnapshotStore.load` 会把 manifest 里的 `instrument_id / as_of /
   fetched_at` 与快照实际值比对，不一致即 `SNAPSHOT_MANIFEST_MISMATCH`；
   `check_consistency()` 可对整个目录做自检。
5. **时点**：每条事实记录 `available_at` 与精度；只有日期时按「当日结束」处理；未知不放行。
6. **重建**：`tools/build_snapshots.py --as-of YYYY-MM-DD` 按日期选行，找不到该行即失败，
   不倒填；未指定 `--as-of` 时取最后一行并记录 `bar_status`。
