# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第五批：**好公司池补治理检验（252 家）** |
| 代码版本 | `phase4-1.0.0` |
| 政策版本 | 个股 `equity-policy-v1.1`（`pol-6d1a5387780fe6a2`，未改）；好公司池 `quality-pool-v1.1-draft`（同阈值，补治理，未批准） |
| 模型版本 | 个股政策 `validated-1.0.0`；指数/ETF 仍 `research-0.4.0` / `unapproved-phase2` |
| 本轮实现提交 | `52bce54` |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `d7f7233`（第四阶段第四批入口，实现 `e2cde54`） |

## 1. 结果

| 项 | 第四批 | 第五批 |
|---|---:|---:|
| 入池 | 144 | **120** |
| 未入池 | 3,108 | 3,114 |
| 待定 | 0 | **18** |
| 范围外 | 1,973 | 1,973 |
| 只差一条 | 108 | 103 |
| 快照 | `qps-e6fd0031c30f1e01` | **`qps-1c1edb9a5fc01bc3`** |
| 测试 | 524 | **532** |

第四批快照与 `report-4d.json` 保留不改；第五批新快照 48 张表、9.31 MB。

## 2. 治理三项覆盖率（J1）

252 家候选 = 第四批入池 144 + 只差一条 108。

| 项 | 来源 / 接口 | 取得 | 覆盖率 |
|---|---|---:|---:|
| 审计意见 2021–2025 | 东方财富 F10 个股资产负债表 `OPINION_TYPE` | 252 / 252 | **100.0%** |
| 处罚/立案/公开谴责 | 东方财富公告检索 + 正文核对（限证监会/证监局、信息披露类） | 252 / 252 | **100.0%** |
| 控股股东质押 | 公司整体质押为 0 直接记 0；否则最新定期报告前十名股东表 | 227 / 252 | **90.1%** |

审计逐家 5 年，缺一年即不可得；处罚只计证监会/证监局的信息披露类处罚与立案，交易所公开谴责计件；
控股股东质押 = 质押股数 ÷ 其持股数，多个控股股东取最高。取不到 18 家记待定，不入池。

## 3. 因治理出池（J3）

24 家第四批入池公司出池：**6 家不通过 + 18 家待定**。

| 类型 | 公司 | 证据 |
|---|---|---|
| 审计非标 | 002555 三七互娱 | 2023、2024 带强调事项段的无保留意见 |
| 审计非标 | 002833 弘亚数控 | 2022 带强调事项段的无保留意见 |
| 处罚/立案 | 002833 弘亚数控 | 实控人收到证监会立案告知书（公告链接） |
| 处罚/立案 | 600233 圆通速递 | 时任董事兼副总裁公开谴责决定（链接） |
| 处罚/立案 | 603259 药明康德 | 股东及执行事务合伙人纪律处分（〔2022〕52 号） |
| 处罚/立案 | 603360 百傲化学 | 公司收到证监会立案告知书（链接） |
| 质押超限 | 603816 顾家家居 | 控股股东质押 **68.38%**（2025），超过 50% |
| 质押待定 | 18 家 | 14 家质押表未取得、4 家无控股股东且公司整体质押 >0；逐家见报告 §1c |

## 4. 周期标记与措辞（J5、J6）

- 周期标记复用个股政策 `classify_cycle → classify_family`，同一套 `SELECTION_POLICY`；
  入池 120 家中 **10 家明显周期企业**、110 家非金融企业；报告入池名单与 30 只成绩单加“周期”列；
- 所有条件理由改为“**观测值 X，要求 ≥/≤/=/< Y，通过/未通过**”；
  测试与报告扫描均未出现“≥ …，未通过”旧句式。

## 5. 存储与账本（J4、J7）

- 新快照 `qps-1c1edb9a5fc01bc3`：第四批 44 张表 + `governance-audit/penalty/pledge/coverage` 4 张表；
  每张表 `*.meta.json` 记录来源、接口、行数、内容哈希；加载逐表校验；
- 好公司池账本追加 1 条（`quality-pool-v1.1-draft`，`pool_hash=qph-42b3d61f02b57b12`），
  旧记录不变；`--check` 2 条 0 问题；
- 个股结论 7 条、候选 A 3 条、官方估值 4 条均 0 问题；
- `run_quality_pool.py --show 600519` 从快照原始表复算财务与治理（逐年审计意见、处罚检索、控股股东 0% 质押）。

## 6. 验收自检（J1–J8）

| 编号 | 结果 |
|---|---|
| J1 | 审计 100%、处罚 100%、质押 90.1%，来源/接口/取得时间逐项记录 |
| J2 | 18 家待定不入池；审计/处罚/质押缺失各有不默认通过测试 |
| J3 | 24 家出池公司逐家有类型、观测值或公告文号/链接 |
| J4 | `--show <code>` 从快照治理表重算；600519 已核 |
| J5 | 周期标记与 `classify_family` 一致（测试） |
| J6 | 措辞修正；测试与报告扫描无旧句式 |
| J7 | 第四批快照/报告、两本既有账本、个股结论、指数/ETF 未变；池账本只追加；**532 passed** |
| J8 | 本文件 + 详细交付文档 + 复现命令 + 提交 ID |

## 7. 复现与文档

```bash
.venv/bin/python tools/build_governance_data.py --workers 4
.venv/bin/python tools/build_governance_data.py --build-tables
.venv/bin/python tools/build_governance_data.py --build-snapshot
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 600519
.venv/bin/python tools/run_quality_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 试运行报告：[docs/phase4e-quality-pool-report.md](docs/phase4e-quality-pool-report.md)
- 详细交付：[docs/phase4e-delivery.md](docs/phase4e-delivery.md)
- 快照说明：[data/quality-pool/README.md](data/quality-pool/README.md)
- 政策：[data/policies/quality-pool-v1.1-draft.json](data/policies/quality-pool-v1.1-draft.json)
