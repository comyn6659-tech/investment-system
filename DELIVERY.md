# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第一批（A. 个股政策 v1 草案试运行；B. 指数候选 A 研究信号与账本） |
| 代码版本 | `phase4-0.1.0` |
| 政策版本 | 引擎 `unapproved-phase2`；个股草案 `equity-policy-v1-draft`（`draft_unapproved`，未批准） |
| 模型版本 | `research-0.4.0`（个股 3 个 + 指数画像 + 候选 A 研究信号，全部 `research`） |
| 本轮提交 | `9bc31ac`（实现 + 数据 + 文档） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮已签收 | `1866e2b`（第三阶段关闭）、`106a9b8`（R1/R2 收尾） |

## 1. 本轮完成范围

| 编号 | 内容 | 结果 |
|---|---|---|
| A | 按设计方草案实现 `equity-policy-v1-draft`：规范化无风险利率、股权风险溢价、取消公司特定溢价、增长与终值规则、终值再投资一致性、机械情景、近似项影响、确定性等级、结论映射 | 政策文件集中版本化；代码无散落阈值；结论只写试运行报告的「预演结论」字段 |
| A | 30 只固定测试集试运行（尽量 10 个财年、至少 7 个） | 30/30 有结果；28 只 11 年、1 只 12 年、1 只 16 年；无低于 10 年 |
| A | 试运行报告：每只路线、状态、三档价值、近似影响、等级与未满足条件、安全边际、预演结论、折现率/终值 ±0.5pp 敏感性；全集分布；安全边际 20/25/30% 与 35/40/45% 敏感性；600519 两模型分歧 | [docs/phase4a-equity-policy-trial.md](docs/phase4a-equity-policy-trial.md) |
| B | 指数报告新增「候选 A 研究信号」：最新周度观察点、官方滚动 PE、5 年周度分位、入场/退出条件、回测摘要与链接 | 三只指数均有信号；`decision` 仍为 `undetermined` |
| B | 只追加信号账本 + 更新/校验命令 | `data/ledger/candidate-a-signals.jsonl`，首次写入 3 条（2026-W45） |

**不做**：未输出正式结论；未改 CLI 的 `research` / `undetermined`；未升级 `validated`；未调指数阈值（20/60/12 个月不变）；未做 ETF 回测；未改第二阶段研究估值数值路径。

## 2. A. 试运行结果摘要

| 维度 | 分布（30 只） |
|---|---|
| 预演结论 | 候选买点 **0**、等待 7、回避 4、无法判断 19 |
| 确定性等级 | A **5**、B **6**、C 19 |
| 状态 | complete 16、partial 2、insufficient_data 2、unsupported 10 |
| 企业族 | 非金融 18、明显周期 6、困境 2、银行 2、保险 1、券商 1 |

达到 A/B 级的 11 只（安全边际 A 25% / B 40%）：

| 代码 | 名称 | 等级 | 预演结论 | 基准价值 | 现价 |
|---|---|---|---|---|---|
| 600519 | 贵州茅台 | B | 等待 | 1,227.36 | 1,402.30 |
| 000895 | 双汇发展 | A | 回避 | 15.30 | 24.24 |
| 600436 | 片仔癀 | A | 回避 | 43.81 | 130.24 |
| 600660 | 福耀玻璃 | A | 等待 | 57.91 | 55.75 |
| 002415 | 海康威视 | A | 等待 | 28.97 | 34.25 |
| 600941 | 中国移动 | A | 等待 | 90.85 | 99.93 |
| 600887 | 伊利股份 | B | 回避 | 20.66 | 30.61 |
| 000333 | 美的集团 | B | 等待 | 92.30 | 87.68 |
| 600309 | 万华化学 | B | 等待 | 104.78 | 69.90 |
| 300750 | 宁德时代 | B | 等待 | 357.29 | 246.66 |
| 600276 | 恒瑞医药 | B | 回避 | 19.03 | 48.22 |

**候选买点为 0**：政策给的 25%/40% 下没有标的同时满足「P ≤ V_基准 ×(1−M)」与「P ≤ V_悲观」。
安全边际若一律按 20–30% 计，600309、300750 会进入候选买点；按 35–45% 计则为 0。
候选买点数量不作为验收条件，按事实交付。

折现率：规范化无风险利率 **2.7629%**（最新 1.6039% 与近 10 年月末均值 2.7629% 取高者），
股权风险溢价 5.14%，股权成本 **7.9029%**。600519 两模型：FCFF 1,227.36、股息折现 1,087.25，
分歧 **−11.42%**（第二阶段约 24%，终值再投资一致性规则缩小了分歧）。

C 级 19 只的主要原因是：路线不支持 10（周期 6、困境 2、保险 1、券商 1）、银行缺风险证据 2、
交叉检查分歧 3、近似项影响 2、杠杆条件 1、多项同时不满足 1（中国建筑）。

**关键异常（报告 §5.1）**：601668 中国建筑账面有息负债 9,405 亿元、权益市值 1,884 亿元，
债务权重 83.3%，WACC 3.11%，FCFF 价值 93.17 元/股（现价 4.56 元）。这不是数据错误，
是「债务权重用账面值 + 债务成本下限为无风险利率」的机械结果；该标的勾稽差额 −273.6%，
已按 partial 输出。高杠杆企业的 WACC 设定已列入待设计方确认的问题。

## 3. B. 指数研究信号与账本

指数报告（文本与 JSON）新增 `payload.candidate_a_signal`：

| 指数 | 观察周 | 观察日 | 官方滚动 PE | 5 年周度分位 | 信号 |
|---|---|---|---|---|---|
| 沪深300 | 2026-W45 | 2026-11-06 | 13.13 | 67.7% | 退出条件成立 |
| 中证500 | 2026-W45 | 2026-11-06 | 22.18 | 68.9% | 退出条件成立 |
| 中证1000 | 2026-W45 | 2026-11-06 | 26.01 | 62.0% | 退出条件成立 |

- 信号与回测共用同一份周度分位函数；本批把逐周分位重构成单点函数，并用回测的 1102 个观察点
  逐条比对，0 差异；
- 原因码新增 `INDEX_SIGNAL_RESEARCH_ONLY`（非阻断）；`decision` 仍为 `undetermined`，
  `model_plan.not_selected` 只剩候选 B；
- 账本只记录**已结束自然周**；同一标的同一周重复运行内容一致则跳过、不一致报冲突；
  `record_id` 为内容哈希，历史被改写会被 `--check` 发现。

## 4. 数据

| 项目 | 内容 |
|---|---|
| 个股快照 | `data/policy-trial/snapshots/` 30 只，`as_of=2026-11-11`，manifest 自检 0 问题 |
| 业务证据 | `data/policy-trial/evidence/` 30 份（行业、主营构成、近 8 年收入与归母净利润） |
| 无风险利率 | `data/policy-trial/macro/cn-10y-yield.json`（中债 10 年期，120 个月末 + 最新值，内容哈希） |
| 数据来源 | 合并报表（东方财富）；分红（巨潮）；价格（新浪不复权日线，push2 被限流时的回退）；公司资料（巨潮股本变动与行业分类） |
| 来源回退记录 | 每只快照的 `coverage` 中记录实际使用的来源；未用其他数据凑 |
| 旧项目 | 未修改、未删除（`/Users/comyn/Documents/deepseek-harness/投资/valuation/` 只读参考） |

## 5. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/build_policy_trial_data.py --as-of 2026-11-11   # 30 只快照 + 宏观序列（需网络）
.venv/bin/python tools/run_equity_policy_trial.py                     # 试运行报告 + JSON
.venv/bin/python tools/update_signal_ledger.py                        # 追加新周度观察点
.venv/bin/python tools/update_signal_ledger.py --check                # 账本完整性
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 6. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 435 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_equity_policy.py` | 34 | L1 政策集中/版本化/无散落阈值（5）；L2 折现率、增长、终值、再投资、情景、FCFF/DDM 手算与 600519 分歧（11）；L3 等级与结论边界（13）；L4/L5 报告覆盖与快照重放（5） |
| `tests/test_index_signal.py` | 14 | 信号边界与真实序列（4）、回测摘要一致性（1）、账本追加/冲突/篡改/重复（6）、命令幂等（1）、指数报告与不可成为价格判断（2） |
| 既有回归 | 387 | 阶段一/二/三全部保留，全部通过 |

**未通过项：无。**

## 7. 与批准的关系

本批只完成「草案试运行 + 研究信号」。政策的批准权在股东（决策②）：设计方复核、
独立 AI 审查、带证据提请决策②之后，政策版本才改为 approved、相关模型才改为 validated，
系统才开始输出正式结论。本批没有、也不能通过这些步骤。

## 8. 提交记录

| 提交 | 内容 |
|---|---|
| `9bc31ac` | **本轮**：政策草案试运行 + 候选 A 研究信号与账本 + 30 只数据 + 48 项新测试 + 文档与示例 |
| `1866e2b` | 交付入口（第三阶段收尾） |
| `106a9b8` | 第三阶段批三收尾 R1/R2（已签收） |
| `698bd50` | 第三阶段批三：候选 A 周度研究回测 |
| `a5620ae` | 第三阶段批二：事后诊断 + 数据可行性 |
| `2f824df` | 第三阶段首轮收尾：W1/W2/E1 |
| `1bb379c` | 第三阶段首轮：指数画像 + ETF 产品资料检查 |
| `82b8d4c` / `317506e` | 阶段二交付与 S1/S2 修复（已签收） |
| `5c4c1c9` 等 | 阶段一（已签收） |

## 9. 详细材料

| 内容 | 文件 |
|---|---|
| 本批实现与验收报告（A + B、L1–L8、发现的问题） | [docs/phase4a-delivery.md](docs/phase4a-delivery.md) |
| 个股政策试运行报告（自动生成，逐只结果与敏感性） | [docs/phase4a-equity-policy-trial.md](docs/phase4a-equity-policy-trial.md) |
| 政策文件（版本化、每参数带依据） | [data/policies/equity-policy-v1-draft.json](data/policies/equity-policy-v1-draft.json) |
| 试运行机器可读结果 | [data/policy-trial/trial-report.json](data/policy-trial/trial-report.json) |
| 试运行数据来源汇总 | [data/policy-trial/build-summary.json](data/policy-trial/build-summary.json) |
| 信号账本与规则 | [data/ledger/candidate-a-signals.jsonl](data/ledger/candidate-a-signals.jsonl)、[data/ledger/README.md](data/ledger/README.md) |
| 指数画像规格（含候选 A 研究信号与账本） | [docs/index-profile-spec.md](docs/index-profile-spec.md) |
| 候选 A 回测记录 | [docs/phase3c-backtest.md](docs/phase3c-backtest.md)、[data/backtest/candidate-a.json](data/backtest/candidate-a.json) |
| 验收清单（阶段一/二/三 + 第四阶段 L1–L8） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 个股研究估值（阶段二） | [docs/phase2-delivery.md](docs/phase2-delivery.md)、[docs/equity-model-spec.md](docs/equity-model-spec.md) |
| 示例请求与输出 | [examples/](examples/) |
