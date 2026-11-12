# 银行池固定快照与预演

本目录保存银行规则 `bank-pool-v1-draft` 的固定快照、机器可读报告与估值预演。
快照 `qps-dc9c12160476db8b`：71 张表、约 10.31 MB；原始下载缓存位于
`data/cache/bank-pool/` 与 `data/cache/governance/`，不提交 git。

## 目录结构

```
data/bank-pool/
├── manifest.json                     # 当前快照指针
├── report.json                       # 银行池试运行机器可读报告
├── valuation-rehearsal.json          # 银行估值预演（不写入正式账本）
├── snapshot-qps-dc9c12160476db8b/
│   ├── manifest.json                 # as_of、窗口、表级哈希、来源
│   ├── bank-indicators.csv.gz        # 42 家银行 2016 年起季度与年度指标（1,773 行）
│   ├── bank-profile.csv.gz           # 总股本、2025 总资产
│   ├── ownership.csv.gz              # 42 家银行所有制标记
│   ├── governance-audit/penalty/pledge/coverage.csv.gz
│   ├── universe/industry/pledge.csv.gz
│   ├── balance-2015…2025.csv.gz      # 与好公司池同一批财务表
│   ├── income-2016…2025.csv.gz
│   ├── cashflow-2016…2025.csv.gz
│   └── dividends-2016-06-30…2025-12-31.csv.gz   # 20 个报告期
└── snapshot-qps-<…>/                 # 历史预快照只在运行时出现，不提交
```

## 口径

- 范围：沪深 A 股行业分类为“银行”的公司 42 家；完整年报 ≥ 7 年、剔除 ST 后 36 家进入评价。
  券商、保险及其他行业不做。
- ROE：东方财富 F10 `RPT_F10_FINANCE_MAINFINADATA` 的 `ROEJQ`（加权 ROE），取年报。
- 不良率 `NONPERLOAN`、拨备覆盖率 `BLDKBBL`、核心一级资本充足率 `HXYJBCZL`、
  净息差 `NET_INTEREST_MARGIN`（只展示）；最新取最新可用报告期，
  不良率 5 年变化取“最新报告期 − 5 年前同一报告期”。
- 分红：2016–2025 现金分红，按财年归集；无记录按 0，报告期表未覆盖才记待定。
- 治理：审计、处罚主体、质押三条与 `quality-pool-v1.2-draft` 完全一致；
  银行审计意见使用 `companyType=3` 的银行报表接口。
- 所有制：依据实际控制人；无实际控制人时用第一大股东；银行边界逐家人工复核，
  记录在 `data/policy-inputs/ownership-review-v1.json`。

## 估值预演

`valuation-rehearsal.json` 只用个股政策 v1.1 的银行条款做预演：三项证据（核心一级、
不良率、拨备覆盖率）来自本快照；预演假设（核心一级下限 8.5%、拨备下限 150%、
风险加权资产 ≈ 总资产 × 65% 等）见 `data/policy-inputs/bank-valuation-rehearsal-v1.json`。
**预演不写入正式结论账本，不改变 CLI 结论；银行估值模型仍为 research。**

## 校验

`engine.quality.snapshot.load_snapshot_dir(path)` 逐表核对 `content_hash` 与行数，
再从表格重建 `Company`；任意一张表被改写都会报 `SnapshotError`。
