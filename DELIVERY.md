# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第三阶段第三批收尾（R1 周度池口径、R2 分区间基准） |
| 代码版本 | `phase3-0.5.1` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `research-0.3.0`（个股 3 个 + 指数画像 1 个，全部 `research`） |
| 本轮提交 | `106a9b8` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮已签收 | `698bd50`（候选 A 回测）、`f60935b`（交付入口） |

## 1. 本轮完成范围（只做 R1、R2）

| 编号 | 问题 | 修复 | 结果 |
|---|---|---|---|
| R1 | 周度参考池取「每周最后一个交易日的值」，该日缺 PE 就让整周作废 | 参考池改为每周**最后一个正值**滚动市盈率；观察点自身仍取当日值，缺失或非正不给分位 | 沪深300 / 中证500 可评估周 545 → **547**，评估起点 2016-03-25 → **2016-03-11**；中证1000 不变 |
| R2 | 开发 / 留出区间没有各自的同期基准 | 两个区间分别计算同期全收益与价格买入持有基准及超额 | 两区间均有基准字段，可由序列复算（有回归） |

**三只指数的全部交易未变**：54 组口径组合（2 口径 × 2 时滞 × 3 成本 × 3 指数）逐笔比对为 0 处差异。
未做其他改动：未调阈值、未加指标、未改 CLI、未升级 `validated`。

## 2. 修正后的基准与分区间结果（K2 / K4）

全样本同期全收益买入持有：沪深300 **+73.87%**、中证500 **+41.39%**、中证1000 +36.16%
（修正前 64.11% / 28.16% / 36.16%——起点挪两周，基准变动 10–13 个百分点，
说明「策略 vs 全期买入持有」对起点很敏感，不宜当作主要证据）。

| 指数 | 区间 | 策略 | 同期全收益买入持有 | 超额 | 同期价格买入持有 | 超额 |
|---|---|---|---|---|---|---|
| 沪深300 | 开发 2016-03-11→2021-01-01 | +78.03% | +92.36% | −14.32% | +72.66% | +5.37% |
| 沪深300 | 留出 2021-01-01→2026-11-11 | +23.77% | −10.58% | +34.34% | −22.63% | +46.39% |
| 中证500 | 开发 | −6.30% | +21.78% | −28.08% | +15.58% | −21.88% |
| 中证500 | 留出 | +27.37% | +14.03% | +13.33% | +3.55% | +23.82% |
| 中证1000 | 开发 2019-10-18→2021-01-01 | +27.58% | +26.99% | +0.59% | +25.87% | +1.71% |
| 中证1000 | 留出 | +24.99% | +4.81% | +20.18% | −1.90% | +26.89% |

主结果（全收益、T+1、成本 10bp）：沪深300 5 笔 +120.34%、中证500 6 笔 +19.34%、
中证1000 4 笔 +59.46%；交易与退出原因未变。

## 3. 数据（K1，未变）

| 指数 | 发布日 | 序列起始 | 交易日 | 有 PE 天数 | 首个 PE |
|---|---|---|---|---|---|
| 沪深300 | 2005-04-08 | 2005-04-08 | 5246 | 3668 | 2011-06-28 |
| 中证500 | 2007-01-15 | 2007-01-15 | 4818 | 3669 | 2011-06-28 |
| 中证1000 | 2014-10-17 | 2014-10-17 | 2935 | 2935 | 2014-10-17 |

## 4. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/build_official_series.py     # 采集 9 条官方日频序列（本轮未重跑）
.venv/bin/python tools/run_candidate_a_backtest.py  # 跑回测，生成 JSON 与报告
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 5. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 387 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_backtest.py` | 26 | 原 21 项 + R1 三项（池内最后正值、观察点当日值、可评估周与起点对齐复验表）+ R2 两项（分区间基准字段与超额、基准可由序列复算） |
| 既有回归 | 361 | 阶段一/二/三首轮与批二全部保留 |

**未通过项：无。**

## 6. 与签收结论的关系

设计方判断未变：证据不足以作为正式买点规则，第一版指数部分不给正式买点，
规则冻结、作为研究信号展示并前向记录。本批只收尾口径，未改规则、未调参。

## 7. 提交记录

| 提交 | 内容 |
|---|---|
| `106a9b8` | **本轮**：R1/R2 收尾 + 5 项新回归 + 文档与入口 |
| `698bd50` | 阶段三批三：候选 A 周度研究回测 |
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
