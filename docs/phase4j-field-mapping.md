# 池成员估值批量字段映射（phase 4j）

本表说明：全市场批量表 → 适配层 `fin.*` 事实 → 个股政策 v1.1 输入。

数据源：
- 财务：东方财富 `RPT_F10_FINANCE_GBALANCE` / `RPT_F10_FINANCE_GINCOME` /
  `RPT_F10_FINANCE_GCASHFLOW`，按报告期批量；
- 分红：好公司池快照的分红表（`RPT_SHAREBONUS_DET`，2021–2025 中期/年度）；
- 行情：`qt.gtimg.cn` 全市场行情快照（收盘前取上一交易日收盘，逐行记录价格日期）；
- 总股本：东方财富 `RPT_F10_EH_EQUITY` 最近一期股本变动。

## 1. 资产负债表

| 批量列 | `fin.<期>.<字段>` | 政策用途 |
|---|---|---|
| `TOTAL_PARENT_EQUITY` | `equity_parent` | ROE、平均归母净资产 |
| `MONETARYFUNDS` | `cash` | 可抵扣现金、非现金等价物 |
| `SHORT_LOAN` | `short_loan` | 有息负债 |
| `NONCURRENT_LIAB_1YEAR` | `current_portion_lt_debt` | 有息负债 |
| `LONG_LOAN` | `long_loan` | 有息负债 |
| `BOND_PAYABLE` | `bond_payable` | 有息负债 |
| `LEASE_LIAB` | `lease_liab` | 有息负债 |
| `MINORITY_EQUITY` | `minority_equity` | 少数股东权益近似项 |
| `INVENTORY` | `inventory` | 营运资本强度 |
| `NOTE_ACCOUNTS_RECE` | `receivables_notes` | 营运资本强度（优先） |
| `ACCOUNTS_RECE` | `receivables` | 营运资本强度（备选） |
| `NOTE_ACCOUNTS_PAYABLE` | **未映射** | 原个股快照 `payables_notes` 为空；为保持 30 只回归同口径，批量路径不引入该字段（营运资本按应付 0 处理） |
| `ACCOUNTS_PAYABLE` | **未映射** | 同上；原快照 `payables` 为空 |
| `SHARE_CAPITAL` | `share_capital` | 面值/历史股数还原 |
| `LOAN_ADVANCE` | `loan_advance` | 金融子公司判定 |
| `ACCEPT_DEPOSIT_INTERBANK` | `accept_deposit_interbank` | 金融子公司判定 |
| `TOTAL_ASSETS` | `total_assets` | 金融子公司/杠杆 |
| `TOTAL_LIABILITIES` | `total_liabilities` | 杠杆/勾稽 |
| `NOTICE_DATE` | `notice_date` | 可获得时间 |

## 2. 利润表

| 批量列 | `fin.<期>.<字段>` | 政策用途 |
|---|---|---|
| `TOTAL_OPERATE_INCOME` | `revenue` | 营业收入、增速、利润率 |
| `OPERATE_INCOME` | `operating_revenue` | 营业收入辅助核对 |
| `OPERATE_PROFIT` | `operating_profit` | EBIT 辅助核对 |
| `TOTAL_PROFIT` | `pretax_profit` | EBIT、实际税率 |
| `INCOME_TAX` | `income_tax` | 实际税率 |
| `NETPROFIT` | `net_profit` | 少数股东损益、净利率 |
| `PARENT_NETPROFIT` | `net_profit_parent` | ROE、派息率、悲观利润率 |
| `FE_INTEREST_EXPENSE` | `interest_expense` | 债务成本 |
| `FINANCE_EXPENSE` | `finance_expense` | EBIT/利息费用备选 |
| `BASIC_EPS` | `eps_basic` | 派息率、面值还原 |
| `NOTICE_DATE` | `notice_date` | 可获得时间 |

最近一期季报：`RPT_F10_FINANCE_GINCOME` 的 `2026-09-30` 行，映射同上；只用于
第 1 年增速（`fin.2026-09-30.revenue`）。

## 3. 现金流量表

| 批量列 | `fin.<期>.<字段>` | 政策用途 |
|---|---|---|
| `NETCASH_OPERATE` | `ocf` | 现金转化、分红覆盖 |
| `CONSTRUCT_LONG_ASSET` | `capex` | 资本开支、现金覆盖 |
| `FA_IR_DEPR` | `depreciation` | 折旧摊销/收入 |
| `IA_AMORTIZE` | `amortization_intangible` | 折旧摊销/收入 |
| `LPE_AMORTIZE` | `amortization_lt_prepaid` | 折旧摊销/收入 |
| `USERIGHT_ASSET_AMORTIZE` | `amortization_right_of_use` | 折旧摊销/收入 |
| `END_CASH_EQUIVALENTS` | `cash_equivalents_component` | 现金等价物分项（不是余额） |
| `END_CCE` | `cash_equivalents_end` | **期末现金余额唯一口径（R3）** |
| `INVENTORY_REDUCE` | `inventory_decrease` | FCFF 勾稽 |
| `OPERATE_RECE_REDUCE` | `operating_receivables_decrease` | FCFF 勾稽 |
| `OPERATE_PAYABLE_ADD` | `operating_payables_add` | FCFF 勾稽 |
| `DEFER_TAX` | `deferred_tax` | FCFF 勾稽 |

## 4. 分红、行情、股本

| 批量来源 | 适配事实 | 政策用途 |
|---|---|---|
| `PRETAX_BONUS_RMB` | `div.<财年><年报/半年报/三季报>.<年度/中期/季度分红>.per_10` | 近 3 年派息率、3 年分红覆盖 |
| `ASSIGN_PROGRESS` | `.status` | 只有已实施分红进入账本 |
| `NOTICE_DATE` | `.per_10` 的 `available_at` | 可获得时间 |
| 行情快照 `f2/前收` | `price.close`、`price.bar_status=closed` | 现价比较、新鲜度 |
| `RPT_F10_EH_EQUITY.TOTAL_SHARES` | `profile.total_shares` | 每股价值换算 |

## 5. 与原个股快照路径的差异（R3 回归披露）

- 批量路径的分红表只覆盖 2021–2025 中期/年度方案；政策派息率与分红覆盖窗口为最近 3 个财年，
  因此不影响 2023–2025 计算。更早的分红只在原快照的 `DivBook` 明细中，不进入政策输入。
- 批量路径没有分部收入；路线判定使用“行业 + 年度收入/利润”生成的最小业务证据，
  `segments=()`。原个股快照路径使用已核实业务证据；30 只回归使用原证据文件，因此不受影响。
- 批量路径用 `NOTICE_DATE` 作为可获得时间；原快照还含 `ex_date`。批量分红状态为“实施分配”，
  与原快照的“实施”等价。
- 回归时为隔离字段差异，批量路径使用原快照的现价、价格日期与总股本；正式池估值使用批量行情与股本。
- 应付类字段在原快照中为空，批量路径也**不映射**，避免用新增字段改变原路径的营运资本口径；
  这是“只换输入来源、不改变原快照可用字段集合”的处理，不是调整政策。30 只回归因此基准价值逐只相同。
