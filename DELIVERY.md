# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第三阶段第三批（候选 A 周度研究回测，理论指数） |
| 代码版本 | `phase3-0.5.0` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `research-0.3.0`（个股 3 个 + 指数画像 1 个，全部 `research`） |
| 本轮提交 | `PENDING`（见 §7） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮已签收 | `a5620ae`（事后诊断 + 数据可行性）、`76e2480`（交付入口） |

## 1. 本轮完成范围

| 部分 | 内容 | 产出 |
|---|---|---|
| 数据 | 三指数官方日频收盘 + 日频滚动 PE + 三只全收益指数收盘（9 条版本化序列，发布日前回溯值已丢弃） | `data/series/`、`tools/build_official_series.py` |
| 周度分位 | ISO 周最后一个交易日为观察点；5 年窗口、每周一条、当前周不入池、覆盖 <90% 不可评估；复用 `percentile_of` | `engine/backtest/weekly.py` |
| 状态机 | ≤20 入场、≥60 退出、满 12 个月到期退出、同点不重入、T+1 主口径 / T+2 敏感性、0/1 仓位 | `engine/backtest/state_machine.py` |
| 收益与成本 | 全收益主口径、价格并列；单边 5/10/20 bp（买卖各一次，(1-c)²）；全收益与价格买入持有双基准；现金 0% | `engine/backtest/metrics.py`、`runner.py` |
| 报告 | 逐笔交易、持有分布、回撤、暴露、换手、最差交易、到期占比、开发/留出区间、逐年表现 | [docs/phase3c-backtest.md](docs/phase3c-backtest.md)、`data/backtest/candidate-a.json` |

**没有做**：候选 B、ETF 可执行回测、参数搜索、新增过滤、CLI 改动、`validated` 升级、正式买点。

## 2. 主结果（全收益、T+1、成本 10bp）

| 指数 | 回测区间 | 成交 | 累计 | 年化 | 最大回撤 | 暴露 | 到期退出 | 同期全收益基准 |
|---|---|---|---|---|---|---|---|---|
| 沪深300 | 2016-03-25 → 2026-11-11 | 5 | +120.34% | +7.71% | −21.95% | 25.6% | 40.0% | +64.11% |
| 中证500 | 2016-03-25 → 2026-11-11 | 6 | +19.34% | +1.68% | −35.44% | 44.1% | 66.7% | +28.16% |
| 中证1000 | 2019-10-18 → 2026-11-11 | 4 | +59.46% | +6.82% | −38.65% | 48.7% | 75.0% | +36.16% |

开发 / 留出（2021-01-01 为界）：沪深300 +78.03% / +23.77%；中证500 −6.30% / +27.37%；
中证1000 +27.58% / +24.99%。T+2 敏感性（10bp）：沪深300 +105.11%、中证500 +13.74%、
中证1000 +57.80%。

**怎么读**：三个指数只有 4–6 笔交易，方向不一致（中证500 开发区间为负），
到期退出占 40%–75%，T+1 与 T+2 之间差别不小。本批不做统计检验，也不判断规则好坏。

## 3. 数据（K1）

| 指数 | 发布日 | 序列起始 | 交易日 | 有 PE 天数 | 首个 PE |
|---|---|---|---|---|---|
| 沪深300 | 2005-04-08 | 2005-04-08 | 5246 | 3668 | 2011-06-28 |
| 中证500 | 2007-01-15 | 2007-01-15 | 4818 | 3669 | 2011-06-28 |
| 中证1000 | 2014-10-17 | 2014-10-17 | 2935 | 2935 | 2014-10-17 |

发布前的官方回溯行已全部丢弃；PE 自 2011-06 才提供，因此 5 年窗口最早 2016 年成立。
逐年数据质量表在报告中。

## 4. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/build_official_series.py     # 采集 9 条官方日频序列
.venv/bin/python tools/run_candidate_a_backtest.py  # 跑回测，生成 JSON 与报告
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

固定序列 ID（示例）：沪深300 官方收盘 `ser-0a043ec0defae285`、滚动 PE `ser-cbbf2d714e0e8c2a`、
全收益 `ser-a1211121f5c38cfa`；完整清单见 `data/series/manifest.json`。

## 5. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 382 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_backtest.py` | 21 | 周度观察点与 ISO 周、5 年窗口边界、当前周排除、覆盖不足、非正 PE、窗口未覆盖、公式复用；状态机入场/退出/到期/不可评估/样本末平仓、T+1 与 T+2；成本口径与曲线一致性；回撤与基准；序列发布日过滤；结果结构；措辞护栏；确定性 |
| 既有回归 | 361 | 阶段一/二/三首轮与批二全部保留 |

**未通过项：无。**

## 6. 缺口与待决定事项

| # | 事项 | 状态 |
|---|---|---|
| G-1 | 候选 B 需要日频 PB | 数据不足，未检验（不改成月度版、不用其他指标代替） |
| G-2 | ETF 可执行回测需要历史净值与分红 | 未做，另排批次 |
| G-3 | 估值公布时刻未知 | T+1 是假设，报告已写明 |
| G-4 | 官方滚动 PE 的加权方式与亏损处理 | 文件未说明，记为未知 |
| G-5 | 交易次数少 | 不做统计检验，不下规则好坏结论 |

是否把候选 A 提交为正式规则、是否给指数正式买点，属产品方向，由设计方与用户决定；
本批不默认选择。

## 7. 提交记录

| 提交 | 内容 |
|---|---|
| `PENDING` | **本轮**：候选 A 周度研究回测 + 21 项新回归 + 文档与入口 |
| `a5620ae` | 阶段三批二：事后诊断 + 数据可行性调查 |
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
| 本批实现报告（候选 A 回测） | [docs/phase3c-delivery.md](docs/phase3c-delivery.md) |
| 回测报告（自动生成，含逐笔交易） | [docs/phase3c-backtest.md](docs/phase3c-backtest.md) |
| 机器可读回测结果 | [data/backtest/candidate-a.json](data/backtest/candidate-a.json) |
| 序列清单（含官方日频系列） | [data/series/manifest.json](data/series/manifest.json) |
| 批二：事后诊断与数据可行性 | [docs/phase3b-delivery.md](docs/phase3b-delivery.md)、[docs/phase3b-diagnostics.md](docs/phase3b-diagnostics.md)、[docs/phase3b-feasibility.md](docs/phase3b-feasibility.md) |
| 阶段三首轮交付（已签收） | [docs/phase3-delivery.md](docs/phase3-delivery.md)、[docs/index-profile-spec.md](docs/index-profile-spec.md)、[docs/phase3-samples.md](docs/phase3-samples.md) |
| 个股研究估值（阶段二） | [docs/phase2-delivery.md](docs/phase2-delivery.md)、[docs/equity-model-spec.md](docs/equity-model-spec.md)、[docs/assumptions-and-sensitivity.md](docs/assumptions-and-sensitivity.md) |
| 验收清单（阶段一/二/三） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 示例请求与输出 | [examples/](examples/) |
