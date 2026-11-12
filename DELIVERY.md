# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第八批收尾：**银行估值预演按已批准政策重跑** |
| 政策版本 | `equity-policy-v1.1`（已批准）+ `bank-check-v1-draft`（未批准，决策③提请）；旧预演参数不再读取 |
| 本轮实现提交 | `b3d1f4e` |
| 上一轮 | `c8efd25`（第四阶段第八批入口，实现 `520800e`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 评价范围内银行 | 36 |
| B 级预演 | 31 |
| C 级预演 | 5（郑州、华夏、民生、光大、浙商） |
| 资料不足 | 0 |
| 候选买点 / 等待 / 回避 / 无法判断 | 19 / 12 / 0 / 5 |
| 股权成本 | 7.9029%（规范化无风险 2.7629% + ERP 5.14%） |
| 现价日期 | 2026-11-12 |
| 测试 | **587 passed** |

## 2. 数学与银行检查

- 折现率：规范化无风险利率 + ERP，三情景同一折现率。
- 增长按 R7：第 1 年封顶 10%；稳态 = min(10%, 5 年与 3 年复合增速)；第 2–5 年线性过渡。
- 派息率按 R10：最近 3 年现金分红总额 ÷ 归母净利润总额；终值按 R1；悲观按 R8。
- 银行检查：三项证据齐全 + 最新核心一级 ≥ 8.5% + 拨备覆盖率 ≥ 150%；通过且最近
  7 个财年至多 1 年亏损、数据完整 → B（安全边际 40%），否则 C；银行不评 A。
- 结论：B 且 complete，现价 ≤ 基准 × 0.6 且 ≤ 悲观 → 候选买点；现价 > 乐观 → 回避；
  其余等待。

## 3. 两个样例（元/股）

| 银行 | 现价 | 悲观 / 基准 / 乐观 | 安全边际门槛价 | 预演结论 | 起约束条件 | 距门槛 | 终值占比 |
|---|---:|---:|---:|---|---|---:|---:|
| 招商银行 | 42.63 | 50.02 / 67.76 / 89.48 | 40.65 | 等待 | 安全边际 | +4.86% | 86.87% |
| 工商银行 | 8.69 | 9.40 / 12.33 / 15.02 | 7.40 | 等待 | 安全边际 | +17.50% | 88.27% |

两家三档价值均有独立手算测试（逐年 EPS、DPS、终值、折现公式）与报告值比对，误差 < 1e-9。
报告逐家给出：现价及日期、三档价值、门槛价、结论、起约束条件、距门槛、终值占比、
未满足条件；另附股权成本 ±0.5pp 的结论变化表。

## 4. 输入与“不读取旧参数”

- `data/bank-pool/valuation-inputs.csv.gz`：营收、归母净利润、ROE、净利率、近 3 年派息率、
  总股本、现价与日期、三项银行证据；由 `tools/build_bank_valuation_inputs.py` 生成。
- 旧版 `bank-valuation-rehearsal-v1.json` 保留为历史；工具不读取。测试在打开该文件时
  直接报错，预演仍完成，证明没有读取其他折现率/增速参数。
- 未另设股权成本、风险加权资产密度、RWA 增速等参数。

## 5. 未改变

- 银行池快照 `qps-dc9c12160476db8b`、银行池报告（入池 11）、银行账本（1 条
  `bph-13b62d2bcfb016c0`）均未改。
- 好公司池 138、稳定收息池 142、既有账本（个股 7、候选 A 3、官方估值 4、好公司池 3、
  收息池 1）均未改；正式结论账本不含银行。
- CLI 对 600036 仍为 `insufficient_data`，估值 `null`；银行模型仍为 research。

## 6. 验收自检（NR1–NR5）

| 编号 | 结果 |
|---|---|
| NR1 | 只读 `equity-policy-v1.1` 与 `bank-check-v1-draft`（及输入数据）；旧参数文件打开即报错测试通过 |
| NR2 | 招商银行、工商银行三档价值独立手算测试通过 |
| NR3 | 报告含第 5 条全部列与 COE ±0.5pp 敏感性表 |
| NR4 | 银行池/其他池/账本/正式结论/CLI 不变；**587 passed** |
| NR5 | 本文件一页以内 |

## 7. 复现

```bash
.venv/bin/python tools/build_bank_pool_data.py --fetch-bank   # 补齐本地原始银行指标缓存
.venv/bin/python tools/build_bank_valuation_inputs.py
.venv/bin/python tools/run_bank_valuation_rehearsal.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 预演报告：[docs/phase4h-bank-valuation-rehearsal.md](docs/phase4h-bank-valuation-rehearsal.md)
- 详细交付：[docs/phase4h-r-bank-valuation-delivery.md](docs/phase4h-r-bank-valuation-delivery.md)
- 银行检查参数：[data/policy-inputs/bank-check-v1-draft.json](data/policy-inputs/bank-check-v1-draft.json)
- 预演输入：[data/bank-pool/valuation-inputs.csv.gz](data/bank-pool/valuation-inputs.csv.gz)
