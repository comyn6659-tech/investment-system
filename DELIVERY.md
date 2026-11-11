# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第三阶段第二批（A 估值分组事后诊断 + B 策略数据可行性调查） |
| 代码版本 | `phase3-0.4.0` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `research-0.3.0`（个股 3 个 + 指数画像 1 个，全部 `research`） |
| 本轮提交 | `PENDING`（见 §7） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮已签收 | `2f824df`（W1/W2/E1）、`61bfd92`（交付入口） |

## 1. 本轮完成范围

只做施工单 §2 允许的两件事；**没有做** A/B 状态机、模拟持仓、交易成本、可执行回测、
参数搜索、月度版策略、CLI 输出改动、正式买点、`validated` 升级；个股部分未动。

| 部分 | 内容 | 产出 |
|---|---|---|
| A 事后诊断 | 三指数月度分位（复用画像函数）+ 3/6/12 个月前瞻价格收益（不含分红）+ 冻结分组统计 + 敏感性组 | [phase3b-diagnostics.md](docs/phase3b-diagnostics.md)、`data/diagnostics/ex-post-diagnostics.json` |
| B 数据可行性 | D1–D4 实际调用记录与结论（限免费、无需账户来源） | [phase3b-feasibility.md](docs/phase3b-feasibility.md)、`data/coverage/strategy-data-feasibility.json` |

## 2. 诊断结果摘要（事后描述，不是策略）

| 指数 | 组 | 3 个月中位数｜正收益比例 | 6 个月 | 12 个月 |
|---|---|---|---|---|
| 沪深300 | 无条件基准 | −0.04%｜50.0% | 0.03%｜50.3% | 1.74%｜52.8% |
| 沪深300 | PE ≤20（8 段） | −1.03%｜44.6% | −2.57%｜43.1% | 1.05%｜52.3% |
| 沪深300 | PE ≥60（9 段） | 1.29%｜55.4% | 0.03%｜50.7% | −1.15%｜44.6% |
| 中证500 | 无条件基准 | 0.14%｜50.8% | 2.10%｜56.7% | 3.13%｜59.3% |
| 中证500 | PE ≤20（6 段） | −0.01%｜50.0% | −1.04%｜46.3% | −0.09%｜50.0% |
| 中证1000 | 无条件基准 | 0.23%｜51.1% | 3.73%｜57.6% | 7.61%｜62.0% |
| 中证1000 | PE ≤20（5 段，仅 23 个月） | 5.54%｜69.6% | 5.10%｜78.3% | 17.24%｜73.9% |

**结果不稳定且指数间方向不一致**：沪深300 与中证500 的低分位组在 3/6 个月上低于同期基准，
中证1000 的低分位组高于基准但只有 23 个月、5 段、样本高度重叠；中证1000 的高分位组在
6/12 个月上也高于基准。本批**不给出任何策略结论**，既没有支持原 B 规则，也没有否定它。

护栏：事后描述；价格收益不含分红；公布时刻与修订未知；月度重叠样本；
不构成策略表现的证明或买卖结论。

## 3. 数据可行性结论（D1–D4）

| 编号 | 需求 | 结论 | 证据 |
|---|---|---|---|
| D1 | ≥5 年、周度或更高频、同口径 PE/PB | **部分** | 官方历史行情文件含日频 `滚动市盈率`（2010-01-01 起，三指数各约 4092–4093 行）；**仍无日频 PB**，PB 只有第三方月度列 |
| D2 | 公布时刻或可复现发布规律 | **未解锁** | 已测返回字段均无发布时间戳 |
| D3 | 全收益指数或可重建总收益的数据 | **已解锁** | 官方全收益指数 H00300 / H00905 / H00852，2010-01-01 → 2026-11-10，各 4093 行，`指数中文全称` 明确为「沪深300全收益指数」等 |
| D4 | 三只 ETF 历史日线价格与净值 | **部分** | 价格 3/3（新浪源，510300 自 2012-05-28、510500 自 2013-03-15、512100 自 2016-11-04）；净值本次东方财富接口读超时 0/3 |

**调用环境**：东方财富 push2/push2his/api.fund 在调查期间限流或超时；记录在案的失败属于
本次调用环境问题，<u>不代表数据不存在</u>（同一批接口当日 16:53 的采集任务成功，ETF 快照即由其构建）。

## 4. 真实样例与可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system

# A：事后诊断（生成机器可读 JSON + Markdown 报告）
.venv/bin/python tools/run_ex_post_diagnostics.py

# A：价格序列（行情限流时自动退回同日缓存，并写入序列说明）
.venv/bin/python tools/build_price_series.py

# B：D1–D4 实际调用（可只重测某几条，失败重测不覆盖早前成功记录）
.venv/bin/python tools/probe_strategy_data.py
.venv/bin/python tools/probe_strategy_data.py --merge --only d4_etf_nav_510300

# CLI 未改动：三指数/三 ETF 仍 complete / research / undetermined
.venv/bin/python -m engine.cli '沪深300'
.venv/bin/python -m engine.cli '510300'
```

固定数据 ID（价格序列）：

| 内容 | ID |
|---|---|
| 沪深300 价格指数日线 | `ser-d14c05e091de9c01` |
| 中证500 价格指数日线 | `ser-caf4924a40523148` |
| 中证1000 价格指数日线 | `ser-28caf9643ab189b3` |

## 5. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 361 passed in 3.39s
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_diagnostics.py` | 19 | 分位复用与最新 t 一致、只用 t 之前数据、覆盖不足、价格对齐、月末对齐、未到期排除、敏感性起点、冻结阈值、边界归属、分段计数、统计量手算、字段齐全、措辞护栏 |
| 既有回归 | 342 | 阶段一 / 二 / 三首轮（含 W1/W2/E1）全部保留通过 |

**未通过项：无。**

## 6. 缺口与决策点

| # | 缺口 | 影响 |
|---|---|---|
| G-1 | 日频 PB 仍缺 | 原周度候选策略需 PE+PB 双指标同频历史，仍未齐备 |
| G-2 | 公布时刻未知 | 仍无法证明信号当时可得 |
| G-3 | ETF 历史净值本次未重测成功 | 历史折溢价回测仍缺一侧 |
| G-4 | 诊断样本重叠、指数间方向不一致 | 不能据此判断原 B 规则的好坏 |

按施工单 §6，下一步选项交给设计方与用户；本批不默认选择任何一条路。

## 7. 提交记录

| 提交 | 内容 |
|---|---|
| `PENDING` | **本轮**：事后诊断 + 数据可行性调查 + 19 项新回归 + 文档与入口 |
| `2f824df` | 阶段三首轮收尾：W1/W2/E1 修复 |
| `1bb379c` | 阶段三首轮：指数画像 + ETF 产品资料检查 |
| `1bb379c` | 阶段三首轮：指数画像 + ETF 产品资料检查 + 24/20 项新回归 |
| `82b8d4c` | 交付入口（阶段二） |
| `317506e` | S1 / S2 修复（阶段二收尾，已签收） |
| `a8c8f7a` | 报告补业务构成 |
| `b2d2d36` / `978e968` / `5455985` / `5c4c1c9` | 阶段二首轮与阶段一 |

历史交付报告保留在 `docs/` 中，本文件始终指向最新一轮。

## 8. 详细材料

| 内容 | 文件 |
|---|---|
| 本批实现报告（A + B） | [docs/phase3b-delivery.md](docs/phase3b-delivery.md) |
| A：事后诊断报告（自动生成） | [docs/phase3b-diagnostics.md](docs/phase3b-diagnostics.md) |
| A：机器可读诊断结果 | [data/diagnostics/ex-post-diagnostics.json](data/diagnostics/ex-post-diagnostics.json) |
| B：可行性调查报告（自动生成） | [docs/phase3b-feasibility.md](docs/phase3b-feasibility.md) |
| B：机器可读调用记录 | [data/coverage/strategy-data-feasibility.json](data/coverage/strategy-data-feasibility.json) |
| 指数画像口径与窗口定义 | [docs/index-profile-spec.md](docs/index-profile-spec.md) |
| 阶段三首轮交付（已签收） | [docs/phase3-delivery.md](docs/phase3-delivery.md)、[docs/phase3-samples.md](docs/phase3-samples.md) |
| 个股研究估值（阶段二） | [docs/phase2-delivery.md](docs/phase2-delivery.md)、[docs/equity-model-spec.md](docs/equity-model-spec.md)、[docs/assumptions-and-sensitivity.md](docs/assumptions-and-sensitivity.md) |
| 验收清单（阶段一/二/三） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 数据覆盖调查 | [docs/data-coverage.md](docs/data-coverage.md) |
| 序列与快照目录规则 | [data/snapshots/README.md](data/snapshots/README.md)、[data/series/manifest.json](data/series/manifest.json) |
| 示例请求与输出 | [examples/](examples/) |
