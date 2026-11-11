# 阶段一返工报告（R1–R10）

> **后续**：本报告针对提交 `ef2ab38`。设计方复验后提出的 F1–F3 已在
> [phase1-rework-f1f3.md](phase1-rework-f1f3.md) 中处理，快照 ID 与测试数量以该报告为准。


- 项目：`/Users/comyn/Documents/deepseek-harness/投资/investment-system/`
- 依据：《第一阶段独立验收与返工指令》（审查日期 2026-11-11，被审查提交 `b104138`）
- 范围：**只修复 R1–R10，不进入第二阶段，不改变任何投资参数**
- 旧系统 `../valuation/` 未修改、未读取
- 返工前基线：审计脚本 14 项检查 **0 通过 / 14 失败**（已复现）
- 返工后：审计脚本 **14 通过 / 0 失败**；项目测试 **134 passed**

---

## 1. R1–R10 修复对照

### R1（P0）快照必须属于请求标的

| 项 | 内容 |
|---|---|
| 根因 | `_load_snapshot` 只要拿到 snapshot_id 就加载，从不核对它属于谁；`SnapshotStore.load` 也不核对请求 ID 与文件声明 ID |
| 改动 | `engine/pipeline.py::Analyzer._load_snapshot` 强制比对 `instrument_id` 与 `asset_class`；`engine/data/snapshot.py::SnapshotStore.load` 传入 `expected_id` 并校验；新增原因码 `SNAPSHOT_INSTRUMENT_MISMATCH` / `SNAPSHOT_ASSET_CLASS_MISMATCH` / `SNAPSHOT_ID_MISMATCH` |
| 测试 | `test_rework_regressions.py::test_r1_snapshot_from_other_instrument_is_rejected`、`test_r1_each_instrument_uses_its_own_latest_snapshot`、`test_r1_asset_class_mismatch_is_rejected` |
| 真实输出 | `510300` + `snap-d76db31ce212b446`（中证500ETF）→ `analysis_status=engine_error`、`reason_codes=[ENGINE_ERROR, SNAPSHOT_INSTRUMENT_MISMATCH]`、`payload.price=null`、`snapshot_id=null` |

### R2（P0）语义硬规则放进出口校验

| 项 | 内容 |
|---|---|
| 根因 | 降级逻辑只写在 `decision/policy.py`，出口 `validate_result` 只拦截 `candidate`；文本出口完全不校验；`data.get()` 让缺失字段被当成 `None` 静默通过 |
| 改动 | `engine/contracts/validation.py`：① 18 个必填字段缺失即违规；`analysis_status/valuation_state/decision/intent/payload` 为 null 即违规；② `candidate/wait/avoid` 一律要求 `validated + complete`；③ research/deprecated 不得给任何正式价格判断；④ payload 严格白名单，未完成或未 validated 时 `valuation / premium_discount / signal` 必须为空；⑤ `research_signal` 必须带 `model_id` 且 `model_status=research`；`render_text` 入口调用 `enforce`；`AnalysisResult.dump_json` 也调用 `enforce` |
| 测试 | `test_r2_research_cannot_export_formal_price_decision`（JSON 与文本两个出口都被拒）、`test_r2_unsupported_cannot_carry_valuation_payload`、`test_r2_missing_status_fields_are_violations`、`test_r2_null_status_fields_are_violations`、`test_r2_research_signal_requires_model_provenance`、`test_r2_priced_payload_requires_complete_validated`、`test_dump_json_cannot_bypass_validation` |
| 真实输出 | research + `neutral` + `wait` → `ContractViolation`（两个出口）；unsupported 结果注入 `valuation.buy_hi=99` → `ContractViolation`；删除三个状态字段 → `["缺少必填字段: ['analysis_status','valuation_state','decision']"]` |

### R3（P1）显式交易所冲突不得被静默改写

| 项 | 内容 |
|---|---|
| 根因 | `_resolve_code` 先按 venue 过滤，过滤结果非空就直接采用，过滤为空则退回全量候选；前缀与后缀同时出现时没有冲突检测 |
| 改动 | `engine/identity/normalize.py` 记录 `prefix_venue/suffix_venue` 与 `venue_conflict`；`engine/identity/resolver.py` 对格式冲突返回 `IDENTITY_VENUE_SYNTAX_CONFLICT`，对「指定交易所无对应标的」返回 `IDENTITY_VENUE_CONFLICT` 并列出实际候选，均不返回 instrument |
| 测试 | `test_r3_wrong_exchange_is_rejected`（601398.SZ、510300.SZ）、`test_r3_prefix_suffix_conflict_is_rejected`（SZ601398.SH）、`test_r3_matching_exchange_still_resolves`、`test_r3_cli_json_returns_clarification_not_crash` |
| 真实输出 | `601398.SZ` → `needs_clarification`、`instrument=null`、`IDENTITY_VENUE_CONFLICT`，退出码 2 |

### R4（P1）无实体的范围排除必须是合法契约

| 项 | 内容 |
|---|---|
| 根因 | 启发式范围排除返回的 `Resolution` 没有 instrument，主流程却 `assert instrument is not None`，把正常输入变成 `AssertionError` + 退出码 3 |
| 改动 | `engine/pipeline.py` 新增 `_scope_only_result`：`instrument=null`、`payload={"payload_type":"none"}`、`valuation_state=not_applicable`、`decision=undetermined`、结构化澄清话术 |
| 测试 | `test_r4_heuristic_scope_exclusion_is_structured`、`test_r4_cli_json_for_scope_only_input` |
| 真实输出 | `--json 全球ETF` → 退出码 0，`analysis_status=unsupported`、`instrument=null`、`OUT_OF_SCOPE_CROSS_BORDER` |

### R5（P1）时间必须解析成带时区的时刻再比较

| 项 | 内容 |
|---|---|
| 根因 | `facts_available_by` 用字符串 `<=` 比较，`08:00+00:00` 会被当成早于 `12:00+08:00`；只有日期的公告被当成当天 00:00 已可知 |
| 改动 | 新增 `engine/data/timeutil.py`：`parse_instant` / `is_available_by` / `end_of_day` / `infer_frequency`；只有日期的可用时间按「当日结束」保守上界处理并标记 `precision=date`；无法解析一律视为未知、不放行；快照 coverage 增加 `point_in_time_granularity` 与 `availability_precision_counts` |
| 测试 | `test_r5_utc_fact_is_not_released_before_local_time`、`test_r5_date_only_is_not_known_at_midnight`、`test_r5_unknown_availability_never_released`、`test_r5_snapshot_records_precision` |
| 真实输出 | `08:00+00:00` 对 `12:00+08:00` → 不释放；`2026-11-10` 对 `2026-11-10T00:00+08:00` → 不释放 |

### R6（P1）不得把新行情倒填到旧日期

| 项 | 内容 |
|---|---|
| 根因 | `AS_OF` 写死为 `2026-11-10`，点位却取实时「最新价」，构建时也不校验行情日期 |
| 改动 | 重写 `tools/build_snapshots.py`：先取日线最后一行确定 `market_date`；行情日期与 `--as-of` 不符直接 `BuildError`；当日未收盘的 bar 标记 `intraday` 且 `available_at=null`，不称收盘；ETF 价格改用 `fund_etf_hist_em` 收盘行；个股不再采集无可靠时刻标记的实时价；快照记录 `coverage.build.mode/market_date/bar_status` |
| 测试 | `test_r6_builder_uses_actual_market_date`、`test_r6_builder_refuses_mismatched_expected_date`、`test_r6_intraday_bar_is_not_labelled_as_close` |
| 真实输出 | 模拟 11-11 行情 `9999` → `index.level.period=2026-11-11`、`available_at=null`（不再写成 11-10 收盘）；本次真实重建 `market_date=2026-11-10`、`bar_status=closed` |

### R7（P1）调查器不得写死结论

| 项 | 内容 |
|---|---|
| 根因 | 全收益探针无条件写 `failed/rows=0`；频率写死「月度」；跟踪误差没调用任何接口却计入「实测」 |
| 改动 | `tools/probe_data_sources.py`：① 全收益逐代码实际调用并按「全空 / 部分可用 / 全部异常」分类；② 频率由观测日期中位间隔计算（`infer_frequency`）；③ 增加 `called` 与 `parameters` 字段；④ 跟踪误差标为 `not_tested`、`called=false`，汇总单列；⑤ 结论限定为「已测来源与参数」 |
| 测试 | `test_r7_total_return_probe_reflects_actual_result`、`test_r7_total_return_probe_marks_empty_and_errors`、`test_r7_untested_tracking_error_is_not_counted`、`test_r7_frequency_is_derived_from_observations` |
| 真实输出 | 模拟三个代码都有数据 → `status=ok, rows=3`；实际重跑：`已调用=22，ok=12，partial=8，failed=2，not_tested=1` |

### R8（P1）加载快照要求 ID 完整并执行结构校验

| 项 | 内容 |
|---|---|
| 根因 | `from_dict` 在 `snapshot_id` 缺失时直接接受；不校验请求 ID；不调用结构校验；重复事实与非法值要手动 `validate()` 才发现 |
| 改动 | `Snapshot.from_dict(data, expected_id=...)`：必填字段检查 → 声明 ID 必须是 `snap-` 前缀 → 声明 ID 等于内容哈希 → 等于请求 ID → 结构校验（重复 fact_id、非法状态/值、非法 instrument_id/as_of）；`SnapshotStore.save` 写盘前同样校验 |
| 测试 | `test_r8_missing_snapshot_id_is_rejected`、`test_r8_content_change_without_new_id_is_rejected`、`test_r8_requested_id_must_match_file_content`、`test_r8_duplicate_facts_are_rejected`、`test_snapshot_save_refuses_invalid_snapshot` |
| 真实输出 | 删除 `snapshot_id` → `SnapshotError`；请求 `snap-000…` 而文件是 `snap-aef6…` → `SNAPSHOT_ID_MISMATCH` |

### R9（P1）同一 as_of 多版本按时间选最新

| 项 | 内容 |
|---|---|
| 根因 | `latest_for` 按 `(as_of, snapshot_id)` 排序，哈希字典序替代了修订时间；manifest 不记录抓取时间 |
| 改动 | manifest 记录 `fetched_at`；`latest_for` 按 `(as_of, fetched_at, snapshot_id)` 排序；明确「哈希只作身份与校验，不作新旧顺序」 |
| 测试 | `test_r9_latest_uses_fetch_time_not_hash_order`、`test_r9_manifest_records_fetch_time` |
| 真实输出 | 旧版 `snap-ffff…`（11-10 抓取）与新版 `snap-0000…`（11-11 抓取）→ 选 `snap-0000…` |

### R10（P1）事实的直接构造 / 便捷构造 / 反序列化规则统一

| 项 | 内容 |
|---|---|
| 根因 | `from_dict` 绕过了构造器的值检查；`validate()` 不检查「已知状态必须有值」 |
| 改动 | `Fact.__post_init__` 统一把字符串状态转枚举并执行 `validate()`；`validate()` 增加：known/known_zero/stale 必须有值、拒绝 NaN/Infinity/布尔、known_zero 必须为 0、`available_at` 必须可解析 |
| 测试 | `test_r10_from_dict_rejects_invalid_known_values`（None/nan/inf/-inf）、`test_r10_direct_and_helper_construction_agree`、`test_r10_missing_status_cannot_smuggle_value_via_from_dict` |
| 真实输出 | 三种非法值全部 `FactError`，与便捷构造器行为一致 |

---

## 2. 测试

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

```text
134 passed in 0.97s
```

分布：识别 26、契约 17、事实 11、流程 20、快照/重放 6、CLI 11、**返工回归 43**。
没有删除或放宽任何原有断言；新增测试全部直接构造非法对象后调用真实出口。

独立复现：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  /Users/comyn/Documents/Codex/2026-11-10/users-comyn-documents-deepseek-harness-valuation/outputs/phase1-readonly-checks.py
# 14 passed; 0 failed; 14 boundary checks
```

---

## 3. CLI JSON 示例

新增/更新后的示例在 `examples/outputs/`，请求在 `examples/requests/`。

| 场景 | 请求 | 退出码 | 关键字段 |
|---|---|---|---|
| 个股（正常） | `equity_601398` | 0 | `unsupported` / `undetermined` / `payload_type=equity` |
| 指数（正常） | `index_000300` | 0 | `level` 无币种；`MISSING_VALUATION_HISTORY` |
| ETF（正常） | `etf_510300` | 0 | 跟踪对象 `INDEX:000300.SH`；`price=4.135` |
| 错交易所 | `venue_conflict_601398SZ`（`601398.SZ`） | 2 | `needs_clarification` / `IDENTITY_VENUE_CONFLICT` / `instrument=null` |
| 启发式范围排除 | `scope_only_global_etf`（`全球ETF`） | 0 | `unsupported` / `OUT_OF_SCOPE_CROSS_BORDER` / `instrument=null` |
| 错误快照（串号） | `snapshot_mismatch_510300` | 3 | `engine_error` / `SNAPSHOT_INSTRUMENT_MISMATCH` / `price=null` |
| 历史复现样例 | `--snapshot snap-23eaf5b0bfc82144` | 3 | `engine_error` / `SNAPSHOT_LEGACY_QUARANTINED` |
| 快照不存在 | `engine_error_bad_snapshot` | 3 | `engine_error` / `SNAPSHOT_NOT_FOUND` |
| 代码歧义 | `ambiguous_000001` | 2 | `IDENTITY_MULTI_MARKET` |
| 行业 ETF（范围外） | `out_of_scope_512880` | 0 | `OUT_OF_SCOPE_INDUSTRY_ETF` |

示例片段（错交易所）：

```json
{
  "analysis_status": "needs_clarification",
  "decision": "undetermined",
  "instrument": null,
  "reason_codes": ["IDENTITY_VENUE_CONFLICT"],
  "clarification": "代码 601398 被指定为「SZ」，但已核实记录中不存在该交易所的标的。实际候选：SSE(EQUITY:601398.SH)。请确认交易所或代码。"
}
```

示例片段（错误快照）：

```json
{
  "analysis_status": "engine_error",
  "decision": "undetermined",
  "instrument_id": "ETF:510300.SH",
  "snapshot_id": null,
  "reason_codes": ["ENGINE_ERROR", "SNAPSHOT_INSTRUMENT_MISMATCH"],
  "warnings": ["快照加载失败：快照 snap-d76db31ce212b446 属于 ETF:510500.SH，与请求标的 ETF:510300.SH 不一致"],
  "payload": {"price": null, "nav": null}
}
```

---

## 4. 修订后的时点定义

| 概念 | 定义 | 存放位置 |
|---|---|---|
| 市场日期 | 行情最后一根已确认的交易日，由日线数据决定，不由常量假定 | `snapshot.as_of` + `coverage.build.market_date` |
| 收盘状态 | `closed`（已收盘）/ `intraday`（当日未收盘，不标为收盘价） | `coverage.build.bar_status` |
| 事实报告期 | 财报报告期末（如 2026-09-30） | `Fact.period` |
| 公告/可获得时间 | 该事实对市场公开可得的时间；只有日期时按「当日结束」处理 | `Fact.available_at` |
| 时间精度 | `second` / `date` / `unknown` | `Fact.availability_precision`，汇总在 `coverage.availability_precision_counts` |
| 抓取时间 | 实际取数时刻（审计用，不代表当时可知） | `Fact.fetched_at` / `snapshot.fetched_at` |
| 快照可获得时间 | 快照内所有事实可获得时间的最大值 | `snapshot.available_at` |
| 时点安全 | 只有全部事实都带秒级可获得时间才为 `true` | `coverage.point_in_time_safe` |
| 历史过滤 | `facts_available_by(snapshot, cutoff)`：解析成带时区时刻比较；未知可用时间一律不放行 | `engine/data/snapshot.py` |

**同日快照版本规则**：`(as_of, fetched_at, snapshot_id)` 字典序取最大。哈希只作身份与完整性校验，不参与新旧排序；`manifest.json` 保存 `fetched_at`，指定旧 ID 仍可精确重放。

**快照重建与作废**：旧快照由「固定旧日期 + 实时最新价」构建，已作废并从仓库移除（git 历史保留）。新旧映射：

| 标的 | 旧（作废） | 新 |
|---|---|---|
| INDEX:000300.SH | `snap-efb00dcacf4c74c8` | `snap-9f0fbdb86c70b66e` |
| INDEX:000905.SH | `snap-549f2af3917da603` | `snap-fffadc4bca3ed918` |
| INDEX:000852.SH | `snap-fd346de1c14562ec` | `snap-56e7c688ea35a45c` |
| ETF:510300.SH | `snap-23eaf5b0bfc82144` | `snap-aef6f29159c9d182` |
| ETF:510500.SH | `snap-1f84eed253a4d726` | `snap-d76db31ce212b446` |
| ETF:512100.SH | `snap-a3a962265466a536` | `snap-2ab47bf9fbfc5199` |
| EQUITY:601398.SH | `snap-f9f0d2c00e8c1fd5` | `snap-decfda2d8866e9cf` |

**历史复现样例**：为让设计方的审计脚本可以原样重跑 R8 两项检查，保留了旧 ETF 快照
`snap-23eaf5b0bfc82144`（`notes` 以 `LEGACY-REPRODUCTION` 开头）。该文件**禁止用于分析**：
分析入口遇到该标记会直接返回 `engine_error` + `SNAPSHOT_LEGACY_QUARANTINED`，
默认选择也不会命中它（`fetched_at` 早于新版本）。其余 6 个旧快照已移除。
说明见 `data/snapshots/README.md`。

---

## 5. 更新后的数据调查

重跑脚本：`tools/probe_data_sources.py`（23 项，实际调用 22 项，未测试 1 项）。

| 结论 | 修订后表述 |
|---|---|
| 全收益指数 | 实际调用了 `H00300/H00905/H00852` 三个代码，**均返回空表**；结论限定为「这三个代码在本次调用中不可得」，不再写成「所有免费来源均不可得」 |
| 估值频率 | 由观测日期计算：乐咕序列中位间隔约 30 天 → `monthly`；结论限定为「本次缓存不足以构成连续周度样本」 |
| 跟踪误差 | 标为 `not_tested`（未调用任何接口），不计入实测数量 |
| 深交所 ETF 清单 | 实际调用报 `ConnectionResetError`，计为 failed |
| 汇总口径 | `actually_called=22, ok=12, partial=8, failed=2, not_tested=1` |

覆盖表：`docs/data-coverage.md`；机器可读：`data/coverage/coverage-report.json`；本次构建记录：`data/coverage/last_build.json`。

---

## 6. 变更摘要

新增文件：

- `engine/data/timeutil.py`（时点解析、频率推断）
- `tests/test_rework_regressions.py`（43 项针对性回归）
- `docs/phase1-rework.md`（本报告）
- `data/coverage/last_build.json`（构建时点记录）
- `examples/requests|outputs/venue_conflict_601398SZ.json`、`scope_only_global_etf.json`、`snapshot_mismatch_510300.json`

重写/修改：

- `engine/data/facts.py`（统一构造与反序列化校验）
- `engine/data/snapshot.py`（严格 ID、结构校验、版本排序、时点过滤）
- `engine/contracts/validation.py`（必填字段、正式价格判断、载荷白名单、研究结果来源）
- `engine/contracts/reason_codes.py`（+7 个原因码）
- `engine/identity/normalize.py`、`engine/identity/resolver.py`（交易所冲突）
- `engine/pipeline.py`（快照绑定、范围排除结构化、错误码映射）
- `engine/contracts/result.py`（`dump_json` 过校验）
- `tools/build_snapshots.py`（重写：真实市场日期、无倒填、盘中不称收盘）
- `tools/probe_data_sources.py`（真实调用分类、频率计算、未测试单列）
- `tools/render_coverage_report.py`（调用状态列与口径）
- 快照、示例、`docs/data-coverage.md`、`docs/acceptance-checklist.md`、`README.md`

---

## 7. 仍未解决的数据缺口

1. **全收益指数**：已测三个代码不可得。未穷举其他来源（不购买、不申请账号），因此只能写「已测来源不可得」。
2. **周度估值历史**：已测来源（官方 20 个交易日、第三方月度缓存）不足以构成连续周度样本；未穷举所有免费来源。
3. **ETF 净值披露时间**：仍无接口提供，日终折溢价继续判为不可计算。
4. **ETF 停复牌状态、跟踪误差序列**：前者无字段，后者未找到接口（标注 `not_tested`）。
5. **深交所 ETF 清单**：接口报错，深市归属仍依赖代码段 + 行情清单推断。
6. **银行资本与资产质量**：阶段二专项核实，本轮不提前采集。
7. **公告时刻精度**：财报公告只有日期，日内时点不可用；已通过 `point_in_time_granularity=day` 显式记录。

---

## 8. 需要设计方确认的两处判断

1. **个股价格本轮未采集**：实时接口没有可靠的「已收盘」标记，与其把盘中价标成收盘价，不如先留空并在 coverage 中记录缺口。若设计方希望阶段一就展示价格，需要指定一个可核实的收盘价来源。
2. **「错误交易所」归类为 `ambiguous` 还是独立状态**：按 Q3/Q4 的答复没有新增枚举，本轮归入 `needs_clarification` + `IDENTITY_VENUE_CONFLICT`，并在 `clarification` 中列出实际交易所。若希望明确区分「冲突」与「多候选」，需要在契约中增加原因码层级或新状态。
