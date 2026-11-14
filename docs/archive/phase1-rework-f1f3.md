# 阶段一收尾返工报告（F1–F3）

- 项目：`/Users/comyn/Documents/deepseek-harness/投资/investment-system/`
- 依据：《第一阶段返工复验：剩余三组收尾问题》（审查日期 2026-11-11，被审查提交 `ef2ab38`）
- 范围：**只修 F1–F3**；已通过的 R1/R3/R4/R5/R6/R10 保留原回归测试，未重写
- 旧系统 `../valuation/` 未修改；未进入第二阶段；未新增或修改任何投资参数
- 代码版本：`phase1-0.1.2`

## 验证结果

| 检查 | 结果 |
|---|---|
| 项目测试 | `142 passed`（上一轮 134，新增 8 项 F1/F3 测试，改写 2 项 R7 测试以匹配新语义） |
| 上一轮独立脚本 `phase1-readonly-checks.py` | `12 passed; 2 failed`；两项失败都是因为该脚本要读取**已移出生产目录**的作废样例 `snap-23eaf5b0bfc82144`（R8 两条检查）。这是复验报告 F3-4「不要为了脚本通过而保留作废样例」的直接结果，等价测试见 §3.4 |
| 本轮复验脚本 `phase1-rework-review-checks.py` | `7 passed; 1 failed`；唯一失败项是 F3 第 8 条「删除备注释放隔离样例」，原因见 §3.4：**作废样例已按要求移出生产目录，脚本找不到文件**，等价测试见 `test_f3_retired_sample_is_out_of_production` |

---

## 1. F1：阻断原因与正式决策、未实现模型与研究价格

### 根因

1. `validate_result` 只在 `decision == candidate` 时检查阻断性原因码，`wait/avoid` 可以带着
   `MISSING_ETF_NAV` 这类阻断原因码通过；`policy.apply_guards` 里「任何阻断原因都降级」的规则
   只作用于流程内，出口并不复核。
2. `research_signal` 只检查 `model_id` 非空、子项 `model_status == research`，不核对模型是否登记、
   版本是否一致、是否与 `model_plan` 一致、整体是否真的执行过模型。

### 改动

| 位置 | 改动 |
|---|---|
| `engine/contracts/validation.py` | 阻断性原因码对**所有**正式价格判断（candidate/wait/avoid）一票否决，错误文案按实际决策生成 |
| `engine/contracts/validation.py` | 新增 `_check_research_signal`：模型必须在 `engine/models/registry.py` 登记；`model_version` 必须等于登记版本；必须等于 `model_plan.primary_model`；整体 `model_status` 必须为 `research`；`analysis_status` 必须为 `complete/partial`；`decision` 必须为 `undetermined` |
| `engine/contracts/validation.py` | 未登记模型、版本不符、计划不符、未执行状态，全部产生违规 |

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_f1_research_cannot_export_formal_price_decision`（上轮） | research + neutral + wait，JSON 与文本两个出口均拒绝 |
| `test_f1_legitimate_research_result_is_allowed` | **正例**：合法研究模型 + `partial` + `undetermined`，JSON 与文本都能输出 |
| `test_f1_research_signal_requires_registered_model` | `nonexistent_model` 被拒 |
| `test_f1_research_signal_requires_matching_version` | 版本不符被拒 |
| `test_f1_research_signal_must_match_model_plan` | 与 `model_plan.primary_model` 不一致被拒 |
| `test_f1_research_signal_not_allowed_on_unsupported` | 未建模结果（unsupported）不能夹带研究价格 |

### 实际输出

```text
# 复验脚本（修复前 → 修复后）
decision=wait,  reason_codes=['MISSING_ETF_NAV'] → blocked_exporters=['JSON','text']
decision=avoid, reason_codes=['MISSING_ETF_NAV'] → blocked_exporters=['JSON','text']

# 未实现模型 + 伪造 research_signal → rejected
# 合法研究结果（partial + research + primary_model 一致）→ 正常输出：
{"analysis_status":"partial","model_status":"research","decision":"undetermined",
 "model_results":[{"result_type":"research_signal",
   "model_id":"index_strategy_a_valuation_reversion",
   "model_version":"0.0.0-research","model_status":"research","pe_percentile_5y":18.2}]}
```

说明：合法研究结果要求「模型已登记 + 版本一致 + 与模型计划一致 + 分析确实执行 + 决策仍未定」，
没有为了通过测试而提前实现任何估值模型，也没有一刀切禁止研究数字。

---

## 2. F2：全收益探针没有真正发出请求

### 根因

`stock_zh_index_daily_em` 的 `symbol` 必须带市场前缀（`sh/sz/csi/bj`）。旧探针直接传
`H00300/H00905/H00852`，适配器在**本地**返回空表（未发 HTTP），却被记成「远端无数据」。
此外：一条目标非空就整体记 `ok`（应为 `partial`）；频率只取每条序列最后一天；
GAPS 仍写「免费源未提供」这类全称结论。

### 改动

| 位置 | 改动 |
|---|---|
| `tools/probe_data_sources.py` | 每个目标依次尝试 `shH00300 / csiH00300 / H00300`，用 `_count_http_gets()` 在 `http.get` 边界统计实际请求次数 |
| 同上 | `_classify_attempt()` 区分 `local_rejected`（无前缀，本地返回）、`remote_empty`（已请求、空表）、`remote_empty_payload`（已请求、响应无数据体）、`remote_error`、`adapter_error`、`data` |
| 同上 | 汇总改为：全成功 `ok` / 部分有数据 `partial` / 全无数据 `failed`；`error` 不再用「空表」泛指 |
| 同上 | 频率与覆盖按**每条序列自己的日期集合**计算（`infer_frequency`），并在 `sample.per_series` 分别列出 |
| 同上 | `parameters` 记录实际尝试的符号列表与说明 |
| 同上 | GAPS 四条表述改为限域结论（本周已测来源/参数） |

### 实测证据（2026-11-11 真实联网重跑）

```text
status: failed
http_attempts: 6
per target:
  H00300 (沪深300全收益)  → remote_empty
  H00905 (中证500全收益)  → remote_empty
  H00852 (中证1000全收益) → remote_empty
attempts:
  shH00300   http_calls=1 rows=0 → remote_empty_payload
  csiH00300  http_calls=1 rows=0 → remote_empty_payload
  H00300     http_calls=0 rows=0 → local_rejected
  …（中证500、中证1000 同上）
verdict: 已按市场前缀实际发出请求，远端对这些代码没有返回数据（HTTP 已发出）；
         这不等于所有免费来源都没有全收益序列
```

结论修正：旧表述「免费源未提供全收益指数」→「以带前缀参数调用东方财富接口，远端无数据；
未穷举其他供应商」。`probe_index_daily` 也改掉了「只有价格指数，没有全收益指数」的越界结论。

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_r7_total_return_probe_reflects_actual_result` | 真实适配器参数路由（`shH00300` 在首位）、返回数据时 `ok` |
| `test_r7_total_return_probe_marks_empty_and_errors` | 全空时 `failed`，不再把「空表」写成接口错误 |
| 复验脚本 `real_adapter_routing` | 只拦截 `http.get`、保留真实适配器：`HTTP_attempts=6` |
| 复验脚本 `partial_probe` | 一条有数据、两条空表 → `partial` |

---

## 3. F3：隔离状态与版本选择依赖未受校验的元信息

### 根因

1. 内容哈希不含顶层 `fetched_at` 与 `notes`，但前者用于版本排序、后者用于隔离判断；
   改备注即可解除隔离，改抓取时间即可改变默认版本而 ID 不变。
2. manifest 与快照之间没有交叉核对。

### 改动

| 位置 | 改动 |
|---|---|
| `engine/data/snapshot.py` | `_content_payload()` 覆盖**除 snapshot_id 外的全部字段**（含 `fetched_at`、`notes`、`producers`、`coverage`）；任何一项改动都会改变 ID |
| `engine/data/snapshot.py` | `load()` 增加 manifest 交叉核对：`instrument_id / as_of / fetched_at` 与快照实际值不一致即 `SNAPSHOT_MANIFEST_MISMATCH` |
| `engine/data/snapshot.py` | 新增 `check_consistency()`：整目录自检（重复 ID、文件缺失、未登记文件、字段不一致） |
| `engine/pipeline.py` | **删除**以 `notes` 前缀判断隔离的逻辑；准入不再由自由文本控制 |
| `data/snapshots/` | 作废样例**移出生产目录**；目录内只保留 7 个当前有效快照 |
| `engine/contracts/reason_codes.py` | 移除 `SNAPSHOT_LEGACY_QUARANTINED`，新增 `SNAPSHOT_MANIFEST_MISMATCH` |

### 迁移说明

- 作废样例（`snap-23eaf5b0bfc82144`，第一版采集器生成）已从 `data/snapshots/` 删除，
  不再作为产品数据存在，也不再需要「按备注隔离」的机制。按旧 ID 取数会得到
  `SNAPSHOT_NOT_FOUND`（`engine_error`），这就是等价的安全结果。
- 因哈希规则变更，7 个快照全部重建，新旧映射：

| 标的 | 上轮 `ef2ab38` | 本轮 `phase1-0.1.2` |
|---|---|---|
| INDEX:000300.SH | `snap-9f0fbdb86c70b66e` | `snap-6e6d5c31a0371ee0` |
| INDEX:000905.SH | `snap-fffadc4bca3ed918` | `snap-1a5ed1b1f7206c14` |
| INDEX:000852.SH | `snap-56e7c688ea35a45c` | `snap-5d60fe1e7fb6ed3b` |
| ETF:510300.SH | `snap-aef6f29159c9d182` | `snap-6938a7a7b2c868c4` |
| ETF:510500.SH | `snap-d76db31ce212b446` | `snap-ff2df2705d7e880f` |
| ETF:512100.SH | `snap-2ab47bf9fbfc5199` | `snap-4c1d2bfec2a65678` |
| EQUITY:601398.SH | `snap-decfda2d8866e9cf` | `snap-fac0481bd5f7d1b5` |

- 重建命令改为显式固定日期：`tools/build_snapshots.py --as-of 2026-11-10`。
  该参数现在**按日期选行**，找不到对应行直接失败；未指定时取最后一行并记录 `bar_status`。

### 测试

| 测试 | 覆盖 |
|---|---|
| `test_f3_metadata_changes_are_caught_by_content_hash` | 改 `notes` / `fetched_at` / `producers` 任一字段 → `SNAPSHOT_HASH_MISMATCH` |
| `test_f3_retired_sample_is_out_of_production` | 旧 ID 不在 manifest，取数得到 `SNAPSHOT_NOT_FOUND` |
| `test_f3_manifest_must_match_snapshot` | manifest 的 `fetched_at` 被改 → `SNAPSHOT_MANIFEST_MISMATCH` |
| `test_f3_snapshot_directory_is_self_consistent` | `check_consistency() == []` |
| `test_f3_default_analysis_uses_registered_snapshot` | 默认分析只命中 manifest 中的当前快照 |

### 关于复验脚本第 8 项

两份复验脚本中共有 3 项（本轮脚本 1 项、上一轮脚本 2 项）需要读取作废样例
`snap-23eaf5b0bfc82144`。该项需要先 `A.store.load('snap-23eaf5b0bfc82144')` 成功，
再清空备注观察是否解除隔离。按复验报告第 F3-4 条「**不要为了让旧审计脚本原样通过，
把作废样例保留在生产数据目录**」，该文件已移除，因此这一步必然报「找不到快照文件」。
本轮用上述 5 项等价测试覆盖同一约束（元信息受哈希保护、隔离样例不在生产目录、
manifest 一致性可检测），并提交迁移说明。该失败是预期的，不是功能回退。

---

## 4. 测试与复现

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 142 passed in 1.10s

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  /Users/comyn/Documents/Codex/2026-11-10/.../phase1-rework-review-checks.py
# 7 passed; 1 failed（见 §3.4）

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  /Users/comyn/Documents/Codex/2026-11-10/.../phase1-readonly-checks.py
# 12 passed; 2 failed —— 两项均因该脚本读取已移除的作废样例，见 §3.4
```

分布：识别 26、契约 17、事实 11、流程 20、快照/重放 6、CLI 11、返工回归 51。

---

## 5. 出口反例与正例（直接送入导出器）

| 对象 | 预期 | 实际 |
|---|---|---|
| `complete + validated + neutral + wait` + `MISSING_ETF_NAV` | 拒绝 | JSON、文本均 `ContractViolation` |
| `complete + validated + expensive + avoid` + 同一阻断码 | 拒绝 | 同上 |
| `unsupported` + `research_signal(model_id=nonexistent_model)` | 拒绝 | `ContractViolation` |
| `unsupported` 载荷注入 `valuation.buy_hi` | 拒绝 | `ContractViolation` |
| 合法研究结果（`partial` + 登记模型 + `undetermined`） | 允许 | JSON/文本正常输出 |
| 正常事实报告（个股 / 指数 / ETF） | 允许 | 见 `examples/outputs/*.json` |

CLI 示例（`examples/outputs/`）本次全部重新生成，快照 ID 与上表一致：

```text
equity_601398        unsupported  snap-fac0481bd5f7d1b5
index_000300         unsupported  snap-6e6d5c31a0371ee0
etf_510300           unsupported  snap-6938a7a7b2c868c4
out_of_scope_512880  unsupported  —（registry 条目，无快照）
scope_only_global_etf unsupported —（instrument=null）
venue_conflict_601398SZ needs_clarification —
snapshot_mismatch_510300 engine_error  —（SNAPSHOT_INSTRUMENT_MISMATCH）
```

---

## 6. 更新后的文档

- `docs/data-coverage.md`（重跑后重新生成：22 项实际调用 + 1 项未测试）
- `data/coverage/coverage-report.json`（含每次调用的符号、HTTP 次数、错误）
- `data/snapshots/README.md`（哈希规则、manifest 校验、构建方式）
- `docs/phase1-delivery.md`（快照 ID、测试数量同步）
- `docs/acceptance-checklist.md`（F 区改写 + 新增 F1–F3 检查项）
- `README.md`（版本与快照规则）

## 7. 仍未解决（与上一轮一致，均为已测范围内的缺口）

1. 全收益指数：东方财富接口对 H00300/H00905/H00852（带前缀）返回无数据；未穷举其他供应商。
2. 周度估值历史：官方 20 个交易日、本次第三方缓存为月度。
3. ETF 净值披露时间、停复牌状态、跟踪误差序列（后者未测试）。
4. 深交所 ETF 清单接口调用报错。
5. 银行资本与资产质量数据（阶段二专项核实）。
6. 个股价格（阶段一不采集，设计方已答复接受）。
