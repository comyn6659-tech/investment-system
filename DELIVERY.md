# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十二批：**银行专用估值 v2（试运行）+ 银行历史估值位置** |
| 本轮实现提交 | `（本次收尾提交，见 git log）` |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 试运行范围 | 36 家范围内银行（42 家历史位置） |
| 预演结论 | 候选买点 **7**、等待 **4**、回避 **0**、无法判断 **25**（25 家均不在银行池） |
| 敏感性（k 溢价 1/2/3pp） | 候选 **10 / 7 / 4** 家，名单见报告 |
| 敏感性（安全边际 20/30/40%） | 候选 **10 / 7 / 4** 家，名单见报告 |
| 历史估值位置 | 41/42 家 ≥5 年（兰州银行 58 个月不足 5 年，不计算分位） |
| 本地测试 / 干净归档 | 本地 **689 passed**；`git archive` 待最终实测（预期 688 passed + 1 skipped） |
| 正式账本 / CLI 银行结论 | 账本 **99 条不变**；CLI 银行仍“无法判断：银行估值未启用” |

## 2. 方法与参数

- 草案：`data/policies/bank-valuation-v2-draft.json`（`bank-valuation-v2-draft`，未批准）。
- 公式：每股价值 = 最新每股净资产 × (ROE − g) ÷ (k − g)。
- ROE 基准 = min(近 3 年平均, 近 5 年平均)；悲观 = 近 5 年最低 − 1pp；乐观 = 近 5 年平均；
  拨备 < 200% 或不良率 5 年上升 > 0.3pp 时三档各再减 1pp。
- k = 规范化无风险利率 2.7629% + ERP 5.14% + 银行溢价 2.0% = **9.9029%**；
  g = 2.0%/1.5%/2.5%（不超过无风险利率）；安全边际 30%。
- 每股净资产 = (最新归母净资产 − 其他权益工具) ÷ 总股本；只算普通股。
- 给结论前提：属于已批准银行池（11 家）、三项证据齐全、最近 7 年无亏损；否则只给价值
  数字与“无法判断”及原因。

## 3. 报告与历史位置

- 逐家三档 ROE、扣减、k、g、每股净资产、三档价值、现价、合理 PB、门槛价、预演结论、
  起约束条件：`docs/phase4l-bank-valuation-v2.md`（36 家全表）。
- 全集分布、k 溢价与安全边际敏感性、与第八批通用股息折现逐家对照：同报告第 2、4、5 节。
- 历史位置：东方财富同源收盘价/市净率 + 分红（除权除息日），计算现价 PB、股息率在近 10 年
  （不足 10 年用全部可得，至少 60 个月）月末序列中的分位；只描述，不给买点。
  银行卡片新增一行：“市净率处于近 10 年 X% 分位，股息率处于 Y% 分位”。
- 结构化数据：`data/bank-pool/valuation-v2.json`、`data/bank-pool/valuation-v2/inputs.csv.gz`、
  `data/bank-pool/valuation-v2/history.csv.gz`。

## 4. 验收自检（Z1–Z6）

| 编号 | 结果 |
|---|---|
| Z1 | 草案版本化、每个参数带依据；招商银行、工商银行三档价值手算测试通过 |
| Z2 | 报告含全部字段、全集分布、k/安全边际敏感性、与第八批逐家对照 |
| Z3 | 25 家非银行池银行只有价值数字与“无法判断”，无候选/等待/回避 |
| Z4 | 历史位置同源同口径、41 家 ≥5 年；银行卡片显示分位一行 |
| Z5 | 正式账本 99 条不变、CLI 银行结论不变；干净归档待最终实测 |
| Z6 | 本文件一页以内 |

## 5. 复现

```bash
.venv/bin/python tools/build_bank_valuation_v2_data.py
.venv/bin/python tools/run_bank_valuation_v2.py
./ask "招商银行怎么样"
scripts/verify-clean-archive.sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 详细报告：[docs/phase4l-bank-valuation-v2.md](docs/phase4l-bank-valuation-v2.md)｜验收清单：
  [docs/acceptance-checklist.md](docs/acceptance-checklist.md)｜草案：
  [data/policies/bank-valuation-v2-draft.json](data/policies/bank-valuation-v2-draft.json)
- 历史位置数据：[data/bank-pool/valuation-v2/](data/bank-pool/valuation-v2/)｜预演结果：
  [data/bank-pool/valuation-v2.json](data/bank-pool/valuation-v2.json)
