# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十批：**好公司池 + 收息池成员的个股估值** |
| 本轮实现提交 | `1948318` |
| 上一轮 | `e0da6d2`（第四阶段第九批入口，实现 `4e8b3b1`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 池成员（两池去重、非银行） | 221 |
| 候选买点 / 等待 / 回避 / 无法判断 | 2 / 37 / 49 / 133 |
| 状态 complete / partial / unsupported | 131 / 74 / 16 |
| 等级 A / B / C | 54 / 45 / 122 |
| 正式结论账本写入 | 88（原 7 条不变，共 95 条） |
| 30 只回归 | **30 / 30**；基准价值最大相对差 **0.0000%** |
| 批量快照 | `qps-f140a9790e4015ea`，50 张表、15.08 MB |
| 测试 | **608 passed** |

## 2. 批量输入与适配（R1、R2）

- 财务按报告期批量取东方财富三张合并报表（2015–2025 年报 + 2025/2026 三季报）；
  分红用好公司池 2021–2025 中期/年度表并补 2023–2025 三季报；行情用全市场快照
  （价格日期 2026-11-12）；总股本用最近一期股本变动。
- 字段映射：`docs/phase4j-field-mapping.md`；适配层
  `engine/pool_valuation/adapter.py` 只把批量表还原成同名 `fin.*`/`div.*`/
  `profile.*`/`price.*` 事实，再交给原政策与估值代码。
- `git diff e0da6d2 -- engine/policy engine/valuation` 为空（测试证明）；应付类字段
  与原快照保持相同可用集合，不新增口径。

## 3. 回归与池成员（R3–R5）

30 只测试股状态、等级、预演结论 **30/30 相同**，基准价值最大差 **0.0000%**；批量路径
使用原快照的现价/价格日期/总股本，只换财务与分红输入。221 家全部有结果；88 家 A/B 且
complete 的公司按“距候选门槛”从近到远列出现价、门槛价、起约束条件、等级、所属池、
所有制、周期标记。无法判断按原因分组：等级 C 48、终值护栏 44、现金流勾稽 25、
路线不支持 16。报告：`docs/phase4j-pool-valuation-report.md`；回归：`docs/phase4j-regression-30.md`。

## 4. 结论账本（R4）

首次写入 **88 条**（分析日 2026-11-13、批量快照 `qps-f140a9790e4015ea`）；原有 7 条
内容不变，账本共 95 条，`--check` 0 问题。只追加；同一标的、同一分析日、同一快照内容
不同 → 冲突不覆盖（既有测试覆盖）。银行不估值；银行池与银行账本未改；银行估值仍未启用。

## 5. 未改变（R7）

好公司池 138、收息池 142、银行池 11 的名单与各自账本不变；三池快照、指数/ETF、
候选 A 账本、官方估值账本不变；既有 7 条正式结论记录不变；全部测试 **608 passed**。

## 6. 验收自检（R1–R8）

| 编号 | 结果 |
|---|---|
| R1 | 映射齐全；50 表 15.08 MB ≤ 200 MB；快照哈希重放测试通过 |
| R2 | 政策/估值代码 diff 为空；适配层有测试 |
| R3 | 30/30 状态/等级/结论相同，基准价值最大差 0.0000% |
| R4 | 221 家全部有结果；账本 95 条（7+88），只追加；冲突规则有测试 |
| R5 | 报告含分布、原因分组、88 家排序与全部列 |
| R6 | `--show 600519` 可从快照独立复算 |
| R7 | 指数/ETF/三池名单与账本不变；608 passed |
| R8 | 本文件一页以内 |

## 7. 复现

```bash
.venv/bin/python tools/build_pool_valuation_data.py
.venv/bin/python tools/run_pool_valuation_regression.py
.venv/bin/python tools/run_pool_valuation.py --no-ledger
.venv/bin/python tools/run_pool_valuation.py
.venv/bin/python tools/run_pool_valuation.py --show 600519
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 批量快照说明：[data/pool-valuation/README.md](data/pool-valuation/README.md)
- 字段映射：[docs/phase4j-field-mapping.md](docs/phase4j-field-mapping.md)
- 回归：[docs/phase4j-regression-30.md](docs/phase4j-regression-30.md)
- 池成员报告：[docs/phase4j-pool-valuation-report.md](docs/phase4j-pool-valuation-report.md)
- 详细交付：[docs/phase4j-delivery.md](docs/phase4j-delivery.md)
- 验收清单：[docs/acceptance-checklist.md](docs/acceptance-checklist.md) 第十批 R1–R8
