# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第九批：**三套标准批准落地 + 恒生科技指数（HSTECH）** |
| 本轮实现提交 | `4e8b3b1` |
| 上一轮 | `1406d65`（第四阶段第八批收尾入口，实现 `b3d1f4e`） |

## 1. 批准落地（Q1、Q2）

| 规则 | 已批准文件 | 本次入池 | 账本 |
|---|---|---:|---|
| 好公司池 | `quality-pool-v1.2.json` | 138 | 4 条（含 v1.2，`qph-fc548bf288b854fe`） |
| 稳定收息池 | `income-pool-v1.json` | 142 | 2 条（含 v1，`iph-3a3ca849f50a9f58`） |
| 银行池 | `bank-pool-v1.json` | 11 | 2 条（含 v1，`bph-13b62d2bcfb016c0`） |

三个 approved 文件均 `status=approved`、`approved=true`、批准日期 2026-11-13；参数与各自草案
逐项相同；`approval` 记录决策③、依据、已知缺口（未经独立审查）、补救（每年复盘）、
下次复核 2027-11-13。用最新快照重跑，名单与草案运行逐家相同；三本池账本各追加 1 条，旧记录不变。
年度重跑：`.venv/bin/python tools/run_annual_pools.py`（先 `--dry-run`）。

## 2. 银行估值（Q3）

`bank-check-v1-draft` 保持 `approved=false`，文件中写明“决策③-4：不启用；银行模型保持 research”。
正式结论账本 7 条无银行；CLI 600036 仍 `insufficient_data`、valuation null；银行池与银行账本
只追加 approved 池记录。

## 3. 恒生科技指数（Q4、Q5）

- 判定：**`short_history_only`**。只用恒生指数有限公司官方数据。
- 官方日收盘：2021-11-11 至 2026-11-12，1,230 个交易日。
- 官方全收益（Gross Total Return，12083.00）：同区间 1,230 个观测。
- 官方估值历史：**不可得**。`historical_pe` / `historical_dy` 的 HSTECH 文件返回 AEM 404 HTML；
  HSI/HSCEI 同路径为 Excel。官方指数单张只有月度当前 PE/股息率，且不提供 PB。
- 官方当前估值：2026-10-30，PE **25.43**、股息率 **1.23%**，PB 不提供。
- 已接入：官方收盘/全收益序列、月度 PE 当前快照、HSTECH 指数快照
  `snap-e2810b5ea15dc211`（港股、港元；`valuation_history.available=false`）。
- 未做：5 年估值分位、候选 A 周度信号、研究回测、信号账本、官方估值前向记录
  （无官方当日值）。未使用第三方估值、ETF 估值或拼接序列。
- 名称识别：恒生科技 / 恒生科技指数 / HSTECH → `INDEX:HSTECH.HKEX`；
  恒生指数、恒生国企、恒生科技 ETF 等其他港股指数/ETF 仍为范围外。
- CLI `恒生科技` 走与其他指数共用的 profile/signal 代码；因无官方 PE 日频历史，
  `insufficient_data` / `undetermined`，不生成信号、不写账本。

## 4. 未改变（Q6）

- 三大既有指数 000300 / 000905 / 000852 的候选 A 结果仍 `complete` / `exit`；
  候选 A 账本仍 3 条，官方估值前向账本仍 4 条（无 HSTECH 记录）。
- 个股结论账本仍 7 条；好公司池/收息池/银行池既有快照、报告与旧账本记录不变。
- 不接入任何港股 ETF 或 QDII。

## 5. 验收自检（Q1–Q7）

| 编号 | 结果 |
|---|---|
| Q1 | 三文件 approved；参数/条件/敏感性/范围与草案逐项相等；批准记录与复核日齐全 |
| Q2 | 三池 approved 重跑名单逐家相同；账本各追加 1 条，旧记录不变 |
| Q3 | 银行估值不启用；CLI 与正式结论账本无银行结论 |
| Q4 | HSTECH 可行性逐项来源与判定；全部 probes 为恒生指数公司官方；PE/DY/PB 历史明确不可得 |
| Q5 | 已接入部分复用共享 profile/signal 代码；三种名称识别与范围外名称均有测试 |
| Q6 | 既有指数/个股/ETF/账本不变；**600 passed** |
| Q7 | 本文件一页以内 |

## 6. 复现

```bash
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_income_pool.py
.venv/bin/python tools/run_bank_pool.py
.venv/bin/python tools/build_hstech_data.py
.venv/bin/python -m engine.cli 恒生科技
.venv/bin/python tools/run_annual_pools.py --dry-run
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 批准文件：[data/policies/README.md](data/policies/README.md)
- 恒生科技可行性：[docs/phase4i-hstech-feasibility.md](docs/phase4i-hstech-feasibility.md)
- 详细交付：[docs/phase4i-delivery.md](docs/phase4i-delivery.md)
- 验收清单：[docs/acceptance-checklist.md](docs/acceptance-checklist.md) 第九批 Q1–Q7
- 年度重跑：[tools/run_annual_pools.py](tools/run_annual_pools.py)
