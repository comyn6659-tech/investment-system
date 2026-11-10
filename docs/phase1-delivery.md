# 阶段一交付报告（身份、结果契约与数据可用性）

- 项目：`/Users/comyn/Documents/deepseek-harness/投资/investment-system/`
- 依据：《投资分析系统设计书 v1.1》《给实现方的首轮施工指令》
- 交付时间：2026-11-11（Asia/Singapore）
- 代码版本：`phase1-0.1.0`；政策版本：`unapproved-phase1`；模型版本：`none`
- 本轮范围：**只做阶段一**，未进入阶段二，未实现任何估值或策略模型

---

## 1. 完成了什么 / 未完成什么

### 完成

1. **干净的新项目**：独立目录、独立 `.venv`、独立 `git`、独立缓存与快照；运行时不 import 旧项目，
   不读旧缓存，也不继承旧投资门槛（旧版 10%/12% 要求、AAA/AA/A 股息率档位、三档 DCF 参数一律未引入）。
2. **标的识别**：18 个已核实条目（3 只首批宽基 + 3 只其他宽基 + 2 只策略指数 + 2 只境外指数 +
   5 只范围内 ETF + 3 只范围外 ETF）加 5580 条 A 股个股清单（沪/深/北交易所清单）。
   能区分个股、宽基指数、具体被动 ETF、范围外对象、歧义输入与非法输入。
3. **ETF → 跟踪指数映射**：每只 ETF 的 `underlying_index_id` 都来自基金概况页的「跟踪标的」
   与业绩比较基准，逐条记录 URL 与抓取时间。
4. **结果契约**：`analysis_status / model_status / valuation_state / decision / reason_codes`
   独立表达；个股、指数、ETF 使用不同载荷；文本与 JSON 共用同一出口校验；研究护栏强制
   research ⇒ `undetermined`。
5. **新入口**：`python -m engine.cli`，默认离线读仓库内固定快照，支持 `--snapshot` 重放，
   退出码区分「正常 / 需澄清 / 引擎错误 / 契约失败」。
6. **数据可用性调查**：23 个接口实测（12 可用 / 8 部分可用 / 3 不可用），逐项记录字段、日期区间、
   行数、频率、可获得时间、缺失率、访问限制与结论；正式缺口 10 条。
7. **固定快照**：7 个（3 只宽基 + 3 只 ETF + 1 只个股），内容哈希寻址，读取时重算比对。
8. **测试**：91 个离线测试全部通过。

### 未完成（按施工单，属于后续阶段）

- 个股估值模型（DCF / 股息折现 / 银行专用）、经营形态判断、参数与敏感性表 → 阶段二
- 指数估值画像、候选 A/B 策略、回测与验证、ETF 执行检查 → 阶段三
- 自然语言入口与旧版对比、skill 接入 → 阶段四
- 未调整任何买卖阈值，未批准任何折现率/股息回报要求，未新增低 ROE 或高分红一刀切规则

---

## 2. 关键设计决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | `unsupported` 同时表示「范围外」与「模型未实现」，用 `reason_codes` 区分 | 设计书的 `analysis_status` 枚举没有 `out_of_scope`；把范围单独放在 `instrument.scope` 上，结论层仍统一为「不形成买点」，避免两套失败语义 |
| D2 | 范围内标的阶段一返回 `unsupported`，但仍输出身份与数据事实载荷 | 设计书 §4 允许「展示不依赖缺失数据的事实」；同时 `model_status=null`、`valuation_state=unknown`，不会伪装成已完成分析 |
| D3 | 名称启发式只用于**排除** | 禁止任何「看起来像宽基就纳入」的路径；代码里只有 `_heuristic_out_of_scope`，有测试守护 |
| D4 | 代码冲突（如 000001）返回模糊而不是按类型偏好挑一个 | 设计书 §5.1「代码有冲突时列出候选，不猜」；`000905`/`000852` 也与深市个股真实冲突，已单列测试 |
| D5 | 个股清单来自交易所官方清单（沪/深/北），逐条标注来源 | 让「输入任意 A 股名称」可用，同时不把清单当成类型判断依据；清单条目只能进入个股流程 |
| D6 | 快照 id = 规范化内容哈希（含抓取时间） | 同一文件重放完全一致；重新抓取就是新快照，旧结论不会被新数据「续上」 |
| D7 | 可获得时间未知的事实保持 `available_at=null`，并标记 `point_in_time_safe=false` | 不能把「不知道何时公布」当成「当时已知道」；`facts_available_by` 不会放行这类事实 |
| D8 | 官方指数估值与第三方估值分成两套 fact id（`index.pe_csindex_1` / `index.pe_ttm`） | 口径不同（聚合方法、亏损股处理未说明），禁止拼成一条看似连续的历史 |
| D9 | 指数点位 `unit=index_point` 且 `currency=null`，构造与出口两次拒绝带币种 | 设计书 §9「指数点位不得标为人民币」 |
| D10 | ETF 净值的披露时间缺失时不计算日终折溢价 | 设计书 §8「同日收盘价对同日已公布净值」，披露时间未知就不成立 |

---

## 3. 可复现命令与结果

### 3.1 安装

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-data.txt pytest
```

运行环境：Python 3.12.16，akshare 1.20.35，pandas 3.1.1，numpy 2.5.4。
`engine` 运行时只用标准库，不需要 akshare。

### 3.2 入口

```bash
PY=.venv/bin/python
$PY -m engine.cli --json '工商银行'
$PY -m engine.cli --json '沪深300'
$PY -m engine.cli --json '510300'
$PY -m engine.cli --json '512880'    # 范围外
$PY -m engine.cli --json '000001'    # 歧义，退出码 2
```

### 3.3 测试结果

```bash
$ .venv/bin/python -m pytest -q
........................................................................ [ 79%]
...................                                                      [100%]
91 passed in 3.61s
```

分布：识别 26、契约 17、事实 11、流程 20、快照/重放 6、CLI 11（含参数化）。

### 3.4 固定快照

| snapshot_id | 标的 | as_of | available_at | 事实数 |
|---|---|---|---|---|
| `snap-efb00dcacf4c74c8` | INDEX:000300.SH 沪深300 | 2026-11-10 | 2026-11-10T15:00+08:00 | 5 |
| `snap-549f2af3917da603` | INDEX:000905.SH 中证500 | 2026-11-10 | 2026-11-10T15:00+08:00 | 5 |
| `snap-fd346de1c14562ec` | INDEX:000852.SH 中证1000 | 2026-11-10 | 2026-11-10T15:00+08:00 | 5 |
| `snap-23eaf5b0bfc82144` | ETF:510300.SH 沪深300ETF华泰柏瑞 → 沪深300 | 2026-11-10 | 2026-11-10T15:00+08:00 | 10 |
| `snap-1f84eed253a4d726` | ETF:510500.SH 中证500ETF南方 → 中证500 | 2026-11-10 | 2026-11-10T15:00+08:00 | 10 |
| `snap-a3a962265466a536` | ETF:512100.SH 中证1000ETF南方 → 中证1000 | 2026-11-10 | 2026-11-10T15:00+08:00 | 10 |
| `snap-f9f0d2c00e8c1fd5` | EQUITY:601398.SH 工商银行 | 2026-09-30 | 2026-11-10T15:00+08:00 | 47 |

来源（每条事实在快照里都有独立 `source`）：

| 来源 | 用途 | 说明 |
|---|---|---|
| 中证指数有限公司 官方指标文件 `000300/000905/000852indicator.xls` | 官方 PE/股息率 | 只有最近 20 个交易日，无发布时间字段 |
| 乐咕乐股 `stock_index_pe_lg / stock_index_pb_lg` | 长历史 PE/PB | 实测**月度**采样，口径无官方说明 |
| 东方财富指数日线 `stock_zh_index_daily_em` | 点位历史 | 沪深300 自 2005-01-04，5306 个交易日 |
| 东方财富 ETF 行情/净值、天天基金基金概况页 | 价格、IOPV、净值、费率、跟踪标的 | 净值无披露时间 |
| 东方财富按报告期现金流量表/资产负债表 | 财报与公告日 | 83/84 个报告期，含 `NOTICE_DATE` |
| 巨潮资讯 `stock_dividend_cninfo` | 分红所属财年与类型 | 「报告时间」= 2025年报 / 2026半年报 |
| 新浪财经 `stock_history_dividend_detail` | 分红实施状态、除权日 | 与巨潮按公告日匹配 |

### 3.5 数据可用性调查

`tools/probe_data_sources.py` 实际调用 23 个接口：12 可用、8 部分可用、3 不可用。
完整表格见 [data-coverage.md](data-coverage.md)，机器可读版本见 `data/coverage/coverage-report.json`。

不可用：全收益指数行情、深交所 ETF 清单、ETF 跟踪误差序列。

---

## 4. 示例：正常结果与失败结果

全部示例在 `examples/outputs/`，请求文件在 `examples/requests/`。

### 4.1 正常结果：个股 `工商银行`

完整 JSON：[equity_601398.json](../examples/outputs/equity_601398.json)

```text
【标的】工商银行（EQUITY:601398.SH）· A股个股 · 市场 CN · 币种 CNY · 范围内
【目的】判断当前是否满足买点条件

【结论】不形成买点结论
  分析状态 unsupported（不适用/未实现） ｜ 模型状态 无 ｜ 价格位置 unknown ｜ 决策 undetermined
  原因：
    - MODEL_NOT_IMPLEMENTED_EQUITY：个股估值模型尚未实现（阶段二）
    - MODEL_SELECTION_NOT_IMPLEMENTED：模型适用性判断尚未实现（阶段二）

【方法】未选主模型：阶段一不实现模型适用性判断，因此不选主模型
  本应评估的模型：
    - equity_dcf_fcff（planned，已实现=False）
    - equity_dividend_discount（planned，已实现=False）
    - bank_dividend_discount（planned，已实现=False）

【估值情景】无。阶段一未实现个股模型，因此没有内在价值、安全边际或买点条件。

【报告期】2026-09-30（公告 2026-10-31）… 共 8 个报告期
【分红记录】10 条（按所属财年与实施状态归集，明细见 JSON）

【数据时点】截至 2026-09-30 ｜ 公开可得时间 2026-11-10T15:00+08:00 ｜ 快照 snap-f9f0d2c00e8c1fd5
```

JSON 关键字段：

```json
{
  "analysis_status": "unsupported",
  "model_status": null,
  "valuation_state": "unknown",
  "decision": "undetermined",
  "reason_codes": ["MODEL_NOT_IMPLEMENTED_EQUITY", "MODEL_SELECTION_NOT_IMPLEMENTED"],
  "payload": {"payload_type": "equity", "valuation": null,
              "dividend_records": [{"fact_id": "div.2025年报.年度分红", "value": 1.689,
                                    "unit": "CNY", "currency": "CNY", "period": "2025年报"}]},
  "missing_data": [{"fact_id": "fin.2026-09-30.net_debt", "status": "not_applicable",
                    "note": "银行以存款/同业负债为主，普通企业净债务口径不适用"}]
}
```

### 4.2 正常结果：指数 `沪深300`

完整 JSON：[index_000300.json](../examples/outputs/index_000300.json)

```json
{
  "analysis_status": "unsupported",
  "valuation_state": "unknown",
  "decision": "undetermined",
  "reason_codes": ["MODEL_NOT_IMPLEMENTED_INDEX", "MISSING_VALUATION_HISTORY"],
  "payload": {
    "payload_type": "index",
    "level": {"value": 4059.11, "unit": "index_point", "currency": null},
    "history_coverage": {
      "valuation_source_official": {"start": "2026-10-14", "end": "2026-11-10",
                                     "observations": 20, "frequency": "daily"},
      "valuation_source_monthly": {"start": "2005-04-29", "end": "2026-11-10",
                                   "observations": 260, "frequency": "monthly"},
      "supports_weekly_5y_history": false,
      "aggregation_method": "unknown",
      "point_in_time_safe": false
    }
  }
}
```

### 4.3 正常结果：ETF `510300`

完整 JSON：[etf_510300.json](../examples/outputs/etf_510300.json)

```text
【跟踪对象】沪深300（INDEX:000300.SH）
【ETF 事实】
  - 价格：4.135 元/份 CNY
  - 净值：4.1361 元/份（净值日期 2026-11-10）；披露时间未知，不能断言收盘时已公布
  - 折溢价：未取得
  - 费用：管理费 0.15%/年，托管费 0.05%/年
  - 交易状态：缺失（状态 missing）
【尚未核实/缺失】
  - etf.nav_disclosed_at / etf.premium_discount_pct / etf.trading_status
```

指数判断与产品执行判断分别输出，互不替代。

### 4.4 失败结果一：代码冲突 `000001`

完整 JSON：[ambiguous_000001.json](../examples/outputs/ambiguous_000001.json)

```text
【结论】不形成买点结论
  分析状态 needs_clarification（需要澄清） ｜ 决策 undetermined
  原因：IDENTITY_MULTI_MARKET：同一代码在不同市场分别对应不同标的

【需要你确认】
「000001」同一代码在不同市场/类型下分别对应不同标的：
- 上证指数（INDEX:000001.SH，in_scope）
- 平安银行（EQUITY:000001.SZ，in_scope）
请给出完整代码（如 510300）或完整名称。
```

退出码 2；没有调用数据源，没有价格结论。

### 4.5 失败结果二：快照不可用

完整 JSON：[engine_error_bad_snapshot.json](../examples/outputs/engine_error_bad_snapshot.json)

```json
{
  "analysis_status": "engine_error",
  "decision": "undetermined",
  "reason_codes": ["ENGINE_ERROR", "SNAPSHOT_NOT_FOUND"],
  "warnings": ["快照加载失败：找不到快照文件: snap-0000000000000000.json"],
  "snapshot_id": null
}
```

退出码 3；明确失败，不降级成更宽松的结论。

### 4.6 范围外：行业 ETF `512880`

完整 JSON：[out_of_scope_512880.json](../examples/outputs/out_of_scope_512880.json)

```json
{
  "analysis_status": "unsupported",
  "valuation_state": "not_applicable",
  "reason_codes": ["OUT_OF_SCOPE_INDUSTRY_ETF"],
  "instrument": {"display_name": "证券ETF国泰",
                 "scope": {"status": "out_of_scope",
                           "reason_code": "OUT_OF_SCOPE_INDUSTRY_ETF",
                           "detail": "跟踪中证全指证券公司指数（行业主题），不属于宽基流程"}}
}
```

---

## 5. 数据覆盖、口径与访问限制

摘要（完整见 [data-coverage.md](data-coverage.md)）：

- 个股：现金流量表/资产负债表按报告期给出 `NOTICE_DATE`，可做时点控制；分红需「巨潮报告时间 +
  新浪进度」两条来源联合归集。基础信息（股本、行业）只有当前值，无历史版本。
- 指数：官方估值仅最近 **20 个交易日**；第三方只有 **月度** 序列（沪深300 自 2005-04，
  中证500 自 2007-01，中证1000 自 2014-10）。**无法满足设计书要求的 5 年周度样本。**
- 全收益指数：免费源不可得。
- ETF：价格、净值、费率、IOPV 可得；净值披露时间、停牌状态、跟踪误差序列不可得。
  三只首批宽基各有一只已核实 ETF 快照（510300 / 510500 / 512100），跟踪对象、费率、价格、净值齐全。
- 口径：官方文件未说明整体法/加权法与亏损成分处理；第三方同样未说明。两套来源不可拼接。
- 收费/访问：全部免费、无需登录、未申请账号、未购买数据。东方财富部分接口较慢（全量 ETF 约 40 秒）。

---

## 6. 与设计不一致之处 / 需要设计方决定

### 6.1 实现层面的差异（已按下方理由处理，请确认）

1. **`out_of_scope` 的表达**：设计书 `analysis_status` 无此枚举。实现为
   `analysis_status=unsupported` + `instrument.scope.status=out_of_scope`，
   `valuation_state=not_applicable`。若设计方希望有独立状态，需扩展枚举。
2. **非法输入用 `needs_clarification`**：空字符串、乱码、未登记名称都返回
   `needs_clarification` 并给出澄清话术（退出码 2），而不是单独的 `invalid` 状态。
3. **`model_status=null`**：阶段一没有任何模型，`model_status` 用 `null` 而不是伪造一个值；
   设计书枚举为 research/validated/deprecated，未定义「无模型」。
4. **ETF 范围外的范围判定放在 `instrument.scope`**，不另设 `underlying_scope` 字段；
   指数中的其他宽基（如中证A500）标为 `in_scope` 但 `in_first_batch=false`，
   结果里追加 `INDEX_NOT_IN_FIRST_BATCH`。

### 6.2 数据事实与设计假设的冲突（需决定）

1. **5 年周度估值不可得**（最关键）。现有免费数据：官方 20 个交易日、第三方月度。
   阶段三要么降频到月度，要么更换数据源，要么另定窗口。**建议：先按月度序列交付估值画像，
   把「周度」作为升级项，并在报告中明确频率差异。**
2. **全收益指数不可得**。建议阶段三用「价格指数 + 显式股息调整」并在报告中说明，
   不把价格指数结果称为投资者实际收益。
3. **净值披露时间不可得**。建议阶段三把「日终折溢价」改为「净值日期对齐的折溢价，
   并标注时点不确定性」，或者在能取到披露时间的渠道出现前不输出折溢价结论。
4. **官方估值口径未说明**。建议在拿到编制方案/指标说明前，不把 `市盈率1` 标记为 PE-TTM，
   也不做跨来源拼接。

### 6.3 需要设计方决定的事项

| # | 事项 | 选项 | 影响 |
|---|---|---|---|
| Q1 | 阶段三估值频率 | 月度 / 坚持周度并换源 / 其他 | 决定 5 年分位是否可行 |
| Q2 | 是否接受「价格指数 + 股息调整」替代全收益 | 接受 / 不接受 | 决定回测口径 |
| Q3 | `analysis_status` 是否增加 `out_of_scope` | 增加 / 维持现状 | 契约版本升级 |
| Q4 | 非法输入是否要独立 `invalid_input` 状态 | 增加 / 维持现状 | 契约与 CLI 退出码 |
| Q5 | 银行专用数据（资本充足率、拨备覆盖率）来源 | 由实现方核实 / 设计方指定 | 阶段二银行模型能否成立 |
| Q6 | 个股清单的更新频率与失效处理 | 每次发版 / 定期重建 | 新股与改名覆盖 |

---

## 7. 下一阶段受阻的真实问题与建议

| 问题 | 现状 | 建议 |
|---|---|---|
| 指数周度估值历史 | 免费源只有月度 | 先按月度交付画像（Q1），周度作为数据升级项 |
| 银行资本/资产质量字段 | 通用财报接口不直接提供 | 阶段二开工前先做一次专项探针，把缺口写进快照 |
| 深交所 ETF 清单接口报错 | `fund_etf_scale_szse` 抛 `TypeError` | 保持代码段 + 行情清单的推断并标注来源；不影响阶段一结论 |
| 净利润/净资产之外的银行专用科目 | 未采集 | 阶段二按银行模型 `required_fields` 逐项补 |
| 旧系统对照 | 本轮按施工单不做对比 | 阶段四再挑 2–3 个标的做新旧差异说明 |

---

## 8. 旧版影响

本轮不涉及旧版：未修改 `../valuation/` 任何文件，未读取其缓存，未调用其代码。
因此不存在「旧结果因修复/参数/数据刷新而变化」的情况——旧版结果与新版没有任何共用路径。

---

## 9. 交付物清单

| 文件 | 说明 |
|---|---|
| `README.md` | 安装、运行、目录结构、阶段边界 |
| `docs/phase1-delivery.md` | 本报告 |
| `docs/data-coverage.md` | 数据覆盖与口径表（自动生成） |
| `docs/acceptance-checklist.md` | 历史风险验收清单，逐项对应实现与测试 |
| `data/registry/instruments.json` | 18 个已核实标的（含证据与取数时间） |
| `data/registry/cn_a_equities.json` | 5580 条 A 股个股清单（含三个交易所来源） |
| `data/snapshots/*.json` | 7 个固定快照 + manifest |
| `data/coverage/coverage-report.json` | 23 个接口的实测记录与 10 条缺口 |
| `examples/requests/*.json`、`examples/outputs/*.json` | 6 组请求/输出示例 |
| `tools/build_identity_catalog.py` | 重建 A 股清单 |
| `tools/build_snapshots.py` | 重建固定快照 |
| `tools/probe_data_sources.py` | 数据可用性调查 |
| `tools/render_coverage_report.py` | 覆盖表渲染 |
| `tests/` | 91 个离线测试 |
