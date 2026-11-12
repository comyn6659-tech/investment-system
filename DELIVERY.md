# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第八批：**银行标准 + 银行估值预演** |
| 政策版本 | 个股 `equity-policy-v1.1`、好公司池、收息池均未改；新增 `bank-pool-v1-draft`（未批准） |
| 本轮实现提交 | `520800e` |
| 上一轮 | `85fd2ce`（第四阶段第七批入口，实现 `83519b5`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| A 股上市银行 | 42 |
| 评价范围（非 ST、完整年报 ≥ 7 年） | 36 |
| 银行池入池 | **11** |
| 未入池 / 待定 | 25 / 0 |
| 只差一条 | 13 |
| 范围外 | 5,189（非银行 4,990、ST 193、年报不足 7 年 6） |
| 新快照 | **`qps-dc9c12160476db8b`**（71 张表、10.31 MB） |
| 银行账本 | 1 条（`bph-13b62d2bcfb016c0`） |
| 测试 | **579 passed** |

## 2. 银行规则（`bank-pool-v1-draft`，未批准）

五组 12 条：会赚钱（亏损年 0、ROE 平均 ≥ 10%、近 3 年 ROE ≥ 9%）；资产干净（不良率 ≤ 1.5%、
5 年变化 ≤ +0.3 个百分点、拨备覆盖率 ≥ 200%）；资本厚（核心一级 ≥ 9.0%）；分红（每个财年
有现金分红、近 5 年分红率 ≥ 25%）；做好人三条与好公司池 v1.2 完全一致（测试）。
敏感性：不良率 1.3/1.5/1.8%、拨备 150/200/250%、核心一级 8.5/9.0/10.0%，只报告不选用。

## 3. 数据、入池与所有制

银行专项指标：东方财富 F10 `RPT_F10_FINANCE_MAINFINADATA`，42 家银行 2016 年起季度与年报
**1,773 行**；36 家评价银行的 ROE、不良率、不良率 5 年变化、拨备、核心一级、分红、审计、
处罚、质押均 **36/36（100%）**。审计改用银行报表 `companyType=3`；三季报多套股东表写法
已适配，兴业/上海/民生质押补足；苏农银行控股股东质押 **69.72%** 未通过。
入池 11 家：张家港行、苏州银行、招商银行、南京银行、兴业银行、农业银行、交通银行、
工商银行、邮储银行、建设银行、中信银行；只差一条 13 家见报告 §3。
所有制：42 家全部标记（央企 9 / 地方国企 29 / 民企 4）；银行池 7 / 3 / 1；只标记，不影响判定（测试）。

## 4. 银行估值预演（N4）

按个股政策 v1.1 `grades.banks`（三项证据 + 银行检查，最高 B 级）对 36 家银行逐家预演：
**B 级 31 家、C 级 5 家、资料不足 0 家**。C 级 5 家（郑州、华夏、民生、光大、浙商）资本或
拨备低于预演适用下限，模型拒绝给估值数字；其余列出路线、状态、等级、悲观/基准/乐观三档价值、
未满足条件与预演结论。预演假设未获批准，**不写入正式结论账本，CLI 对 600036 仍为
`insufficient_data`（测试）**。

## 5. 快照与账本

- 新快照 `qps-dc9c12160476db8b`：财务 44 + 分红 20 + 治理 4 + 所有制 1 + 银行指标 1 + 概况 1 = 71 张表。
- 银行账本 `data/ledger/bank-pool.jsonl` 追加 1 条，`--check` 1 条 0 问题。
- 好公司池 138、稳定收息池 142、既有账本（个股 7、候选 A 3、官方估值 4、好公司池 3、
  收息池 1）均未改；个股结论账本不含银行。

## 6. 验收自检（N1–N7）

| 编号 | 结果 |
|---|---|
| N1 | 规则版本化、参数带依据；12 条条件均有等于/略差/缺数据测试 |
| N2 | 银行指标来源与覆盖率逐项报告；缺银行指标→待定有测试 |
| N3 | 报告含首页、名单、只差一条、漏斗、敏感性、两家测试银行、所有制 |
| N4 | 预演 36 家均有路线/状态/等级/三档价值/结论；正式账本与 CLI 不变（测试） |
| N5 | 600036 2025 ROE/不良/拨备/核心一级与快照源逐项一致（测试）；`--show` 可复算 |
| N6 | 既有池/账本/结论/指数不变；新账本只追加；**579 passed** |
| N7 | 本文件一页以内，含复现命令、测试结果、提交 ID |

## 7. 复现

```bash
.venv/bin/python tools/build_bank_pool_data.py --fetch-bank --fetch-governance --workers 6
.venv/bin/python tools/build_bank_pool_data.py --fetch-ownership
.venv/bin/python tools/build_bank_pool_data.py --build-tables --build-snapshot
.venv/bin/python tools/run_bank_pool.py --show 600036
.venv/bin/python tools/run_bank_pool.py
.venv/bin/python tools/run_bank_pool.py --check
.venv/bin/python tools/run_bank_valuation_rehearsal.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 银行池报告：[docs/phase4h-bank-pool-report.md](docs/phase4h-bank-pool-report.md)
- 估值预演：[docs/phase4h-bank-valuation-rehearsal.md](docs/phase4h-bank-valuation-rehearsal.md)
- 详细交付：[docs/phase4h-delivery.md](docs/phase4h-delivery.md)
- 政策：[data/policies/bank-pool-v1-draft.json](data/policies/bank-pool-v1-draft.json)
- 快照说明：[data/bank-pool/README.md](data/bank-pool/README.md)
