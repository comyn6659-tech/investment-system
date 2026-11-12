# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第三批：**指数线加入创业板指（399006）** |
| 代码版本 | `phase4-1.0.0` |
| 政策版本 | 个股 `equity-policy-v1.1`（`pol-6d1a5387780fe6a2`，未改） |
| 模型版本 | 个股政策 `validated-1.0.0`；指数/ETF 仍 `research-0.4.0` / `unapproved-phase2` |
| 本轮实现提交 | `15f7aea` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `efbacfa`（第四阶段第二批入口，实现 `71d1beb`） |

## 1. 结论：官方历史估值不可得，按“短历史”交付

| 数据 | 是否官方 | 是否取得 | 处理 |
|---|---|---|---|
| 399006 日收盘 | 是（国证指数） | 是，2010-06-01～2026-11-11，3,993 条 | 接入序列库、指数快照 |
| 399606 全收益日收盘 | 是（国证指数） | 是，同区间、3,993 条 | 接入序列库（收益口径） |
| 399006 日度/月度 PE 历史 | 是（国证指数/深交所） | **否**；只有最新交易日 PE/PB 快照 | 只作“官方短历史”单日并列事实；**不计算分位** |
| 159915 产品资料 | 否（天天基金/东财） | 是 | 纳入既有 ETF 产品资料检查 |

官方当前快照（2026-11-11）：PE 滚动 **25.1325**、PE 静态 31.6183、PB **4.07**。

- 没有用 CEIC 的创业板板块 PE（约 35.39）、深交所行业加权 PE、第三方指数 PE 或任何拼接序列补历史；
- 因此 **没有做** 5 年月度估值画像、候选 A 周度研究信号、研究回测、399006 的候选 A 账本记录；
- 逐项探针、URL、字段、起止与“为什么不用板块 PE”见
  [docs/phase4c-chinext-feasibility.md](docs/phase4c-chinext-feasibility.md)。

## 2. 已接入（Q1、Q2、Q3）

| 项 | 内容 |
|---|---|
| 399006 收盘序列 | `cnindex.official_history` / `official_close`，3,993 条，内容哈希与序列库 manifest |
| 399606 全收益序列 | 同源，`total_return_close`，3,993 条 |
| 官方当前 PE/PB | `official_pe_ratio_1`、`official_pb_ratio_1`，各 1 条（2026-11-11），`daily_short` |
| 指数快照 | `INDEX:399006.SZ`，`as_of=2026-11-11`；仅官方来源；`valuation_history.available=false` |
| 指数画像 | `insufficient_data`，`metrics={}`；PE/PB 只进入 `official_short_history`，明确“仅作并列事实” |
| CLI | `undetermined` / `insufficient_data` / 无 `model_status` / 无 `policy_conclusion` |

- 接口原始 3,994 行含基日 2010-05-31 的 1000 点基点行；按发布日 2010-06-01 截断，丢弃 1 行。
- `official_only=True` 与候选 A 的 `cnindex./csindex.` 白名单双重校验：第三方估值即使被放进序列库，也不能进入分位。
- 没有 `official_pe_rolling` 序列；这是“不做信号/回测”的直接原因（Q4 不适用）。

## 3. 名称识别（Q6）

| 输入 | 结果 |
|---|---|
| `创业板指`、`创业板指数`、`399006` | `INDEX:399006.SZ` |
| `创业板` | `needs_clarification`：“创业板”是交易板块，不是指数；询问是否指创业板指 |
| `创业板50`、`创业板综`、`创业板R` 等 | `out_of_scope`，`OUT_OF_SCOPE_INDEX_VARIANT` |

注册表新增 `INDEX:399006.SZ`，带国证指数、深交所两方身份证据，并标记 `official_valuation_only=true`。

## 4. ETF 159915（Q7 的一部分）

- 注册表新增 `ETF:159915.SZ`（创业板ETF易方达），跟踪 `INDEX:399006.SZ`；
- 产品快照：价格、单位净值、成交额/成交量、费率、跟踪标的；沿用既有 ETF 产品资料检查，不做执行检查；
- 价格/成交在东方财富 push2 限流时改用新浪财经等价日线（`sina.etf_hist`），快照内如实标注；
  净值仍来自东方财富；没有把新浪价格伪装成东财数据。

## 5. 未做及原因（Q4、Q5）

| 未做 | 原因 |
|---|---|
| 5 年月度估值画像 | 没有官方日度/月度历史估值序列；不能用第三方或板块 PE 代替 |
| 候选 A 周度研究信号 | 规则需要官方 PE 日频历史；只有 1 个交易日，无法形成 5 年周度池 |
| 候选 A 研究回测 | 同上；回测文件未新增 399006 条目 |
| 399006 候选 A 账本记录 | 没有信号可写；账本仍 3 条既有记录 |

## 6. 未变化项（Q7）

| 检查项 | 结果 |
|---|---|
| 沪深300 / 中证500 / 中证1000 | 仍 `complete`；2026-W45 均为 `exit`；阈值未改 |
| 候选 A 账本 | 仍 3 条，`--check` 0 问题；无 399006 记录 |
| 个股政策 / 结论账本 | `equity-policy-v1.1` 未改；结论账本仍 7 条 |
| 回测 | `data/backtest/candidate-a.json` 未改，无 399006 条目 |
| ETF 既有结果 | `510300` / `510500` / `512100` 快照与产品检查未重建 |
| 旧项目 | `/Users/comyn/Documents/deepseek-harness/投资/valuation/` 未修改、未删除 |

## 7. 测试（Q7）

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 497 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_chinext.py` | 17 | Q1–Q8：可行性、官方序列与哈希、非官方来源拒绝、共用代码、无回测/账本记录、名称三情形、CLI、快照、ETF、既有指数不变 |
| 既有测试 | 480 | 阶段一/二/三/四批一/四批二回归 |

## 8. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/build_chinext_data.py --probe-only   # 只核查，写可行性 JSON
.venv/bin/python tools/build_chinext_data.py                # 核查 + 写序列/快照/ETF 快照
.venv/bin/python -m engine.cli 创业板指                     # 指数：insufficient_data / undetermined
.venv/bin/python -m engine.cli 创业板                       # 应要求澄清
.venv/bin/python -m engine.cli 创业板50                     # 应为范围外
.venv/bin/python -m engine.cli 159915                       # ETF 产品资料检查
.venv/bin/python -m engine.cli --check-conclusions          # 个股结论账本：7 条，0 问题
.venv/bin/python tools/update_signal_ledger.py --check      # 候选 A 账本：3 条，0 问题
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 9. 提交记录

| 提交 | 内容 |
|---|---|
| `15f7aea` | **本轮实现**：可行性核查、399006/399606 序列、官方当前 PE/PB、指数快照、名称识别、ETF 159915 |
| 本文件所在提交 | 交付入口（记录实现提交 `15f7aea`） |
| `efbacfa` / `71d1beb` | 第四阶段第二批（批准 v1.1、正式结论、新鲜度、结论账本） |
| `28f3fb6` / `7ccf8f6` | 第四阶段 4a-R |
| `3ad76cd` / `3a910c0` | 第四阶段第一批收尾 |
| `b7ef983` / `9bc31ac` | 第四阶段第一批 |
| `1866e2b` / `106a9b8` | 第三阶段收尾（已签收） |

## 10. 详细材料

| 内容 | 文件 |
|---|---|
| 本批实现与 Q1–Q8 验收 | [docs/phase4c-delivery.md](docs/phase4c-delivery.md) |
| 创业板指数据可行性报告 | [docs/phase4c-chinext-feasibility.md](docs/phase4c-chinext-feasibility.md) |
| 机器可读可行性记录 | [data/coverage/chinext-feasibility.json](data/coverage/chinext-feasibility.json) |
| 数据构建脚本 | [tools/build_chinext_data.py](tools/build_chinext_data.py) |
| 创业板指验收测试 | [tests/test_chinext.py](tests/test_chinext.py) |
| 验收清单（Q1–Q8） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 个股正式结论（上一批） | [docs/phase4b-delivery.md](docs/phase4b-delivery.md) |
| 结论账本 | [data/ledger/equity-conclusions.jsonl](data/ledger/equity-conclusions.jsonl) |
| 候选 A 信号账本 | [data/ledger/candidate-a-signals.jsonl](data/ledger/candidate-a-signals.jsonl) |
| 旧项目（只读） | `/Users/comyn/Documents/deepseek-harness/投资/valuation/`（未改） |
