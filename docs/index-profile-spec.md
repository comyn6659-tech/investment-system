# 指数估值画像：口径与窗口定义

版本：`index-profile/1`｜序列契约：`index-series/1`｜模型：`index_valuation_profile` `0.3.0-research`
代码版本：`phase3-0.3.0`

本文件定义「5 年月度估值画像」到底算了什么、不算什么。它**不**定义买卖规则。

---

## 1. 为什么是月度

第三阶段施工指令 §3 批准使用**同一来源、同一指标口径的月度参考样本**做描述性画像。
现有调查已取得第三方月度长序列，但未取得满足原周度要求的连续周度样本。

- 只调整本轮画像的**采样口径**；
- 原周度候选策略仍**未**批准改成月度交易策略；
- **不插值、不前向填充、不重复值扩频**，因此不存在任何由月度数据生成的周度观测。

---

## 2. 固定历史窗口

| 项目 | 定义 |
|---|---|
| 主窗口 | 分析时点所在月份之前的 **60 个完整自然月** |
| 每月观测 | 至多一个：先按有效性过滤，再取该月内**最后一个**有效且有实际日期的同口径记录 |
| 当前未结束月份 | **不进入**参考池 |
| 比较值 | 该来源**最新可用的同口径实际观测**，显示其真实日期；不把旧值改写成今天 |
| 参考池 | 只含**早于比较值日期**的记录；比较值若落在池内必须移除 |
| 有效月份门槛 | **54 个**（90% 数据完整性工程规则） |
| 覆盖不足 | 不给该指标的「5 年月度分位」，只展示已取得的当前值与缺口；**不**改名为「成立以来分位」 |
| 10 年窗口 | 本轮不强制，避免非必要范围 |

例（分析时点 2026-11-10）：分析月 = 2026-11，窗口 = 2021-11 … 2026-10，共 60 个月。
若最新观测是 2026-11-10，它不在窗口内；若最新观测是 2026-10-30，它落在窗口里，
会被移出参考池（池大小变为 59）。

---

## 3. 百分位算法

```text
分位 = 100 × (参考池中小于当前值的数量 + 0.5 × 等于当前值的数量) / 参考池有效观测数量
```

- 用**原始有效数值**计算，只在展示时四舍五入到 1 位小数；
- **PE 与 PB 分别计算**，不拼成一个综合分数；
- 参考池为空或覆盖不足 → 不给分位（返回 `null`，并写明原因）。

实现：`engine/series/window.py::build_window` / `percentile_of`。

### 3.1 PE 的有效性

- 观测值 **≤ 0**：不进入 PE 分布，并在结果里列出被剔除的观测；
  **同月若仍有有效观测，该月仍然保留**（先过滤、再取每月最后一条）。
  如果反过来先按月取最后一条、再过滤非正值，会让「月中有效、月末无效」的月份整月消失，
  从而错误降低覆盖或改变分布；
- 比较值本身 ≤ 0：不给该 PE 分位，**不**退回更早的正值冒充当前值；
- PE 不可用时**不会**把标签换成 PB，两者各自独立。

### 3.2 PB 的有效性

PB 按供应商列定义直接使用；本轮未取得该列的定义说明，因此记为 `unknown`，不做额外筛选。

---

## 4. 每期额外硬性规则

| 规则 | 处理 |
|---|---|
| 未来日期记录（晚于分析时点） | 剔除，并在 `dropped_future` 中列出 |
| 同月多条记录 | 先剔除无效值，再保留最后一条有效记录；被剔除的计入 `dropped_non_positive` |
| 无法解析的日期 | 剔除并记录 |
| 非有限数值 | 序列装载阶段直接拒绝（`Float('inf')`/NaN 不入库） |

---

## 5. 数据来源与口径记录

每条序列是一个**同源同列**文件（`data/series/ser-*.json`），`series_id` 即内容哈希。
序列记录以下字段：

| 字段 | 本轮取值 |
|---|---|
| 来源 id / 发布方 / 接口 / URL | `legulegu.index_pe_pb`（乐咕乐股）/ AKShare `stock_index_pe_lg`、`stock_index_pb_lg` |
| 原始列名 | `滚动市盈率`、`市净率`（保持供应商原名，未改名） |
| 聚合方法 | `unknown`（来源未说明） |
| 分母口径 | `unknown` |
| 亏损样本处理 | `unknown` |
| 观测日期 / 抓取时间 | 逐条记录 / 来源级记录 |
| 修订与历史版本可得性 | `unknown` |
| 转换规则 | 「原列，未做换算」 |
| 频率标记 | `monthly_reference` |

官方短历史单独成序列（`csindex.indicator_file`，列 `市盈率1`，频率 `daily_short`，
约 20 个交易日）：**只并列展示，不参与长历史排名**。

官方短历史同样受画像 `as_of` 限制：只展示**观测日期不晚于 `as_of`** 的记录，
`latest`、`observation_count` 与 `coverage_start/end` 都按可见记录计算；
晚于该日的记录计入 `excluded_future_observations`，不展示数值。
若截至 `as_of` 没有任何可见记录，返回 `status = no_observation_by_as_of`、
`latest = null` 并列入缺口，**不硬取未来值**。

### 5.1 不允许的拼接

- 不用官方最新 PE 去排第三方历史池；
- 不把「静态市盈率 / 等权滚动市盈率 / 市盈率中位数」等不同列拼成一条序列；
- 同一列内部若发生无法解释的口径变化，不应继续当作一致历史（本轮未发现，但没有能力验证，
  因此把 `revision_availability` 记为 `unknown` 并在报告里写明限制）。

### 5.2 关于「历史可交易性」

历史发布时间与修订版本未知，因此：

- 允许用它做「按本次取得数据的历史分布描述性统计」；
- **阻断**把它伪称为严格的历史可交易信号；
- 本轮**不**填造 `available_at`，也**不**宣称回测已消除未来信息。

---

## 6. 输出与用途边界

画像块（`payload.valuation_profile`，`payload_version = index-profile/1`）包含：
每条指标的原列名、当前值与日期、分位（原始值 + 展示值）、有效样本数、参考区间、
中位数、覆盖月份、缺失月份、被剔除观测、来源与限制，以及 `is_trading_signal: false`。

画像块本身明确**不**输出：

- 目标点位（不把分位乘点位）；
- 买卖点、candidate/wait/avoid；
- 综合估值分数。

`decision` 恒为 `undetermined`；`valuation_state` 恒为 `unknown`。
分位低不等于可以买，分位高也不等于应该卖。

第四阶段起，指数报告在画像之外**另加**一块「候选 A 研究信号」
（`payload.candidate_a_signal`），用官方日频滚动市盈率的周度分位计算，
与第三方月度画像不是同一条序列。见第 8 节。

---

## 7. ETF 如何复用同一画像

ETF 分支只增加产品资料，**不改指数算法**：

- 通过 `Analyzer._index_profile(underlying, as_of)` 生成同一份画像；
- 原样嵌入 `payload.product_check.underlying_index_profile`；
- 回归测试逐字节比较「从指数进入」与「从 ETF 进入」的画像 JSON（`test_index_profile_is_identical_from_index_and_etf_entry`）。

产品资料侧的口径见 [phase3-delivery.md](phase3-delivery.md) §3。

---

## 8. 候选 A 研究信号与只追加账本（第四阶段第一批 B）

### 8.1 信号块

`payload.candidate_a_signal` 与 `payload.strategy_state.candidate_a_signal` 为同一份对象：

| 字段 | 内容 |
|---|---|
| `model_id` | `index_strategy_a_valuation_reversion` |
| `model_status` / `approved` / `research_signal` / `is_trading_signal` | `research` / `false` / `true` / `false` |
| `rule` | 入场分位 20、退出分位 60、窗口 5 年、覆盖门槛 90%、持有 12 个日历月、T+1 收盘执行（研究口径）、`rule_version = candidate-a/1` |
| `latest` | 最新周度观察点：`week`、`date`、`pe`、`percentile`、`percentile_display`、可用周/窗口周、覆盖、`week_complete` |
| `signal` | `entry`（≤20）/ `exit`（≥60）/ `none` / `not_evaluable`；只描述条件，不跟踪持仓 |
| `entry_condition_met` / `exit_condition_met` | 条件是否成立 |
| `backtest` | 同一规则的回测摘要（全收益 / T+1 / 10bp）：区间、笔数、累计、最大回撤、同期买入持有、超额、报告与数据路径 |
| `series_ids` | 计算所用的官方收盘、滚动市盈率、全收益序列 id |
| `ledger` | 只追加账本路径、已有记录数、最后一周与信号 |

信号使用与回测**同一个** `weekly_percentile_at` / `percentile_of`，避免报告与回测两套口径。
观察点只取 `date <= as_of` 的周；若最新观察点所在自然周尚未结束，`week_complete = false`
并注明「周度规则要到该周最后一个交易日收盘后才最终确定」。

指数结果的原因码增加 `INDEX_SIGNAL_RESEARCH_ONLY`（非阻断），`decision` 仍为 `undetermined`。
`model_plan.research_signals` 记录该信号；`model_plan.not_selected` 只剩候选 B。

### 8.2 账本

`data/ledger/candidate-a-signals.jsonl`：一行一条记录，只追加。规则与字段见
[data/ledger/README.md](../data/ledger/README.md)。更新命令：

```bash
.venv/bin/python tools/update_signal_ledger.py            # 追加
.venv/bin/python tools/update_signal_ledger.py --dry-run  # 预览
.venv/bin/python tools/update_signal_ledger.py --check    # 完整性
```

只记录**已结束自然周**的观察点；同一标的同一周重复运行内容一致则跳过，不一致则报冲突；
`record_id` 是内容哈希，历史被改写会被 `--check` 发现。
