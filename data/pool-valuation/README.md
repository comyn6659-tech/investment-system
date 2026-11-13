# 池成员估值批量快照

本目录保存“好公司池 + 收息池”非银行成员估值的固定批量输入快照。
快照 `qps-f140a9790e4015ea`：50 张表、约 15.08 MB；不修改好公司池快照、
三池名单/账本、政策或估值代码。

## 目录

```
data/pool-valuation/
├── manifest.json                 # 当前快照指针
├── regression-30.json            # 30 只测试股：批量路径 vs 原快照路径
├── pool-member-valuation.json    # 221 家池成员估值结果
└── snapshot-qps-f140a9790e4015ea/
    ├── manifest.json             # as_of、表级哈希、来源
    ├── balance-2015…2025.csv.gz  # 按报告期批量的合并资产负债表
    ├── income-2016…2025.csv.gz   # 合并利润表
    ├── cashflow-2016…2025.csv.gz # 合并现金流量表
    ├── interim_income-2025-09-30.csv.gz / 2026-09-30.csv.gz
    ├── dividends-2021…2025.csv.gz + dividends-2023/24/25-09-30.csv.gz
    ├── shares.csv.gz             # 最近一期总股本（池内缺失逐只补齐）
    ├── price.csv.gz              # 全市场行情快照（逐行价格日期）
    ├── universe.csv.gz / industry.csv.gz
    └── *.meta.json               # 来源、口径、行数、内容哈希
```

## 口径

- 财务：东方财富 `RPT_F10_FINANCE_GBALANCE/GINCOME/GCASHFLOW`，按报告期批量；
  只保留政策实际使用的字段（见 `docs/phase4j-field-mapping.md`）。
- 分红：好公司池快照的 2021–2025 中期/年度分红表，另补 2023–2025 三季报分红
  （政策派息率与 3 年分红覆盖窗口需要）。
- 行情：腾讯财经全市场行情快照；收盘前取上一交易日收盘，逐行记录价格日期。
- 总股本：东方财富 `RPT_F10_EH_EQUITY` 最近一期股本变动；池内 82 个缺失代码逐只补齐。
- 应付类字段在原个股快照中为空，批量路径也不映射，保持回归同口径（见映射文档）。

## 回归与结果

- 30 只测试股：状态、等级、预演结论 **30/30 相同**；基准价值最大相对差 **0.0000%**。
- 池成员：221 家（好公司池 138 + 收息池 142，去重后 221）；银行不估值。
- 正式结论写入 `data/ledger/equity-conclusions.jsonl`（只追加）；已有 7 条不变。

## 复现

```bash
.venv/bin/python tools/build_pool_valuation_data.py        # 采集 + 构建快照
.venv/bin/python tools/run_pool_valuation_regression.py    # 30 只回归
.venv/bin/python tools/run_pool_valuation.py --no-ledger   # 只出报告
.venv/bin/python tools/run_pool_valuation.py               # 追加正式结论账本
.venv/bin/python tools/run_pool_valuation.py --show 600519 # 抽查复算
```
