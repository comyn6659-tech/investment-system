# 候选 A 研究信号账本（只追加）

文件：`candidate-a-signals.jsonl`（JSON Lines，一行一条记录，按追加顺序）。

## 规则

- 只追加：任何已有记录不得修改或删除；`record_id` 是记录内容（不含追加时刻）的哈希，
  历史被改写时 `tools/update_signal_ledger.py --check` 会报错。
- 同一标的同一 ISO 周只允许一条记录：重复运行内容一致 → 跳过；内容不一致 → 报冲突并退出。
- 只记录**已结束自然周**的周度观察点（该周最后一个已收盘交易日）；未结束的周不写入，
  避免把盘中临时值固化成历史。
- 首次运行从最新一个已结束的周度观察点开始，不回溯全部历史；之后每次运行把新出现的
  周度观察点按日期顺序追加。

## 字段

| 字段 | 含义 |
|---|---|
| `instrument_id` | 指数标的，例如 `INDEX:000300.SH` |
| `week` / `date` | ISO 周与观察日（该周最后一个交易日） |
| `pe` | 观察日官方滚动市盈率 |
| `percentile` | 该值在之前 5 个日历年周度池中的分位（0–100） |
| `evaluable` | 分位是否可计算（覆盖不足/缺值时为 false） |
| `signal` | `entry`（≤20）/ `exit`（≥60）/ `none` / `not_evaluable`；只描述条件，不是买卖指令 |
| `series_ids` | 计算所用的官方收盘、滚动市盈率与全收益序列 id（内容哈希） |
| `rule_version` | 规则版本（当前 `candidate-a/1`） |
| `engine_version` | 生成记录时的代码版本 |
| `record_id` | 内容哈希（不含 `recorded_at`） |
| `recorded_at` | 追加时刻（不参与内容哈希） |

## 命令

```bash
.venv/bin/python tools/update_signal_ledger.py            # 追加新观察点
.venv/bin/python tools/update_signal_ledger.py --dry-run  # 只显示将追加什么
.venv/bin/python tools/update_signal_ledger.py --check    # 校验完整性
```

账本只记录研究信号，规则与阈值未获批准；它不是交易记录，也不代表任何持仓。

---

# 个股正式结论账本（决策②后）

文件：`equity-conclusions.jsonl`（JSON Lines，一行一条记录，按追加顺序）。

## 规则

- 只有 `candidate` / `wait` / `avoid` 写入；`undetermined` 不写。
- 同一标的、同一分析日、同一快照：内容一致 → 跳过；内容不同 → 报冲突，拒绝覆盖。
- 每个 CLI 正式结论运行都会尝试写入；重复运行不会重复写。
- `python -m engine.cli --check-conclusions`（或 `--conclusion-ledger` 指定文件）重新计算哈希，
  发现历史被改写、重复记录或 `undetermined` 混入时返回 1。

## 字段

| 字段 | 含义 |
|---|---|
| `analysis_date` | 分析日 |
| `code` / `instrument_id` | 标的 |
| `policy_version` / `policy_digest` | 已批准政策标识与内容哈希 |
| `snapshot_id` | 所用快照 |
| `price` / `price_date` | 现价与价格日期 |
| `values` | 悲观 / 基准 / 乐观三档每股价值 |
| `grade` / `margin_of_safety` | 确定性等级与安全边际 |
| `conclusion` | candidate / wait / avoid |
| `binding_condition` | 起约束条件（安全边际或悲观价值） |
| `record_id` | 内容哈希（不含 `recorded_at`） |
| `recorded_at` | 追加时刻（不参与哈希） |

2026-11-12 首次运行写入 7 条（海天、双汇、美的、茅台、宁德、恒瑞、中国移动）。
