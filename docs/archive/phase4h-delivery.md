# 第四阶段第八批交付：银行标准 + 银行估值预演

本批只做 `batch4h-bank-instructions.md`：新增 `bank-pool-v1-draft`、逐行取银行专项指标、
银行池试运行、所有制标记、银行估值预演与新账本。不做券商/保险，不改好公司池、
稳定收息池、个股政策与结论账本、指数与 ETF。

## 1. 结果

| 项 | 数值 |
|---|---:|
| A 股上市银行 | 42 |
| 评价范围（非 ST、完整年报 ≥ 7 年） | 36 |
| 银行池入池 | **11** |
| 未入池 / 待定 | 25 / 0 |
| 只差一条 | 13 |
| 范围外 | 5,189（非银行 4,990、ST 193、年报不足 7 年 6） |
| 新快照 | **`qps-dc9c12160476db8b`**（71 张表、10.31 MB） |
| 银行账本 | 1 条（`bph-13b62d2bcfb016c0`） |
| 测试 | **579 passed** |

## 2. 银行规则 `bank-pool-v1-draft`（未批准）

五组 12 条，阈值全部来自政策文件：

- 会赚钱：窗口内亏损年数 = 0；ROE 平均 ≥ 10%；最近 3 年 ROE 平均 ≥ 9%。
- 资产干净：最新不良率 ≤ 1.5%；不良率 5 年变化 ≤ +0.3 个百分点；最新拨备覆盖率 ≥ 200%。
- 资本厚：最新核心一级资本充足率 ≥ 9.0%。
- 分红：窗口内每个财年都有现金分红；最近 5 年分红率 ≥ 25%。
- 做好人：审计、处罚主体、质押三条与 `quality-pool-v1.2-draft` 完全一致（测试比对）。
- 敏感性：不良率 1.3/1.5/1.8%、拨备 150/200/250%、核心一级 8.5/9.0/10.0%，只报告不选用。

## 3. 数据（N2、N5）

- 银行专项指标：东方财富 F10 `RPT_F10_FINANCE_MAINFINADATA`，42 家银行 2016 年起
  季度与年报共 **1,773 行**；ROE（ROEJQ）、不良率（NONPERLOAN）、拨备覆盖率（BLDKBBL）、
  核心一级（HXYJBCZL）、净息差（NET_INTEREST_MARGIN，只展示）。
- 覆盖率：36 家评价范围内银行，ROE 10 年、最新不良率、不良率 5 年变化、拨备、核心一级、
  分红、审计、处罚、质押均为 **36/36（100%）**；净息差 36/36（只展示）。
- 审计意见：银行报表使用 `companyType=3`（普通企业 `companyType=4` 对银行返回空）。
- 质押：银行三季报表格存在“普通股/优先股、前十名/前 10 名、含或不含‘持股情况’”多种写法，
  已逐类修正；3 家解析失败（兴业、上海、民生）已补足。苏农银行控股股东质押 **69.72%**，
  超过 50% 未通过；贵阳银行 49.77% 通过。
- 快照 `bank-indicators.csv.gz` 与 `bank-profile.csv.gz` 记录逐行来源；`--show 600036`
  可从快照复算 2016–2026 年逐期指标。

## 4. 入池与只差一条（N3）

入池 11 家：张家港行、苏州银行、招商银行、南京银行、兴业银行、农业银行、交通银行、
工商银行、邮储银行、建设银行、中信银行。

只差一条 13 家：渝农商行（ROE 9.97%）、中国银行（拨备 198.30%）、成都银行/青岛银行/
江苏银行（核心一级 8.85%/8.76%/8.67%）、杭州/江阴/常熟/平安/无锡/长沙/宁波（分红率
19.6%–23.2%）、北京银行（近 3 年 ROE 8.03%）。

## 5. 所有制（只标记，不改判定）

42 家银行全部标记：央企 9、地方国企 29、民企 4；银行池 11 家为央企 7、地方国企 3、民企 1。
依据实际控制人；无实际控制人时用第一大股东；银行边界逐家人工复核，
记录在 `data/policy-inputs/ownership-review-v1.json`。测试证明所有制不影响任何条件判定。

## 6. 银行估值预演（N4）

按个股政策 v1.1 `grades.banks`（三项证据 + 银行检查，最高 B 级）对 36 家评价范围内银行
逐家预演：**B 级 31 家、C 级 5 家、资料不足 0 家**。C 级 5 家为郑州、华夏、民生、光大、浙商，
资本或拨备低于预演适用下限（核心一级 8.5%、拨备 150%），模型拒绝给估值数字。

每家预演列出：路线 `bank_dividend_discount`、状态、等级、悲观/基准/乐观三档每股价值、
未满足条件与预演结论。预演假设（核心一级下限、拨备下限、风险加权资产密度、增速、
三情景 COE/增长/终值）记录在 `data/policy-inputs/bank-valuation-rehearsal-v1.json`，
**未获批准**。预演不写入正式结论账本，CLI 对 600036 仍为 `insufficient_data`（测试）。

## 7. 快照与账本（N6）

- 新快照 `qps-dc9c12160476db8b`：财务 44 + 分红 20 + 治理 4 + 所有制 1 + 银行指标 1 + 银行概况 1 = 71 张表。
- 银行账本 `data/ledger/bank-pool.jsonl` 追加 1 条（`bank-pool-v1-draft`，入池 11，
  `pool_hash=bph-13b62d2bcfb016c0`），`--check` 1 条 0 问题。
- 好公司池（138）、稳定收息池（142）、既有账本（个股 7、候选 A 3、官方估值 4、
  好公司池 3、收息池 1）均未改；个股结论账本不含银行，CLI 银行结论不变。

## 8. 复现

```bash
.venv/bin/python tools/build_bank_pool_data.py --fetch-bank
.venv/bin/python tools/build_bank_pool_data.py --fetch-governance --workers 6
.venv/bin/python tools/build_bank_pool_data.py --fetch-ownership
.venv/bin/python tools/build_bank_pool_data.py --build-tables
.venv/bin/python tools/build_bank_pool_data.py --build-snapshot
.venv/bin/python tools/run_bank_pool.py
.venv/bin/python tools/run_bank_pool.py --show 600036
.venv/bin/python tools/run_bank_pool.py --check
.venv/bin/python tools/run_bank_valuation_rehearsal.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 银行池报告：[phase4h-bank-pool-report.md](phase4h-bank-pool-report.md)
- 估值预演：[phase4h-bank-valuation-rehearsal.md](phase4h-bank-valuation-rehearsal.md)
- 政策：[bank-pool-v1-draft.json](../data/policies/bank-pool-v1-draft.json)
- 快照说明：[data/bank-pool/README.md](../data/bank-pool/README.md)
