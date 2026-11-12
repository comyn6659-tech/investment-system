# 稳定收息型固定快照

本目录保存稳定收息型的固定快照、候选名单与报告。快照 `qps-d8fb7e3718675b93`
是第七批的最终快照（69 张表、约 10.27 MB）；财务表沿用第六批
`qps-3ebbd24be612bf7e`，分红补到 2016–2025 共 20 个报告期，另含治理四表与所有制一表。
原始下载缓存位于 `data/cache/quality-pool/` 与 `data/cache/governance/`，
已被 `.gitignore` 排除，不提交 git。

## 目录结构

```
data/income-pool/
├── manifest.json                     # 当前快照指针
├── candidates.json                   # 收息型财务候选（财务全过 + 只差一条）
├── report.json                       # 最近一次试运行的机器可读报告
├── snapshot-qps-d8fb7e3718675b93/
│   ├── manifest.json                 # as_of、窗口、表级哈希、来源
│   ├── universe.csv.gz / industry.csv.gz / pledge.csv.gz
│   ├── balance-2015…2025.csv.gz      # 与好公司池同一批财务表
│   ├── income-2016…2025.csv.gz
│   ├── cashflow-2016…2025.csv.gz
│   ├── dividends-2016-06-30…2025-12-31.csv.gz   # 20 个报告期
│   ├── governance-audit.csv.gz       # 252 家好公司候选 + 收息型候选
│   ├── governance-penalty.csv.gz     # 主体身份与是否计入
│   ├── governance-pledge.csv.gz      # 控股股东/第一大股东质押
│   ├── governance-coverage.csv.gz
│   └── ownership.csv.gz              # 两池公司 + 30 只测试集的所有制标记
└── snapshot-qps-<…>/                 # 历史预快照只在运行时出现，不提交
```

## 口径

- 窗口与好公司池相同：2016–2025 完整财年；不足 10 年从上市当年起算，至少 7 年。
- 分红：`RPT_SHAREBONUS_DET` 按报告期取 2016–2025 中期与年度方案；
  每 10 股派现（含税）÷ 10 × 总股本，按财年归集。无现金分红记录按 0 处理；
  报告期表未覆盖的财年才记待定。
- 治理：只对财务条件全过或只差一条的 402 家候选逐家查；口径与
  `quality-pool-v1.2-draft` 完全一致（审计、处罚主体、质押含第一大股东）。
- 所有制：依据东方财富 F10 `ACTUAL_HOLDER`（实际控制人）；无实际控制人时用
  第一大股东；规则边界与人工复核记录在 `data/policy-inputs/ownership-review-v1.json`。
  所有制只标记，不参与任何条件判断。

## 校验

`engine.quality.snapshot.load_snapshot_dir(path)` 逐表核对 `content_hash` 与行数，
再从表格重建 `Company`；任意一张表被改写都会报 `SnapshotError`。
`snapshot_id = qps-` + 清单规范化内容的 SHA-256 前 16 位。
