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
