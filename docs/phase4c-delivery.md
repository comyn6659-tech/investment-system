# 第四阶段第三批交付：指数线加入创业板指（399006）

日期：2026-11-12｜施工单：`batch4c-chinext-instructions.md`｜代码版本：`phase4-1.0.0`（未改版本号）

## 1. 结论先行

**官方历史估值不可得，本批按 `short_history_only` 交付。**

| 数据 | 是否官方 | 是否取得 | 本批处理 |
|---|---|---|---|
| 399006 日收盘 | 是（国证指数） | 是，2010-06-01～2026-11-11，3,993 条 | 接入序列库、指数快照 |
| 399606 全收益日收盘 | 是（国证指数） | 是，同区间、3,993 条 | 接入序列库（收益口径） |
| 399006 日度/月度 PE 历史 | 是（国证指数/深交所） | **否**；只有最新交易日 PE/PB 快照 | 只作“官方短历史”单日并列事实；**不计算分位** |
| 159915 产品资料 | 否（天天基金/东财） | 是 | 纳入既有 ETF 产品资料检查 |

官方当前快照（2026-11-11）：PE 滚动 **25.1325**、PE 静态 31.6183、PB **4.07**。
没有用 CEIC 的创业板板块 PE（约 35.39）、行业加权 PE、第三方指数 PE 或任何拼接序列来补历史。

因此本批**没有**做：5 年月度估值画像、候选 A 周度研究信号、研究回测、399006 的候选 A 账本记录。
原因和逐项探针见 [phase4c-chinext-feasibility.md](phase4c-chinext-feasibility.md)。

## 2. Q1–Q8 验收

| 编号 | 结果 | 证据 |
|---|---|---|
| Q1 | 可行性报告逐项给出四项数据的来源、字段、起止、频率、是否官方，以及做了/没做及原因 | [phase4c-chinext-feasibility.md](phase4c-chinext-feasibility.md) §2–§3；`data/coverage/chinext-feasibility.json`；`test_feasibility_record_is_explicit` |
| Q2 | 估值/信号只用官方同源；非官方来源不能进入分位 | `official_only=True` 过滤 + 候选 A 的 `cnindex./csindex.` 白名单；`test_non_official_profile_series_is_rejected`、`test_non_official_signal_series_is_rejected` |
| Q3 | 与前三只指数共用 `build_index_profile` / `build_candidate_a_signal` / `SeriesStore`；序列哈希可独立重算 | `test_shared_profile_and_signal_functions`、`test_series_hash_can_be_recomputed`、`test_official_short_history_is_used_by_profile` |
| Q4 | 未做回测；未新增 399006 回测条目 | `test_no_chinext_backtest_entry`；`data/backtest/candidate-a.json` 未改 |
| Q5 | 账本既有 3 条不变；无 399006 新记录（无信号可写） | `test_ledger_has_no_chinext_record_and_existing_three_unchanged`；`update_signal_ledger.py --check` 0 问题 |
| Q6 | 创业板指/创业板指数/399006 可识别；“创业板”澄清；“创业板50”范围外 | `test_name_resolution_three_cases`、`test_cli_bare_board_needs_clarification` |
| Q7 | 前三只指数、个股结论、结论账本、ETF 既有结果不变；全套测试通过 | `test_existing_three_indices_unchanged`；个股账本 7 条；**497 passed** |
| Q8 | DELIVERY.md 汇总、复现命令、测试结果、提交 ID | `DELIVERY.md` §1–§9 |

## 3. 实现内容

### 3.1 序列库

| 序列 | metric | 频率 | 观测 | 来源 |
|---|---|---|---|---|
| 399006 收盘 | `official_close` | daily | 3,993（2010-06-01～2026-11-11） | `cnindex.official_history` |
| 399606 全收益 | `total_return_close` | daily | 3,993（同区间） | `cnindex.official_history` |
| 399006 PE 当前快照 | `official_pe_ratio_1` | daily_short | 1（2026-11-11） | `cnindex.index_list` |
| 399006 PB 当前快照 | `official_pb_ratio_1` | daily_short | 1（2026-11-11） | `cnindex.index_list` |

- 接口原始 3,994 行含基日 2010-05-31 的 1000 点基点行；按发布日 2010-06-01 截断，丢弃 1 行。
- 内容哈希、起止与观测数进入 `data/series/manifest.json`，指数报告“序列库覆盖”可见。
- 没有 `official_pe_rolling` 序列；这是本批“不做信号/回测”的直接原因。

### 3.2 指数快照与画像

- 快照 `INDEX:399006.SZ`，`as_of=2026-11-11`，事实仅来自 `cnindex.official_history` 与 `cnindex.index_list`。
- 覆盖块写死：`valuation_history.available=false`、`supports_5y_window=false`、`supports_weekly_5y_history=false`。
- 画像状态 `insufficient_data`，`metrics={}`；`official_short_history` 含 PE/PB 各 1 条，明确“仅作并列事实，不参与长历史排名”。
- 画像限制与用途改为短历史专用文案，不再套用“供应商月度样本”的说明。
- CLI：`undetermined` / `insufficient_data` / 无 `model_status` / 无 `policy_conclusion`。

### 3.3 名称识别

| 输入 | 结果 |
|---|---|
| `创业板指`、`创业板指数`、`399006` | `INDEX:399006.SZ` |
| `创业板` | `needs_clarification`：“创业板”是交易板块，不是指数；询问是否指创业板指 |
| `创业板50`、`创业板综`、`创业板R` 等 | `out_of_scope`，`OUT_OF_SCOPE_INDEX_VARIANT` |

注册表新增 `INDEX:399006.SZ`，带国证指数、深交所两方身份证据，并标记 `official_valuation_only=true`。

### 3.4 ETF 159915

- 注册表新增 `ETF:159915.SZ`（创业板ETF易方达），跟踪 `INDEX:399006.SZ`。
- 产品快照：价格、单位净值、成交额/成交量、费率、跟踪标的；沿用既有 ETF 产品资料检查，不做执行检查。
- 产品资料来自天天基金/东方财富，和既有 ETF 同口径；不进入指数估值分位。
- 东方财富 push2 行情接口限流时，价格/成交改用新浪财经等价日线（`sina.etf_hist`），
  快照内 `etf.price.source.source_id` 与 `price_history.source_id` 均如实写为新浪；
  净值仍来自东方财富。没有把新浪价格伪装成东财数据。

## 4. 没有变化的部分

| 项 | 结果 |
|---|---|
| 沪深300 / 中证500 / 中证1000 信号 | 仍为 `complete`，2026-W45 均为 `exit`；阈值未改 |
| 候选 A 账本 | 仍 3 条，`check()` 0 问题；没有 399006 记录 |
| 个股政策与结论账本 | `equity-policy-v1.1` 未改；账本仍 7 条 |
| 回测 | `data/backtest/candidate-a.json` 未改，无 399006 条目 |
| ETF 既有结果 | `510300`/`510500`/`512100` 快照与产品检查未重建 |
| 旧项目 | `/Users/comyn/Documents/deepseek-harness/投资/valuation/` 未修改、未删除 |

## 5. 测试与复现

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 497 passed

.venv/bin/python tools/build_chinext_data.py --probe-only
.venv/bin/python tools/build_chinext_data.py
.venv/bin/python -m engine.cli 创业板指
.venv/bin/python -m engine.cli 创业板
.venv/bin/python -m engine.cli 创业板50
.venv/bin/python -m engine.cli --check-conclusions
.venv/bin/python tools/update_signal_ledger.py --check
```

| 测试文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_chinext.py` | 17 | Q1–Q8：可行性、官方序列与哈希、非官方来源拒绝、共用代码、无回测/账本记录、名称三情形、CLI、快照、ETF、既有指数不变 |
| 既有测试 | 480 | 阶段一/二/三/四批一/四批二回归 |

## 6. 限制

- 只针对本次已测官方接口；不代表所有官方（含付费/授权）渠道都没有历史估值。
- 官方当前 PE/PB 的加权方式、亏损样本处理未说明，记为 `unknown`；单日快照不用于盘中历史判断。
- 创业板指成分股更替大，未来即使取得官方历史，分位也只与自身历史比较，不能与沪深300直接比水平。
- 没有为了让分位、信号或回测通过而修改候选 A 阈值、股票池或数据口径。
- 159915 价格源因东财限流使用新浪等价日线；这是调用环境问题，不是数据不存在。

## 7. 提交记录

| 提交 | 内容 |
|---|---|
| 待回填 | 本批实现 |
| 待回填 | 交付入口（记录实现提交） |
