# 银行池固定快照与预演

本目录保存银行规则 `bank-pool-v1` 的固定快照、机器可读报告与估值预演。
2026-11-13 决策③批准 `bank-pool-v1`（参数与草案逐项相同）；银行估值仍为 research，
`bank-check-v1-draft` 保持 draft 且明确“不启用”，预演不进入正式结论。
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

`valuation-rehearsal.json` 按**已批准个股政策 v1.1 的股息折现路线**重跑：

- 折现率：规范化无风险利率 + ERP（当前 7.9029%），三情景同一折现率；
- 增长：R7（第 1 年封顶 10%、稳态 = min(10%, 5 年与 3 年复合增速)、第 2–5 年线性过渡）；
- 派息率：R10 总额口径近 3 年平均；终值：R1；悲观：R8；
- 银行检查：`data/policy-inputs/bank-check-v1-draft.json`（三项证据 + 核心一级 ≥ 8.5% +
  拨备覆盖率 ≥ 150%），通过且最近 7 个财年至多 1 年亏损、数据完整 → B，否则 C；
- 输入表：`valuation-inputs.csv.gz`（营收/净利润/ROE/净利率/派息率/现价与日期），
  由 `tools/build_bank_valuation_inputs.py` 生成；银行池快照、报告与账本不变。

旧版 `data/policy-inputs/bank-valuation-rehearsal-v1.json` 保留为历史，**不再被读取**。
**预演不写入正式结论账本，不改变 CLI 结论；银行估值模型仍为 research。**

## 银行专用估值 v2 试运行（第十二批）

- 草案：`data/policies/bank-valuation-v2-draft.json`，参数与依据全部版本化；状态为
  `draft_unapproved`，只做试运行。
- 输入/历史数据：`valuation-v2/inputs.csv.gz`（36 家估值输入 + 42 家历史位置）、
  `valuation-v2/history.csv.gz`（月末 PB / 滚动 12 个月股息率）、`valuation-v2/manifest.json`。
- 报告：`valuation-v2.json` 与 `docs/archive/phase4l-bank-valuation-v2.md`；36 家中 25 家不在银行池，
  只给价值数字与无法判断，不出现候选/等待/回避。
- 历史位置：东方财富同源收盘价/市净率 + 分红（除权除息日），42 家中 41 家至少 5 年，
  兰州银行 58 个月不足 5 年，不计算分位；只描述，不给买点。
- v2/v2.1 试运行报告不写正式账本；2026-11-14 决策④批准后由 CLI/卡片按 v2.1 写正式结论。

## 银行估值 v2.1 修订（第十二批收尾）

- 草案：`data/policies/bank-valuation-v2.1-draft.json`；基准 ROE 纳入最新财年，悲观 ROE 增加近 3 年
  下滑速度外推 3 年，乐观 ROE 改近 3 年平均；新增描述性隐含长期回报率。
- 报告：`valuation-v2.1.json` 与 `docs/archive/phase4l-bank-valuation-v2.1.md`；保留 v2 基线
  `valuation-v2.json` 供逐家对照。
- 银行卡片新增隐含长期回报率一行（仅描述，不参与结论）。
- **2026-11-14 决策④批准 v2.1**：`data/policies/bank-valuation-v2.1.json`（status=approved，
  参数与草案逐项相同）；银行池 11 家 CLI/卡片给正式结论，并首次写入结论账本 11 条；
  非银行池只给三档价值与无法判断。第八批通用股息折现银行路线与 `bank-check-v1-draft` 已被取代。

## 校验

`engine.quality.snapshot.load_snapshot_dir(path)` 逐表核对 `content_hash` 与行数，
再从表格重建 `Company`；任意一张表被改写都会报 `SnapshotError`。
