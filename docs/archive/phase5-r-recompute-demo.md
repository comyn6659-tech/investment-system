# 第五阶段收尾：正式结论复算演示（E3）

- 时间：2026-11-15（模拟日期）/ 2026-11-16（后台审计）
- 目的：证明新记录带追溯字段、`./ask --recompute` 可逐项复算；并展示旧记录的可复算与不可复算结果。
- 真实账本全程未改：`data/ledger/equity-conclusions.jsonl` 保持 **110 行**，
  sha256 `39894efe49dacb171e6c5ec2eae97af5a5a23d673649fdeceb4dd19cb91fb532`。
  新记录全部写在 `/tmp/batch5r-demo/equity-conclusions.jsonl`（临时账本）。

## 1. 生成新记录（真实写入路径，临时账本）

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
INVESTMENT_SYSTEM_LEDGER_DIR=/tmp/batch5r-demo \
  .venv/bin/python -m engine.ask --no-update --analysis-date 2026-11-15 "招商银行怎么样"
INVESTMENT_SYSTEM_LEDGER_DIR=/tmp/batch5r-demo \
  .venv/bin/python -m engine.ask --no-update --analysis-date 2026-11-15 "600519 怎么样"
```

生成两条正式结论（分析日 2026-11-15）：

| record_id | 标的 | 价格 / 价格日期 | 结论 | trace.financial | trace.quote |
|---|---|---|---|---|---|
| `concl-4b87957712b5cd4b` | 600036 招商银行 | 42.93 / 2026-11-13 | 候选买点 | `bank_valuation_inputs`：`data/bank-pool/valuation-v2/inputs.csv.gz`（sha256 `f2e8684a…`）+ history | `live_quotes`：`data/pool-valuation/live/quotes-2026-11-13.json`（sha256 `b3e0786b…`） |
| `concl-cbf7b62718f3e5cd` | 600519 贵州茅台 | 1,435.26 / 2026-11-13 | 等待 | `batch_snapshot`：`data/pool-valuation/snapshot-qps-f140a9790e4015ea`（manifest sha256 已校验） | `live_quotes`：同上 |

两条记录都含：`trace.policy.sha256`（政策文件哈希）、`trace.financial`（财务快照/路径/哈希）、
`trace.quote`（行情快照 id/路径/哈希/价格日期），以及顶层 `price_date`。

## 2. `./ask --recompute`（新记录，逐项一致）

```bash
.venv/bin/python -m engine.ask --recompute concl-4b87957712b5cd4b \
  --ledger /tmp/batch5r-demo/equity-conclusions.jsonl
```

输出摘要（exit 0）：

```json
{"record_id":"concl-4b87957712b5cd4b","code":"600036","analysis_date":"2026-11-15",
 "status":"consistent","trace_present":true,"mismatched_fields":[],
 "fields":[{"name":"values.pessimistic","relative_difference":0.0,"ok":true},
           {"name":"values.base","relative_difference":0.0,"ok":true},
           {"name":"values.optimistic","relative_difference":0.0,"ok":true},
           {"name":"price","relative_difference":0.0,"ok":true},
           {"name":"price_date","ok":true},
           {"name":"grade","ok":true},
           {"name":"margin_of_safety","relative_difference":0.0,"ok":true},
           {"name":"conclusion","ok":true},
           {"name":"binding_condition","ok":true}]}
```

`600519` 新记录（`concl-cbf7b62718f3e5cd`）同样 `consistent`，9 项全部 `ok`、相对差 0.0。

## 3. 旧记录（历史不改）

```bash
./ask --recompute concl-88c210a0d4d016ad      # 第 1 行，600519 / 2026-11-12
```

输出：`status=consistent`、`trace_present=false`，备注「旧记录无追溯字段；使用保留的单标的快照
文件尽力复算」；9 项全部一致（exit 0）。

```bash
./ask --recompute concl-469933db0d7e70bd      # 第 96 行，600519 / 2026-11-13
```

输出（exit 2）：

```json
{"record_id":"concl-469933db0d7e70bd","code":"600519","analysis_date":"2026-11-13",
 "status":"not_recomputable",
 "reason":"旧记录缺少追溯字段（未记录批量财务快照 ID / 行情快照 ID / 政策 sha256），且派生快照 snap-9487dfafa48e24dc 不在任何保留的批量结果中，无法证明所用批量快照版本"}
```

## 4. 旧记录全量试算（2026-11-16）

| 结果 | 条数 | 说明 |
|---|---:|---|
| 一致 | 104 | 7 条单标的快照 + 88 条批量派生（映射到 `qps-f140a9790e4015ea`）+ 11 条银行记录等 |
| 不一致（预期历史差异） | 2 | 第 54 行（600486 原「回避」）、第 61 行（601216 原「候选买点」）：按当前引擎因周期清单补全重算为「无法判断」；后续已有改判记录（第 97–98 行） |
| 不可复算（缺追溯） | 2 | 第 96 行（施工单指定）、第 99 行（同一派生快照 `snap-9487dfafa48e24dc` 的同类记录） |
| 改判记录（不参与复算） | 2 | 600486 / 601216 的 `record_type=reversal` 记录 |

清单与原因详见 `docs/维护说明.md` §7.3。
