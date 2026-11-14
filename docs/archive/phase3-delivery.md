# 第三阶段首轮交付：宽基指数估值画像 + ETF 产品资料检查

日期：2026-11-11｜代码版本 `phase3-0.3.1`（W1/W2/E1 收尾）｜政策版本 `unapproved-phase2`｜模型版本 `research-0.3.0`

本文件交代本轮实现范围、真实输出、测试结果与剩余缺口。
口径与窗口的完整定义见 [index-profile-spec.md](index-profile-spec.md)，
真实数字见 [phase3-samples.md](phase3-samples.md)（自动生成）。

**本轮不实现交易回测、不实现 A/B 入场出场状态机、不调参、不生成正式波段买点。**
所有研究结果保持 `decision=undetermined`，模型状态保持 `research`，没有升级 `validated`。

---

## 1. 交付了什么

| 环节 | 实现位置 | 状态 |
|---|---|---|
| 版本化指数序列（月度长序列 + 官方短序列） | `engine/series/models.py`、`data/series/` | 已实现，`series_id` = 内容哈希 |
| 月度窗口与分位算法 | `engine/series/window.py` | 已实现，纯函数 |
| 指数画像组装 | `engine/series/profile.py` | 已实现，PE/PB 分开 |
| 指数分析分支 | `engine/pipeline.py::_index_result` | 已实现，走现有 CLI |
| ETF 产品资料检查 | `engine/reporting/product_check.py` | 已实现 |
| ETF 分析分支 | `engine/pipeline.py::_etf_result` | 已实现，复用同一指数画像 |
| 载荷扩展 | `engine/contracts/payloads.py`（`valuation_profile` / `product_check`） | 有版本、可选，向后兼容 |
| 出口校验 | `engine/contracts/validation.py` | 新增 `index_profile` 结果类型与两项载荷检查 |
| 文本报告 | `engine/reporting/text_summary.py` | 与 JSON 同源同校验 |
| 数据采集 | `tools/build_index_series.py`、`tools/build_snapshots.py`（ETF 扩展） | 已跑通 |

模型登记：`index_valuation_profile` → `status=research`、`implemented=true`、`version=0.3.0-research`。
两个策略模型仍是 `research` + `implemented=false`，本轮不实现。

---

## 1.1 W1 / W2 / E1 收尾（复验报告三项）

复验报告：`phase3-review-and-handover-boundary.md`。只修这三项，未扩大重构、未改投资政策、未开始回测。

### W1：先按月取最后值、再过滤非正 PE，丢掉整月有效观测

**根因**：`build_window` 先用月末记录覆盖月中记录，再剔除非正值。因此「月中有效、月末非正」的月份
整个消失，覆盖被错误降低（反例：应从 60 个月掉到 53 个月，分位变成 `null`）。

**修复**：`engine/series/window.py` 改为**先按有效性过滤，再按月取最后一条**
（`eligible` → `by_month`）。被剔除的非正观测仍完整列在 `dropped_non_positive`，
说明文字明确「同月若仍有有效观测，该月仍保留」。
**当前最新 PE 自身非正时仍不给分位**，不回退到旧正值。

修复后同一反例：参考池 60 个月、分位 50.0%、`insufficient_reason = null`。

### W2：官方短序列未受画像 as_of 限制

**根因**：长序列在 `build_window` 里剔除了未来记录，官方短序列却直接取 `observations[-1]`，
序列更新后重放旧日期快照会把后一天的官方值展示出来。

**修复**：`engine/series/profile.py` 对官方短序列同样只保留 `date <= as_of` 的观测；
`latest`、`observation_count`、`coverage_start/end` 都按可见记录计算，
未来记录计入 `excluded_future_observations`。截至 `as_of` 没有可见记录时返回
`status = no_observation_by_as_of`、`latest = null`，并列入 `missing`，不硬取未来值。

修复后：追加 `2026-11-11 = 999` 的合成记录，画像 `as_of = 2026-11-10` 的官方最新值仍是
`2026-11-10 / 13.82`，`excluded_future_observations = 1`。

### E1：ETF 直接沿用缓存折溢价

**根因**：`product_check` 读取快照里已存的折溢价，只核了部分日期与 NAV 正值，
没有核价格是否可用、是否收盘价、NAV 数值事实自身日期，也没有验证派生值是否仍与当前价格/净值一致。

**修复**：`engine/reporting/product_check.py` 在使用处统一核验：
价格可用且为 `closed`、NAV 可用且为正、`NAV 数值事实 period == nav_date`、价格与净值同一估值日期、
价格为正。条件齐备时**用价格与净值重算**，缓存值只作一致性核对
（`cached_value` / `cache_consistent`；不一致时改用重算值并在 `note` 里说明）。
任一条件不满足就把值置空、`status = rejected` 并写明原因，不再输出旧值。

四个变体的修复后行为：

| 变体 | 修复前 | 修复后 |
|---|---|---|
| 价格改为 5、NAV 4.1361 | 仍输出 −0.026595% | 重算为 **+20.886826%**，`cache_consistent = false` |
| NAV 数值事实 period 改为 11-09、`nav_date` 仍 11-10 | 仍输出旧值 | `rejected`，说明「NAV 数值事实日期与 nav_date 不一致」 |
| `bar_status` 改为 `intraday` | 仍输出旧值 | `rejected`，说明「不是已收盘价」 |
| 价格设为 missing、保留日期 | 仍输出旧值 | `rejected`，说明「价格缺失」 |

正常三只 ETF 的折溢价与缓存完全一致（`cache_consistent = true`），数值未变。

### 正常样例是否变化

**没有变化**：三只指数 PE/PB 分位（75.8/39.2、75.0/76.7、65.0/34.2）、参考池 60 个月、
中位数、官方短历史最新值与观测数（20）全部与修复前一致；三只 ETF 折溢价仍为
−0.026595% / −0.222890% / −0.209143%，`analysis_status = complete`、`decision = undetermined` 不变。

## 2. 指数画像（I1 / I2 / I3）

口径要点（完整见 [index-profile-spec.md](index-profile-spec.md)）：

- 窗口 = 分析时点所在月份之前的 **60 个完整自然月**；每月至多一个观测，取该月内最后一个有效观测；
- 比较值 = 该来源最新可用观测，显示真实日期；若落在参考池中会移除；
- 有效月份 < 54（90%）→ 不给该指标分位；
- 分位 = `100 × (小于 + 0.5×等于) / 有效观测数`，原始值计算、展示才取整；
- PE 与 PB 分开计算；PE ≤ 0 不参与分布。

三只首批指数的真实结果（2026-11-10）：

| 指数 | 点位 | PE（原列：滚动市盈率） | PE 分位 | PB（原列：市净率） | PB 分位 | 有效样本 |
|---|---|---|---|---|---|---|
| 沪深300 | 4,059.11 | 12.65 | 75.8% | 1.32 | 39.2% | 60/60 |
| 中证500 | 6,645.89 | 22.72 | 75.0% | 2.05 | 76.7% | 60/60 |
| 中证1000 | 6,579.36 | 25.58 | 65.0% | 2.06 | 34.2% | 60/60 |

- 三只指数都覆盖满 60 个月，没有缺失月份；
- 官方短历史（`市盈率1`，约 20 个交易日）单独并列，**不参与**长历史排名；
- 每只指数的 `series_ids` 随结果输出，可按 id 从 `data/series/` 复算。

---

## 3. ETF 产品资料检查（I4）

每只 ETF 输出「跟踪对象 + 产品事实 + 资料缺口」，并附上**同一份**指数画像。

| ETF | 跟踪指数 | 价格（日期/状态） | 单位净值（日期） | 披露时刻 | 事后同日折溢价 |
|---|---|---|---|---|---|
| 510300 | 000300 | 4.135（2026-11-10 / closed） | 4.1361（2026-11-10） | 未知 | −0.027% |
| 510500 | 000905 | 6.670（2026-11-10 / closed） | 6.6849（2026-11-10） | 未知 | −0.223% |
| 512100 | 000852 | 2.672（2026-11-10 / closed） | 2.6776（2026-11-10） | 未知 | −0.209% |

### 3.1 事后同日折溢价

只有**价格与单位净值对应同一估值日期**、且两者都已实际取得时才计算：

```text
事后同日折溢价 = 市场收盘价 / 同日单位净值 − 1
```

出口再核一次前提（价格日期 = 净值日期 = 折溢价记录日期，且净值 > 0）；
任一不成立就把值置空、状态记为 `rejected` 并写明原因（`test_cross_day_premium_is_refused`
等 4 项测试）。净值披露时刻未知，因此**不能**据此证明当日收盘前可交易，也不用于回测当日成交。

### 3.2 成交与交易状态

- 成交额、成交量：来自不复权日线的当日值；
- 「近 N 个交易日平均成交额」按**实际取得天数**报告（本轮三只都是 20 天）；
  不足 20 天时标签改为「近 5 个交易日平均成交额」且 `is_full_20d=false`（有回归）；
- 停复牌状态、买卖价差：**未取得**，逐条写入 `missing`，并给原因码
  `ETF_TRADING_STATUS_UNKNOWN`；不因有价格就断言当前可交易；
- 申购/赎回状态（场内买入/场内卖出）来自基金净值表，明确标注**不等于**二级市场交易状态。

### 3.3 费用

管理费 0.15%/年、托管费 0.05%/年，来自基金概况页；**未标注生效日期**，报告里注明以基金合同为准。

### 3.4 不作的事

不制定高溢价、流动性、费率的买卖门槛；不做 ETF 排行或推荐另一只基金；
「资料齐备」不等于「满足买入条件」，指数估值位置与 ETF 产品资料不互相替代。

---

## 4. 同一指数两个入口得到同一画像（I4）

`_index_profile()` 是唯一入口，ETF 分支只是调用它并把结果原样嵌入
`payload.product_check.underlying_index_profile`。
回归 `test_index_profile_is_identical_from_index_and_etf_entry` 对三组
（指数名 vs 其 ETF）逐字节比较 JSON，并要求 `model_plan.series_ids` 一致。

---

## 5. 测试

```text
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
342 passed in 3.55s
```

| 文件 | 项数 | 本轮新增覆盖 |
|---|---|---|
| `tests/test_index_profile.py` | 30 | 分位手算与并列值、60 个月窗口边界、每月去重、当前值排除、覆盖不足（<54）、非正 PE（入池与当前值两种）、未来日期剔除、中位数/区间手算、序列哈希与篡改检测、画像确定性、真实序列覆盖 60/60 |
| `tests/test_etf_product.py` | 26 | 同日折溢价公式、跨日拒算、净值缺失/非正、停复牌与价差显式缺失、披露时刻未知、20 日均额天数、费率来源、三只 ETF 对应指数、指数/ETF 画像一致性、出口护栏 |
| 既有回归 | 286 | 个股研究估值、阶段一契约与快照（全部保留通过） |
| 本轮新增（W1/W2/E1） | 12 | W1 三例、W2 三例、E1 六例；复验脚本 6 个 FAIL 全部转为 PASS |

阶段一/二的断言只改了三类「模型已实现」后的语义更新（指数/ETF 由 `unsupported` 变为
`complete`+`research`），其余保持不变；新增的 `index_profile` 结果类型有独立的来源校验。

---

## 6. 数据与序列

| 内容 | 位置 | 说明 |
|---|---|---|
| 指数月度序列 6 条 | `data/series/ser-*.json` | 3 指数 × PE/PB，`monthly_reference` |
| 官方短序列 3 条 | `data/series/ser-*.json` | `daily_short`，约 20 个交易日 |
| 序列 manifest | `data/series/manifest.json` | 记录来源、起止日期、观测数 |
| 指数快照 3 个 | `data/snapshots/` | 点位与官方估值事实（未重建） |
| ETF 快照 3 个 | `data/snapshots/` | 新增成交额、20 日均额、同日折溢价、申购赎回状态、价差缺失 |
| 业务证据 6 份 | `data/evidence/` | 阶段二，未改 |

`SnapshotStore.check_consistency()` 与 `SeriesStore.check_consistency()` 均为 `[]`。
被取代的旧 ETF 快照已用 `tools/prune_snapshots.py` 清出生产目录（脚本会打印取代关系）。

---

## 7. 剩余缺口及其影响

| # | 缺口 | 对描述统计的影响 | 对未来策略研究的影响 | 对可执行回测的影响 |
|---|---|---|---|---|
| G1 | 历史发布时间与修订版本未知 | 不影响「按本次数据的历史分布」 | 需要另行核实时点可得性 | **阻断**：无法证明当时已知，回测会引入未来信息 |
| G2 | 月采样（非周度） | 已按批准口径做月度画像 | 周度策略仍缺数据，不得用插值扩频 | **阻断**周度候选 |
| G3 | 聚合方法 / 分母口径 / 亏损样本处理 unknown | 只能作同源同列的相对位置 | 需要供应商说明 | 口径不明时不宜作为成交依据 |
| G4 | 停复牌与盘口价差未取得 | 已列为缺失 | 会影响流动性假设 | **阻断**「可成交性」判断 |
| G5 | 净值披露时刻未知 | 同日折溢价只能事后计算 | — | **阻断**用当日折溢价做当日成交 |
| G6 | 官方指标只有 20 个交易日 | 只作并列事实 | 不能用于长历史 | 不能用于长历史 |
| G7 | 非首批宽基（上证指数、中证A500、深证成指）无序列 | 明确返回 `insufficient_data` + 缺口 | 需要补序列 | — |

这些缺口都限定在「本轮已测来源与参数」范围内，不写成「所有免费数据均不可得」。

---

## 8. 与既有契约的关系

- `analysis-result/1` 未变；`valuation_profile` / `product_check` 是**可选** payload 字段，
  阶段一/二的历史结果仍通过校验（`PAYLOAD_OPTIONAL_FIELDS`）。
- 新增 `model_results.result_type = index_profile`，校验要求：模型已登记且已实现、版本一致、
  与 `model_plan.primary_model` 一致、整体 `model_status=research`、
  `analysis_status ∈ {complete, partial}`、`decision=undetermined`、
  且 `is_trading_signal` 必须显式为 `false`。
- 指数画像不允许 `valuation_state ∈ {attractive, neutral, expensive}`；
  ETF 不允许占用正式 `payload.premium_discount` 字段（同日折溢价只写在 `product_check`）。
- 阶段一/二硬约束全部保留：阻断性原因码对正式价格判断一票否决；研究模型不得输出 `candidate`；
  文本与 JSON 共用同一个校验器。

---

## 9. 边界声明

- 本轮**不实现**回测、不实现 A/B 状态机、不调参、不生成正式波段买点；
- **不修改**已签收的个股估值政策（相关回归全部通过）；
- 不改旧系统、不购买数据、不申请账户、不做基金排行或推荐；
- 画像数字是「相对该供应商历史分布的位置」，不是未来上涨概率，也不是内在价值折扣。
