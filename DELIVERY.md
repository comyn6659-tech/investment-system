# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第三阶段首轮（宽基指数估值画像 + 指定 ETF 产品资料检查） |
| 代码版本 | `phase3-0.3.0` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `research-0.3.0`（个股 3 个 + 指数画像 1 个，全部 `research`） |
| 本轮提交 | `PENDING`（见 §7） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮已签收提交 | `317506e`（S1/S2）、`82b8d4c`（交付入口） |

## 1. 本轮完成范围

| 能力 | 状态 | 位置 |
|---|---|---|
| 版本化指数估值序列（月度长序列 + 官方短序列，内容哈希） | 已实现 | `engine/series/models.py`、`data/series/` |
| 60 个月窗口、每月去重、当前值排除、并列值分位 | 已实现 | `engine/series/window.py` |
| 指数描述性画像（PE/PB 分开，不拼综合分） | 已实现 | `engine/series/profile.py` |
| ETF 产品资料完整性检查 + 事后同日折溢价 | 已实现 | `engine/reporting/product_check.py` |
| CLI 文本/JSON 同语义、出口护栏 | 已实现 | `pipeline.py`、`validation.py`、`text_summary.py` |
| 交易回测、A/B 进出状态机、目标点位 | **未实现（本轮不做）** | — |

真实样本：沪深300 / 中证500 / 中证1000 三只指数与 510300 / 510500 / 512100 三只 ETF
全部 `complete / research / undetermined`。

## 2. 对施工单的逐项答复

| 施工单要求 | 本轮做法 | 证据 |
|---|---|---|
| §3 月度描述性画像（60 个完整自然月、每月一条、当前值不入池、≥54 个月） | 按口径实现为纯函数；覆盖不足时不给分位 | [index-profile-spec.md](docs/index-profile-spec.md) §2、`tests/test_index_profile.py` |
| §3.2 分位公式（含并列值、原始值、展示才取整、PE/PB 分开） | 严格按公式实现；PE≤0 不入池、也不当当前值 | 同上 §3；`test_percentile_hands` 系列 |
| §3.3 口径与可追溯性（来源/原字段/聚合/分母/亏损处理/日期/抓取/修订/哈希/转换） | 逐条写入序列文件；unknown 就写 unknown | `data/series/ser-*.json`、§5 |
| §4 指数报告五项内容 | 点位与日期、PE/PB 当前值与日期、分位/样本/区间/中位数、来源限制、用途声明 | [phase3-samples.md](docs/phase3-samples.md) |
| §5 ETF 报告（跟踪对象/价格时刻/净值披露/费率/成交与天数/价差与停复牌/不设门槛） | 全部输出；取不到的逐条列为缺失 | `docs/phase3-delivery.md` §3 |
| §5 折溢价授权（同日、事后命名、不得用于未来回测） | 仅在价格=净值同一估值日期时计算，出口再核一次 | `test_cross_day_premium_is_refused` 等 |
| §6 同一指数两个入口一致 | 单一 `_index_profile()`，ETF 原样嵌入 | `test_index_profile_is_identical_from_index_and_etf_entry` |
| §7 I1–I6 | 见 §5 与验收清单 | [acceptance-checklist.md](docs/acceptance-checklist.md) |
| §8 交付方式 | 本文件即入口，只返回本路径 | 本文件 |

## 3. 真实示例与可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system

# 指数画像（三只首批指数）
.venv/bin/python -m engine.cli '沪深300'
.venv/bin/python -m engine.cli '中证500'
.venv/bin/python -m engine.cli '中证1000'
.venv/bin/python -m engine.cli --json '沪深300'

# ETF：产品资料 + 关联同一份指数画像
.venv/bin/python -m engine.cli '510300'
.venv/bin/python -m engine.cli '510500'
.venv/bin/python -m engine.cli '512100'

# 用请求文件复现示例输出
.venv/bin/python -m engine.cli --json --request examples/requests/index_000300_profile.json
.venv/bin/python -m engine.cli --json --request examples/requests/etf_510500.json
.venv/bin/python tools/render_examples.py          # 重新生成 examples/outputs/
.venv/bin/python tools/render_phase3_report.py     # 重新生成 docs/phase3-samples.md

# 重新采集序列与 ETF 快照（需要网络）
.venv/bin/python tools/build_index_series.py
.venv/bin/python tools/build_snapshots.py --only etf --as-of 2026-11-10
.venv/bin/python tools/prune_snapshots.py
```

关键数字（2026-11-10，详见 [phase3-samples.md](docs/phase3-samples.md)）：

| 指数 | 点位 | PE（滚动市盈率） | PE 分位 | PB | PB 分位 | 样本 |
|---|---|---|---|---|---|---|
| 沪深300 | 4,059.11 | 12.65 | 75.8% | 1.32 | 39.2% | 60/60 |
| 中证500 | 6,645.89 | 22.72 | 75.0% | 2.05 | 76.7% | 60/60 |
| 中证1000 | 6,579.36 | 25.58 | 65.0% | 2.06 | 34.2% | 60/60 |

| ETF | 跟踪 | 价格（状态） | 净值（日期） | 同日折溢价 | 近 20 日均额 |
|---|---|---|---|---|---|
| 510300 | 000300 | 4.135（closed） | 4.1361（2026-11-10） | −0.027% | 87.34 亿元（20 天） |
| 510500 | 000905 | 6.670（closed） | 6.6849（2026-11-10） | −0.223% | 51.67 亿元（20 天） |
| 512100 | 000852 | 2.672（closed） | 2.6776（2026-11-10） | −0.209% | 51.86 亿元（20 天） |

固定数据 ID：

- 指数序列（月度）：PE `ser-8d315ba2ab78c6ec`（000300）、PB `ser-370e315c2b1150f2`（000300）等，
  完整清单见 `data/series/manifest.json`；
- 官方短序列：`ser-fb818db17e36a9a4`（000300，`daily_short`）；
- 快照：`snap-6e6d5c31a0371ee0`（000300）、`snap-1a5ed1b1f7206c14`（000905）、
  `snap-5d60fe1e7fb6ed3b`（000852）、`snap-d6570cf8645d49e4`（510300）、
  `snap-5a28f88d0e6bbf4f`（510500）、`snap-429b5c94870b9db2`（512100）。

## 4. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 330 passed in 3.72s
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_index_profile.py` | 24 | 分位手算与并列值、窗口边界、每月去重、当前值排除、覆盖不足、非正 PE、未来日期、序列哈希与重放 |
| `tests/test_etf_product.py` | 20 | 同日折溢价与跨日拒算、NAV 缺失/非正、停复牌与价差缺失、20 日均额天数、指数/ETF 画像一致、出口护栏 |
| 既有回归 | 286 | 个股研究估值（阶段二）与阶段一契约/快照/身份 |

**未通过项：无。** 阶段一/二断言只做了「模型已实现」后的语义更新（指数/ETF 由
`unsupported` 变为 `complete` + `research`），硬约束未放宽。

## 5. 数据缺口及其分别的影响

| 缺口 | 对描述统计 | 对未来策略研究 | 对可执行回测 |
|---|---|---|---|
| 历史发布时间/修订版本未知 | 不影响本次分布统计 | 需另行核实时点可得性 | **阻断**（会引入未来信息） |
| 月度采样（非周度） | 已按批准口径完成 | 周度策略缺数据，禁止插值扩频 | **阻断**周度候选 |
| 聚合方法/分母/亏损处理 unknown | 只能同源同列比较 | 需要供应商说明 | 口径不明不宜作成交依据 |
| 停复牌与盘口价差未取得 | 已列为缺失 | 影响流动性假设 | **阻断**可成交性判断 |
| 净值披露时刻未知 | 折溢价只能事后计算 | — | **阻断**当日成交 |
| 官方指标仅 20 个交易日 | 只作并列事实 | 不能用于长历史 | 不能用于长历史 |
| 非首批宽基无序列 | 明确返回 `insufficient_data` + 缺口 | 需补序列 | — |

## 6. 尚未解决与需要设计方决定的事项

| # | 事项 | 现状 |
|---|---|---|
| D1 | 周度样本是否可获得、是否需要更换来源 | 本轮按批准口径用月度；周度仍缺，未用插值替代 |
| D2 | 供应商口径（聚合方法/分母/亏损处理） | 来源未说明，记为 unknown；是否需要付费或官方来源由设计方决定 |
| D3 | 分位阈值与进出规则 | 本轮**不实现**；阈值未批准，不生成任何信号 |
| D4 | 历史可交易性与修订版本 | 未取得；未来做回测前必须解决 |
| D5 | ETF 停复牌、盘口、IOPV、跟踪误差数据源 | 本轮未取得；如需高溢价/流动性门槛，先解决数据 |
| D6 | 个股正式参数审批（P1–P13） | 仍为研究候选，未升级 |

## 7. 提交记录

| 提交 | 内容 |
|---|---|
| `PENDING` | **本轮**：指数画像 + ETF 产品资料检查 + 24/20 项新回归 + 文档与入口 |
| `82b8d4c` | 交付入口（阶段二） |
| `317506e` | S1 / S2 修复（阶段二收尾，已签收） |
| `a8c8f7a` | 报告补业务构成 |
| `b2d2d36` / `978e968` / `5455985` / `5c4c1c9` | 阶段二首轮与阶段一 |

历史交付报告保留在 `docs/` 中，本文件始终指向最新一轮。

## 8. 详细材料

| 内容 | 文件 |
|---|---|
| 本轮实现报告 | [docs/phase3-delivery.md](docs/phase3-delivery.md) |
| 指数画像口径与窗口定义 | [docs/index-profile-spec.md](docs/index-profile-spec.md) |
| 真实样例与序列 ID（自动生成） | [docs/phase3-samples.md](docs/phase3-samples.md) |
| 验收清单（阶段一/二/三） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 个股研究估值交付（阶段二） | [docs/phase2-delivery.md](docs/phase2-delivery.md) |
| 个股模型规格 | [docs/equity-model-spec.md](docs/equity-model-spec.md) |
| 参数来源与敏感性 | [docs/assumptions-and-sensitivity.md](docs/assumptions-and-sensitivity.md) |
| 数据覆盖调查 | [docs/data-coverage.md](docs/data-coverage.md) |
| 序列与快照目录规则 | [data/snapshots/README.md](data/snapshots/README.md)、[data/series/manifest.json](data/series/manifest.json) |
| 示例请求与输出 | [examples/](examples/) |
