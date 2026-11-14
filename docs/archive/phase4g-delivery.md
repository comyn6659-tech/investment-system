# 第四阶段第七批交付：稳定收息型标准 + 所有制标记

本批只做 `batch4g-income-pool-instructions.md`：新增 `income-pool-v1-draft` 草案、
分红补到 2016–2020、收息池试运行、所有制标记与新账本。不改好公司池规则与结果，
不做银行，不评券商保险，不改个股政策与结论。

## 1. 结果

| 项 | 数值 |
|---|---:|
| 收息型入池 | **142** |
| 收息型未入池 | 3,110 |
| 收息型待定 | 0 |
| 只差一条 | 244 |
| 评价范围 / 范围外 | 3,252 / 1,973 |
| 收息型候选（财务全过 + 只差一条） | 402 |
| 与好公司池同时入池 | 59 |
| 好公司池（未改） | 138 |
| 新快照 | **`qps-d8fb7e3718675b93`**（69 张表、10.27 MB） |
| 测试 | **563 passed** |

## 2. 稳定收息型规则 `income-pool-v1-draft`（未批准）

四组 11 条，阈值全部来自政策文件：

- 利润稳：窗口内亏损年数 = 0；ROE 平均 ≥ 8%；最低一年 ROE ≥ 5%；
  归母净利润同比下降超过 30% 的年数 ≤ 1。
- 分红稳：窗口内每个财年都有现金分红；最近 5 年现金分红率 ≥ 40%。
- 分红有现金撑着：最近 5 年自由现金流 ÷ 现金分红 ≥ 1.0；最新财年净有息负债 / EBITDA < 4。
- 做好人：审计、处罚主体、质押三条与 `quality-pool-v1.2-draft` 完全一致（测试比对）。

每条条件的等于/略差/缺年份测试见 `tests/test_income_pool.py`；
敏感性报告 ROE 平均 6/8/10%、分红率 30/40/50%、负债上限 3/4/5 倍，只报告不选用。

## 3. 分红补齐（2016–2020）

`RPT_SHAREBONUS_DET` 按报告期补取 2016–2020 共 10 个报告期，与 2021–2025 合并为 20 个报告期；
2016–2020 共 13,610 行。各年评价范围内公司中有现金分红记录的比例分别为
78.0% / 81.4% / 75.6% / 73.9% / 73.5%，无记录按 0 元处理。
空派现字段 213 行均为送转股方案（无现金分红），按 0 处理；分红表未覆盖的财年才记待定
（`test_income_missing_dividend_data_pends_dividend_and_fcf`）。

## 4. 治理补查（M3）

财务条件全过或只差一条的 402 家逐家查审计、处罚/立案、质押；与好公司池候选重叠的公司
直接复用第五、六批缓存。覆盖率 **审计 402/402、处罚 402/402、质押 402/402**，口径与
`quality-pool-v1.2-draft` 一致（测试比对条件定义、参数与判定结果）。

补查中 6 家质押表解析失败：宁沪高速、国投电力、山东出版、中南传媒、宁波精达的 PDF 把
持股数拆成“2,742,578, 825”形式，改用股东姓名定位行后取得；孚日股份三季报为扫描件，
改用东方财富重要股东质押明细，控股股东质押 **52.12%**，超过 50% 未通过。

## 5. 收息池结果

- 漏斗：亏损年淘汰 1,731、ROE 平均淘汰 483、最低 ROE 淘汰 447、下滑年数淘汰 71、
  未分红年淘汰 69、分红率淘汰 224、FCF/分红淘汰 77、负债淘汰 3、审计淘汰 1、
  处罚淘汰 1、质押淘汰 3，最终 142。
- 只差一条 244 家，逐家列出差哪一条、观测值、门槛与距离。
- 与好公司池同时入池 59 家（报告 §1d）。

## 6. 所有制标记（M5）

两池全部公司 + 30 只测试集共 **439** 家，标记覆盖率 100%：

| 类别 | 好公司池（138） | 稳定收息池（142） |
|---|---:|---:|
| 央企 | 10 | 18 |
| 地方国企 | 22 | 38 |
| 民企 | 93 | 76 |
| 外资 | 6 | 3 |
| 其他 | 7 | 7 |

依据东方财富 F10 `ACTUAL_HOLDER`（实际控制人）；无实际控制人时用第一大股东；
规则边界与人工复核记录在 `data/policy-inputs/ownership-review-v1.json`。
所有制只标记，测试证明改所有制不改变任何条件判定（`test_ownership_does_not_change_judgement`）。

## 7. 快照与账本

- 新快照 `qps-d8fb7e3718675b93`：财务 44 张 + 分红 20 张 + 治理 4 张 + 所有制 1 张 = 69 张；
  逐表哈希校验；`data/income-pool/manifest.json` 指向该快照。
- 好公司池快照 `qps-3ebbd24be612bf7e`、`report.json`、`report-4e.json`、池账本 3 条均未改。
- 新账本 `data/ledger/income-pool.jsonl` 追加 1 条
  （`income-pool-v1-draft`，快照 `qps-d8fb7e3718675b93`，142 家，`pool_hash=iph-3a3ca849f50a9f58`）；
  `--check` 1 条 0 问题。
- 个股结论 7 条、候选 A 3 条、官方估值 4 条、好公司池账本 3 条均 0 问题。

## 8. 复现

```bash
.venv/bin/python tools/build_income_pool_data.py --build-preliminary
.venv/bin/python tools/build_income_pool_data.py --financial-candidates
.venv/bin/python tools/build_income_pool_data.py --fetch-governance --workers 6
.venv/bin/python tools/build_income_pool_data.py --fetch-ownership
.venv/bin/python tools/build_income_pool_data.py --build-tables
.venv/bin/python tools/build_income_pool_data.py --build-snapshot
.venv/bin/python tools/run_income_pool.py
.venv/bin/python tools/run_income_pool.py --show 600519
.venv/bin/python tools/run_income_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 试运行报告：[phase4g-income-pool-report.md](phase4g-income-pool-report.md)
- 政策：[income-pool-v1-draft.json](../data/policies/income-pool-v1-draft.json)
- 所有制复核：[ownership-review-v1.json](../data/policy-inputs/ownership-review-v1.json)
- 快照说明：[data/income-pool/README.md](../data/income-pool/README.md)
