# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十一批收尾：**只读已提交快照 + 改判记录 + launchd 测试** |
| 本轮实现提交 | `（收尾提交，见 git log）` |
| 上一轮 | `fc63e88`（第四阶段第十一批入口，实现 `301f4aa`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 周期清单 / 池成员重跑 | `cyclical-industries-v1`；221 家中结论改变 **2** 家（君正集团、扬农化工） |
| 30 只测试股重跑 | 结论改变 **0** 家 |
| `ask` 固定问题集 | **46 / 46**（含 2 条改判历史问答），10 类意图 |
| 端到端 30 股 / 4 指数 / 2 银行 / 3 ETF | **30/30、4/4、2/2、3/3** |
| 正式结论账本 | **98 条**：96 条原有 + 2 条改判（君正、扬农），0 问题 |
| 干净归档（`git archive`，无 `data/cache`） | 全部测试 + 46 条问题集通过 |
| 测试 | **660 passed**（干净归档中运行） |

## 2. 收尾三项（T1–T3）

- **T1 只读已提交快照**：`ask`、`update`、池成员估值及测试不再读取 `data/cache/` 或任何被
  git 忽略的路径；所有制改读 `data/income-pool/snapshot-qps-d8fb7e3718675b93/ownership.csv.gz`，
  收入池分红改读同一已提交快照。新增扫描测试（`tests/test_clean_inputs.py`）：
  运行时只允许 `tools/build_*`、`tools/probe_*`、`tools/compress_*` 取数/整理脚本引用缓存。
  `update` 的运行状态改读已提交的只追加日志 `data/ledger/update-log.jsonl`，不再有隐藏状态文件。
- **T2 改判记录**：当账本中某标的最新一条是候选买点/等待/回避、而新评估为无法判断时，追加
  `record_type=reversal`、`conclusion=undetermined` 的记录（原因码、中文原因、政策版本、
  快照 ID、分析日、`reversal_of`）；旧记录不改，从未有正式结论的标的不写。
  已为君正集团（明显周期/化学原料）与扬农化工（明显周期/农化制品）各补 1 条；
  “君正集团以前的结论”显示 2 条，最新为无法判断及原因。
- **T3 launchd 测试**：非 macOS 环境跳过并注明“系统只支持 macOS 定时任务”；本机 macOS 下
  plist 经 `plistlib` 校验（18:30、RunAtLoad=false），安装/卸载 dry-run 均不落盘。

## 3. 对话入口与五个真实样例

`./ask "<中文问题>"`（`--detail` 展开、`--json` 结构化），规则化识别、不调用大模型；
正式结论按规则写入只追加账本，无法判断仅在改判时追加；Claude 使用说明见 `SKILL.md`。
完整卡片见 `docs/phase4k-samples.md`。

1. **问：茅台怎么样** → 等待；现价 1,435.26 元（2026-11-13），候选门槛 686.80 元；正式结论（政策 v1.1）。
2. **问：招商银行怎么样** → 无法判断（银行估值未启用）；PB 0.97、股息率 4.7%、ROE 13.4%、不良 0.94%、拨备 389.39%、核心一级 14.39%。
3. **问：沪深300 现在什么位置** → 无法判断（研究画像）；PE 12.65（分位 76%）、PB 1.32（分位 39%）、候选 A 2026-W45 exit（研究信号，规则未批准）。
4. **问：好公司池有哪些** → 138 家（quality-pool-v1.2，快照 2026-11-12）。
5. **问：君正集团以前的结论** → 2 条；最新为无法判断（改判），原因：明显周期企业路线首版未实现。

## 4. 验收自检（S1–S9 + U1–U4）

| 编号 | 结果 |
|---|---|
| S1 | 周期清单版本化、带申万依据；221+30 重跑；改变 2 家；君正专节说明 |
| S2 | 正式/研究标签测试通过；validated 版本去掉 `-research`；研究模型无「正式结论」 |
| S3 | 399006/159915 范围外；其余指数、ETF 结果不变；官方估值旧 4 条不改 |
| S4 | update 只追加/重复跳过/冲突不覆盖；launchd 可安装可卸载，未自动安装 |
| S5 | 46 条问题覆盖 10 类意图，每类 ≥3 种说法，全部自动测试 |
| S6 | 卡片结构符合要求；默认 8–10 行，≤25 行；`--detail` 展开 |
| S7 | 问题集 46/46；30 股/4 指数/2 银行/3 ETF 全过；禁用词 0；日期/来源齐全 |
| S8 | 660 passed；账本 98 条 0 问题；旧记录不变；所有账本只追加 |
| S9 | 本文件一页以内；5 个真实问答样例见上，完整卡片见 `docs/phase4k-samples.md` |
| U1 | `git archive` 干净目录中全部测试与 46 条问题集通过；无 `data/cache` 引用 |
| U2 | 君正、扬农各 1 条改判；历史问答显示 2 条，最新为无法判断及原因；旧记录不变 |
| U3 | 其他账本、结论、池名单不变；全部测试通过 |
| U4 | 本文件一页以内 |

## 5. 复现

```bash
scripts/verify-clean-archive.sh                  # git archive 干净目录：全部测试 + 46 条问题集
.venv/bin/python tools/run_cyclical_rerun.py --write-ledger
./update && ./update --check
./ask "君正集团以前的结论" && ./ask --detail "招商银行怎么样"
.venv/bin/python tools/run_ask_acceptance.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 详细交付：[docs/phase4k-delivery.md](docs/phase4k-delivery.md)｜周期重跑：[docs/phase4k-cyclical-rerun.md](docs/phase4k-cyclical-rerun.md)｜端到端验收：[docs/phase4k-acceptance.md](docs/phase4k-acceptance.md)
- 样例：[docs/phase4k-samples.md](docs/phase4k-samples.md)｜Claude 使用说明：[SKILL.md](SKILL.md)｜问题集：[data/ask/questions.json](data/ask/questions.json)｜验收清单：[docs/acceptance-checklist.md](docs/acceptance-checklist.md)
