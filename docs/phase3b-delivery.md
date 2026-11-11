# 第三阶段第二批交付：事后诊断 + 策略数据可行性

日期：2026-11-11｜代码版本 `phase3-0.4.0`｜政策版本 `unapproved-phase2`｜模型版本 `research-0.3.0`

本批只做两件事：**A. 估值分组的事后诊断**、**B. 策略数据可行性调查（D1–D4）**。

**没有做**（按施工单 §2 冻结）：A/B 进出状态机、模拟持仓、交易成本、可执行回测、
参数搜索、月度版策略、CLI 输出改动、正式买点、`validated` 升级；个股部分未动。

---

## 1. A：估值分组的事后诊断

### 1.1 做了什么

| 步骤 | 做法 |
|---|---|
| 价格序列 | 新增三指数日线价格序列（`price_index_close`，**价格指数、不含分红**），版本化入库，`series_id` 为内容哈希 |
| 信号时点 | 月度估值序列的每个观测日 t；分位**复用** `build_window`（as_of = t），60 个月、门槛 54、当前值不入池 |
| 起点价格 | 不晚于 t 的最后一个交易日收盘（事后同日对齐） |
| 终点价格 | 不晚于「t + h 个日历月」月末的最后一个交易日收盘，h = 3/6/12 |
| 未到期 | 价格未覆盖到终点 → 记为未到期、不计入、不截断 |
| 敏感性 | 起点改为「下一个有分位的月度观测日」，终点不变 |
| 分组 | 只用冻结阈值：PE ≤20/20–60/≥60、PB ≤40/40–70/≥70、B 入场 PE≤20 且 PB≤40、B 退出 PE≥60 或 PB≥70、无条件基准 |

产出：[phase3b-diagnostics.md](phase3b-diagnostics.md)（人读）、
`data/diagnostics/ex-post-diagnostics.json`（机器可读，含逐月样本）。

### 1.2 样本规模

| 指数 | 观测日 | 可评估 | 分位不足 | 可评估区间 |
|---|---|---|---|---|
| 沪深300 | 260 | 206 | 54 | 2009-10-30 → 2026-11-10 |
| 中证500 | 239 | 185 | 54 | 2011-07-29 → 2026-11-10 |
| 中证1000 | 146 | 92 | 54 | 2019-04-30 → 2026-11-10 |

### 1.3 结果（中位数｜正收益比例）

| 指数 | 组 | 3 个月 | 6 个月 | 12 个月 |
|---|---|---|---|---|
| 沪深300 | 无条件基准 | −0.04%｜50.0% | 0.03%｜50.3% | 1.74%｜52.8% |
| 沪深300 | PE ≤20（8 段） | −1.03%｜44.6% | −2.57%｜43.1% | 1.05%｜52.3% |
| 沪深300 | PE ≥60（9 段） | 1.29%｜55.4% | 0.03%｜50.7% | −1.15%｜44.6% |
| 中证500 | 无条件基准 | 0.14%｜50.8% | 2.10%｜56.7% | 3.13%｜59.3% |
| 中证500 | PE ≤20（6 段） | −0.01%｜50.0% | −1.04%｜46.3% | −0.09%｜50.0% |
| 中证500 | PE ≥60（4 段） | 0.72%｜52.6% | 2.53%｜64.8% | 2.51%｜60.4% |
| 中证1000 | 无条件基准 | 0.23%｜51.1% | 3.73%｜57.6% | 7.61%｜62.0% |
| 中证1000 | PE ≤20（5 段，仅 23 个月） | 5.54%｜69.6% | 5.10%｜78.3% | 17.24%｜73.9% |
| 中证1000 | PE ≥60（4 段） | 0.32%｜57.1% | 13.16%｜61.1% | 20.44%｜75.0% |

### 1.4 怎么读这些数字

- **三个指数之间方向不一致**：沪深300 与中证500 的低分位组在 3/6 个月上低于同期基准，
  中证1000 的低分位组高于基准——但中证1000 只有 **23 个月、5 段**，且分组样本高度重叠。
- 中证1000 的高分位组在 6/12 个月上也高于基准，与「低分位更好」的直觉相反；
  这说明在这段样本里，分位与随后 3–12 个月价格走势没有稳定关系。
- 所有区间的四分位跨度都很大（见明细表），中位数差几个百分点不构成任何可操作信息。
- 12 个月组的到期样本更少（沪深300 PE≥60 组 78 个月里只有 65 个到期）。
- 因此本批**不给出任何策略结论**：既没有支持原 B 规则，也没有否定它。

护栏（报告开头已写明）：事后描述；价格收益不含分红；公布时刻与修订未知；
月度重叠样本；不构成策略表现的证明或买卖结论。

---

## 2. B：策略数据可行性（D1–D4）

详细调用记录见 [phase3b-feasibility.md](phase3b-feasibility.md)，
机器可读记录在 `data/coverage/strategy-data-feasibility.json`。

| 编号 | 需求 | 结论 | 关键证据 |
|---|---|---|---|
| D1 | ≥5 年、周度或更高频、同口径 PE/PB | **部分** | 中证官方历史行情文件（`stock_zh_index_hist_csindex`）含日频 `滚动市盈率`，2010-01-01 起、三指数各约 4092–4093 行；**但仍没有日频 PB**，PB 只有第三方月度列 |
| D2 | 公布时刻或可复现发布规律 | **未解锁** | 所有已测返回字段都没有发布时间戳；官方文件也不提供 |
| D3 | 全收益指数或可重建总收益的数据 | **已解锁** | 官方全收益指数日频历史：H00300 / H00905 / H00852，2010-01-01 → 2026-11-10，各 4093 行（`指数中文全称` 分别标注为「沪深300全收益指数」等） |
| D4 | 三只 ETF 历史日线价格与历史单位净值 | **部分** | 价格：新浪源 3/3 满足 ≥5 年日频（510300 自 2012-05-28、510500 自 2013-03-15、512100 自 2016-11-04）；净值：本次东方财富接口读超时 0/3 |

### 2.1 本次调用环境（影响解读）

- 东方财富 push2 / push2his / api.fund.eastmoney.com 在调查期间返回限流或读超时
  （首次提示约 5 分钟，反复触发可能 40 分钟以上）。
- 记载的失败属于**本次调用环境问题**，不代表数据不存在：同一批接口在当日 16:53
  的采集任务中成功——`data/snapshots/` 的三只 ETF 快照就是由 `fund_etf_hist_em`
  与 `fund_etf_fund_info_em` 构建的。
- 因此 D4 的净值部分「本次未取得」，需要择时重测才能定论；价格部分已用备用来源（新浪）实测。

**本批不据此改变任何策略规格，也不进入回测。**

---

## 3. 验收条件对照（J1–J7）

| 编号 | 要求 | 落实 | 证据 |
|---|---|---|---|
| J1 | 价格序列版本化入库、可重放、口径标明不含分红 | ✅ | `data/series/ser-*.json`（`price_index_close`，`frequency=daily`）、`SeriesStore.check_consistency()=[]`；序列 notes 与 `definition_note` 都写明「价格指数，不含分红」 |
| J2 | 每个 t 只用不晚于 t 的数据、复用画像函数、最新 t 与画像一致、有小样本测试 | ✅ | `engine/diagnostics.percentile_at` 直接调 `build_window`；`test_percentile_at_latest_matches_profile_output` 对三指数核对 75.833333 / 75.0 / 65.0；`test_percentile_at_uses_only_data_up_to_that_date` |
| J3 | 起止点、未到期排除、敏感性按单实现；有独立手算测试 | ✅ | `forward_price_return` + `calendar_month_end`；`test_forward_return_hand_calculation`、`test_forward_return_endpoint_is_month_end_not_same_day`、`test_forward_return_not_matured_is_excluded`、`test_sensitivity_start_shifts_to_next_observation_date` |
| J4 | 只用冻结阈值；字段齐全；三指数分开 | ✅ | `FROZEN_THRESHOLDS` 无修改入口；`test_thresholds_are_frozen`、`test_bucket_boundaries_belong_to_low_and_high`、`test_diagnostics_end_to_end_on_real_series`（字段齐全、分段计数、三指数分文件） |
| J5 | 措辞护栏；CLI 与 research/undetermined 不变；既有测试通过 | ✅ | 报告开头四条护栏；`test_report_avoid_conclusory_wording`（不含「胜率/显著/建议/有效」）；CLI 无改动，三指数/三 ETF 仍 `complete / research / undetermined` |
| J6 | D1–D4 实际调用记录与结论 | ✅ | `docs/phase3b-feasibility.md`、`data/coverage/strategy-data-feasibility.json`（含参数、字段、行数、日期范围、频率、错误、环境说明） |
| J7 | DELIVERY.md 链接报告、机器可读结果、复现命令、测试结果与提交 ID | ✅ | [DELIVERY.md](../DELIVERY.md) |

---

## 4. 测试

```text
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
361 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_diagnostics.py` | 19 | 分位复用与一致性、只用 t 之前的数据、覆盖不足、价格对齐、月末对齐、未到期、敏感性起点、冻结阈值、边界归属、分段计数、分位/统计量手算、端到端字段、措辞护栏 |
| 既有回归 | 342 | 阶段一/二/三首轮（含三次反例修复）全部保留 |

---

## 5. 数据与复现

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system

# 1) 价格序列（行情接口被限流时自动退回同日缓存，并记录在序列 notes 里）
.venv/bin/python tools/build_price_series.py

# 2) 事后诊断（生成机器可读 + Markdown）
.venv/bin/python tools/run_ex_post_diagnostics.py

# 3) 策略数据可行性调查（D1–D4，实际调用）
.venv/bin/python tools/probe_strategy_data.py
#    只重测某几条（失败重测不会抹掉早前成功记录）：
.venv/bin/python tools/probe_strategy_data.py --merge --only d4_etf_nav_510300

# 4) 全部测试
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

固定数据 ID：

| 内容 | ID |
|---|---|
| 沪深300 价格指数 | `ser-d14c05e091de9c01` |
| 中证500 价格指数 | `ser-caf4924a40523148` |
| 中证1000 价格指数 | `ser-28caf9643ab189b3` |
| PE / PB 月度序列 | 见 `data/series/manifest.json`（阶段三首轮已交付，未改动） |
| 快照 | 未改动（阶段三首轮的三指数/三 ETF 快照） |

---

## 6. 剩余缺口与决策点

| # | 缺口 | 影响 |
|---|---|---|
| G-1 | 日频 PB 仍缺 | 原周度候选策略需要 PE+PB 双指标同频历史，仍未齐备 |
| G-2 | 公布时刻未知 | 仍无法证明信号在当时的可得时点 |
| G-3 | ETF 历史净值本次未重测成功 | 历史折溢价回测仍缺一侧数据 |
| G-4 | 诊断样本重叠、指数间方向不一致 | 不能据此判断原 B 规则的好坏 |

按施工单 §6，决策点交给设计方与用户，本批不默认选择任何一条路。

---

## 7. 边界

- 不实现 A/B 进出状态机、不模拟持仓、不计交易成本、不做可执行回测；
- 不做参数搜索、不做月度版策略、不改 CLI 输出、不升级 `validated`；
- 个股部分未改动；阶段一/二/三首轮已签收内容未重做。
