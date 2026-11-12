# 第四阶段第九批交付：三套标准批准落地 + 恒生科技指数

本批只做 `batch4i-approval-and-hstech-instructions.md`：三套公司标准按决策③批准落地、以 approved
规则重跑三池并追加账本、银行估值明确不启用；新增恒生科技指数（HSTECH）官方数据可行性核查与
已取得部分接入。不改任何规则阈值，不启用银行估值，不改个股结论，不做对话入口。

## 1. 批准落地（Q1、Q2）

| 规则 | 已批准文件 | 草案 | 参数 | 本次入池 |
|---|---|---|---|---:|
| 好公司池 | `quality-pool-v1.2.json` | `quality-pool-v1.2-draft.json` | 逐项相同 | 138 |
| 稳定收息池 | `income-pool-v1.json` | `income-pool-v1-draft.json` | 逐项相同 | 142 |
| 银行池 | `bank-pool-v1.json` | `bank-pool-v1-draft.json` | 逐项相同 | 11 |

- 三个文件均为 `status=approved`、`approved=true`、批准日期 2026-11-13；
  `approval` 记录决策③、依据、已知缺口（未经独立审查）、补救（每年复盘）、下次复核 2027-11-13。
- 用已有最新快照与 approved 规则各重跑一次；名单与草案运行**逐家相同**（测试比对
  `in_pool` 代码列表，并比对 `counts`）。
- 三本池账本各追加 1 条，只追加：
  - 好公司池 4 条：v1 / v1.1-draft / v1.2-draft / v1.2，`qph-fc548bf288b854fe`；
  - 稳定收息池 2 条：v1-draft / v1，`iph-3a3ca849f50a9f58`；
  - 银行池 2 条：v1-draft / v1，`bph-13b62d2bcfb016c0`。
- 年度重跑命令：`.venv/bin/python tools/run_annual_pools.py`（先 `--dry-run` 查看步骤）。
  该命令按顺序重建三池快照、以 approved 规则重跑、追加账本；本批未执行重建。

## 2. 银行估值（Q3）

- `bank-check-v1-draft` 保持 `approved=false`，新增 `decision3`：
  **“不启用（2026-11-13，决策③-4）；银行模型保持 research”**。
- 正式结论账本 7 条无银行；CLI 600036 仍 `insufficient_data`、valuation null；
  银行池与银行账本不变（只追加 approved 池记录）。

## 3. 恒生科技指数（Q4、Q5）

### 3.1 可行性判定

`docs/phase4i-hstech-feasibility.md` 与 `data/coverage/hstech-feasibility.json` 逐项记录：

| 数据 | 判定 |
|---|---|
| 官方日收盘（02083.00） | 可得，2021-11-11 至 2026-11-12，1,230 个交易日 |
| 官方全收益（12083.00） | 可得，同区间 1,230 个观测；净全收益 22083.00 仅登记不混用 |
| 官方 PE 历史 | 不可得；`historical_pe/.../hstech.xlsx` 返回 AEM 404 HTML，HSI/HSCEI 同路径为 Excel |
| 官方股息率历史 | 不可得；同上 |
| 官方 PB 历史 | 不可得；官方指数单张不提供 PB |
| 官方当前估值 | 月度指数单张（2026-10-30）：PE 25.43、股息率 1.23%，PB 不提供 |
| 本批判定 | `short_history_only` |

所以只接入官方收盘、全收益、月度当前 PE 快照与指数快照；**不做** 5 年估值分位、候选 A 周度信号、
研究回测、信号账本。未使用任何第三方估值、ETF 估值或拼接序列。

### 3.2 已接入

- 序列：`official_close`（1,230 条）、`total_return_close`（1,230 条）、
  `official_pe_ratio_1`（2026-10-30，25.43）。
- 快照：`snap-e2810b5ea15dc211`，as_of 2026-11-12；来源仅 `hsi.official_history` 与 `hsi.factsheet`；
  覆盖块 `valuation_history.available=false`、`supports_5y_window=false`；港股/港元。
- 名称识别：恒生科技 / 恒生科技指数 / HSTECH → `INDEX:HSTECH.HKEX`；
  恒生指数、恒生国企、恒生科技 ETF 等其他港股指数/ETF 仍为范围外。
- CLI `恒生科技` 走与其他指数共用的 `build_index_profile` 与 `build_candidate_a_signal`；
  因无官方 PE 日频历史，结果为 `insufficient_data` / `undetermined`，不生成信号、不写账本。

## 4. 未改变（Q6）

- 三大既有指数 000300 / 000905 / 000852 的候选 A 结果仍 `complete` / `exit`；
  候选 A 账本仍 3 条，官方估值前向账本仍 4 条（无 HSTECH 记录）。
- 个股结论账本仍 7 条；好公司池/收息池/银行池既有快照、报告与旧账本记录不变。
- 不接入任何港股 ETF 或 QDII。

## 5. 验收自检（Q1–Q7）

| 编号 | 结果 |
|---|---|
| Q1 | 三文件 approved；parameters/groups/sensitivity/scope 与草案逐项相等；approval 与 next_review_date 齐全 |
| Q2 | 三池 approved 重跑名单逐家相同；账本各追加 1 条，旧记录不变 |
| Q3 | 银行估值不启用；CLI 与正式结论账本无银行结论 |
| Q4 | HSTECH 可行性逐项来源与判定；全部 probes 为恒生指数公司官方；PE/DY/PB 历史明确不可得 |
| Q5 | 已接入部分复用共享 profile/signal 代码；三种名称识别与范围外名称均有测试 |
| Q6 | 既有指数/个股/ETF/账本不变；**600 passed** |
| Q7 | `DELIVERY.md` 一页以内 |

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

- 恒生科技可行性：[docs/phase4i-hstech-feasibility.md](phase4i-hstech-feasibility.md)
- 批准文件：[data/policies/README.md](../data/policies/README.md)
- 验收清单：[docs/acceptance-checklist.md](acceptance-checklist.md) 第九批 Q1–Q7
