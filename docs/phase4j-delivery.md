# 第四阶段第十批交付：好公司池与收息池成员的个股估值

本批只做 `batch4j-pool-valuation-instructions.md`：为两个池的全部非银行成员建立
全市场批量估值输入、适配到已批准个股政策 v1.1、完成 30 只回归、跑池成员估值并追加
正式结论账本。不改政策、不改估值代码、不估值银行、不扩到池外。

## 1. 数据与适配（R1、R2）

- 新快照：**`qps-f140a9790e4015ea`**，50 张表、**15.08 MB**（≤ 200 MB），内容哈希可重放。
- 财务：东方财富 `RPT_F10_FINANCE_GBALANCE/GINCOME/GCASHFLOW`，按报告期批量
  （2015–2025 年报 + 2026/2025 三季报）；分红：好公司池 2021–2025 中期/年度表 +
  2023–2025 三季报补充；行情：腾讯全市场快照（逐行价格日期）；总股本：
  `RPT_F10_EH_EQUITY` 最近一期（池内 82 个缺失代码逐只补齐）。
- 字段映射：`docs/phase4j-field-mapping.md`，逐项列出批量列 → `fin.*` → 政策用途。
  应付类字段原快照为空，批量路径也不映射，保持回归同口径。
- 适配层：`engine/pool_valuation/adapter.py`，把批量表还原成与个股快照同名的
  `fin.*` / `div.*` / `profile.total_shares` / `price.close` 事实，再交给原
  `engine.policy.trial.evaluate_company` 与 `engine.policy.valuation`；政策与估值代码
  `git diff e0da6d2 -- engine/policy engine/valuation` 为空（测试证明）。

## 2. 30 只回归（R3）

| 指标 | 结果 |
|---|---|
| 状态相同 | 30 / 30 |
| 等级相同 | 30 / 30 |
| 预演结论相同 | 30 / 30 |
| 基准价值最大相对差 | **0.0000%** |

回归时批量路径使用原快照的现价、价格日期、总股本，只替换财务与分红输入来源，以隔离字段差异；
差异记录在 `data/pool-valuation/regression-30.json` 与 `docs/phase4j-regression-30.md`。
未改政策、未改原快照。

## 3. 池成员估值（R4、R5）

- 池成员：好公司池 138 + 收息池 142，去重后 **221 家**；银行不估值。
- 结论：候选买点 **2**、等待 **37**、回避 **49**、无法判断 **133**。
- 状态：complete 131、partial 74、unsupported 16；等级 A 54、B 45、C 122。
- 无法判断原因：等级 C 48、终值连续性护栏 44、现金流勾稽 25、路线不支持（周期）16。
- 88 家 A/B 且 complete 的公司按距候选门槛从近到远排序，列现价、门槛价、起约束条件、
  等级、所属池、所有制、周期标记。
- 正式结论账本首次写入 **88 条**；原有 7 条不变，账本共 **95 条**，`--check` 0 问题。
  同一标的、同一分析日、同一快照内容不同 → 冲突不覆盖（既有规则与测试）。
- 报告：`docs/phase4j-pool-valuation-report.md`；报告含结论分布、原因分组、排序、账本口径。

## 4. 抽查复算（R6）

```bash
.venv/bin/python tools/run_pool_valuation.py --show 600519
```

只从批量快照重建该股票的输入事实，交给原政策代码复算，打印输入、三档价值、等级与结论；
不写账本。测试 `test_show_recomputes_from_snapshot` 覆盖。

## 5. 未改变（R7）

- 好公司池 138、收息池 142、银行池 11 的名单与各自账本不变；好公司池/收息池/银行池快照不变。
- 既有 7 条正式结论记录内容不变，本次只追加 88 条。
- 指数、ETF、候选 A 账本、官方估值账本不变；银行估值仍未启用。
- 全部测试：**608 passed**。

## 6. 验收自检（R1–R8）

| 编号 | 结果 |
|---|---|
| R1 | 映射表齐全；50 表 15.08 MB ≤ 200 MB；快照哈希重放测试通过 |
| R2 | `engine/policy`、`engine/valuation` diff 为空；适配层测试通过 |
| R3 | 30/30 状态/等级/结论相同，基准价值最大差 0.0000% |
| R4 | 221 家全部有结果；账本 95 条（7+88），只追加；冲突测试通过 |
| R5 | 报告含分布、原因分组、88 家排序与全部列 |
| R6 | `--show 600519` 可从快照独立复算 |
| R7 | 指数/ETF/三池名单与账本不变；608 passed |
| R8 | `DELIVERY.md` 一页以内 |

## 7. 复现

```bash
.venv/bin/python tools/build_pool_valuation_data.py
.venv/bin/python tools/run_pool_valuation_regression.py
.venv/bin/python tools/run_pool_valuation.py --no-ledger
.venv/bin/python tools/run_pool_valuation.py
.venv/bin/python tools/run_pool_valuation.py --show 600519
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 批量快照说明：[data/pool-valuation/README.md](../data/pool-valuation/README.md)
- 字段映射：[docs/phase4j-field-mapping.md](phase4j-field-mapping.md)
- 回归：[docs/phase4j-regression-30.md](phase4j-regression-30.md)
- 池成员报告：[docs/phase4j-pool-valuation-report.md](phase4j-pool-valuation-report.md)
- 验收清单：[docs/acceptance-checklist.md](acceptance-checklist.md) 第十批 R1–R8
