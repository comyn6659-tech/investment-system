# 第四阶段第六批交付：好公司池收尾（K1–K3）

本批只做 `batch4f-closeout-instructions.md` 的 K1–K3：补齐质押数据、无控股股东改用第一大股东口径、
处罚按主体身份重新计数；重跑 252 家候选，出第六批快照与报告、第五批→第六批对照表，池账本追加。
不改任何阈值，不扩大候选范围，不改个股政策、既有结论账本、指数与 ETF。

## 1. 结果

| 项 | 第四批 | 第五批 | 第六批 |
|---|---:|---:|---:|
| 入池 | 144 | 120 | **138** |
| 未入池 | 3,108 | 3,114 | 3,114 |
| 待定 | 0 | 18 | **0** |
| 范围外 | 1,973 | 1,973 | 1,973 |
| 只差一条 | 108 | 103 | 110 |
| 快照 | `qps-e6fd0031c30f1e01` | `qps-1c1edb9a5fc01bc3` | **`qps-3ebbd24be612bf7e`** |
| 测试 | 524 | 532 | **541** |

252 家候选 = 第四批入池 144 + 只差一条 108，固定读 `report-4d.json`，不随批次结果变化。

## 2. K1：质押补数

第五批 25 家质押不可得（14 家第四批入池 + 4 家只差一条 + 7 家无控股股东）。本批：

- 修正定期报告 PDF 的“前 10 名股东”页定位（原实现漏掉 `前 10名`/`前 10 名` 空格写法，且整行删除页眉会误删首行），
  跨页拼接后按持股数定位质押行；
- 股东表取不到时，改用东方财富重要股东质押明细（`RPTA_APP_ACCUMDETAILS`）或年报释义；
- 名称不一致的逐家记录别名/补正依据（`data/policy-inputs/governance-review-v1.json`）。

结果：**质押覆盖 252/252（100%）**；14 家第四批入池逐家有最终来源，另 4 家只差一条同类补齐；
剩余待定 **0**。逐家来源见报告 §1e。

## 3. K2：无控股股东按第一大股东口径

按公司公告“无控股股东”的 8 家：格力、生益科技、重百集团、伊利、顺络电子、药明康德、康龙化成、汇川技术。
名义持有人（香港中央结算、HKSCC NOMINEES 等）排除；第一大股东与其披露的一致行动人合计。

- 格力：第一大股东珠海明骏 859,566,532 股全部质押（100%），一致行动人董明珠 124,184,839 股无质押，
  合计 **87.38%**，超过 50%，未通过；来源 2026 年三季报 PDF。
- 伊利：呼和浩特投资 190,270,000 / 538,535,826 = **35.33%**，通过。
- 顺络电子：恒顺通与中量投成长 10 号合计 **42.51%**，通过。
- 生益、重百、药明康德、康龙、汇川：0%–4.59%，通过。
- 边界：50% 整通过、50.00001% 不通过，见测试 `test_v12_pledge_boundary_exactly_50_and_above`。

## 4. K3：处罚主体身份

只计公司本身、控股股东/实际控制人及其一致行动人、被处分事项发生时任职的董监高，且限信息披露类事项。
其他股东/其他主体只记录不计入；主体身份不明记待定。逐条阅读公告正文后的判定存
`data/policy-inputs/governance-review-v1.json`。

- **药明康德**：3 条均为股东上海瀛翊（非控股、非一致行动人）的减持信息披露处罚/纪律处分 → 只记录，
  处罚件数 0，回到池内。
- **弘亚数控**：实控人立案事由为内幕交易（非信息披露）→ 不计入；仅审计非标 1 条，转为只差一条。
- **圆通速递**：时任董事兼副总裁公开谴责计入 1 件；短线交易立案/处罚不计入，仍不通过。
- **通策医疗**：实控人信息披露立案/处罚 + 公司及实控人纪律处分计入 3 件，不通过。
- **三七互娱**：公司及实控人、时任董监高的信息披露处罚/立案/公开谴责计入 4 件，叠加审计非标，不通过。
- **百傲化学**：公司信息披露立案计入 1 件，不通过。
- 其他 12 家公司的公告（内幕交易、短线交易、环保、安全生产、金融监管、子公司/关联财务公司等）
  均只记录不计入。

报告 §1g 列出全部 9 条计入记录的日期、类型、主体、身份与依据；§1d 对照表逐家列出第五批→第六批变化与原因。

## 5. 第五批 → 第六批对照

27 家状态变化：待定→入池 17、未入池→只差一条 7、未入池→入池 1（药明康德）、只差一条→未入池 1
（三七互娱）、待定→只差一条 1（格力）。只列变化，逐家原因见报告 §1d。

## 6. 快照、账本与边界

- 第六批快照 `qps-3ebbd24be612bf7e`：48 张表、9.32 MB；治理表新增
  主体/身份/依据/一致行动人/最终来源列；加载仍逐表校验哈希。
- 第五批快照 `qps-1c1edb9a5fc01bc3`、`report-4e.json`、`report-4d.json` 保留不改。
- 池账本追加第 3 条（`quality-pool-v1.2-draft`，138 家，`pool_hash=qph-fc548bf288b854fe`），
  前两条与 `record_id` 不变；`--check` 3 条 0 问题。
- 个股结论 7 条、候选 A 3 条、官方估值 4 条均 0 问题；个股政策、指数/ETF 结果未改。

## 7. 复现

```bash
.venv/bin/python tools/build_governance_data.py --refetch-pledge --workers 4
.venv/bin/python tools/build_governance_data.py --build-tables
.venv/bin/python tools/build_governance_data.py --build-snapshot
.venv/bin/python tools/run_quality_pool.py
.venv/bin/python tools/run_quality_pool.py --show 000651
.venv/bin/python tools/run_quality_pool.py --show 603259
.venv/bin/python tools/run_quality_pool.py --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 试运行报告：[phase4f-quality-pool-report.md](phase4f-quality-pool-report.md)
- 政策：[quality-pool-v1.2-draft.json](../data/policies/quality-pool-v1.2-draft.json)
- 人工核对输入：[governance-review-v1.json](../data/policy-inputs/governance-review-v1.json)
- 快照说明：[data/quality-pool/README.md](../data/quality-pool/README.md)
