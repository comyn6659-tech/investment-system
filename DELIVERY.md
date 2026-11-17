# 交付入口

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/投资/投资分析系统/investment-system/` |
| 本轮阶段 | 第七阶段返工：**红线扫描判定一致性与可审计性（R1–R6）** |
| 本轮实现提交 | `4b583df`（交付文档单独提交） |
| 范围 | 只改监测代码、测试、文档与运行记录；未改任何政策文件、阈值、旧账本记录 |

## 1. 修了什么

| 编号 | 返工内容 | 验证 |
|---|---|---|
| R1 判定统一 | 新增 `engine/monitoring/classify.py` 为监测与建池共用的唯一事件判定函数；`tools/build_governance_data.py` 改为调用它。已提交建池快照/人工核对按 `art_code` 沿用原判定；样板句改正则去除（覆盖“任何”“或者”等变体）；内幕交易、短线交易、环保、安全生产、金融监管显式判“不计入”。 | 国药股份 2024-01-10 公告正文夹具（`tests/fixtures/600511-2024-01-10.txt`）：`counted=False`，理由 `内幕交易（他公司股票），非信息披露类`；建池与监测结果一致。 |
| R2 不确定待定 | 正文取不到、或正文无法同时确定主体/事项，记 `pending`（待定），不计入、不算通过；只有凭正文（或已提交/人工核对判定）计入的事件才触发剔除。 | `test_r2_missing_content_is_pending_and_not_a_pass`、`test_r2_content_without_matter_is_pending`：待定公司状态为 `incomplete`，不产生红线命中。 |
| R3 备用源/质押失败 | 新浪列表首页解析不到行、翻页上限未覆盖起始日 → 记未完成；质押比例 >100%、明细早于最新定期报告 → 不采用明细；无可用数据 → 未完成。 | 新浪首页空、翻页不覆盖、比例 153%、2015 年过期明细、无数据五类测试；真实 600285 首次明细超时后自动沿用已提交质押表。 |
| R4 扫描证据 | `monitoring-scans.jsonl` 升为 `monitoring-scan/2`，逐家写：质押（比例/主体/截至日/来源/状态）、审计（年份→意见/来源）、公告（eastmoney/sina/已提交、检索区间、事件、是否计入、理由）。只追加。 | `test_r4_member_dict_contains_pledge_audit_penalty_evidence`、`test_r4_scan_record_appends_evidence_and_keeps_old_records`；真实扫描 232 家全部带证据，`check` 无问题。 |
| R5 清理缓存 | 删除 `data/cache/monitoring/2026-11-17/`；生产取数不再读写任何监测缓存；维护说明写明“监测不使用缓存”。 | 目录已不存在；`tests/test_clean_inputs.py` 通过；`sources.py` 已移除缓存读写代码。 |
| R6 重新首扫 | 修复后真实全量扫描（不覆盖旧记录，追加新记录）。 | 232/232 覆盖（100%），命中 **0**，待定 **0**；国药股份判定为 `ok`（理由同上）。第一次扫描 600285 质押接口超时（231/232），修复“明细失败沿用已提交质押表”后重跑 232/232；两次均只追加。 |

## 2. 验收数字

- **本地测试：811 passed**（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`）。
- **干净归档：810 passed + 1 skipped**（`git archive` 后重跑；`tests/test_pool_valuation.py` 无 `.git` 跳过）。
- **新增测试：22 项**（R1–R4；R5 由目录/清洁输入测试覆盖；R6 为真实运行）。
- **真实扫描记录**：`data/ledger/monitoring-scans.jsonl` 新增 2 条 `monitoring-scan/2`（第一次 231/232，第二次 232/232）；旧记录首字节不变。
- **国药股份（600511）**：状态 `通过`；公告 2024-01-10 立案，正文明确为副总经理个人“涉嫌内幕交易其他公司股票”“不涉及本公司股票交易”，按 matter_scope 不计入；扫描记录理由为 `内幕交易（他公司股票），非信息披露类`。
- **数据完整性**：`data/policies` 零差异；已有账本记录只追加、无删除/改写；`data/cache/monitoring/2026-11-17/` 已删除。

## 3. 复现命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider   # 811 passed
scripts/verify-clean-archive.sh                                                # 810 passed + 1 skipped
.venv/bin/python tools/run_monitoring.py check                                 # problems: []
.venv/bin/python tools/run_monitoring.py scan --dry-run                        # 232/232，命中 0，待定 0
```
