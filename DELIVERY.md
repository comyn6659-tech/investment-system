# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段 **4a-R**：按 Fable 审查逐条决定修订个股政策（R1–R14）并重跑 30 只 |
| 代码版本 | `phase4-0.2.0` |
| 政策版本 | 引擎 `unapproved-phase2`；个股草案 `equity-policy-v1.1-draft`（`pol-0d399706138ab5ed`，`draft_unapproved`，未批准）；v1 文件保留 |
| 模型版本 | `research-0.4.0`（全部 `research`，未升级 `validated`） |
| 本轮实现提交 | `7ccf8f6` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `3ad76cd`（M1–M5 收尾入口）、`3a910c0`（收尾实现） |

## 1. 本轮范围：只做 R1–R14 + 重跑 30 只

| 编号 | 修订 | 结果 |
|---|---|---|
| R1 | FCFF 终值的新投资回报率基准/悲观取 WACC，与折现率同口径；股息路线仍取股权成本 | 万华基准价值 104.78 → 60.46；FCFF 终值 g 自动抵消 |
| R2 | 终值连续性护栏：默认路线、显式期 FCFF ≤ 0、终值占比 ≥ 90% | 万华、比亚迪三项全触发；中国建筑触发默认路线；均 partial / 无法判断 |
| R3 | 期末现金只用 `END_CCE`；`END_CASH_EQUIVALENTS` 还原为分项 | 30 只快照重建；茅台 1,264.3 亿元，非现金等价物代理 0 |
| R4 | 近似项分方向：只把可能让价值变低的计入等级；报告列出每股金额（§3.7） | 茅台非现金等价物只披露；少数股东 3.0% 计入 |
| R5 | 少数股东取消 15% 触发线 | 海康 5.8% 超 B 级上限 → C；茅台 3.0% → B |
| R6 | 股息路线同样做现金流勾稽，并加 3 年分红覆盖条件 | 片仔癀 261.2% → partial；福耀覆盖不足 → C |
| R7 | 第 1 年封顶 10%；稳态取 5 年与 3 年 CAGR 较低者；第 2–5 年线性过渡 | 万华第 1 年 28.9% → 10.0%；宁德 56.2% → 10.0% |
| R8 | 悲观情景加入近 5 年最低 EBIT / 净利率 | 7 只 A/B 的起约束条件逐只列出；比亚迪悲观价值 ≤ 0 不展示 |
| R9 | 资本权重与杠杆护栏改用净有息负债；报告 30/40/50% 敏感性（§3.6） | 中国建筑净债务 6,230 亿元、净权重 76.8%、WACC 3.49%；仅它在三档都触发 |
| R10 | 派息率用总额对总额（期末股数还原），修正送转失真；P6 选路使用同一口径 | 比亚迪 64.7% → 22.9%；路线 股息折现 → FCFF |
| R11 | 基期异常触发 partial / 无法判断；000858 更正后数据标 restated | 000858 状态 partial；`available_at=2026-04-30`，原值 609.45 亿元入备注 |
| R12 | 报告：分表、终值占比、起约束条件、门槛距离、读法、漂移、倍数表 | 报告 §1.1、§3.1/3.2、§3.3、§5、§6 全部落地 |
| R13 | 缺数据不得默认通过；营运资本/利息费用缺失逐只披露 | 中国移动缺 2016/2017 → A 不通过、B 通过 |
| R14 | 债务权重 < 1% 时债务成本显示「不适用（债务可忽略）」 | 9 只显示该文案，计算不变 |

**不做**：不改 ERP（5.14%）、不改无风险利率规则、不改安全边际 25%/40%、不增加银行数据、
不动指数与 ETF、不改账本、不升级 `validated`、不输出正式结论。

## 2. 试运行结果（30 只，v1 → v1.1）

| 维度 | v1 | v1.1 |
|---|---|---|
| 预演结论 | 候选 0、等待 7、回避 4、无法判断 19 | 候选 **0**、等待 3、回避 4、无法判断 23 |
| 确定性等级 | A 5、B 6、C 19 | A 4、B 4、C 22 |
| 状态 | complete 16、partial 2、insufficient 2、unsupported 10 | complete 11、partial 7、insufficient 2、unsupported 10 |

达到 A/B 级（可给预演结论）的 8 只：贵州茅台（B，等待）、海天味业（A，回避）、
双汇发展（A，回避）、美的集团（A，等待）、宁德时代（B，等待）、恒瑞医药（B，回避）、
中国移动（B，回避）；格力电器等级 A 但状态 partial（勾稽 −30.2%），按研究参考分表。

主要变化（逐只对照见报告 §7）：

- 万华化学：R1/R2 后基准价值 60.46 元、终值占比 117.6%、显式期 FCFF −612.1 亿元，
  三项护栏全触发 → 研究参考。
- 比亚迪：R10 修正派息率后改走 FCFF；终值占比 160.5%，R2 触发；R8 悲观价值 ≤ 0 → 研究参考。
- 海天味业 C → A：定期存款不再被当作受限资金计入等级（R3/R4）。
- 片仔癀 A → C：R6 股息路线也做勾稽，差额 261.2% → partial。
- 中国移动 A → B：R13 的 10 年窗口缺 2016/2017；福耀 A → C：R6 分红覆盖不足；
  海康 A → C：R5 少数股东差额 5.8%。
- 伊利 B → C：交叉检查分歧 44.1%（规则未改，FCFF 口径随 R1/R7 变化）。

**候选买点仍为 0**：按 v1.1 倍数表，A 级约需 6.2–14.6 倍、B 级约 5.0–11.7 倍市盈率。
这是严格度的正常结果，不作为验收条件；是否接受由股东在决策②判断。

## 3. N1–N9 验收

| 编号 | 结果 |
|---|---|
| N1 | R1–R14 均在政策文件有参数与依据；17 项 R 系列测试（含边界）；`test_policy_module_has_no_hardcoded_policy_numbers` |
| N2 | 万华 partial / C / 无法判断；三项 R2 原因码齐全 |
| N3 | 茅台 `END_CCE` 1,264.3 亿元、代理 0；等级 B、基准 1,144.66 元、候选门槛 686.80 元 |
| N4 | 手算测试通过；报告 §3.5 实测未触发 R2 的 FCFF 基准价值对 g ±0.5pp 最大变动 **0.49%**（< 1%；全部 FCFF 1.07%，仅比亚迪且已判不可用） |
| N5 | 比亚迪 64.7% → 22.9%，新旧并列；路线改为 FCFF |
| N6 | 报告满足 R12 全部条目（分表、终值占比、起约束、门槛距离、读法、漂移、倍数表；另有 §3.6 杠杆三档、§3.7 近似项明细） |
| N7 | 30 只全部有结果；报告 §7 共 30 行对照（状态、等级、三档价值、结论、原因） |
| N8 | 指数信号、账本、CLI、回测不变；账本 3 条 0 问题；CLI 仍 `research` / `undetermined`；**459 passed** |
| N9 | 本文件汇总、复现命令、提交 ID |

## 4. 未变化项（回归证据）

| 检查项 | 结果 |
|---|---|
| 指数候选 A 信号 | 三只指数 2026-W45 仍为退出条件成立；分位 67.72 / 68.90 / 62.01 |
| 账本 | `data/ledger/candidate-a-signals.jsonl` 仍 3 条；`update_signal_ledger.py --check` 0 问题 |
| CLI | 沪深300：`analysis_status=complete`、`model_status=research`、`decision=undetermined` |
| 回测数据 | `data/backtest/candidate-a.json` 未改 |
| 旧项目 | `/Users/comyn/Documents/deepseek-harness/投资/valuation/` 未修改、未删除 |
| ETF / 银行 | ETF 逻辑未改；两家银行仍资料不足 |

## 5. 数据

| 项目 | 内容 |
|---|---|
| 个股快照 | 30 只按 R3 重建，`as_of=2026-11-11`；manifest 30 条、一致性 0 问题 |
| 现金口径 | `cash_equivalents_end` 只来自 `END_CCE`；`cash_equivalents_component` 保留分项 |
| 000858 重述 | 2025 年一季度/中报/三季报 `restated=true`，`available_at=2026-04-30` |
| 重建幂等 | 重复运行产生相同快照 ID（已验证） |
| v1 对照 | `data/policy-trial/trial-report-v1.json`、`docs/phase4a-equity-policy-trial-v1.md` 保留 |
| 无风险利率 | `data/policy-trial/macro/cn-10y-yield.json`（120 个月末 + 最新值） |

## 6. 测试

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 459 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_equity_policy.py` | 55 | L1–L5 + M3/M4 + 17 项 R1–R14（终值与 g 抵消、三项护栏边界、END_CCE、近似项方向、少数股东、股息勾稽与覆盖、增长封顶/稳态/插值、最低利润率、净债务、派息率、重述、报告门槛、窗口缺口、债务成本显示） |
| `tests/test_index_signal.py` | 17 | 指数信号与账本（未改） |
| 既有回归 | 387 | 阶段一/二/三全部保留 |

## 7. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/rebuild_policy_trial_snapshots.py    # R3/R11 重建快照（幂等、不联网）
.venv/bin/python tools/run_equity_policy_trial.py           # 生成报告与 JSON
.venv/bin/python tools/update_signal_ledger.py --check      # 账本完整性
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 8. 与批准的关系

本轮只完成施工单 4a-R。之后：设计方验收 N1–N9（独立复算）→ Fable 只做定向复核
（只检查 F1–F8、F10 是否被 R1–R14 解决，以及修订是否引入新的同类问题，不再全面审查）→
带证据提请股东决策②。批准之前，系统仍不输出正式结论，`decision` 恒为 `undetermined`。

决策②需要股东决定的三件事：严格度（A 级约 6–15 倍、B 级约 5–12 倍市盈率才出现候选买点）、
已知局限（同一股权成本、ERP 与规范化利率口径、只能前向验证）、是否批准 v1.1 作为正式政策。

## 9. 提交记录

| 提交 | 内容 |
|---|---|
| `7ccf8f6` | **本轮实现**：政策 v1.1（R1–R14）+ 30 只快照重建 + 试运行重跑 + 报告/测试 |
| `3ad76cd` | M1–M5 收尾入口（记录 `3a910c0`） |
| `b7ef983` / `9bc31ac` | 第四阶段第一批（政策 v1 试运行 + 候选 A 信号与账本） |
| `1866e2b` / `106a9b8` | 第三阶段收尾（已签收） |
| `698bd50` / `a5620ae` / `2f824df` / `1bb379c` | 第三阶段各批（已签收） |
| `82b8d4c` / `317506e` / `5c4c1c9` 等 | 阶段二、阶段一（已签收） |

## 10. 详细材料

| 内容 | 文件 |
|---|---|
| 本轮实现与 N1–N9 验收报告（含 R1–R14 逐项、关键数字、数据重建） | [docs/phase4a-r-delivery.md](docs/phase4a-r-delivery.md) |
| 个股政策 v1.1 试运行报告（自动生成，含 v1→v1.1 逐只对照） | [docs/phase4a-equity-policy-trial.md](docs/phase4a-equity-policy-trial.md) |
| v1 试运行报告（对照，保留） | [docs/phase4a-equity-policy-trial-v1.md](docs/phase4a-equity-policy-trial-v1.md) |
| 政策文件 v1.1（每参数带依据） | [data/policies/equity-policy-v1.1-draft.json](data/policies/equity-policy-v1.1-draft.json) |
| 政策文件 v1（保留） | [data/policies/equity-policy-v1-draft.json](data/policies/equity-policy-v1-draft.json) |
| 政策目录说明（R1–R14 摘要） | [data/policies/README.md](data/policies/README.md) |
| 试运行机器可读结果 | [data/policy-trial/trial-report.json](data/policy-trial/trial-report.json) |
| v1 对照结果 | [data/policy-trial/trial-report-v1.json](data/policy-trial/trial-report-v1.json) |
| 快照重建工具 | [tools/rebuild_policy_trial_snapshots.py](tools/rebuild_policy_trial_snapshots.py) |
| 信号账本与规则 | [data/ledger/candidate-a-signals.jsonl](data/ledger/candidate-a-signals.jsonl)、[data/ledger/README.md](data/ledger/README.md) |
| 验收清单（阶段一/二/三 + L1–L8 + M1–M5 + N1–N9） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 上一轮交付（M1–M5） | [docs/phase4a-delivery.md](docs/phase4a-delivery.md) |
