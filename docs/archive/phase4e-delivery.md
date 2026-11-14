# 第四阶段第五批交付：好公司池补治理检验

日期：2026-11-12｜施工单：`batch4e-governance-instructions.md`｜
入口：[DELIVERY.md](../DELIVERY.md)｜报告：[phase4e-quality-pool-report.md](phase4e-quality-pool-report.md)｜
上一批：[phase4d-delivery.md](phase4d-delivery.md)

## 1. 结果

| 项 | 第四批 | 第五批 |
|---|---:|---:|
| 入池 | 144 | **120** |
| 未入池 | 3,108 | 3,114 |
| 待定 | 0 | **18** |
| 范围外 | 1,973 | 1,973 |
| 只差一条 | 108 | 103 |
| 新快照 | `qps-e6fd0031c30f1e01` | **`qps-1c1edb9a5fc01bc3`** |

第四批快照、报告（`report-4d.json`）原样保留；第五批新增 4 张治理表，新快照 48 张表、9.31 MB。

## 2. 治理三项覆盖率（J1）

252 家候选 = 第四批入池 144 + 只差一条 108。

| 项 | 来源 / 接口 | 取得 | 覆盖率 |
|---|---|---:|---:|
| 审计意见 2021–2025 | 东方财富 F10 个股资产负债表 `OPINION_TYPE` | 252 / 252 | **100.0%** |
| 处罚/立案/公开谴责 | 东方财富公告检索（立案/行政处罚/公开谴责/纪律处分），正文核对 | 252 / 252 | **100.0%** |
| 控股股东质押 | 公司整体质押为 0 直接记 0；否则最新定期报告前十名股东表 | 227 / 252 | **90.1%** |

- 审计意见逐家 5 个财年，缺任一年即不可得；
- 行政处罚只计**证监会/证监局**作出且属信息披露类的事件；市场监管局处罚、短线交易等不计；
- 立案只计与信息披露相关且主体为证监会/证监局；交易所公开谴责计件；
- 控股股东名称取国网 F10；质押比例 = 控股股东质押股数 ÷ 其持股数；多个控股股东取最高比例；
- 取不到：18 家记待定（14 家质押表未取得、4 家无控股股东且公司整体质押 >0），不入池。

## 3. 因治理出池（J3）

24 家第四批入池公司第五批未入池：**6 家治理不通过 + 18 家待定**。

| 类型 | 公司 | 证据 |
|---|---|---|
| 审计非标 | 002555 三七互娱 | 2023、2024 带强调事项段的无保留意见 |
| 审计非标 | 002833 弘亚数控 | 2022 带强调事项段的无保留意见 |
| 处罚/立案 | 002833 弘亚数控 | 实控人收到证监会立案告知书（公告链接） |
| 处罚/立案 | 600233 圆通速递 | 时任董事兼副总裁公开谴责决定（链接） |
| 处罚/立案 | 603259 药明康德 | 股东及执行事务合伙人纪律处分（〔2022〕52 号） |
| 处罚/立案 | 603360 百傲化学 | 公司收到证监会立案告知书（链接） |
| 质押超限 | 603816 顾家家居 | 控股股东质押 **68.38%**（2025 年报），超过 50% |
| 质押待定 | 18 家 | 最新定期报告股东表未取得，或无控股股东且公司整体质押 >0；逐家列在报告 §1c |

报告 §1c 逐条给出条件、观测值或公告文号/链接；每一条都可由新快照的治理表复核。

## 4. 周期属性标记（J5）

沿用个股政策 `engine/evidence/applicability.py::classify_family` 与同一套
`SELECTION_POLICY` 阈值（明显周期：周期行业 + 收入波动/回撤；困境：近四年亏损）：

- 入池 120 家中 **10 家“明显周期企业”**，110 家“非金融企业”；
- 报告入池名单与 30 只成绩单均加“周期”一列；
- 只标记，不改好公司池规则；测试 `test_cycle_marker_matches_policy_family_classification`
  用同一份证据比较 `classify_cycle` 与 `classify_family` 的 family。

## 5. 措辞修正（J6）

所有条件理由改为“**观测值 X，要求 ≥/≤/=/< Y，通过/未通过**”，不再出现
“观测值 ≥ 门槛，未通过”这类读起来像已满足的句子。测试
`test_condition_reason_wording_is_not_misleading` 与报告扫描
`test_4e_report_has_governance_sections_and_no_misleading_wording` 双重校验。

## 6. 存储与账本（J4、J7）

- 新快照 `qps-1c1edb9a5fc01bc3`：第四批 44 张表 + `governance-audit/penalty/pledge/coverage` 4 张表；
  每张表 `*.meta.json` 记录来源、接口、行数、内容哈希；加载时逐表校验。
- 好公司池账本追加 1 条（`policy=quality-pool-v1.1-draft`，`pool_hash=qph-42b3d61f02b57b12`），
  旧记录不变；`--check` 2 条 0 问题。
- 个股结论 7 条、候选 A 3 条、官方估值 4 条均 0 问题；第四批快照与 `report-4d.json` 未改。
- `run_quality_pool.py --show 600519` 从新快照原始表重算财务与治理，可逐项复核。

## 7. 验收自检（J1–J8）

| 编号 | 结果 |
|---|---|
| J1 | 审计 100%、处罚 100%、质押 90.1%，来源/接口/取得时间逐项记录 |
| J2 | 待定 18 家均不入池；测试覆盖审计/处罚/质押缺失不默认通过 |
| J3 | 24 家出池公司逐家有类型、观测值或公告文号/链接 |
| J4 | `--show <code>` 从快照治理表重算；快速验证 600519 |
| J5 | 周期标记复用个股政策 `classify_family`；测试一致 |
| J6 | 措辞改为“观测值 X，要求 Y”；测试与报告扫描均无旧句式 |
| J7 | 第四批快照/报告、两本既有账本、个股结论、指数/ETF 未变；入池账本只追加；**532 passed** |
| J8 | 本文件 + DELIVERY 一页内汇总、复现命令、提交 ID |

## 8. 复现

```bash
.venv/bin/python tools/build_governance_data.py --workers 4
.venv/bin/python tools/build_governance_data.py --build-tables
.venv/bin/python tools/build_governance_data.py --build-snapshot
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 600519
.venv/bin/python tools/run_quality_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```
