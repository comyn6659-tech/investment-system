# 固定快照目录说明

## 当前快照（10 个）

| snapshot_id | 标的 | as_of | 事实数 | 构建方式 |
|---|---|---|---|---|
| `snap-6e6d5c31a0371ee0` | INDEX:000300.SH | 2026-11-10 | 5 | 按 `--as-of` 选行，closed bar |
| `snap-1a5ed1b1f7206c14` | INDEX:000905.SH | 2026-11-10 | 5 | 同上 |
| `snap-5d60fe1e7fb6ed3b` | INDEX:000852.SH | 2026-11-10 | 5 | 同上 |
| `snap-d6570cf8645d49e4` | ETF:510300.SH | 2026-11-10 | 19 | ETF 日线 + 成交额/20 日均额 + 同日折溢价 |
| `snap-5a28f88d0e6bbf4f` | ETF:510500.SH | 2026-11-10 | 19 | 同上 |
| `snap-429b5c94870b9db2` | ETF:512100.SH | 2026-11-10 | 19 | 同上 |
| `snap-791670f4b3c68cab` | EQUITY:600519.SH | 2026-11-10 | 544 | 三张合并报表 × 12 期 + 分红 + 已收盘价 |
| `snap-855281f9cc5afb1f` | EQUITY:600900.SH | 2026-11-10 | 528 | 同上 |
| `snap-e701028693917737` | EQUITY:601398.SH | 2026-11-10 | 550 | 同上 + 银行专项缺口字段 |
| `snap-fac0481bd5f7d1b5` | EQUITY:601398.SH | 2026-09-30 | 46 | 阶段一样本，保留用于历史重放 |

同一标的同一 `as_of` 只保留**抓取时间最新**的一版；被取代的中间版本由
`tools/prune_snapshots.py` 清除（脚本会打印「谁被谁取代」）。
作废样例已移出本目录（见 `docs/archive/phase1-rework-f1f3.md` 的迁移说明），
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
   每个快照都带 `availability_precision_counts` 与 `availability_unknown`。
6. **重建**：
   - 指数 / ETF：`tools/build_snapshots.py --as-of YYYY-MM-DD`
   - 个股：`tools/build_equity_snapshots.py --as-of YYYY-MM-DD [--only 600519]`
   按日期选行，找不到该行即失败，不倒填；未指定 `--as-of` 时取最后一行并记录 `bar_status`。
   个股脚本对每个网络接口做 3 次重试，仍失败就整体报错，不写残缺快照。

## ETF 快照新增字段（阶段三）

- `etf.turnover_cny` / `etf.volume_shares`：当日成交额与成交量（不复权日线）；
- `etf.turnover_avg_20d_cny` / `etf.turnover_avg_days_used`：末尾至多 20 个交易日的平均成交额
  与**实际使用天数**（不足 20 天不称「20 日均值」）；
- `etf.premium_discount_same_day_pct`：**事后同日折溢价**，只在价格与净值同一估值日期、
  且都已实际取得时计算；不满足条件时记 `missing` 并写明原因；
- `etf.subscription_status` / `etf.redemption_status`：基金净值表的申购/赎回状态，
  **不等于**二级市场交易状态；
- `etf.bid_ask_spread_cny` / `etf.trading_status`：未取得，记 `missing`。

指数画像所需的长期序列不在快照里，而在 `data/series/`（`series_id` = 内容哈希），
结果与 `docs/archive/phase3-samples.md` 都引用这些 id。

## 个股快照的口径

- 三张报表均为**合并口径、累计数（年初至期末）**，金额单位「元」；
  年报/中报/季报不能直接混加，TTM 由 `engine/financials/periods.py` 按
  「最新累计 + 上年年报 − 上年同期累计」计算，缺比较期即拒绝，不做比例年化。
- 归母与少数股东分开记录（`net_profit_parent` / `minority_equity`）。
- 每 10 股派息按原始口径记录（`div.*.per_10`），换算成每股价在模型层完成；
  特别分红单列，所属财年缺失的记录不猜年份。
- 价格为**不复权日线的收盘价**，附带 `price.bar_status`；`intraday` 不得当作收盘价，
  也不能用于现价比较。
- 银行另有 6 个专项字段（不良率、拨备覆盖率、核心一级资本充足率、资本充足率、
  风险加权资产、监管下限）记为 `missing`，并在 `coverage.bank_specific.attempts`
  里写明已尝试的接口——不填造、不用普通企业口径替代。
