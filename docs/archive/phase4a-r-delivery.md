# 第四阶段 4a-R 交付：个股政策 v1.1 草案（R1–R14）

日期：2026-11-12｜依据：设计方《Fable 审查意见的判断 + 个股政策 v1.1 草案修订 + 施工单 4a-R》
（`fable-review-judgement-and-batch4a-r.md`）｜审查对象：`3a910c0`（入口 `3ad76cd`），政策 v1
（`pol-239f088ae9552f1a`）

本文件只覆盖本轮 4a-R：按 R1–R14 修订政策、重建快照、重跑 30 只试运行、给出 N1–N9 证据。
不重新做全面审查，不改指数 / ETF / 账本 / CLI 的对外行为。

## 1. 范围与边界

| 项 | 内容 |
|---|---|
| 政策 | 新增 `equity-policy-v1.1-draft`（`pol-0d399706138ab5ed`，`draft_unapproved`）；v1 文件保留不删 |
| 修订 | 只做 R1–R14；不改 ERP（5.14%）、不改无风险利率规则、不改安全边际 25%/40%、不增加银行数据、不动指数与 ETF、不升级 `validated` |
| 试运行 | 30 只固定测试集全部重跑；新旧逐只对照见报告 §7 |
| 快照 | 30 只按 R3 重建（只用 `END_CCE`）；000858 按 R11 标 `restated` 并改 `available_at` |
| 对外行为 | CLI 仍 `research` / `undetermined`；候选 A 信号、账本、回测数据不变 |

## 2. R1–R14 实现与证据

| 编号 | 实现 | 关键结果 / 证据 |
|---|---|---|
| R1 | `engine/policy/valuation.py::terminal_spec` 按路线取新投资回报率：FCFF 基准/悲观 = WACC，股息 = 股权成本，乐观 = 近 5 年平均 ROE；政策 `terminal.reinvestment.new_investment_return` | FCFF 基准终值 = 末期 NOPAT × (1+g) ÷ WACC，g 抵消；万华基准价值 104.78 → 60.46 |
| R2 | `engine/policy/trial.py::continuity_guardrail_reasons`：默认路线、显式期 FCFF ≤ 0、终值占比 ≥ 90%，触发即 partial / C / 无法判断 | 万华三项全触发；比亚迪三项全触发；中国建筑触发默认路线；报告 §3.3 列出全部终值占比与距 90% 距离 |
| R3 | `tools/build_equity_snapshots.py` 映射改为 `END_CCE` → `cash_equivalents_end`；`END_CASH_EQUIVALENTS` → `cash_equivalents_component`；`tools/rebuild_policy_trial_snapshots.py` 重建 30 只 | 茅台 `END_CCE` 1,264.3 亿元、分项 84.7 亿元；「非现金等价物货币资金」= 0（原 432.2 亿元） |
| R4 | `trial.py::_approximations`：`direction=counted/disclose_only`；计入等级只取可能让价值变低的项 | 茅台非现金等价物 0 元/股（只披露）；少数股东 34.39 元/股（计入，占 3.0%） |
| R5 | 取消 15% 触发线；对所有标的计算同市盈率隐含值与账面值差额，隐含值高于账面才计入 | 茅台 3.0%、宁德 3.5%、海康 5.8%、万华 6.6%；海康因此 C |
| R6 | 现金流勾稽对所有路线执行；股息路线加 3 年「分红总额 ≤ 经营现金流 − 资本开支」条件，不满足为 C | 片仔癀勾稽 261.2% → partial / C；福耀分红覆盖不足 → C；中国移动覆盖 17.4% 通过 |
| R7 | 第 1 年封顶 10%；稳态 = min(10%, 5 年 CAGR, 3 年 CAGR)；第 2–5 年线性过渡；报告列出 5 年/3 年 CAGR | 万华第 1 年 28.9% → 10.0%、稳态 7.1%；宁德 56.2% → 10.0% |
| R8 | 悲观情景 FCFF 的 EBIT 利润率取近 5 年最低；股息路线基期 EPS × min(1, 5 年最低净利率 ÷ 基期净利率)；报告写明起约束条件 | 30 只中 7 只 A/B 的起约束条件全部列出；比亚迪悲观价值 ≤ 0，按护栏不展示 |
| R9 | 资本权重与杠杆护栏改用净有息负债；净现金公司 WACC = 股权成本；报告 30/40/50% 敏感性 | 中国建筑净债务 6,230 亿元、净权重 76.8%、WACC 3.49%；格力净债务权重 22.1% |
| R10 | 派息率 = 每股派息 × 期末股数 ÷ 归母净利润；股数 = 期末股本 ÷ 面值（面值由基准年推出），剔除特别分红 | 比亚迪三年平均 64.7% → 22.9%；主模型由股息折现改为 FCFF（R6/R10 后重新判定） |
| R11 | 基期营收同比 > 40% 或归母净利润 > 60% 触发 partial / 无法判断；000858 中期数据标 `restated=true`、`available_at=2026-04-30`，备注保存原值 609.45 亿元 | 000858 状态 partial、结论无法判断；清单仍披露 −54.6% / −71.9% |
| R12 | 报告改造：可结论/仅研究参考分表、终值占比、起约束条件、门槛距离（<10% 标「临界」）、等级与「回避」含义、删除错误表述、无风险利率漂移、候选买点倍数表 | `docs/phase4a-equity-policy-trial.md` §1.1、§3.1–3.5、§5、§6 |
| R13 | 亏损年数、经营现金流/利润、分红覆盖窗口缺年份即判不通过并列出；营运资本、利息费用缺失逐只披露 | 中国移动缺 2016/2017 → A 级不通过、B 级通过；报告 §2 与 §3.4 列出缺口 |
| R14 | 债务权重 < 1% 时债务成本显示「不适用（债务可忽略）」，计算不变 | 9 只显示该文案（茅台、五粮液、海天、片仔癀、福耀、海康、宁德、恒瑞、大秦铁路） |

## 3. 试运行结果（30 只）

| 维度 | v1（`3a910c0`） | v1.1（本轮） |
|---|---|---|
| 预演结论 | 候选 0、等待 7、回避 4、无法判断 19 | 候选 **0**、等待 3、回避 4、无法判断 23 |
| 确定性等级 | A 5、B 6、C 19 | A 4、B 4、C 22 |
| 状态 | complete 16、partial 2、insufficient 2、unsupported 10 | complete 11、partial 7、insufficient 2、unsupported 10 |

达到 A/B 级的 8 只：贵州茅台（B，等待）、海天味业（A，回避）、双汇发展（A，回避）、
美的集团（A，等待）、宁德时代（B，等待）、恒瑞医药（B，回避）、中国移动（B，回避）；
另有格力电器等级 A 但状态 partial（勾稽 −30.2%），按研究参考分表、不给结论。

**变化不是“变好/变差”，而是规则变严后的事实**：

- 万华化学：R1/R2 后基准价值 60.46 元、终值占比 117.6%、显式期 FCFF −612.1 亿元，
  三项护栏全触发 → 研究参考。
- 比亚迪：R10 修正派息率后改走 FCFF；显式期 FCFF 为负、终值占比 160.5%，
  R2 触发；R8 悲观价值 ≤ 0，按护栏不展示 → 研究参考。
- 海天味业 C → A：旧版把定期存款当受限资金扣除，R3/R4 后不再计入等级。
- 片仔癀 A → C：R6 让股息路线也做勾稽，勾稽差额 261.2% → partial。
- 中国移动 A → B：R13 的 10 年亏损窗口缺 2016/2017，A 级不通过；B 级 7 年窗口完整。
- 福耀玻璃 A → C：R6 的 3 年分红覆盖条件不满足。
- 海康威视 A → C：R5 取消少数股东触发线后，同市盈率差额 5.8% 超过 B 级 5%。
- 伊利股份 B → C：交叉检查分歧 44.1% 超过 B 级 40%（规则未改，FCFF 口径随 R1/R7 变化）。

**候选买点仍为 0**：按 v1.1 重算的倍数表，A 级约需 6.2–14.6 倍、B 级约 5.0–11.7 倍市盈率；
这是政策严格度的正常结果，不是本轮验收条件。是否接受由股东在决策②判断。

## 4. N1–N9 验收证据

| 编号 | 通过标准 | 证据 |
|---|---|---|
| N1 | R1–R14 每项在政策文件中有参数与依据，代码无散落数字；每项至少一个测试（含边界） | 政策 v1.1 每节点带 `basis`/`source`；`test_policy_module_has_no_hardcoded_policy_numbers`；R1–R14 对应测试 17 项（见 §6） |
| N2 | 万华化学状态 partial、无法判断，原因码说明 R2 哪一条触发 | 状态 partial、等级 C、结论 undetermined；原因码 `TERMINAL_GUARDRAIL_BY_DEFAULT`、`..._EXPLICIT_CASH`、`..._TERMINAL_SHARE` 三项全部出现 |
| N3 | 茅台现金取 `END_CCE`、受限代理为 0；报告列出新等级与新价值 | 期末现金 1,264.3 亿元；非现金等价物 0；等级 B（少数股东 3.0% 计入），基准价值 1,144.66 元、候选门槛 686.80 元 |
| N4 | R1 后 FCFF 基准价值对终值增长 ±0.5pp 变化 < 1%（手算 + 报告） | 手算 `test_r1_fcff_terminal_roi_is_wacc_and_growth_cancels`；报告 §3.5 实测最大变动 **0.49%**（未触发 R2 的 FCFF 标的；全部 FCFF 为 1.07%，仅比亚迪，其值已因终值占比 160.5% 触发 R2） |
| N5 | 比亚迪派息率总额口径，新旧并列；路线按新值重判 | 22.9%（v1 64.7%），报告 §7 并列；路线 股息折现 → FCFF，状态 partial |
| N6 | 报告满足 R12 全部条目 | 报告 §1.1、§3.1/3.2 分表、§3.3 终值占比、§5 起约束、§6 倍数表、读法；`test_r12_report_presentation` |
| N7 | 30 只全部有结果；新旧逐只对照表含状态、等级、三档价值、结论、变化原因 | 报告 §7 共 30 行；`test_trial_covers_all_thirty_codes` |
| N8 | 指数、账本、CLI、回测不变；全部测试通过 | `update_signal_ledger.py --check` 0 问题（3 条）；CLI 沪深300 仍 `research` / `undetermined`；指数/ETF 测试全过；**459 passed** |
| N9 | DELIVERY.md 汇总、复现命令、提交 ID | 项目根 `DELIVERY.md` |

## 5. 数据重建（R3 / R11）

| 项 | 内容 |
|---|---|
| 重建方式 | `tools/rebuild_policy_trial_snapshots.py`：以既有快照的原始事实为输入，改字段映射与时点标记，重算内容哈希；不联网、不引入新数据，保证 v1 → v1.1 的差异只来自政策 |
| 数量 | 30 只全部重建；`manifest.json` 30 条，`check_consistency()` 0 问题 |
| 现金字段 | `cash_equivalents_end` 只来自 `END_CCE`；`cash_equivalents_component` 保留分项，不参与估值 |
| 000858 | 2025-03-31 / 06-30 / 09-30 的事实 `restated=true`，`available_at=2026-04-30`；2025 三季报营收备注原值 609.45 亿元 |
| 幂等 | 重复运行产生相同快照 ID（已验证两次运行一致） |
| v1 对照 | `data/policy-trial/trial-report-v1.json`、`docs/phase4a-equity-policy-trial-v1.md` 保留，未被覆盖 |

## 6. 测试

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 459 passed
```

| 文件 | 项数 | 本轮新增 |
|---|---|---|
| `tests/test_equity_policy.py` | 55 | R1–R14 共 17 项：R1 终值与 g 抵消、R2 三项边界、R3 快照口径、R4 方向、R5 取消触发线、R6 股息勾稽与覆盖、R7 封顶/稳态/插值、R8 最低利润率、R9 净债务与净现金、R10 比亚迪派息率、R11 阈值边界与重述、R12 报告与 10% 临界、R13 窗口缺口、R14 显示 |
| `tests/test_index_signal.py` | 17 | 未改 |
| 既有回归 | 387 | 阶段一/二/三全部保留 |

## 7. 复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/rebuild_policy_trial_snapshots.py    # R3/R11 重建快照（幂等、不联网）
.venv/bin/python tools/run_equity_policy_trial.py           # 生成报告与 JSON
.venv/bin/python tools/update_signal_ledger.py --check      # 账本完整性
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 8. 未做与已知局限

- 不改 ERP 与规范化人民币利率的搭配口径（审查认为有道理但不确定），作为决策②材料。
- 不改有息负债科目（短期融资券、永续债仍未计入，F12）；只披露。
- 不改股本生效日（F11）；本批快照日未发现不复权价格与股本错配。
- 不做银行资本与资产质量数据；两家银行仍资料不足。
- 不重新做全面审查；Fable 只做定向复核（F1–F8、F10 是否被 R1–R14 解决）。
- 未升级 `validated`，未输出正式结论；`decision` 仍为 `undetermined`。

## 9. 提交

见 `DELIVERY.md` 的提交记录（实现提交 + 入口提交）。
