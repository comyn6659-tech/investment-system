# 好公司池固定快照

本目录保存**固定快照**与清单；第四批快照（`snapshot-qps-e6fd0031c30f1e01`）保留不改，
第五批新增治理表后生成新快照。原始下载缓存位于 `data/cache/quality-pool/`，
已被 `.gitignore` 排除，不提交 git。

## 当前快照

| snapshot_id | 内容 | 状态 |
|---|---|---|
| `qps-e6fd0031c30f1e01` | 第四批：财务/分红/名单/公司整体质押，44 张表 | 保留不改，v1 政策复核 |
| `qps-1c1edb9a5fc01bc3` | 第五批：在第四批 44 张表上增加 4 张治理表，48 张表 | 当前快照，v1.1 政策使用 |

## 目录结构

```
data/quality-pool/
├── manifest.json                     # 当前快照指针、表数、压缩后总字节
├── snapshot-qps-<16位哈希>/
│   ├── manifest.json                 # 快照清单：as_of、窗口、每张表的元数据与哈希
│   ├── universe.csv.gz               # 沪深 A 股名单、上市日期、板块、简称
│   ├── industry.csv.gz               # 最新年报行业（识别金融业）
│   ├── pledge.csv.gz                 # 公司整体质押比例（旁证，不参与规则）
│   ├── governance-audit.csv.gz       # 252 家候选：2021–2025 审计意见
│   ├── governance-penalty.csv.gz     # 252 家候选：处罚/立案/公开谴责公告
│   ├── governance-pledge.csv.gz      # 252 家候选：控股股东质押
│   ├── governance-coverage.csv.gz    # 252 家候选治理三项可得性
│   ├── balance-2015.csv.gz …         # 资产负债表，按报告期
│   ├── income-2016.csv.gz …          # 利润表，按报告期
│   ├── cashflow-2016.csv.gz …        # 现金流量表，按报告期
│   └── dividends-2021-06-30.csv.gz … # 分红方案，按报告期
└── report.json                       # 最近一次试运行的机器可读报告
```

每张 `*.csv.gz` 旁边有同名 `*.csv.gz.meta.json`，统一记录一次：
`source_id`、`publisher`、`interface`、`url`、`report_date`、`columns`、
`rows`、`content_hash`（压缩文件字节的 SHA-256）、`size_bytes`。

## 为什么是表格而不是逐只 JSON

个股快照的“一只一个 JSON、每个数字带来源”格式适合少量标的；
全市场 5,000+ 家公司 × 10 年会膨胀到不可维护。本批按施工单补充要求改为
“报告期 × 报表类型”的 csv.gz 表格，来源与接口在表级元数据记录一次，
内容哈希按表计算；快照清单只汇总表哈希。

## 校验与读取

- `engine.quality.snapshot.load_snapshot_dir(path)` 会逐表核对 `content_hash` 与行数，
  再从表格重建 `Company` 对象；篡改任意一张表都会报 `SnapshotError`。
- `snapshot_id = qps-` + 清单规范化内容的 SHA-256 前 16 位；
  清单又包含每张表的哈希，因此表或清单被改写都会换 id。

## 原始缓存大小

压缩后的原始表格与固定快照合计约 **7 MB**（见 `manifest.json` 的 `total_bytes`），
远低于 1 GB 上限；若未来超过 1 GB，工具应先停下并报告实际大小。
