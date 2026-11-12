# 第四阶段第二批交付：启用个股正式结论（决策②）

日期：2026-11-12｜依据：`decision2-and-batch4b-instructions.md`（决策②记录 + 施工单）

## 1. 范围与边界

| 项 | 内容 |
|---|---|
| 批准 | `data/policies/equity-policy-v1.1.json`：`status=approved`、`approved=true`，参数与 v1.1 草案逐项相同（`pol-6d1a5387780fe6a2`） |
| 模型 | 只有 `equity_dcf_fcff` 与 `equity_dividend_discount` 改为 `validated`；银行股息、指数画像、候选 A、ETF 保持原状态 |
| CLI | 个股政策路径与试运行共用 `engine.policy.trial.evaluate_company`，不另写估值 |
| 结论 | `complete` + A/B → candidate / wait / avoid；C、partial、不支持、资料不足 → `undetermined` |
| 新鲜度 | 价格超 3 个交易日、应有更新年报、缺政策字段 → `undetermined` + 原因码 |
| 账本 | `data/ledger/equity-conclusions.jsonl` 只追加；首次写入 7 条 |
| 不做 | 不改政策参数、不扩股票池、不改指数/ETF、不做对话入口、不翻译条件名 |

## 2. P1–P8 验收证据

| 编号 | 结果 |
|---|---|
| P1 | approved 文件与 v1.1 草案 `parameters` 逐项相同；批准记录含 `approved_by=股东`、`approved_at=2026-11-12`、`decision=决策②`、`review_date=2027-05-31`、依据与已知缺口；草案保留未删；`POLICY_VERSION="equity-policy-v1.1"` |
| P2 | `MODELS` 中只有 `equity_dcf_fcff`、`equity_dividend_discount` 为 `validated`；其余 5 个模型状态不变；测试断言集合相等，新增升级会失败；模型结果与 `model_plan` 均为 `validated` |
| P3 | 30 只逐只比对：**30/30 一致**（0 差异）；CLI 与试运行共用 `evaluate_company`，测试用 monkeypatch 证明 CLI 会调用该函数 |
| P4 | 文本/JSON 输出含：结论、等级及含义、三档价值及用途、起约束条件、距候选门槛百分比、主要未满足条件、政策版本、快照 ID、免责声明；C/partial/不支持/资料不足均为 `undetermined` 并带原因码 |
| P5 | 三种情形各有测试：价格过期（`STALE_PRICE`）、应有更新年报（`STALE_FINANCIALS`）、缺政策字段（`MISSING_POLICY_FIELD`）；另有价格过期端到端测试，结果 `partial` / `undetermined` |
| P6 | 账本追加、重复跳过、内容冲突、篡改检测各有测试；`--check` 可发现改写；首次写入 7 条 = 30 只中给出正式结论的数量 |
| P7 | 指数信号、指数账本、ETF、回测数据不变；沪深300 仍 `undetermined` / `research`，分位 67.72%；候选 A 账本仍 3 条；**480 passed** |
| P8 | 本文件 + `DELIVERY.md` + 复现命令 + 提交 ID |

## 3. 30 只正式结论

| 结论 | 数量 | 标的 |
|---|---|---|
| 候选买点 | 0 | — |
| 等待 | 3 | 贵州茅台（B）、美的集团（A）、宁德时代（B） |
| 回避 | 4 | 海天味业（A）、双汇发展（A）、恒瑞医药（B）、中国移动（B） |
| 无法判断 | 23 | 其余（C 级、partial、不支持、资料不足） |

- 7 只正式结论：茅台 1,144.66 元基准 / 候选门槛 686.80；海天 17.41 / 13.06；双汇 14.95 / 11.21；
  美的 76.99 / 57.74；宁德 242.04 / 145.23；恒瑞 19.45 / 11.67；中国移动 76.82 / 46.09。
- 格力电器等级 A 但状态 partial（勾稽 −30.2%），按 `undetermined`，不写账本。
- 逐个结论、三档价值与距门槛见 `docs/phase4b-equity-conclusions.md`。

## 4. 数据新鲜度

| 检查 | 规则 | 结果 |
|---|---|---|
| 价格 | 价格日期距分析日 > 3 个交易日 | 30 只快照 `as_of=2026-11-11`，分析日 2026-11-12，全部通过 |
| 年报 | 分析日 ≥ 4 月 30 日时应包含上一年年报 | 30 只快照最新年报均为 2025，全部通过 |
| 字段 | 政策所需价格/股本/年报字段 | 4 只（银行、券商、保险）缺 2025 年报字段，但它们本来就不支持/资料不足；政策路径不对其出正式结论 |

## 5. 结论账本

文件：`data/ledger/equity-conclusions.jsonl`（`equity-conclusion-ledger/1`）。

| 字段 | 含义 |
|---|---|
| `analysis_date` / `code` / `instrument_id` | 分析日与标的 |
| `policy_version` / `policy_digest` | 政策标识与内容哈希 |
| `snapshot_id` / `price` / `price_date` | 快照与现价 |
| `values` | 悲观 / 基准 / 乐观三档每股价值 |
| `grade` / `margin_of_safety` | 等级与安全边际 |
| `conclusion` | candidate / wait / avoid（undetermined 不写） |
| `binding_condition` | 起约束条件（文本） |
| `record_id` | 内容哈希（不含 `recorded_at`），用于发现改写 |
| `recorded_at` | 追加时刻（不参与哈希） |

- 同一标的、同一分析日、同一快照：内容一致 → 跳过；内容不同 → 冲突，拒绝覆盖。
- `python -m engine.cli --check-conclusions` 返回 0/1 并列出问题。
- 首次运行写入 7 条；重复运行新增 0、跳过 7。

## 6. 测试

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 480 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_equity_conclusion.py` | 21 | P1–P7：批准一致性、模型状态、30/30、CLI 字段、新鲜度三情形与端到端、账本追加/跳过/冲突/篡改、指数与回测不变 |
| `tests/test_equity_policy.py` | 55 | 4a-R 政策与试运行回归 |
| `tests/test_index_signal.py` | 17 | 指数信号与账本 |
| 既有回归 | 387 | 阶段一/二/三 |

## 7. 复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/run_equity_conclusions.py --date 2026-11-12   # 30 只结论 + 首次写账本
.venv/bin/python -m engine.cli '贵州茅台' --analysis-date 2026-11-12  # 单只 CLI
.venv/bin/python -m engine.cli --check-conclusions                    # 账本校验
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 8. 未做与已知限制

- 不改任何政策参数；不改指数（仍研究信号 / `undetermined`）、ETF、回测。
- 不做对话入口；条件名保持英文原名，待对话入口批翻译。
- 银行、保险、券商、强周期、困境公司仍不给结论；不扩股票池。
- 数据新鲜度按周一至周五计数，不含法定节假日（略严）。
- 已知政策局限见批准文件 `approval.known_limitations`；首次复盘 2027-05。

## 9. 提交

见 `DELIVERY.md` 的提交记录（实现提交 + 入口提交）。
