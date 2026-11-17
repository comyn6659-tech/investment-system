# 交付入口

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/投资/投资分析系统/investment-system/` |
| 本轮阶段 | 第七阶段收尾：**质押红线改为每周实查（P1–P3）** |
| 本轮实现提交 | `e0c444b`（交付文档单独提交） |
| 范围 | 只改质押取数、证据与测试/文档；未改政策文件、阈值、公告/审计逻辑、旧账本记录 |

## 1. 修了什么

| 编号 | 修复 | 验证 |
|---|---|---|
| P1 每家都查明细 | 去掉“已提交质押比例为 0 就跳过”。基准日 = 已提交表 `report_date`（缺失用池快照取数日）；基准日后无新公告才沿用已提交比例，证据写“明细已查，基准日后无新公告”；有控股股东（无控股股东时第一大股东）新公告则按最新累计质押股数 ÷ 持股数重算；早于基准日的明细一律不用；比例 >100% 或持股数缺失 → 未完成。 | 已提交 0 + 基准日后新质押 60% → 命中；已提交 0 + 仅 2015 年旧明细 → 比例 0 通过；153% → 未完成。 |
| P2 取不到就未完成 | 明细请求本次扫描内重试 3 次、间隔递增；仍失败 → 本周未完成；删除“失败沿用已提交表”分支。 | 连续失败 3 次 → `live_checked=False`、状态 `incomplete`，不命中、不通过。 |
| P3 证据与汇总 | 每家质押证据增加 `live_checked`、`base_date`、`new_announcements`；扫描记录与返回汇总增加 `live_checked` 家数；CLI 状态/摘要显示。 | 真实扫描 232/232 家 `live_checked=True`；`check` 无问题。 |

## 2. 真实运行与验收数字

- **真实全量扫描（追加新记录）**：232/232 覆盖，`live_checked=232`，命中 **0**，待定 **0**，未完成 **0**；`data/ledger/monitoring-scans.jsonl` 旧记录逐字节不变。
- **国药股份（600511）**：已提交比例为 0，仍实查明细；基准日 2026-11-12，基准日后 0 条新公告，比例 0，状态通过。
- **重算变化**：8 家因基准日后新公告重算（如 600285 羚锐制药 27.32%→23.04%、601100 恒立液压 0→10.09%），均仍 ≤50%，无新增命中。
- **本地测试：816 passed**（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`）。
- **干净归档：815 passed + 1 skipped**（`git archive` 后重跑；`tests/test_pool_valuation.py` 无 `.git` 跳过）。
- **新增测试：5 项**（P1 两项、P2 一项、P3 两项）。
- **数据完整性**：`data/policies` 零差异；已有账本只追加、无删除/改写；未改公告/审计判定。

## 3. 复现命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider   # 816 passed
scripts/verify-clean-archive.sh                                                # git archive：815 passed + 1 skipped
.venv/bin/python tools/run_monitoring.py check                                 # problems: []
.venv/bin/python tools/run_monitoring.py scan --dry-run                        # 232/232，live_checked=232，命中 0
```
