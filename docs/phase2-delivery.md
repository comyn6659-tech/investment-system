# 阶段二首轮交付：个股研究估值

日期：2026-11-11｜代码版本 `phase2-0.2.1`（S1/S2 收尾）｜政策版本 `unapproved-phase2`｜模型版本 `equity-research-0.2.0`

本文件交代实现范围、运行方式、真实输出、测试结果与未解决事项。
按《第一阶段签收与第二阶段首轮施工指令》，本轮只做个股研究估值，
**不进入指数策略、不做回测、不做网站/自动交易/仓位管理，也不自行升级 `validated`。**

---

## 1. 交付了什么

| 链路环节 | 实现位置 | 状态 |
|---|---|---|
| 个股身份 | `engine/identity/`（阶段一） | 沿用，未改 |
| 固定财务/分红/价格事实 | `data/snapshots/`（3 只个股新快照） | 已采集，含来源/报告期/公告日/口径 |
| 结构化业务证据 | `data/evidence/*.json`（6 家） | 已采集，内容哈希即 `evidence_id` |
| 模型适用性（三条路线 + 未实现路线） | `engine/evidence/applicability.py` | 已实现，规则可重放 |
| 纯折现内核 | `engine/valuation/discounting.py` | 已实现，与取数/选型/报告分离 |
| FCFF 路线与桥接 | `engine/valuation/fcff.py`、`engine/financials/bridge.py` | 已实现，含 OCF 勾稽 |
| 股息折现路线 | `engine/valuation/ddm.py`、`engine/financials/dividends.py` | 已实现，含特别分红与超额分配处理 |
| 银行专用路线 | `engine/valuation/bank.py` | 数学与约束已实现；真实样本因证据缺失停住 |
| 研究假设配置 | `engine/assumptions/schema.py`、`data/assumptions/*.json` | 已实现，内容哈希 + 未批准状态 |
| 编排与结果 | `engine/valuation/runner.py`、`engine/pipeline.py` | 已实现，走现有 CLI，无平行入口 |
| 报告 | `engine/reporting/text_summary.py` | 文本与 JSON 同源同校验，含五项必备内容 |
| 参数来源与敏感性 | `docs/assumptions-and-sensitivity.md` | 已交付 |
| 真实输出证据表 | `docs/phase2-samples.md`（自动生成） | 已交付 |

三条新增模型的登记状态：`status=research`、`implemented=true`、`version=0.2.0-research`
（`engine/models/registry.py`）。**没有任何一条标记为 `validated`。**

---

## 2. 运行方式

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system

# 非金融公司：FCFF 主模型 + 股息折现交叉检查
.venv/bin/python -m engine.cli '600519' \
  --assumptions data/assumptions/600519-fcff-base.json \
  --cross-check-assumptions data/assumptions/600519-ddm-crosscheck.json

# 成熟非金融公司：股息折现主模型
.venv/bin/python -m engine.cli '600900' --assumptions data/assumptions/600900-ddm-base.json

# 银行：数学已实现，但资本/资产质量证据缺失 → insufficient_data
.venv/bin/python -m engine.cli '601398'

# 未实现路线：保险 / 周期 / 券商 → unsupported + 适用模型类别
.venv/bin/python -m engine.cli '601318'
.venv/bin/python -m engine.cli '601088'
.venv/bin/python -m engine.cli '600030'

# JSON 出口（与文本同源、同校验）
.venv/bin/python -m engine.cli --json '600519' \
  --assumptions data/assumptions/600519-fcff-base.json

# 请求文件方式（示例可复现）
.venv/bin/python -m engine.cli --json --request examples/requests/equity_600519_research.json
```

重跑数据与证据（需要网络，非交付必需）：

```bash
.venv/bin/python tools/build_equity_snapshots.py --as-of 2026-11-10
.venv/bin/python tools/build_business_evidence.py
.venv/bin/python tools/build_macro_evidence.py --start 20261001 --as-of 2026-11-10
.venv/bin/python tools/prune_snapshots.py          # 同标的同 as_of 只保留最新一版
.venv/bin/python tools/render_phase2_report.py     # 重新生成 docs/phase2-samples.md
.venv/bin/python tools/render_examples.py          # 重新生成 examples/outputs/
```

---

## 2.1 S1 / S2 收尾（复验报告两项）

复验报告：`phase2-review-and-closeout.md`。只修这两项，未扩大范围、未调参、未升级 `validated`。

### S1：少数股东权益缺失被当成零

**根因**：`build_equity_bridge` 用 `annual.number("minority_equity") or 0.0` 把「缺失」和「确认无此项」
合成同一个结果；同一个函数里还有一个 `absent_as_zero` 全局开关，可以一次性授权所有桥接项归零。

**修复**（`engine/financials/bridge.py`）：

- 删除全局 `absent_as_zero`，改为 `bridge_policy` **逐项**声明：每个桥接项
  （`debt_components` / `cash` / `minority_equity` / `non_operating_assets` / `preferred_equity`）
  只有「有来源的数值（含来源确认的零）」或「该项独立的零声明 + 依据」两种合法来源；
- 两者都没有 → `insufficient_data`（`MODEL_INPUT_MISSING`），不再产出「少扣一项」的完整估值；
- 每项的处理方式（`sourced` / `sourced_zero` / `asserted_zero` + 依据）随结果输出
  （`research_valuation.scenarios[].detail.equity_bridge_items`），可逐项审计；
- 同类旁路一并处理：`cash` 必须有可用数值；`preferred_equity` 不再由默认参数静默置零；
  `debt_components` 的零声明必须带依据，且仍保留「无负债却有利息费用」的冲突检查。

**修复后复现**（复验脚本同一操作：只把 600519 基准财年 `minority_equity` 改成 missing）：

```text
原样本：      complete，基准研究价值 1341.1280337369838 元/股
该字段缺失：  insufficient_data，没有研究估值（research_valuation = null）
             原因码含 MISSING_MODEL_INPUT，说明写「少数股东权益…缺失不能被当成零」
```

已知零仍可计算（`sourced_zero`），冲突同样拒绝。

### S2：没有持续资金来源的超额分配被零增长终值无限延续

**根因**：`check_growth_consistency` 只在 `terminal_growth > 0` 时检查留存约束；
`value_dividend` 又把末期 DPS 直接乘 `(1+g)` 当终值首期。把增长设成 0 只是取消了增长需求，
超额分配的资金需求仍在，于是每年 120 的永续分配被照单折现。

**修复**（`engine/valuation/ddm.py`）：

- 终值首期不再默认沿用末期 DPS：末期 DPS > 末期 EPS 且启用终值时，
  没有显式可持续终值就拒绝（`TERMINAL_DISTRIBUTION_UNFUNDED`），**与终值增长正负无关**；
- 允许在有依据时拆分：`sustainable_terminal_dps` 必须 ≤ 末期每股收益、必须附资金来源说明，
  终值首期改为该值，超额部分只进明确预测期，并记录 `EXCESS_DISTRIBUTION_TERMINAL_SPLIT`；
- 不静默把派息率截到 100%，也不是只加提示照原值计算；
- 银行路线复用同一内核，同步受该约束（已加回归）。

**修复后复现**（复验脚本同一合成输入：EPS 100、派息率 120%、r=10%、g=0）：

```text
自动永续：拒绝 TERMINAL_DISTRIBUTION_UNFUNDED，没有估值数字
显式可持续终值 100：1075.8157 元/股（= 100/10% + 20 × [1 − 1.1^-5]/10%）
100% 派息 + 零增长：1000.0（仍退化为永续年金）
```

### 真实样本研究价值是否变化

**没有变化**：600519 基准 1341.1280337369838 元/股、600900 基准 20.967224587337128 元/股，
与复验报告记录的数值一致。原因：两个样本的少数股东权益都有来源数值，派息率均低于 100%，
因此两项修复只收紧了「缺证据」的路径，没有改变已有样本的口径。
`600519-fcff-base.json` 的 `assumption_id` 由 `assump-489b99582aab805c` 变为
`assump-a9e9ce0fcbaf355f`——这是把 `absent_as_zero` 换成 `bridge_policy` 的**口径变更**，
不是数据刷新，也不是为了改变估值而调参；另外两份假设文件的 id 未变。

## 3. 真实样本结果

完整数字与逐行桥接表见 [phase2-samples.md](phase2-samples.md)（由真实运行生成，非手写）。

| 样本 | 分析状态 | 企业族 | 主模型 | 研究估值 | 情景区间 | 现价 | 差距 |
|---|---|---|---|---|---|---|---|
| 600519 贵州茅台 | `complete` | 非金融 | `equity_dcf_fcff` | 1,341.13 元/股 | 1,061.55 – 1,555.57 | 1,439.00 | −6.8% |
| 600900 长江电力 | `complete` | 非金融 | `equity_dividend_discount` | 20.97 元/股 | 16.53 – 25.57 | 29.67 | −29.3% |
| 601398 工商银行 | `insufficient_data` | 银行 | `bank_dividend_discount`（未执行） | — | — | — | — |
| 601318 中国平安 | `unsupported` | 保险 | 未进入模型 | — | — | — | — |
| 601088 中国神华 | `unsupported` | 明显周期 | 未进入模型 | — | — | — | — |
| 600030 中信证券 | `unsupported` | 券商 | 未进入模型 | — | — | — | — |

三条硬性事实：

1. **所有个股结果的 `decision` 都是 `undetermined`**，没有 `candidate` / `wait` / `avoid`；
   `model_status` 只有在真正跑出研究估值时才是 `research`，否则为 `null`。
2. 600519 的 FCFF 历史桥接与经营现金流勾稽差额 **1.91%**（容差 15%），
   因此是 `complete` 而非 `partial`。
3. 600519 的交叉检查用同一份财务事实、同一折现率，股息路线给出 1,015.59 元/股（−24.3%），
   差异来源已在 `docs/assumptions-and-sensitivity.md` §7 解释（终值增长低于可持续增长，
   股息路线的永续项不含留存收益再投资价值），**没有取平均、没有择优**。

### 3.1 为什么 600519 用 FCFF 而 600900 用股息

选择只看经营指标，不看股价（`decide()` 的签名里没有价格参数）：

- 600519：资本开支/收入 1.8% < 10%，FCFF/归母净利润 0.72 ≥ 0.70 → 自由现金流本身就是可分配来源 → FCFF 主。
- 600900：资本开支/收入 21.4% ≥ 10%，三年平均派息率 71.9% ≥ 55% → 股东回报由分配路径决定 → 股息主，
  且本轮不提供 FCFF 交叉检查（需要对目标资本结构作额外假设，该假设未获批准）。

---

## 4. 测试与验证

```text
$ PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
286 passed in 2.99s
```

阶段一为 142 项；本轮新增 144 项，分组如下：

| 文件 | 项数 | 覆盖内容 |
|---|---|---|
| `tests/test_valuation_kernel.py` | 61 | 折现恒等式、零增长终值退化为永续年金、折现率单调性、负现金流保留、终值边界、权益桥接符号/单位/重复计数、桥接项逐项零声明（含缺失/已知零/冲突）、每 10 股转换、特别分红剥离、超额分配与终值可持续性、增长一致性、银行资本与拨备约束 |
| `tests/test_applicability.py` | 25 | 企业族判定、行业只作线索、周期需波动证据、未实现路线不进 DCF、选择不依赖价格、证据文件哈希、报告期与 TTM 规则 |
| `tests/test_assumptions.py` | 13 | 假设可重放、内容哈希、状态不能升级为 validated、驱动项长度、来源必填、折现率 > 终值增长 |
| `tests/test_phase2_pipeline.py` | 45 | 真实样本完整链路、价格变化不影响内在价值与选择、盘中价不参与比较、银行缺口、未实现路线、无证据/无假设、跨标的假设、时点过期、契约护栏、JSON 与文本一致 |

阶段一原有回归（契约、身份、快照、时点、出口护栏）全部保留并通过；
本轮**只改了三处**与阶段一行为直接相关的断言，均为模型已实现后的语义更新，
并保持同等强度：

| 原断言 | 现断言 | 原因 |
|---|---|---|
| 个股结果 `unsupported` + `MODEL_NOT_IMPLEMENTED_EQUITY` | `insufficient_data`（缺研究假设），且 `research_valuation is None` | 个股模型已实现；缺假设时仍不得输出数字 |
| 分红记录里查 `period` 与 `notes` 文本 | 查 `dividend_summary` 的财年归集、类型、状态、`ltm` 与财年合计不同 | 分红已结构化归集，文本匹配让位于结构校验 |
| CLI `工商银行` 期望 `unsupported` | 期望 `insufficient_data`（退出码仍为 0） | 同上 |

### 4.1 数值验证（P2-2 对应）

- 单期现值 `100/1.1 = 90.909…`；多期与年金均为独立手算；
- 常数正现金流 + 零增长终值 = `CF/r`（`test_perpetuity_is_limit_of_zero_growth_terminal`）；
- 提高折现率不得提高价值（含终值情形，5 个折现率单调下降）；
- 增加债务使普通股价值等额下降、增加现金等额上升、股数翻倍每股价值减半；
- `EquityBridge` 没有 `net_debt` 入参，重复计数在类型层面不可能（`TypeError` 测试）；
- 每 10 股 → 每股、中期+年度按财年相加、特别分红单列、LTM ≠ 财年合计、未实施/非现金类型剔除；
- 非法终值边界（`r ≤ g`）、末期负现金流永续、NaN/Infinity 一律拒绝。

### 4.2 反例（P2-4 对应）

| 反例 | 处理 | 测试 |
|---|---|---|
| 低 ROE（3%）+ 高派息（90%）+ 正增长 | 拒绝，报「增长超过可持续增长」 | `test_low_roe_with_positive_growth_is_refused_with_explanation` |
| 低 ROE + 零增长 | 允许计算，不新增「一律不能估值」规则 | `test_low_roe_with_zero_growth_is_still_valued_and_flagged` |
| 超额分红（派息率 120%） | 非阻断问题 + 禁止正终值增长 | `test_payout_above_earnings_*` |
| 特别分红（含所属财年缺失） | 单列、不进入 DPS、不外推 | `test_special_dividend_never_enters_ordinary_dps`、600519 真实样本 |
| 亏损/困境 | 企业族判定为困境 → `unsupported`；股息/银行路线拒绝负盈利 | `test_loss_making_company_is_distressed_not_fcff`、`test_loss_making_basis_is_rejected_by_dividend_model` |
| 明显周期 | 行业线索 + 收入波动 → `unsupported`，不送进 DCF | `test_cyclical_needs_industry_and_volatility_together`、601088 真实样本 |
| 银行关键字段缺失 | `insufficient_data` + 列出缺失字段与已尝试接口 | `test_bank_route_stops_with_concrete_data_gap` |
| 无业务证据 | `insufficient_data`，不按行业套模型 | `test_equity_without_business_evidence_stops_at_insufficient` |
| 无研究假设 / 假设属于别的标的 / 假设时点过期 / 模型不匹配 | 全部 `insufficient_data`，不输出数字 | `test_equity_without_assumptions_*` 等 4 项 |

---

## 5. 本轮新增/变更的数据

### 5.1 快照（`data/snapshots/`，共 10 个）

| snapshot_id | 标的 | as_of | 事实数 | 说明 |
|---|---|---|---|---|
| `snap-791670f4b3c68cab` | EQUITY:600519.SH | 2026-11-10 | 544 | 三张报表 × 12 期 + 分红 + 已收盘价 |
| `snap-855281f9cc5afb1f` | EQUITY:600900.SH | 2026-11-10 | 528 | 同上 |
| `snap-e701028693917737` | EQUITY:601398.SH | 2026-11-10 | 550 | 同上 + 银行专项缺口字段 |
| `snap-fac0481bd5f7d1b5` | EQUITY:601398.SH | 2026-09-30 | 46 | 阶段一样本，保留用于历史重放 |
| `snap-6e6d5c31a0371ee0` / `snap-1a5ed1b1f7206c14` / `snap-5d60fe1e7fb6ed3b` | 三只宽基指数 | 2026-11-10 | 5 | 阶段一，未改 |
| `snap-6938a7a7b2c868c4` / `snap-ff2df2705d7e880f` / `snap-4c1d2bfec2a65678` | 三只 ETF | 2026-11-10 | 9 | 阶段一，未改 |

`SnapshotStore.check_consistency()` 返回 `[]`。同一标的同一 `as_of` 的中间版本已用
`tools/prune_snapshots.py` 清除（清单里会打印「谁被谁取代」），不放两份同义快照。

个股快照新增字段（相对阶段一）：利润表 10 项、现金流量表 10 项（含折旧摊销拆分与
营运资本补充资料）、资产负债表 17 项（含少数股东权益、归母权益、有息负债各科目）、
`price.close / price.trade_date / price.bar_status`、每股分红对应的除权日与实施状态。
银行另有 6 个专项字段记为 `missing` 并写明已尝试的四个接口。

### 5.2 业务证据（`data/evidence/`，6 家）

`EQUITY-600519/600900/601398/601318/601088/600030.SH.json`：
行业（仅线索）、最新报告期主营构成（按产品/地区，含收入占比与毛利率）、
近 8 个财年营业收入与归母净利润、来源清单。由 `tools/build_business_evidence.py` 生成。

### 5.3 宏观基准（`data/evidence/macro/`）

`market-rates-2026-11-10.json`：中国国债 10 年 1.5965%、5 年 1.3097%、30 年 2.0363%，
来源 AKShare `bond_zh_us_rate`，取数时间 2026-11-11。

### 5.4 研究假设（`data/assumptions/`，3 份）

`600519-fcff-base.json`（`assump-489b99582aab805c`）、
`600519-ddm-crosscheck.json`（`assump-37be4516f09a996d`）、
`600900-ddm-base.json`（`assump-67e3d6ec4045a5b7`）。
每份都含：标的、模型与版本、适用日期、基准财年、预测期、逐年驱动项、
折现率构建、终值规则、每个参数的来源/推导与类别、未批准状态。

---

## 6. 未解决事项与已知局限

| # | 事项 | 影响 | 下一步 |
|---|---|---|---|
| L1 | 银行资本充足率、不良率、拨备覆盖率、风险加权资产无免费来源 | 601398 只能给 `insufficient_data`，本轮没有银行样本出价 | 需要年报原文或付费/持牌数据源；数学与约束已实现并有测试 |
| L2 | 600900 缺 FCFF 交叉检查 | 只有单条路线；已说明原因是目标资本结构假设未获批准 | 待 P11 批准后补 |
| L3 | 交叉检查分歧 24.3%，接近 25% 阈值 | 未触发 `CROSS_CHECK_DIVERGENCE`，但分歧本身说明终值增长假设与可持续增长不一致 | 请设计方判断终值增长口径（P3） |
| L4 | 非经营性资产一律按 0 处理 | 长期股权投资等被计入 EV，偏保守 | 需要拆分口径后再做一次 |
| L5 | 受限货币资金未从现金中扣除 | 可能高估股权价值（本项目样本影响很小） | 需要「货币资金明细」科目 |
| L6 | 少数股东权益按账面扣除，未按公允价值 | 口径粗糙 | 同上 |
| L7 | 营运资本变动优先用现金流量表补充资料；资产负债表口径只作交叉核对 | 两者差额已在结果里记录 | 若补充资料缺失则退回资产负债表口径并标注 |
| L8 | 数据只到 2026-11-10，之后的价格与公告未纳入 | 结论有明确时点 | 重跑 `tools/build_equity_snapshots.py` |
| L9 | 业务证据为人工/脚本整理，未做自动化财报文本阅读 | 仍可能漏掉业务结构变化 | 后续可接 LLM 阅读，但不属本轮 |
| L10 | 行业分类来自东方财富，只作线索 | 若分类错误，周期/金融判定可能偏移 | 判定同时要求主营构成或波动证据 |
| L11 | 港股/美股、保险、券商、控股、周期、困境路线未实现 | 这些标的返回 `unsupported` + 所需证据 | 后续阶段 |
| L12 | 指数与 ETF 仍是阶段一状态 | 本轮未动 | 阶段三 |

---

## 7. 与阶段一契约的兼容性

- `analysis-result/1` 未变；`payload` 新增的 `research_valuation` / `dividend_summary`
  是**可选字段**，阶段一的历史结果（不含这两项）仍然通过校验（`PAYLOAD_OPTIONAL_FIELDS`）。
- 阶段一硬约束全部保留：
  - 阻断性原因码对 `candidate` / `wait` / `avoid` 一票否决；
  - 研究模型不得输出 `candidate`；
  - `unsupported` 结果不得携带任何定价结果；
  - 文本与 JSON 共用同一个校验器，序列化后再校验一次。
- 新增研究估值的来源约束（`validation._check_research_valuation`）：
  模型必须已登记且 `implemented`、版本一致、与 `model_plan.primary_model` 一致、
  整体 `model_status=research`、`decision=undetermined`、
  `valuation_state` 不得是 `attractive/neutral/expensive`、
  且不得占用正式 `payload.valuation` 字段。

---

## 8. 边界声明

- 本轮**不进入**指数回测、网站、自动交易、仓位管理、每日任务、新 skill 接入。
- **不修改**旧项目 `../valuation/`（本轮未读写其代码或缓存）。
- **未购买数据、未申请账户**。
- 三条模型均为 `research`；本轮的估值数字**不是**买点、不是买入上限、不是已验证策略。
- 没有为了得到「更便宜」的结论而回填增长、折现率或正常化现金流；
  参数先写来源与推导，再看结果（见 `docs/assumptions-and-sensitivity.md` §8）。
