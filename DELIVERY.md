# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十二批收尾：**银行专用估值 v2.1（试运行）** |
| 本轮实现提交 | `（本次收尾提交，见 git log）` |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 试运行范围 | 36 家范围内银行（42 家历史位置沿用 v2 数据） |
| v2.1 预演结论 | 候选买点 **3**、等待 **8**、回避 **0**、无法判断 **25**（25 家均不在银行池） |
| v2 → v2.1 变化 | 候选买点 7→3、等待 4→8；三档 ROE 与价值逐家变化见报告 |
| 3×3 敏感性矩阵 | k 溢价 1/2/3pp × 安全边际 20/30/40%，九格均列候选名单 |
| 历史估值位置 | 41/42 家 ≥5 年；兰州银行 58 个月不足 5 年 |
| 本地测试 / 干净归档 | 本地 **690 passed**；`git archive` 待最终实测（预期 689 passed + 1 skipped） |
| 正式账本 / CLI 银行结论 | 账本 **99 条不变**；CLI 银行仍“无法判断：银行估值未启用” |

## 2. v2.1 修订

- 基准 ROE = min(最新财年 ROE, 近 3 年平均, 近 5 年平均)。
- 悲观 ROE = min(近 5 年最低 − 1pp, 最新财年 − 3 × 近 3 年年均下滑幅度)；近 3 年未下滑时
  后一项不适用；资产质量扣减规则不变。
- 乐观 ROE = 近 3 年平均（不再用 5 年平均）。
- 新增隐含长期回报率 = 2% + (最新财年 ROE − 2%) ÷ 现价市净率；只描述，不参与结论；
  报告逐家列出并与 k=9.90% 对比，银行卡片新增一行。
- k、g、安全边际 30%、只有银行池可给结论等其余参数不变。

## 3. 报告

- v2.1 逐家结果、全集分布、隐含长期回报率、k 对比：
  `docs/phase4l-bank-valuation-v2.1.md`（36 家全表）。
- v2 → v2.1 逐家对照（三档 ROE、基准价值、结论变化）：报告第 5 节。
- k 溢价 × 安全边际 3×3 矩阵（每格数量 + 名单）：报告第 4 节。
- 与第八批通用股息折现逐家对照：报告第 6 节。
- 结构化数据：`data/bank-pool/valuation-v2.1.json`；v2 基线保留在 `valuation-v2.json`。
- 草案与全部参数依据：`data/policies/bank-valuation-v2.1-draft.json`。

## 4. 验收自检（ZR1–ZR5）

| 编号 | 结果 |
|---|---|
| ZR1 | 三档 ROE 规则已实现；招商银行、兴业银行手算测试（含下滑外推）通过 |
| ZR2 | 隐含长期回报率公式手算通过；招商银行卡片显示“约 14.5%”描述行 |
| ZR3 | v2→v2.1 对照 36 家；3×3 矩阵九格均含数量与名单 |
| ZR4 | 本地 690 passed；干净归档待最终实测；账本 99 条、CLI 银行结论不变 |
| ZR5 | 本文件一页以内 |

## 5. 复现

```bash
.venv/bin/python tools/run_bank_valuation_v21.py
./ask "招商银行怎么样"
scripts/verify-clean-archive.sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 详细报告：[docs/phase4l-bank-valuation-v2.1.md](docs/phase4l-bank-valuation-v2.1.md)｜验收清单：
  [docs/acceptance-checklist.md](docs/acceptance-checklist.md)｜草案：
  [data/policies/bank-valuation-v2.1-draft.json](data/policies/bank-valuation-v2.1-draft.json)
- v2.1 结果：[data/bank-pool/valuation-v2.1.json](data/bank-pool/valuation-v2.1.json)｜v2 基线：
  [data/bank-pool/valuation-v2.json](data/bank-pool/valuation-v2.json)
