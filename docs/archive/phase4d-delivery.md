# 第四阶段第四批交付：好公司池（可行性 + 规则草案试运行）

日期：2026-11-12｜施工单：`batch4d-quality-pool-instructions.md`｜
入口：[DELIVERY.md](../DELIVERY.md)｜可行性：[phase4d-quality-pool-feasibility.md](phase4d-quality-pool-feasibility.md)｜
试运行报告：[phase4d-quality-pool-report.md](phase4d-quality-pool-report.md)

## 1. 结论

| 项 | 结果 |
|---|---|
| 财务覆盖率 | **99.45%**（3,234 / 3,252），门槛 95%，**继续** |
| 沪深 A 股总数 | 5,225（沪 2,319 / 深 2,906） |
| 范围外 | 1,973：ST/*ST 193、金融业 122、完整年报不足 7 年 1,658 |
| 评价范围 | 3,252 |
| 入池 | **144** |
| 未入池 | 3,108 |
| 待定 | 0 |
| 只差一条 | 108 |
| 未检验条件 | 3 条：审计意见、处罚/立案、控股股东质押比例 |
| 快照 | `qps-e6fd0031c30f1e01`，44 张压缩表，9.30 MB |
| 测试 | **524 passed** |

规则仍是**未批准草案**（`quality-pool-v1-draft`，`approved=false`）；入池不等于买入，
结果不用于调整阈值。

## 2. 数据可行性（G1）

| 数据 | 来源 / 接口 | 覆盖 | 起止 | 结论 |
|---|---|---|---|---|
| 资产负债表 | 东方财富 F10 `RPT_F10_FINANCE_GBALANCE` | 评价范围 99.45% | 2015–2025 年报 | 可得 |
| 利润表 | 东方财富 F10 `RPT_F10_FINANCE_GINCOME` | 同上 | 2016–2025 年报 | 可得 |
| 现金流量表 | 东方财富 F10 `RPT_F10_FINANCE_GCASHFLOW` | 同上 | 2016–2025 年报 | 可得 |
| 审计意见 | 东方财富 `RPT_AUDIT_OPINION` 等均不存在；akshare 无函数 | 0 | — | 不可得，未检验 |
| 处罚/立案/公开谴责 | 东方财富无批量报表；证监会 feed 不覆盖全市场 5 年 | 0 | — | 不可得，未检验 |
| 控股股东质押比例 | 只有公司整体质押比例 `RPT_CSDC_LIST`（2,246 家，2026-11-06） | 口径不符 | — | 不可得，未检验；公司整体比例只作旁证 |
| 现金分红 | 东方财富 `RPT_SHAREBONUS_DET` | 19,014 行、4,417 家 | 2021–2025 十个报告期 | 可得 |

逐年财务覆盖率 99.84%–100.00%；18 家不完整公司是实际缺年报/缺核心字段，
按“窗口内缺年报 = 该条不通过”处理。详细探针与“为什么不替代”见可行性报告。

## 3. 存储格式（补充要求）

- 按“报告期 × 报表类型”存 **csv.gz**：`balance-2015…2025`、`income-2016…2025`、
  `cashflow-2016…2025`、`dividends-2021-06-30…2025-12-31`，另有 `universe/industry/pledge`；
- 每张表旁 `*.meta.json` 统一记录来源、接口、URL、报告期、列、行数、**内容哈希**；
- 快照清单汇总表哈希得到 `snapshot_id`；加载时逐表校验；
- **不再使用**“一只股票一个 JSON、每个数字带来源”的格式；
- 原始下载缓存在 `data/cache/quality-pool/`（git 忽略），压缩后约 **9.3 MB**，
  低于 1 GB 上限，未触发“停下报告大小”。

## 4. 规则实现（G2、G3）

政策文件：[data/policies/quality-pool-v1-draft.json](../data/policies/quality-pool-v1-draft.json)

- 4 组 11 条条件；每个参数都有 `basis` 与 `source`，代码不另设阈值；
- 边界测试：ROE 平均 15.00%/14.99%、ROE 占比 80%/70%、近 3 年 12%/11.99%、
  现金转换 0.80/0.79、FCF = 0、亏损 1 年、净负债/EBITDA = 2.00、分红率 20%/19.9%；
- 缺数据：缺年报 → 不通过；缺字段 → 待定；未检验 ≠ 通过；
- 政策 `approved=false`，测试拒绝把草案改成 `approved=true`。

## 5. 试运行报告（G5）

报告 [docs/phase4d-quality-pool-report.md](phase4d-quality-pool-report.md) 含：

1. 首页：总数、范围外、评价、入池/未入池/待定、未检验条件；
2. 入池名单 144 家（行业 + 四组关键数字）；
3. 只差一条 108 家（差哪条、观测值、门槛、距门槛）；
4. 漏斗：单独不通过 vs 顺序淘汰；
5. 敏感性：ROE 12/15/18% → 188/144/74；现金转换 0.7/0.8/0.9 → 146/144/131；
   质押 30/50/70% 不适用（条件未检验，不拿公司整体比例替代）；
6. 固定 30 只测试集逐只成绩单（含 4 家金融股“范围外”）；
7. 行业分布（59 个行业）。

## 6. 账本（G7）

| 账本 | 条数 | 校验 |
|---|---:|---|
| `data/ledger/quality-pool.jsonl` | 1 | `run_quality_pool.py --check` 0 问题 |
| `data/ledger/official-valuation-daily.jsonl` | 4 | `update_official_valuation_ledger.py --check` 0 问题 |

- 好公司池账本：入池名单 + `pool_hash` + 规则/快照哈希；同日同规则同快照重复运行跳过；
- 官方估值记录：2026-11-11 的沪深300/中证500/中证1000/创业板指各一条；
  CSI 只给 PE（`indexCsiDsPe` 字段 `peg`，未标滚动）、PB 记 null 并注明；
  创业板指 PE 滚动 25.1325、PB 4.07；不参与任何计算。

## 7. 未变化项（G8）

| 检查 | 结果 |
|---|---|
| 个股结论账本 | 7 条，`--check-conclusions` 0 问题 |
| 候选 A 账本 | 3 条，`--check` 0 问题 |
| 个股政策 | `equity-policy-v1.1` / `pol-6d1a5387780fe6a2` 未改 |
| 指数与 ETF 结果 | 既有快照/序列/回测未改；无 399006 新增信号或回测条目 |
| 全部测试 | **524 passed** |

## 8. 验收自检

| 编号 | 结果 |
|---|---|
| G1 | 可行性报告逐项来源/覆盖率/起止；99.45% ≥ 95%，继续 |
| G2 | 政策版本化、参数带依据；代码无散落阈值；边界测试覆盖每条条件 |
| G3 | 缺数据测试证明不会默认通过；缺年报不通过、缺字段待定、未检验单列 |
| G4 | 同一快照重跑逐家结果相同（`test_same_snapshot_replays_identically`） |
| G5 | 报告含 §2C 七项 |
| G6 | `run_quality_pool.py --show 600519` 从快照原始表独立复算逐年 ROE/现金流/分红 |
| G7 | 两本账本只追加、重复跳过、冲突拒绝、篡改可检出，各有测试 |
| G8 | 上述未变化项检查通过；524 tests |
| G9 | 本文件 + DELIVERY + 复现命令 + 提交 ID |

## 9. 复现

```bash
.venv/bin/python tools/build_quality_pool_data.py --probe-only
.venv/bin/python tools/build_quality_pool_data.py
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 600519
.venv/bin/python tools/run_quality_pool.py --check
.venv/bin/python tools/update_official_valuation_ledger.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```
