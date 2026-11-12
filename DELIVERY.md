# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第七批：**稳定收息型标准 + 所有制标记** |
| 政策版本 | 个股 `equity-policy-v1.1`、好公司池 `quality-pool-v1.2-draft` 均未改；新增 `income-pool-v1-draft`（未批准） |
| 本轮实现提交 | `83519b5` |
| 上一轮 | `35c174a`（第四阶段第六批入口，实现 `c0ad6cb`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 稳定收息型入池 | **142** |
| 未入池 / 待定 / 范围外 | 3,110 / 0 / 1,973 |
| 只差一条 | 244 |
| 收息型财务候选（财务全过 + 只差一条） | 402 |
| 与好公司池同时入池 | 59 |
| 好公司池（未改） | 138 |
| 新快照 | **`qps-d8fb7e3718675b93`**（69 张表、10.27 MB） |
| 测试 | **563 passed** |

## 2. 稳定收息型规则（`income-pool-v1-draft`，未批准）

四组 11 条：利润稳（亏损年 0、ROE 平均 ≥ 8%、最低一年 ROE ≥ 5%、同比下滑 >30% 年数 ≤ 1）；
分红稳（每个财年有现金分红、最近 5 年分红率 ≥ 40%）；分红有现金撑着（最近 5 年 FCF ÷ 分红 ≥ 1.0、
最新财年净有息负债 / EBITDA < 4）；做好人三条与好公司池 v1.2 完全一致（测试比对）。
敏感性报告 ROE 6/8/10%、分红率 30/40/50%、负债上限 3/4/5 倍，只报告不选用。

## 3. 数据、治理与所有制

分红补到 2016–2020：20 个报告期（2016–2025），2016–2020 共 13,610 行；各年评价范围内公司有分红记录
78.0%/81.4%/75.6%/73.9%/73.5%，无记录按 0；空派现 213 行为送转；表未覆盖财年才记待定。
治理对 402 家候选 **审计 402/402、处罚 402/402、质押 402/402**，复用缓存、口径同 v1.2；6 家质押解析
失败已逐家补足（孚日股份改用质押明细，控股股东质押 52.12% 未通过）。
所有制：两池 + 30 只测试集共 **439** 家，标记覆盖率 **100%**，依据实际控制人性质；无实际控制人时用
第一大股东；边界与人工复核见 `data/policy-inputs/ownership-review-v1.json`。分布：好公司池 央企 10 /
地方国企 22 / 民企 93 / 外资 6 / 其他 7；收息池 央企 18 / 地方国企 38 / 民企 76 / 外资 3 / 其他 7。
只标记，不影响判定（测试）。

## 4. 快照与账本

- 新快照 `qps-d8fb7e3718675b93`：财务 44 + 分红 20 + 治理 4 + 所有制 1 = 69 张表；逐表哈希校验。
- 好公司池快照 `qps-3ebbd24be612bf7e`、`report.json`、`report-4e.json`、池账本 3 条均未改。
- 新账本 `data/ledger/income-pool.jsonl` 追加 1 条（`income-pool-v1-draft`，142 家，
  `pool_hash=iph-3a3ca849f50a9f58`），`--check` 1 条 0 问题；其他账本 7/3/4 条 0 问题。

## 5. 验收自检（M1–M8）

| 编号 | 结果 |
|---|---|
| M1 | 规则版本化、参数带依据；11 条条件均有等于/略差/缺年份测试 |
| M2 | 分红 20 个报告期覆盖报告；缺失财年→待定有测试 |
| M3 | 治理 402/402 覆盖；与 v1.2 口径一致有测试 |
| M4 | 报告含首页、名单、只差一条、漏斗、敏感性、30 只、行业、两池交集 |
| M5 | 所有制 439 家 100% 标记；不影响判定有测试 |
| M6 | `--show 600519` 从快照复算；同快照重放逐家一致 |
| M7 | 好公司池快照/报告/账本不变；其他账本不变；新账本只追加；**563 passed** |
| M8 | 本文件一页以内，含复现命令、测试结果、提交 ID |

## 6. 复现

```bash
.venv/bin/python tools/build_income_pool_data.py --build-preliminary --financial-candidates
.venv/bin/python tools/build_income_pool_data.py --fetch-governance --workers 6
.venv/bin/python tools/build_income_pool_data.py --fetch-ownership --build-tables --build-snapshot
.venv/bin/python tools/run_income_pool.py --show 600519
.venv/bin/python tools/run_income_pool.py
.venv/bin/python tools/run_income_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 收息型报告：[docs/phase4g-income-pool-report.md](docs/phase4g-income-pool-report.md)
- 详细交付：[docs/phase4g-delivery.md](docs/phase4g-delivery.md)
- 政策：[data/policies/income-pool-v1-draft.json](data/policies/income-pool-v1-draft.json)
- 快照说明：[data/income-pool/README.md](data/income-pool/README.md)
