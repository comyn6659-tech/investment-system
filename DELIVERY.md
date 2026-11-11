# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第三阶段首轮收尾（W1 / W2 / E1 三项边界修复） |
| 代码版本 | `phase3-0.3.1` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `research-0.3.0`（个股 3 个 + 指数画像 1 个，全部 `research`） |
| 本轮提交 | `2f824df` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮提交 | `1bb379c`（阶段三首轮主体）、`37a2659`（交付入口） |

## 1. 本轮完成范围

只修复复验报告的三项边界问题（W1 / W2 / E1），未扩大重构、未改投资政策、未开始回测。

| 编号 | 问题 | 修复位置 | 修复后 |
|---|---|---|---|
| W1 | 先按月取最后值、再过滤非正 PE，丢掉整月有效观测 | `engine/series/window.py` | 先过滤有效性再按月取最后一条；反例由「53 月 / 无分位」变为「60 月 / 50.0%」 |
| W2 | 官方短序列未受画像 `as_of` 限制，展示未来观测 | `engine/series/profile.py` | 只展示 `date <= as_of` 的记录；追加的未来记录计入 `excluded_future_observations`，不再展示 |
| E1 | ETF 直接沿用缓存折溢价，未校验当前价格/NAV/收盘状态 | `engine/reporting/product_check.py` | 使用处统一核验并**重算**，缓存只作一致性核对；四个变体均不再输出错误旧值 |

能力清单（本轮之前已交付，未改动）：版本化指数序列、60 个月窗口与分位、
指数描述性画像、ETF 产品资料检查、CLI/JSON 同语义出口。
交易回测、A/B 进出状态机、目标点位仍**未实现**，本轮不做。

真实样本：沪深300 / 中证500 / 中证1000 与 510300 / 510500 / 512100 全部
`complete / research / undetermined`，数字与修复前一致。

## 2. 反例修复证据（复验脚本 6 个 FAIL → PASS）

复验脚本（只读，内存构造反例）现在的输出：

```text
PASS W1 月内最后有效值: {"expected_pool": 60, "actual_pool": 60, "percentile": 50.0, "reason": null}
PASS W2 官方序列日期上限: {"as_of": "2026-11-10", "latest": {"date": "2026-11-10", "value": 13.82}, "export": "accepted"}
PASS E1 price_changed:      {"premium_pct": 20.886825753729354, "expected_pct": 20.886825753729354}
PASS E1 nav_period_changed: {"premium_pct": null, "status": "partial"}
PASS E1 intraday:           {"premium_pct": null, "status": "partial"}
PASS E1 price_missing:      {"premium_pct": null, "status": "partial"}
SUMMARY: 0 failed checks; groups W1 / W2 / E1
```

| 变体 | 修复前 | 修复后 |
|---|---|---|
| 月中有效、月末 PE=−1（7 个月） | 53 月、分位 `null` | **60 月、分位 50.0%** |
| 官方序列追加 2026-11-11=999 | 展示 999（晚于 `as_of`） | 展示 2026-11-10 / 13.82，未来记录计数为 1 |
| ETF 价格改为 5、NAV 4.1361 | 仍输出 −0.026595% | 重算 **+20.886826%**，`cache_consistent=false` |
| NAV 事实 period 改 11-09、`nav_date` 仍 11-10 | 仍输出旧值 | `rejected`（日期不一致） |
| `bar_status` 改 `intraday` | 仍输出旧值 | `rejected`（不是已收盘价） |
| 价格 missing、保留日期 | 仍输出旧值 | `rejected`（价格缺失） |

**正常样例不变**：三指数 PE/PB 分位 75.8/39.2、75.0/76.7、65.0/34.2（参考池 60 个月），
官方短历史 20 条、最新 2026-11-10；三 ETF 折溢价 −0.026595% / −0.222890% / −0.209143%；
全部 `complete / research / undetermined`。

## 3. 对施工单的逐项答复

| 施工单要求 | 本轮做法 | 证据 |
|---|---|---|
| W1 每月最后一个**有效**观测 | 先过滤有效性、再按月取最后一条；当前值非正仍拒算 | [phase3-delivery.md](docs/phase3-delivery.md) §1.1、`tests/test_index_profile.py` |
| W2 官方短序列日期上限 | 只展示 `date <= as_of`；全在未来时报缺口 | 同上 |
| E1 派生折溢价出口核验 | 使用处统一核验并重算，缓存仅作一致性核对 | 同上 |
| 正常样例与 research/undetermined 不变 | 数字逐项比对一致，未升级 `validated` | §2 末段 |
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
# 342 passed in 3.48s

# 复验脚本（只读）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  /Users/comyn/Documents/Codex/2026-11-10/users-comyn-documents-deepseek-harness-valuation/outputs/phase3-review-checks.py
# SUMMARY: 0 failed checks; groups W1 / W2 / E1   （退出码 0）
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_index_profile.py` | 30 | 分位手算与并列值、窗口边界、每月去重、当前值排除、覆盖不足、非正 PE、未来日期、序列哈希与重放；**W1 三例、W2 三例** |
| `tests/test_etf_product.py` | 26 | 同日折溢价与跨日拒算、NAV 缺失/非正、停复牌与价差缺失、20 日均额天数、指数/ETF 画像一致、出口护栏；**E1 六例** |
| 既有回归 | 286 | 个股研究估值（阶段二）与阶段一契约/快照/身份 |

**未通过项：无。** 本轮新增 12 项回归，覆盖三项反例及其正例边界；
正常样例数字与 `research/undetermined` 状态保持不变。

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
| `2f824df` | **本轮**：W1/W2/E1 修复 + 12 项新回归 + 文档与入口 |
| `1bb379c` | 阶段三首轮：指数画像 + ETF 产品资料检查 + 24/20 项新回归 |
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
