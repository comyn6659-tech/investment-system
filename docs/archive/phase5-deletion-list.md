# 第五阶段删除清单

- 时间：2026-11-14
- 依据：《第五阶段施工单：整理收尾》第二节第 1 条（先出清单、再删除）
- 仓库：`/Users/comyn/Documents/deepseek-harness/投资/investment-system/`
- 基线：`7d6bd38`（第四阶段第十三批签收）；本轮实现从 `b3ae227`/`75f3644` 起
- 原则：只删除**不被任何当前命令引用**、且不属于禁删范围的文件；不确定时保留。

## 1. 删除项（3 个目录，323 个受版本控制文件，41,814,043 字节）

| # | 路径 | 类型 | 受版本控制文件 | 字节 | 删除理由 |
|---|---|---|---:|---:|---|
| 1 | `data/bank-pool/snapshot-qps-d350327902d589e6/` | 目录 | 123 | 10,367,702 | 已被取代的草案运行产物 |
| 2 | `data/pool-valuation/snapshot-qps-999748bb51f1f856/` | 目录 | 99 | 15,531,042 | 已被取代的草案运行产物 |
| 3 | `data/pool-valuation/snapshot-qps-23cf6397c6bc0488/` | 目录 | 101 | 15,915,299 | 已被取代的草案运行产物 |

合计：**323 个文件、41,814,043 字节（约 39.9 MiB）**。

### 1.1 为什么属于「已被取代的草案运行产物」

- 银行池当前快照是 `qps-dc9c12160476db8b`：`data/bank-pool/manifest.json` 的 `current`、
  `data/bank-pool/report.json` 与 `report-v1-draft.json` 的 `meta.snapshot_id`、以及
  `data/ledger/bank-pool.jsonl` 都指向它。`d350327902d589e6` 是同目录下的更早一次预运行，
  不在任何清单指针里；`data/bank-pool/README.md` 明确写「历史预快照只在运行时出现，不提交」，
  它是误提交的预运行快照。
- 池成员估值当前批量快照是 `qps-f140a9790e4015ea`：`data/pool-valuation/manifest.json` 的
  `current`、`pool-member-valuation.json`、`cyclical-rerun.json`、`regression-30.json` 的
  `batch_snapshot_id` 都指向它。`999748bb51f1f856`（49 张表）与 `23cf6397c6bc0488`
  （50 张表）是同一批数据的前两次试运行快照，均已被 `f140…` 取代。
- 两者都不是任何账本或池账本写入时引用的快照（见 §3 账本扫描结果）。

### 1.2 引用检查命令与结果（删除前执行）

逐个快照 id 搜索除自身目录以外的全部代码、配置、文档、数据与账本：

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
for id in qps-d350327902d589e6 qps-999748bb51f1f856 qps-23cf6397c6bc0488; do
  grep -rn "$id" . \
    --include=*.py --include=*.json --include=*.jsonl --include=*.md \
    --include=*.sh --include=*.toml --include=*.txt \
    --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ \
    --exclude-dir="snapshot-$id"
done
```

结果：**三个 id 均为 0 处引用**。

当前指针核对：

```bash
.venv/bin/python - <<'EOF'
import json
print('bank pool manifest:', json.load(open('data/bank-pool/manifest.json'))['current'])
print('bank report meta:', json.load(open('data/bank-pool/report.json'))['meta']['snapshot_id'])
print('pool-valuation manifest:', json.load(open('data/pool-valuation/manifest.json'))['current'])
for f in ('pool-member-valuation','cyclical-rerun','regression-30'):
    d = json.load(open(f'data/pool-valuation/{f}.json'))
    print(f, d['meta']['batch_snapshot_id'])
EOF
```

结果：`snapshot-qps-dc9c12160476db8b`、`qps-dc9c12160476db8b`、
`snapshot-qps-f140a9790e4015ea`、`qps-f140a9790e4015ea`（三份报告一致）——
全部指向**保留**的快照。

删除范围核对（受版本控制文件只可能来自这三个目录本身）：

```bash
git ls-files | grep -E 'qps-d350327902d589e6|qps-999748bb51f1f856|qps-23cf6397c6bc0488' \
  | grep -vE '^data/(bank|pool-valuation)/snapshot-' ; echo "exit=$?"
```

结果：无输出、`exit=1`（没有目录外的受版本控制文件）。

## 2. 禁删项：全部保留（删除前后的证据）

| 禁删项 | 保留情况 | 证据 |
|---|---|---|
| 所有账本（8 个 `.jsonl`） | 未改动 | 8 个文件 sha256 与 `batch5-baseline/before/summary.json` 完全一致（§4） |
| 账本引用的池快照及其依赖表 | 5 个池快照全部保留 | `scripts/verify-ledger-snapshots.py`：账本 8 个；池快照 5 个、单标的快照 7 个全部存在且哈希通过 |
| 已批准与草案政策文件（16 个 `.json`） | 未改动 | 16 个政策文件 sha256 与基线一致（§4） |
| 测试（`tests/`、问题集） | 未删除 | `pytest` 694 passed；问题集 67/67 |
| `.git` 历史 | 未改写 | 只追加提交，不 amend 已复核提交、不 push |
| 创业板指与 159915 的既有证据 | 保留 | 4 个 `data/series/ser-*.json`、2 个指数/ETF 快照、`data/coverage/chinext-feasibility.json` 仍在；`tests/test_chinext.py` 依赖它们，`docs/archive/phase4c-chinext-feasibility.md` 引用其编号 |

账本引用快照的实际清单（由脚本从 8 个账本抽取）：

- 池快照：`qps-dc9c12160476db8b`（银行池）、`qps-d8fb7e3718675b93`（收息池）、
  `qps-1c1edb9a5fc01bc3`、`qps-3ebbd24be612bf7e`、`qps-e6fd0031c30f1e01`（好公司池三版）。
- 单标的快照：`snap-68c3f130b7f03b1b` 等 7 个 `snap-*.json` 文件。
- 批量路径逐公司派生 id：89 个（`snap-*` 但未落盘，属既有设计），其中 88 个可在保留的
  `data/pool-valuation/pool-member-valuation.json` / `cyclical-rerun.json` 追溯到；
  1 个（`snap-9487dfafa48e24dc`，2026-11-13 贵州茅台）只记录在账本里，对应批量快照
  `qps-f140a9790e4015ea` 已保留。本清单不删除任何上述文件。

### 2.1 评估过但决定不删除的项

| 项 | 不删理由 |
|---|---|
| `data/series/ser-*.json`（创业板指 4 条序列）、`data/snapshots/snap-6a6d…`、`snap-e459…` | `tests/test_chinext.py` 逐条校验序列内容与快照；`data/coverage/chinext-feasibility.json` 仍引用其编号；删除会留下悬空证据并削弱既有测试 |
| `data/bank-pool/valuation-v2.json` | `tools/run_bank_valuation_v21.py` 以它为 v2 基线做逐家对照 |
| `data/bank-pool/report-v1-draft.json`、`income-pool/report-v1-draft.json` | `tests/test_approval_and_hstech.py` 用它们核对草案与批准文书的名单一致 |
| `data/policy-trial/trial-report-v1.json` | `tools/run_equity_policy_trial.py` 作为 v1 对照读取 |
| `data/policy-inputs/bank-check-v1-draft.json`、各 `*-draft.json` 政策 | 批准依据，施工单明确保留 |
| `data/quality-pool/snapshot-qps-e6fd…`、`qps-1c1ed…` | 池账本引用且被复核（`data/ledger/quality-pool.jsonl` 3 个快照 id） |
| `data/policy-trial/snapshots/`、`data/snapshots/` | 账本单标的快照引用与回放测试依赖 |
| `tools/build_chinext_data.py` | 已移出指数的历史取数工具；保留以使保留下来的创业板序列可复核、可重建 |

## 3. 本地缓存清理（git 忽略，不计入受版本控制文件数）

以下均为 `.gitignore` 排除的运行缓存，删除后运行时不依赖（删除后全量测试通过）：

| 路径 | 大小（删除前） | 说明 |
|---|---:|---|
| `data/cache/`（保留 `.gitkeep`） | 49,744 KB | 原始下载缓存；三个 `snap-*` 快照已提交，测试不读缓存（`tests/test_clean_inputs.py`） |
| `data/policy-trial/cache/` | 14,520 KB | 政策试运行原始缓存 |
| `data/policy-trial/logs/` | 132 KB | 试运行日志 |
| `.pytest_cache/` | 28 KB | pytest 缓存 |
| 26 个 `__pycache__/` | 4,076 KB | 字节码缓存 |
| `.DS_Store` / `data/.DS_Store` | 18,440 字节 | macOS 元数据 |

缓存与 `.venv`、`.git` 都不计入“受版本控制文件数”；`git status --ignored` 可见其均为忽略项。

## 4. 删除后的验证（实际运行，2026-11-14）

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python ../batch5-baseline/capture.py after
.venv/bin/python scripts/compare-capture.py ../batch5-baseline/before ../batch5-baseline/after
.venv/bin/python scripts/verify-ledger-snapshots.py
```

| 检查 | 结果 |
|---|---|
| 全量测试（删除 3 个快照 + 清空全部 git 忽略缓存后） | **694 passed**（141s） |
| 122 张 ask 卡片重抓 | 24 张变化、98 张逐字节相同；24 张全部可归入施工单第一节三处修正 |
| 归一化核对 | 把三处修正（含「最新交易日 → 具体日期」同一替换）归一化后，122 张逐字节相同；无意外差异 |
| 账本哈希 | 8 个账本 sha256 与 `before/summary.json` 一致 |
| 政策哈希 | 16 个政策文件 sha256 与 `before/summary.json` 一致 |
| 账本引用快照 | 池快照 5 个、单标的快照 7 个全部存在且内容哈希通过；89 个逐公司派生 id 按既有设计处理（88 个可追溯、1 个只记在账本） |
| 受版本控制文件（仅删除项） | 删除 **323 个文件、41,814,043 字节**（1440 → 1119；新增的校验脚本与本清单另计，最终数见 DELIVERY.md） |
| 工作树总字节（含 `.venv`、`.git`） | 607,163,555 → **498,628,236 字节**（-108,535,319 字节：缓存清理约 68 MB + 快照删除约 40 MB） |

> 说明：worktree 统计用 `capture.py` 的 `du_bytes`（递归所有文件，包含 `.venv` 与 `.git`）；
> 受版本控制文件统计用 `git ls-files` + `stat`，不含 `.venv`、`.git`、被忽略缓存。
