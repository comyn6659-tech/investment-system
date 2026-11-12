# 政策文件

| 文件 | 版本 | 状态 | 用途 |
|---|---|---|---|
| `equity-policy-v1.1-draft.json` | `equity-policy-v1.1-draft` | `draft_unapproved` | 个股政策 v1.1 草案（**当前试运行版本**）：按审查 F1–F14 的 R1–R14 修订 |
| `equity-policy-v1-draft.json` | `equity-policy-v1-draft` | `draft_unapproved` | v1 草案，保留作对照，不删除；v1.1 的 `supersedes` 指向它 |

## v1.1 相对 v1 的修订（R1–R14）

| 编号 | 修订 |
|---|---|
| R1 | FCFF 终值的新投资回报率基准/悲观取 WACC，与折现率同口径；股息路线仍取股权成本 |
| R2 | 新增终值连续性护栏：默认路线、显式期 FCFF ≤ 0、终值占比 ≥ 90% |
| R3 | 期末现金只用 `END_CCE`；`END_CASH_EQUIVALENTS` 还原为现金等价物分项 |
| R4 | 近似项分方向：只把可能让价值变低的计入等级，其余只披露 |
| R5 | 少数股东取消 15% 触发线，按隐含值与账面值差额计入 |
| R6 | 两条路线对等：股息路线也做现金流勾稽，并加 3 年分红覆盖条件 |
| R7 | 第 1 年封顶 10%；稳态取 5 年与 3 年复合增速的较低者；第 2–5 年线性过渡 |
| R8 | 悲观情景加入近 5 年最低 EBIT/净利率 |
| R9 | 资本权重与杠杆护栏改用净有息负债；报告 30/40/50% 敏感性 |
| R10 | 派息率用总额对总额（期末股数还原），修正送转失真 |
| R11 | 基期异常触发即 partial / 无法判断；000858 更正后数据标 restated |
| R12 | 报告呈现：终值占比、起约束条件、门槛距离、仅研究参考分表、漂移与倍数表 |
| R13 | 缺数据不得默认通过，写明缺失年份；营运资本/利息费用缺失逐只列出 |
| R14 | 债务权重 < 1% 时债务成本显示为「不适用（债务可忽略）」 |

## 规则

- 每个参数都带 `basis`（理由）或 `source`（来源），由 `engine/policy/equity.py` 校验；
- 代码只按路径取参数，不另设业务数字（`tests/test_equity_policy.py` 有改政策即改行为的测试）；
- `approved` 必须为 `false`：草案不得自我批准。批准权在股东（决策②）；
- 本政策只用于试运行报告的「预演结论」字段，不进入 CLI 的正式结果路径。

试运行报告：`docs/phase4a-equity-policy-trial.md`；机器可读结果：`data/policy-trial/trial-report.json`；
v1 对照结果：`data/policy-trial/trial-report-v1.json`。
