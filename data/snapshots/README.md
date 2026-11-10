# 固定快照目录说明

## 当前快照（7 个）

| snapshot_id | 标的 | as_of | 说明 |
|---|---|---|---|
| `snap-9f0fbdb86c70b66e` | INDEX:000300.SH | 2026-11-10 | 日线最后一行，bar_status=closed |
| `snap-fffadc4bca3ed918` | INDEX:000905.SH | 2026-11-10 | 同上 |
| `snap-56e7c688ea35a45c` | INDEX:000852.SH | 2026-11-10 | 同上 |
| `snap-aef6f29159c9d182` | ETF:510300.SH | 2026-11-10 | ETF 日线收盘行 |
| `snap-d76db31ce212b446` | ETF:510500.SH | 2026-11-10 | 同上 |
| `snap-2ab47bf9fbfc5199` | ETF:512100.SH | 2026-11-10 | 同上 |
| `snap-decfda2d8866e9cf` | EQUITY:601398.SH | 2026-09-30 | 最近报告期 |

## 历史复现样例（1 个，禁止用于分析）

| snapshot_id | 说明 |
|---|---|
| `snap-23eaf5b0bfc82144` | 第一版采集器生成（固定旧日期 + 实时最新价）。`notes` 以 `LEGACY-REPRODUCTION` 开头，仅用于复现审计脚本 R8 的两项检查。分析入口遇到该标记会返回 `engine_error` + `SNAPSHOT_LEGACY_QUARANTINED` |

## 规则

1. **内容寻址**：`snapshot_id` = 规范化内容的 SHA-256 前 16 位，加载时重算比对。
2. **绑定**：加载时必须提供请求 ID，且声明 ID、内容哈希、请求 ID 三者一致；分析入口还会核对
   `instrument_id` 与 `asset_class`。
3. **版本排序**：`latest_for` 按 `(as_of, fetched_at, snapshot_id)` 取最新；哈希不参与新旧判断。
4. **时点**：每条事实记录 `available_at` 与精度；只有日期时按「当日结束」处理；未知不放行。
5. **重建**：`tools/build_snapshots.py` 以真实行情日期为准，与 `--as-of` 不符即失败，不倒填。
