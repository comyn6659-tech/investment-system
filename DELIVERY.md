# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第四批：**好公司池（数据可行性 + 规则草案试运行）** |
| 代码版本 | `phase4-1.0.0` |
| 政策版本 | 个股 `equity-policy-v1.1`（`pol-6d1a5387780fe6a2`，未改）；好公司池 `quality-pool-v1-draft`（未批准） |
| 模型版本 | 个股政策 `validated-1.0.0`；指数/ETF 仍 `research-0.4.0` / `unapproved-phase2` |
| 本轮实现提交 | `e2cde54` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `802f31c`（第四阶段第三批入口，实现 `15f7aea`） |

## 1. 结论

| 项 | 结果 |
|---|---|
| 财务覆盖率 | **99.45%**（3,234 / 3,252），门槛 95%，**继续** |
| 沪深 A 股总数 | 5,225（沪 2,319 / 深 2,906） |
| 范围外 | 1,973：ST/*ST 193、金融业 122、完整年报不足 7 年 1,658 |
| 评价范围 | 3,252 |
| **入池** | **144** |
| 未入池 | 3,108 |
| 待定 | 0 |
| 只差一条 | 108 |
| 未检验条件 | 3 条：审计意见、处罚/立案、控股股东质押比例 |
| 固定快照 | `qps-e6fd0031c30f1e01`，44 张压缩表，**9.30 MB** |
| 测试 | **524 passed** |

规则为**未批准草案**（`approved=false`）；入池只回答“过去十年用公开数据看是不是一家好公司”，
不回答“现在便不便宜”，不预言未来，**入池 ≠ 买入**；结果不用于调整阈值。

## 2. 数据可行性（G1）

| 数据 | 来源 / 接口 | 覆盖 | 起止 | 结论 |
|---|---|---|---|---|
| 资产负债表 | 东方财富 F10 `RPT_F10_FINANCE_GBALANCE` | 评价范围 99.45% | 2015–2025 年报 | 可得 |
| 利润表 | 东方财富 F10 `RPT_F10_FINANCE_GINCOME` | 同上 | 2016–2025 年报 | 可得 |
| 现金流量表 | 东方财富 F10 `RPT_F10_FINANCE_GCASHFLOW` | 同上 | 2016–2025 年报 | 可得 |
| 审计意见 | `RPT_AUDIT_OPINION` 等不存在；akshare 无函数 | 0 | — | 不可得，未检验 |
| 处罚/立案/公开谴责 | 东方财富无批量报表；证监会 feed 不覆盖全市场 5 年 | 0 | — | 不可得，未检验 |
| 控股股东质押比例 | 只有公司整体质押比例 `RPT_CSDC_LIST`（2,246 家，2026-11-06） | 口径不符 | — | 不可得，未检验；公司整体比例只作旁证 |
| 现金分红 | 东方财富 `RPT_SHAREBONUS_DET` | 19,014 行、4,417 家 | 2021–2025 十个报告期 | 可得 |

逐年财务覆盖率 99.84%–100.00%；18 家不完整公司是实际缺年报/缺核心字段，
按“窗口内缺年报 = 该条不通过”处理。逐项探针、URL 与“为什么不替代”见
[docs/phase4d-quality-pool-feasibility.md](docs/phase4d-quality-pool-feasibility.md)。

## 3. 存储格式（补充要求）

- 按“报告期 × 报表类型”存 **csv.gz**：`balance-2015…2025`、`income-2016…2025`、
  `cashflow-2016…2025`、`dividends-2021-06-30…2025-12-31`，另有 `universe/industry/pledge`；
- 每张表旁 `*.meta.json` 统一记录来源、接口、URL、报告期、列、行数、**内容哈希**；
- 快照清单汇总表哈希得到 `snapshot_id`；加载时逐表核对，篡改即拒绝；
- **不再使用**“一只股票一个 JSON、每个数字带来源”的格式；
- 原始下载缓存在 `data/cache/quality-pool/`（git 忽略），固定快照与清单提交；
- 压缩后总量 **9.3 MB**，低于 1 GB 上限，未触发“停下报告大小”。

## 4. 规则草案与试运行（G2–G6）

规则文件：[data/policies/quality-pool-v1-draft.json](data/policies/quality-pool-v1-draft.json)

- 4 组 11 条条件：会赚钱 3、赚真钱 2、经得起风浪 2、做好人 4；
- 每个参数带 `basis` / `source`；代码无散落阈值；
- 边界测试覆盖每条可检验条件（等于门槛、略低、缺年份）；
- 缺数据：缺年报 → 不通过；缺字段 → 待定；全市场不可得 → 未检验；未检验 ≠ 通过；
- 固定 30 只测试集逐只成绩单，含 4 家金融股“范围外”。

试运行报告：[docs/phase4d-quality-pool-report.md](docs/phase4d-quality-pool-report.md)
（首页、入池 144、只差一条 108、漏斗、敏感性、30 只、行业分布 7 项齐备）。

敏感性（只报告，不选用）：

| 参数 | 取值 → 入池数 |
|---|---|
| ROE 平均 | 12% → 188；15% → 144；18% → 74 |
| 现金转换 | 0.7 → 146；0.8 → 144；0.9 → 131 |
| 质押上限 | 30/50/70% **不适用**：控股股东质押条件未检验，不拿公司整体比例替代 |

## 5. 账本（G7）

| 账本 | 条数 | 校验 |
|---|---:|---|
| `data/ledger/quality-pool.jsonl` | 1 | `run_quality_pool.py --check` 0 问题 |
| `data/ledger/official-valuation-daily.jsonl` | 4 | `update_official_valuation_ledger.py --check` 0 问题 |

官方估值记录（2026-11-11）：沪深300 PE 13.14、中证500 PE 22.48、中证1000 PE 26.64
（CSI 只给 PE，字段 `peg`，未标滚动；PB 记 null 并注明）；创业板指 PE 滚动 25.1325、PB 4.07。
该账本不参与任何计算，只为创业板指将来积累同源历史。

## 6. 未变化项（G8）

| 检查 | 结果 |
|---|---|
| 个股结论账本 | 7 条，`--check-conclusions` 0 问题 |
| 候选 A 账本 | 3 条，`--check` 0 问题 |
| 个股政策 | `equity-policy-v1.1` / `pol-6d1a5387780fe6a2` 未改 |
| 指数与 ETF 结果 | 既有快照/序列/回测未改；无 399006 新增信号或回测条目 |
| 全部测试 | **524 passed** |

## 7. 验收自检（G1–G9）

| 编号 | 结果 |
|---|---|
| G1 | 可行性报告逐项来源/覆盖率/起止；99.45% ≥ 95%，继续 |
| G2 | 政策版本化、参数带依据；代码无散落阈值；边界测试覆盖每条条件 |
| G3 | 缺数据测试证明不会默认通过；缺年报不通过、缺字段待定、未检验单列 |
| G4 | 同一快照重跑逐家结果相同；实际 5,225 家重放结果哈希一致 |
| G5 | 报告含 §2C 七项 |
| G6 | `run_quality_pool.py --show 600519` 从快照原始表独立复算逐年 ROE/现金流/分红 |
| G7 | 两本账本只追加、重复跳过、冲突拒绝、篡改可检出，各有测试 |
| G8 | 上述未变化项检查通过；524 tests |
| G9 | 本文件 + 详细交付文档 + 复现命令 + 提交 ID |

## 8. 复现

```bash
.venv/bin/python tools/build_quality_pool_data.py --probe-only
.venv/bin/python tools/build_quality_pool_data.py
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 600519
.venv/bin/python tools/run_quality_pool.py --check
.venv/bin/python tools/update_official_valuation_ledger.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

## 9. 详细文档

- [docs/phase4d-quality-pool-feasibility.md](docs/phase4d-quality-pool-feasibility.md)：可行性逐项报告
- [docs/phase4d-quality-pool-report.md](docs/phase4d-quality-pool-report.md)：试运行报告（机器可读：`data/quality-pool/report.json`）
- [docs/phase4d-delivery.md](docs/phase4d-delivery.md)：本批交付与 G1–G9 证据
- [data/quality-pool/README.md](data/quality-pool/README.md)：快照目录、表级元数据与哈希说明
