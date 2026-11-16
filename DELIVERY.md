# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/投资/投资分析系统/investment-system/` |
| 本轮阶段 | 第七阶段：**持续监测（红线即时剔除 + 财报季更新 + 变动提醒）**（M1–M7） |
| 本轮实现提交 | `61f2037`（本文档与交付记录同批提交） |

## 1. 系统是什么、怎么用

A 股个股 / 境内宽基指数 / 境内普通被动 ETF / 三池名单的分析引擎：所有结论由**已批准规则 +
已提交固定数据**重算，内部不调用大模型；只前向验证，账本只追加、可复算。

```bash
./ask "招商银行怎么样"              # 卡片（默认 ≤25 行）；--detail 展开、--json 结构化、--no-update 离线
./ask "最近有什么变动"              # 最近 365 天的出池 / 结论变化 / 财报更新 / 扫描失败
./ask --recompute <record_id>      # 按记录引用的快照与政策复算并逐项对比（只读）
./web                              # 本地页面：只监听 127.0.0.1，自动打开浏览器；关闭终端即停止
./update                           # 刷新前向记录（只追加）；含每周红线扫描与到期财报季
.venv/bin/python tools/run_monitoring.py scan   # 立即做一次红线扫描（联网）
```

## 2. 三份文档与证据

| 文档 | 内容 |
|---|---|
| [使用说明](docs/使用说明.md) | 终端 / 本地页面 / Claude Code 怎么问、五种类型、卡片怎么读、出池与「财报未更新」、`--recompute` |
| [规则说明](docs/规则说明.md) | 已批准的全部规则、决策①–⑤、监测与财报季（§3.6–3.7）、已知局限 |
| [维护说明](docs/维护说明.md) | 每日更新、年度重跑、复盘、监测运维、测试隔离、出错处理 |

首次真实扫描报告：[phase7-monitoring-scan.md](docs/archive/phase7-monitoring-scan.md)（232/232，0 命中）。
页面截图：[提问](docs/evidence/web-ask.png)｜[买点总览](docs/evidence/web-overview.png)｜[好公司池](docs/evidence/web-pools.png)。

## 3. 全部批准记录（旧规则与本轮审批）

| 批准对象 | 决策 | 批准日期 | 下次复核 |
|---|---|---|---|
| 个股政策 v1.1（`equity-policy-v1.1`） | 决策② | 2026-11-12 | 2027-05-31 |
| 三池：好公司池 v1.2 / 稳定收息池 v1 / 银行池 v1 | 决策③-1/2/3 | 2026-11-13 | 2027-11-13 |
| 银行专用估值 v2.1（`bank-valuation-v2.1`） | 决策④ | 2026-11-14 | 2027-05-31 |
| **持续监测 `pool-monitoring-v1`（红线即时剔除、财报季、变动提醒）** | **决策⑤** | **2026-11-17** | **2027-11-13** |
| 指数正式买点：不给；只前向记录候选 A 研究信号 | 决策① | 2026-11-12 | — |

**下一次复核日期：2027-05-31**。本轮**不改任何估值规则、阈值、结论数字与账本旧记录**；
新增的只是「何时重跑」与「红线即时剔除」。

## 4. 本轮结果（M1–M7）

| 项 | 结果 |
|---|---|
| M1 政策版本化 | 新增 `data/policies/pool-monitoring-v1.json`（approved，决策⑤，批准 2026-11-17，复核 2027-11-13）；红线三项口径与好公司池 v1.2 **逐项相同**（测试对比主体、事项、质押 50%、审计 5 年、处罚 5 年/0 件） |
| M2 五种情形 | 立案 → 剔除；非标审计 → 剔除；质押 51% → 剔除；质押 50% → 保留；取数失败 → 「本周未完成」，不通过也不剔除。剔除只追加池账本 `exclusion` 记录（旧名单记录逐字节不变）；同一公司多个池都追加；重复扫描不重复剔除；通知失败不影响扫描 |
| M3 财报季 | 5/5、9/5、11/5 后首次更新按报告期抓取；生成新批量快照、旧快照保留；`data/pool-valuation/manifest.json` 切换 `current`；个股估值与银行 v2.1 输入随之切换（追溯字段变化）；缺新报表的公司在卡片注明「财报未更新」；结论变化按既有规则追加正式记录或改判记录，结论不变不写 |
| M4 变动提醒 | `data/ledger/changes.jsonl` 只追加（至少保留 1 年）；页面「数据状态 → 最近变动」与 `./ask "最近有什么变动"` **同一列表、同一排序**；出池或候选买点新增/消失发 macOS 通知（`osascript`），失败不影响任务（测试用替身捕获命令） |
| M5 首次真实扫描 | 2026-11-17 对三池并集 **232 家**逐家扫描：覆盖 **232/232（100%）**，命中 **0**，未完成 **0**；扫描记录写入 `data/ledger/monitoring-scans.jsonl`；报告附数据来源与口径 |
| M6 干净归档 | 本地 **774 passed**；`git archive` 干净归档 **773 passed + 1 skipped**（无 `.git` 的一项跳过）；问题集 **68/68**、测试股 30/30、指数 4/4、银行 2/2、ETF 3/3，全部通过=True |
| M7 文档 | 使用说明、规则说明、维护说明、README、SKILL 已更新；本文件一页以内 |
| 账本与政策 | 相对 `4e2a9d3`：旧账本、旧政策文件**无修改**；新增 `pool-monitoring-v1.json`、`monitoring-scans.jsonl`、`phase7-monitoring-scan.md` 与监测代码/测试 |

## 5. 冻结验收自检（M1–M7）

| 编号 | 结果 |
|---|---|
| M1 | `tests/test_monitoring.py::test_m1_*`：政策批准记录、版本、复核日；红线主体/事项/质押/审计/处罚口径与 `quality-pool-v1.2.json` 逐项相等 |
| M2 | `test_m2_*`：五种夹具情形、质押 50% 边界、取数失败、只追加、旧记录字节不变、多池追加、通知失败容忍 |
| M3 | `test_m3_*`：新快照/旧快照保留/指针切换/估值切换/缺报表提示/正式结论追加/改判追加/银行输入新版本文件 |
| M4 | `test_m4_*`：页面与 `ask` 变动列表一致；`osascript` 命令被替身捕获；非 macOS 跳过；失败不抛出 |
| M5 | `tools/run_monitoring.py scan` 首次真实运行；`docs/archive/phase7-monitoring-scan.md` 记录覆盖率、命中与来源；`check` 0 问题 |
| M6 | 本地全量测试与干净归档如上；`git diff --name-status 4e2a9d3..HEAD -- data/ledger data/policies` 只有新增文件，旧文件不变 |
| M7 | 本文件 + 三份文档 + 首次扫描报告 |

## 6. 说明

- **首次扫描的公告来源**：东方财富公告接口当次返回 567（限流），改用新浪公告列表（最近 120 天）；
  更早公告由已提交的 `governance-review-v1` 与池快照 `governance-penalty.csv.gz` 覆盖（建池时已
  逐条核对到 2026-11-12）。审计意见用已提交池快照（2021–2025 年报），质押用最新定期报告 +
  更新更晚的质押明细；三项口径与好公司池 v1.2 完全相同。后续每周重试东方财富接口。
- **银行估值输入的刷新范围**：v2.1 的 ROE / 不良率 / 拨备是年度指标，年报季同时刷新银行输入；
  半年报/三季报只更新个股收入表，不把季度数当年度用（详见《规则说明》§3.7）。
- **批前日常追加未提交**（按月提交约定保留，本批未删改、未提交）：`data/ledger/equity-conclusions.jsonl`、
  `data/ledger/official-valuation-daily.jsonl`、`data/ledger/update-log.jsonl` 中的真实运行追加。
- **新增文件**：`data/policies/pool-monitoring-v1.json`、`data/ledger/monitoring-scans.jsonl`、
  `docs/archive/phase7-monitoring-scan.md`；出池时才创建 `changes.jsonl` 与池账本 `exclusion` 记录。
  同一 ISO 周已扫描就跳过；取数失败记「本周未完成」，下周重试，不默认通过、不剔除。

## 7. 复现

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider              # 774 passed
scripts/verify-clean-archive.sh                                # git archive：773 passed + 1 skipped；68/68；全部通过=True
.venv/bin/python tools/run_monitoring.py status                # 最近扫描、覆盖率、出池、变动
.venv/bin/python tools/run_monitoring.py check                 # 0 问题
./ask --no-update "最近有什么变动"                              # 与页面同一列表
git diff --name-status 4e2a9d3..HEAD -- data/ledger data/policies  # 旧文件不变，只有新增
.venv/bin/python scripts/verify-ledger-snapshots.py            # 快照与清单哈希通过
```

依据与证据：[使用说明](docs/使用说明.md)｜[规则说明](docs/规则说明.md)｜[维护说明](docs/维护说明.md)｜
[首次扫描报告](docs/archive/phase7-monitoring-scan.md)。政策：[data/policies/](data/policies/)；账本：[data/ledger/](data/ledger/)。
