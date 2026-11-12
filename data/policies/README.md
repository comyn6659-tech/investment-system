# 政策文件

| 文件 | 版本 | 状态 | 用途 |
|---|---|---|---|
| `equity-policy-v1-draft.json` | `equity-policy-v1-draft` | `draft_unapproved` | 个股政策 v1 草案：折现率、预测、终值、情景、质量、确定性等级与结论映射 |

## 规则

- 每个参数都带 `basis`（理由）或 `source`（来源），由 `engine/policy/equity.py` 校验；
- 代码只按路径取参数，不另设业务数字（`tests/test_equity_policy.py` 有改政策即改行为的测试）；
- `approved` 必须为 `false`：草案不得自我批准。批准权在股东（决策②）；
- 本政策只用于试运行报告的「预演结论」字段，不进入 CLI 的正式结果路径。

试运行报告：`docs/phase4a-equity-policy-trial.md`；机器可读结果：`data/policy-trial/trial-report.json`。
