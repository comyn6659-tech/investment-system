# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第二批：**启用个股正式结论**（决策②批准 v1.1） |
| 代码版本 | `phase4-1.0.0` |
| 政策版本 | 个股 `equity-policy-v1.1`（`pol-6d1a5387780fe6a2`，`approved=true`，批准日 2026-11-12，复核日 2027-05-31）；v1.1 草案与 v1 保留 |
| 模型版本 | 个股政策结果 `validated-1.0.0`（FCFF / 股息折现）；其余路径 `research-0.4.0` |
| 模型状态 | 只有 `equity_dcf_fcff`、`equity_dividend_discount` 为 `validated`；银行股息、指数画像、候选 A、ETF 保持原状态 |
| 本轮实现提交 | `71d1beb` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `28f3fb6`（4a-R 入口，实现 `7ccf8f6`） |

## 1. 决策②与批准落地（P1、P2）

| 项 | 内容 |
|---|---|
| 决定 | A：按现状批准 `equity-policy-v1.1-draft`，安全边际 A 25% / B 40% 不变 |
| 批准文件 | `data/policies/equity-policy-v1.1.json`：`status=approved`、`approved=true`；参数与草案逐项相同（测试比对） |
| 批准记录 | `approval.approved_by=股东`、`approved_at=2026-11-12`、`decision=决策②`、`review_date=2027-05-31`、依据与已知缺口/局限 |
| 复核安排 | 无风险利率与 ERP 每半年更新（下一次 2027-05）；结论账本首次复盘 2027-05；规则每年复核；规则修改须重新批准 |
| 模型状态 | 只把政策实际使用的 FCFF / 股息折现改为 `validated`，并在模型中记录批准依据；测试断言 validated 集合恰好为这两个 |
| 不做 | 未改任何政策参数、未扩股票池、未改指数/ETF/回测、未做对话入口、未翻译条件名 |

`POLICY_VERSION = "equity-policy-v1.1"`；个股政策结果 `model_version="validated-1.0.0"`，
指数/ETF/研究路径仍报 `unapproved-phase2` / `research-0.4.0`，避免混淆。

## 2. 30 只正式结论（P3、P4）

| 结论 | 数量 | 标的 |
|---|---|---|
| 候选买点 | 0 | — |
| 等待 | 3 | 贵州茅台（B）、美的集团（A）、宁德时代（B） |
| 回避 | 4 | 海天味业（A）、双汇发展（A）、恒瑞医药（B）、中国移动（B） |
| 无法判断 | 23 | C 级、partial、路线不支持、资料不足 |

- **与试运行 v1.1 预演结论 30/30 一致，0 差异**；CLI 与试运行共用
  `engine.policy.trial.evaluate_company`（测试用 monkeypatch 证明）。
- 7 只正式结论的候选门槛：茅台 686.80、海天 13.06、双汇 11.21、美的 57.74、
  宁德 145.23、恒瑞 11.67、中国移动 46.09（元/股）。
- 格力电器等级 A 但状态 partial（勾稽 −30.2%），按 `undetermined`，不写账本。
- 逐只三档价值、现价、距门槛与原因见 [docs/phase4b-equity-conclusions.md](docs/phase4b-equity-conclusions.md)。

CLI 文本/JSON 均包含：结论、等级及含义（数据完整度与模型稳定性，不是公司质量）、
三档价值及用途、起约束条件、距候选门槛百分比、主要未满足条件、政策版本与快照 ID、
“不构成投资建议”。C / partial / 不支持 / 资料不足一律 `undetermined` 并给原因码。

## 3. 数据新鲜度护栏（P5）

| 检查 | 规则 | 结果 |
|---|---|---|
| 价格 | 价格日期距分析日 > 3 个交易日 | 30 只 `as_of=2026-11-11`、分析日 2026-11-12，全部通过 |
| 年报 | 分析日 ≥ 4 月 30 日应包含上一年年报 | 30 只最新年报均为 2025，全部通过 |
| 字段 | 政策所需价格/股本/年报字段 | 4 只金融类缺 2025 年报字段，但它们本来就不支持/资料不足，不出正式结论 |

三种情形各有测试；价格过期端到端测试结果为 `partial` / `undetermined` + `STALE_PRICE`。
规则写在批准文件的 `runtime_parameters.freshness`，**不属于投资参数**，不参与 P1 的逐参数比较。

## 4. 结论账本（P6）

文件：`data/ledger/equity-conclusions.jsonl`（`equity-conclusion-ledger/1`）。

- **首次写入 7 条** = 30 只中给出正式结论的数量；重复运行新增 0、跳过 7。
- 每条含：分析日期、代码、政策版本/哈希、快照 ID、价格及日期、三档价值、等级、
  安全边际、结论、起约束条件、内容哈希 `concl-…`。
- 同一标的 + 同一分析日 + 同一快照：内容一致跳过；内容不同报冲突；`undetermined` 不写。
- `.venv/bin/python -m engine.cli --check-conclusions` 可发现改写/重复，返回 0/1。

## 5. 未变化项（P7）

| 检查项 | 结果 |
|---|---|
| 指数信号 | 三只指数 2026-W45 仍为退出条件成立；沪深300 分位 67.72%；`decision=undetermined`、`model_status=research` |
| 指数账本 | `data/ledger/candidate-a-signals.jsonl` 仍 3 条，`--check` 0 问题 |
| ETF | 产品检查逻辑未改，既有测试全过 |
| 回测数据 | `data/backtest/candidate-a.json` 未改 |
| 旧项目 | `/Users/comyn/Documents/deepseek-harness/投资/valuation/` 未修改、未删除 |

## 6. 测试

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 480 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_equity_conclusion.py` | 21 | P1–P7：批准一致性、模型状态、30/30、CLI 字段、新鲜度三情形与端到端、账本追加/跳过/冲突/篡改、指数与回测不变 |
| `tests/test_equity_policy.py` | 55 | 4a-R 政策与试运行回归 |
| `tests/test_index_signal.py` | 17 | 指数信号与账本 |
| 既有回归 | 387 | 阶段一/二/三 |

## 7. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/run_equity_conclusions.py --date 2026-11-12   # 30 只结论 + 首次写账本
.venv/bin/python -m engine.cli '贵州茅台' --analysis-date 2026-11-12  # 单只 CLI（正式结论）
.venv/bin/python -m engine.cli --check-conclusions                    # 结论账本校验
.venv/bin/python tools/update_signal_ledger.py --check                # 指数信号账本校验
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 8. 与复核、复盘的关系

决策②已批准 v1.1；本轮只负责把批准落地为可运行的正式结论。按批准记录：
2027-05 首次复盘结论账本并更新无风险利率/ERP；规则每年复核；任何规则修改须重新批准；
不因单只股票结果改规则。已知缺口（v1.1 未经 Fable 定向复核）以结论账本 + 6 个月复盘补救。

## 9. 提交记录

| 提交 | 内容 |
|---|---|
| `71d1beb` | **本轮实现**：批准落地 + 模型 validated + CLI 正式结论 + 新鲜度 + 结论账本 + 30 只 |
| `28f3fb6` | 4a-R 交付入口（实现 `7ccf8f6`） |
| `3ad76cd` | 4a 收尾入口（实现 `3a910c0`） |
| `b7ef983` / `9bc31ac` | 第四阶段第一批（政策 v1 试运行 + 候选 A 信号与账本） |
| `1866e2b` / `106a9b8` | 第三阶段收尾（已签收） |

## 10. 详细材料

| 内容 | 文件 |
|---|---|
| 本轮实现与 P1–P8 验收报告 | [docs/phase4b-delivery.md](docs/phase4b-delivery.md) |
| 30 只正式结论表（自动生成） | [docs/phase4b-equity-conclusions.md](docs/phase4b-equity-conclusions.md) |
| 已批准政策文件 | [data/policies/equity-policy-v1.1.json](data/policies/equity-policy-v1.1.json) |
| v1.1 草案（保留） | [data/policies/equity-policy-v1.1-draft.json](data/policies/equity-policy-v1.1-draft.json) |
| 政策目录说明 | [data/policies/README.md](data/policies/README.md) |
| 结论账本 | [data/ledger/equity-conclusions.jsonl](data/ledger/equity-conclusions.jsonl)、[data/ledger/README.md](data/ledger/README.md) |
| 30 只运行结果（机器可读） | [data/policy-trial/conclusion-run.json](data/policy-trial/conclusion-run.json) |
| 个股政策试运行报告（v1.1） | [docs/phase4a-equity-policy-trial.md](docs/phase4a-equity-policy-trial.md) |
| 验收清单（阶段一/二/三 + L1–L8 + M1–M5 + N1–N9 + P1–P8） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 上一轮交付（4a-R） | [docs/phase4a-r-delivery.md](docs/phase4a-r-delivery.md) |
