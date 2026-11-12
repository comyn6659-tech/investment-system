# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第六批：**好公司池收尾（K1–K3，决策③前最后一批）** |
| 代码版本 | `phase4-1.0.0` |
| 政策版本 | 个股 `equity-policy-v1.1`（未改）；好公司池 `quality-pool-v1.2-draft`（同阈值，补 K1–K3，未批准） |
| 本轮实现提交 | `c0ad6cb` |
| 上一轮 | `68fbc46`（第四阶段第五批入口，实现 `52bce54`） |

## 1. 结果（252 家候选固定）

| 项 | 第四批 | 第五批 | 第六批 |
|---|---:|---:|---:|
| 入池 | 144 | 120 | **138** |
| 未入池 | 3,108 | 3,114 | 3,114 |
| 待定 | 0 | 18 | **0** |
| 范围外 | 1,973 | 1,973 | 1,973 |
| 只差一条 | 108 | 103 | 110 |
| 快照 | `qps-e6fd0031c30f1e01` | `qps-1c1edb9a5fc01bc3` | **`qps-3ebbd24be612bf7e`** |
| 测试 | 524 | 532 | **541** |

## 2. K1–K3

- **K1 质押补数**：25 家不可得逐家换来源重取；修正定期报告 PDF 页定位与跨页解析，表内取不到的用
  东方财富重要股东质押明细/年报释义补足。治理三项覆盖率 **审计 252/252、处罚 252/252、质押 252/252**；
  14 家第四批入池逐家有最终来源（另 4 家只差一条同类补齐），**剩余待定 0**。
- **K2 第一大股东口径**：8 家无控股股东（格力、生益、重百、伊利、顺络、药明康德、康龙、汇川）按
  第一大股东（含披露的一致行动人合计）质押股数 ÷ 持股数，名义持有人排除，门槛仍 50%。
  格力：珠海明骏 859,566,532/859,566,532 = 100%，含董明珠合计 **87.38%**，未通过，来源 2026 三季报 PDF；
  边界 50% 整通过、50.00001% 不通过有测试。
- **K3 处罚主体**：只计公司本身、控股股东/实控人及一致行动人、被处分事项发生时任职的董监高，且限信息披露类。
  药明康德 3 条股东（上海瀛翊，非控股/非一致行动人）事项改只记录，回池；弘亚数控实控人内幕交易不计入，
  仅剩审计非标；圆通、通策、三七互娱、百傲按主体计入后不通过。9 条计入记录逐条列出主体、身份、文号。

## 3. 快照、账本与对照

- 第六批快照 `qps-3ebbd24be612bf7e`：48 张表、9.32 MB；治理表新增主体/身份/依据/一致行动人/最终来源列。
- 第五批快照、`report-4e.json`、`report-4d.json` 保留不改；个股结论 7 条、候选 A 3 条、官方估值 4 条 0 问题。
- 池账本追加第 3 条（`quality-pool-v1.2-draft`，138 家，`pool_hash=qph-fc548bf288b854fe`），
  前两条不变，`--check` 3 条 0 问题。
- 第五批→第六批 **27 家状态变化**（待定→入池 17、未入池→只差一条 7、未入池→入池 1、只差一条→未入池 1、
  待定→只差一条 1），逐家原因见报告 §1d。

## 4. 验收自检（K1–K5）

| 编号 | 结果 |
|---|---|
| K1 | 14 家入池逐家有最终来源；剩余待定 0（≤14） |
| K2 | 8 家按第一大股东口径；50% 边界测试；格力数据与来源齐全 |
| K3 | 9 条计入记录均有主体/身份/依据；药明康德回池；主体不明→待定有测试 |
| K4 | 对照表 27 家；第五批快照/报告不变；既有账本不变、池账本只追加；**541 passed** |
| K5 | 本文件一页以内，含复现命令、测试结果、提交 ID |

## 5. 复现

```bash
.venv/bin/python tools/build_governance_data.py --refetch-pledge --workers 4
.venv/bin/python tools/build_governance_data.py --build-tables
.venv/bin/python tools/build_governance_data.py --build-snapshot
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 000651
.venv/bin/python tools/run_quality_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 试运行报告：[docs/phase4f-quality-pool-report.md](docs/phase4f-quality-pool-report.md)
- 详细交付：[docs/phase4f-delivery.md](docs/phase4f-delivery.md)
- 政策：[data/policies/quality-pool-v1.2-draft.json](data/policies/quality-pool-v1.2-draft.json)
- 人工核对输入：[data/policy-inputs/governance-review-v1.json](data/policy-inputs/governance-review-v1.json)
- 快照说明：[data/quality-pool/README.md](data/quality-pool/README.md)
