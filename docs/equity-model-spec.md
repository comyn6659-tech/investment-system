# 个股研究估值模型规格（阶段二首轮）

版本：`model-applicability/1`（选择规则）｜模型版本：`0.2.0-research`（三条路线）
状态：全部 `research`。本文件描述**已实现的计算口径**，不是已批准的投资政策。

---

## 0. 共同约定

| 项目 | 约定 |
|---|---|
| 折现时点 | 年末折现，预测期首期为 `t=1`，`t` 与预测年份一一对应（`base_fiscal_year + t`） |
| 有限期现值 | `Σ CF_t / (1+r)^t`，`t = 1…N` |
| 永续增长终值 | `TV_N = CF_(N+1) / (r − g)`，现值再乘 `1/(1+r)^N`；**必须** `r > g`，否则报 `RATE_NOT_GREATER_THAN_GROWTH` |
| 终值首期现金流 | 必须显式给出：末期现金流 × `(1+g)`，或由调用方显式传入；内核不会自动把末期现金流永续化 |
| 末期现金流异常 | 末期 FCFF/DPS ≤ 0 时拒绝启用永续终值（`TERMINAL_BASIS_MISSING`），不截断、不改符号 |
| 现金流为负 | 允许；内核保留负值，不做「为通过模型而截成 0」 |
| 数值卫生 | 任何 `NaN` / `±Infinity` 在入参与出参都被拒绝 |
| 单位 | 金额一律「元（CNY）」；每股金额「元/股」；股数「股」；比率用小数（0.25 表示 25%） |
| 折现率与现金流匹配 | FCFF 用 WACC；股息用股权成本；两者不混用 |
| 研究状态 | 三条路线均 `status=research`；结果 `model_status=research`、`decision=undetermined`、`valuation_state=unknown` |

折现数学在 `engine/valuation/discounting.py`，只做数学，不接触取数、模型选择或报告。

---

## 1. 模型选择（适用性）

实现位置：`engine/evidence/applicability.py`，规则版本 `model-applicability/1`。

### 1.1 输入

- **结构化业务证据**（`data/evidence/*.json`，内容哈希即 `evidence_id`）：
  行业分类（仅线索）、主营构成（按产品/地区，含收入占比与毛利率）、近 8 个财年收入与归母净利润、来源清单。
- **经营指标**（来自快照，不含股价）：资本开支/收入、FCFF/归母净利润、三年平均派息率、分红年数、派息稳定性。

### 1.2 企业族判定

| 企业族 | 触发条件（按顺序） |
|---|---|
| 保险 | 行业含「保险」，或前两大分部含寿险/财险/再保险/健康险 |
| 券商 | 行业含「证券」 |
| 银行 | 行业含「银行」 |
| 控股 | 行业含「综合 / 控股」 |
| 困境 | 最近 4 个财年出现归母净利润为负 |
| 明显周期 | 行业属周期类**且**（收入同比增速标准差 ≥ 8% 或 收入最大回撤 ≥ 15%） |
| 非金融 | 以上均未命中 |

行业只是线索：周期类行业必须同时有收入波动证据，否则按非金融处理（见测试
`test_cyclical_needs_industry_and_volatility_together`、`test_industry_alone_cannot_force_a_model_choice`）。

### 1.3 路线映射

| 企业族 | 结果 |
|---|---|
| 非金融 | 在 FCFF 与股息折现之间选主模型（规则见 1.4），另一条作交叉检查 |
| 银行 | `bank_dividend_discount`；普通 FCFF 与普通股息折现都被显式拒绝（`MODEL_NOT_APPLICABLE_TO_BANK`） |
| 保险 / 券商 / 控股 / 明显周期 / 困境 | `unsupported` + `MODEL_FAMILY_NOT_IMPLEMENTED`，返回适用模型类别与所需证据清单，**不进任何折现模型** |

### 1.4 非金融内部的主模型选择

```
若 资本开支/收入 < 10% 且 FCFF/归母净利润 ≥ 0.70  → FCFF 主 + 股息交叉检查
否则若 三年平均派息率 ≥ 55%                      → 股息主 + FCFF 交叉检查
否则                                            → FCFF 主 + 股息交叉检查
```

阈值是研究候选（随结果输出 `selection_policy`），不是已批准政策。
**判定函数不接收价格参数**，因此选择不会随股价变化（测试 `test_selection_does_not_depend_on_price`）。

### 1.5 缺证据时

- 没有业务证据 → `insufficient_data` + `MISSING_BUSINESS_EVIDENCE`，**不按行业白名单直接计算**。
- 有业务证据但缺经营指标 → `insufficient_data`，说明「无法在两条路线中选择」。

---

## 2. FCFF 路线（`equity_dcf_fcff`）

### 2.1 适用性

- 非金融企业；
- 有依据的经营预测（收入增速、EBIT 利润率可逐项给出并追溯来源）；
- 可核实的现金流口径与资本投入（现金流量表能把折旧摊销、营运资本、资本开支拆开）；
- 资本开支轻、现金转化高时优先选它。

### 2.2 历史桥接表（`engine/financials/bridge.py`）

严格区分「企业自由现金流」与「经营现金流 − 资本开支」。逐行口径：

| 行 | 取值 | 说明 |
|---|---|---|
| 净利润（含少数股东） | `NETPROFIT` | 合并利润表 |
| 加：所得税费用 | `INCOME_TAX` | |
| ＝利润总额 | `TOTAL_PROFIT` | 与上一行构成勾稽核对 |
| 加：净利息费用 | `FINANCE_EXPENSE`（缺失时退回 `FE_INTEREST_EXPENSE`） | 财务费用净额可为负（净利息收入）；退回时口径偏保守 |
| ＝EBIT | 利润总额 + 净利息费用 | |
| 减：按 EBIT 口径所得税 | `EBIT × 实际税率`；实际税率 = 所得税/利润总额 | 税率超出 `[−20%, 60%]` 直接拒绝，要求先解释税收调整 |
| ＝NOPAT | `EBIT × (1 − t)` | |
| 加：折旧与摊销 | `FA_IR_DEPR + IA_AMORTIZE + LPE_AMORTIZE + USERIGHT_ASSET_AMORTIZE` | 现金流量表补充资料 |
| 减：资本开支 | `CONSTRUCT_LONG_ASSET` | 购建固定资产、无形资产和其他长期资产支付的现金 |
| 减：营运资本增加 | 取负号的（`INVENTORY_REDUCE + OPERATE_RECE_REDUCE + OPERATE_PAYABLE_ADD`） | 优先使用**现金流量表补充资料的披露数**；同时用「存货 + 应收 − 应付」的资产负债表口径交叉核对，差额如实记录 |
| ＝FCFF | NOPAT + 折旧摊销 − 资本开支 − 营运资本增加 | |

**与经营现金流的勾稽**：

```
OCF 反推 FCFF = OCF + 净利息费用 × (1 − 税率) − 资本开支
差额 = FCFF − OCF 反推 FCFF；相对差额 = 差额 / |OCF|
```

- 相对差额 ≤ 容差（`extras.bridge_tolerance`，本轮 15%）→ 视为勾稽通过；
- 超出容差 → 结果降级为 `partial`，附 `BRIDGE_RECONCILIATION_GAP` 与差额明细，**不摊平差额**。

未处理项（如实声明）：少数股东损益、递延税、投资收益、公允价值变动、重分类等仍会落在差额里。

**合并范围内含金融子公司时**（例如集团财务公司）：其客户存款、同业存放、发放贷款的变动会进入
合并经营现金流，但不属于普通企业的营运资本，勾稽差额正是发现这类问题的信号。
本轮样本的差额分别为 1.91%（600519）与 8.05%（600900），都在容差内，因此保留 `complete`；
若某标的因此超出容差，会降级为 `partial` 并附差额，而不是把差额摊进某一科目。

### 2.3 预测与折现

年度驱动项（每组假设逐年给出）：

```
revenue_growth[t], ebit_margin[t], tax_rate[t], da_pct_revenue[t], capex_pct_revenue[t],
nwc_pct_incremental_revenue（标量）
FCFF_t = revenue_t × ebit_margin_t × (1 − tax_t) + revenue_t × da_pct_t
         − revenue_t × capex_pct_t − Δrevenue_t × nwc_pct
```

终值：`CF_(N+1) = FCFF_N × (1 + g)`，`g` 来自假设的终值规则并附来源；要求 `r > g`。

### 2.4 企业价值 → 普通股股权价值

```
EV = 有限期 FCFF 现值 + 终值现值
普通股价值 = EV − 有息负债 + 货币资金 + 非经营性资产 − 少数股东权益 − 其他权益工具
每股价值 = 普通股价值 / 当前总股本
```

实现上 `EquityBridge` **不提供 `net_debt` 字段**：净债务是派生量，若同时允许
`net_debt` 与 `debt/cash`，同一项就可能被计两次。因此「重复计数」在类型层面不可能发生。

| 桥接项 | 口径 | 本轮样本处理 |
|---|---|---|
| 有息负债 | 短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债 + 租赁负债 | 报表未列出的科目必须由假设显式声明 `absent_as_zero`（附理由），否则拒绝计算 |
| 货币资金 | `MONETARYFUNDS` | 未拆分受限资金，属已知简化 |
| 非经营性资产 | 本轮取 0 | 来源报表未把长期股权投资拆成非经营性项目，该项被计入 EV（偏保守），结果里明确说明 |
| 少数股东权益 | `MINORITY_EQUITY` | 按账面权益扣除，未按公允价值重估 |
| 其他权益工具 | 本轮取 0 | 样本无优先股/永续债；若存在需显式给出 |
| 冲突检测 | 全部负债科目缺失但有利息费用且无租赁负债 | 直接拒绝（`BRIDGE_ITEM_INVALID`），视为负债口径与利息费用冲突 |

### 2.5 必要数据与失败状态

| 必要数据 | 缺失/不可用时的状态 |
|---|---|
| 基准财年年报（收入、利润总额、所得税、财务费用、经营现金流、资本开支、货币资金、少数股东权益） | `insufficient_data` + `MISSING_MODEL_INPUT` |
| 上一年资产负债表（用于资产负债表口径的营运资本交叉核对） | 若补充资料齐备仍可计算；两者都缺则 `insufficient_data` |
| 当前总股本 | `insufficient_data` + `MISSING_SHARE_COUNT` |
| 研究假设文件（与主模型一致） | `insufficient_data` + `MISSING_MODEL_INPUT` |
| 已收盘价格 | 不影响估值；只影响现价比较（`PRICE_UNAVAILABLE_FOR_COMPARISON`） |

---

## 3. 成熟非金融股息折现（`equity_dividend_discount`）

### 3.1 适用性

- 非金融企业；
- 有持续分配依据（连续多个财年含年度末期分配）；
- 能解释现金覆盖与再投资需要（派息率、ROE、留存收益与增长互相可解释）；
- 当前高股息本身**不构成**适用性证明；特别分红**不构成**可持续分配证据。

### 3.2 分红归集（`engine/financials/dividends.py`）

| 概念 | 规则 |
|---|---|
| 每 10 股 → 每股 | 除以 10；巨潮「派息比例」为税前每 10 股 |
| 所属财年 | 取巨潮「报告时间」（`YYYY年报 / 半年报 / 三季报`）；缺失则**不猜年份**，单列 `unlabeled_*` |
| 财年合计 | 该财年内「年度分红 + 中期/季度分配」的每股金额之和；两者不重复计入 |
| 特别分红 | 单列，**不进入**可持续 DPS，也不外推为未来普通股息 |
| 股改分红等非现金类型 | 不计入 DPS |
| 实施状态 | 来源写「实施」→ 计入；未取到状态但除权日已过（≤ 分析日）→ 计入并记录依据 `ex_date_passed`；其余视为未实施，单列 |
| 完整财年 | 只有含「年度分红」的财年才算完整；只有中期分配的财年不算 |
| 「过去十二个月实际支付」 | 按除权日滚动 12 个月统计，**与「某财年对应分配」不是同一指标**，两者在报告中分别标注 |

### 3.3 预测与折现

```
EPS_t = EPS_(t−1) × (1 + eps_growth_t)，EPS_0 = 基准财年归母净利润 / 当前总股本
DPS_t = EPS_t × payout_t
每股价值 = Σ DPS_t / (1+r)^t + [DPS_N × (1+g) / (r − g)] / (1+r)^N
```

- `r` 是股权成本（不是 WACC）；
- **不再扣企业净债务**：`DividendDrivers` 结构里没有债务字段，重复扣减不可能发生；
- 终值 `DPS_(N+1)` 必须为正，否则拒绝启用终值。

### 3.4 一致性与反例处理

| 情形 | 处理 |
|---|---|
| 派息率 > 100%（超额分配） | 不直接拒绝；记为 `DISTRIBUTION_NOT_COVERED_BY_EARNINGS`（非阻断），说明需以存量现金或融资支撑 |
| 超额分配 + 正终值增长 | 拒绝（`GROWTH_NOT_FUNDED_BY_RETENTION`）：没有留存就没有内生增长 |
| 终值增长 > ROE × 末期留存率 | 拒绝（`GROWTH_NOT_FUNDED_BY_RETENTION`），报出可持续增长上限 |
| 低 ROE | 不拒绝；只要终值增长不超过「ROE × 留存率」即可计算（测试 `test_low_roe_with_zero_growth_is_still_valued_and_flagged`） |
| 基准期亏损（EPS ≤ 0） | 拒绝该路线（`MODEL_INPUT_MISSING`），并说明「亏损期无法由盈利外推股息」；企业族判定会把连续亏损归入困境路线 |
| 负 FCF | 不影响本路线的数学；在 FCFF 交叉检查里体现 |

### 3.5 必要数据

基准财年归母净利润、归母权益（算 ROE）、当前总股本、至少一个完整财年的分红（含年度末期）、
研究假设（EPS 增长路径、派息率路径、股权成本、终值规则）。

---

## 4. 银行专用股息折现（`bank_dividend_discount`）

### 4.1 适用性

- 商业银行（行业与主营构成共同指向存贷业务）；
- 必须有可核实的**资本**与**资产质量**口径；
- **不使用**普通企业净债务，也**不使用**通用经营现金流门槛；
- 「国有 / 大银行」不构成资本或风险检查的替代。

### 4.2 必须先过的检查（`engine/valuation/bank.py`）

| 检查 | 字段 | 不通过时 |
|---|---|---|
| 资本充足 | `cet1_ratio ≥ required_cet1_ratio` | 拒绝：`BANK_CAPITAL_BELOW_REQUIREMENT` |
| 拨备覆盖 | `provision_coverage ≥ required_provision_coverage` | 拒绝：`BANK_PROVISION_BELOW_REQUIREMENT` |
| 资本缓冲趋势 | `cet1_ratio` 与上期比较 | 非阻断问题 `BANK_CAPITAL_BUFFER_NARROWING` |
| 资产质量趋势 | `npl_ratio` 与上期比较 | 非阻断问题 `BANK_ASSET_QUALITY_DETERIORATING` |
| 分配是否由盈利覆盖 | `net_profit_parent > 0` | 拒绝：`MODEL_INPUT_MISSING` |
| 资本一致性上限 | `payout ≤ 1 − (ΔRWA × required_cet1) / net_profit` | 非阻断问题 `BANK_DISTRIBUTION_ABOVE_CAPITAL_CEILING`（说明需消耗资本缓冲或补充资本） |

监管下限（`required_cet1_ratio`、`required_provision_coverage`）必须来自可核实来源并写进假设文件的
`extras.bank_risk`。**代码里没有隐含默认值**；缺任何一个必需字段 → `insufficient_data` +
`MISSING_BANK_RISK_EVIDENCE`，并列出已尝试的接口。

### 4.3 估值数学

复用第 3 节的股息折现内核，但 DPS 由「归母净利润 × 派息率 / 总股本」推出，
折现率用股权成本；**不从企业价值出发，也不扣净债务**。
银行自身的 DPS 路径与股息内核结果在 `value_bank_dividend` 内做一致性核对，不一致即报错。

### 4.4 本轮真实样本的结论

工商银行（601398）：净利润、归母权益、分红、总股本均已取到，
但**核心一级资本充足率、不良率、拨备覆盖率、风险加权资产、监管下限**在已测的四个免费接口中都不提供
（详见 `docs/phase2-samples.md` 的缺口表）。因此该样本返回 `insufficient_data`，
不填造任何风险阈值，也不输出估值数字。

---

## 5. 状态语义

| 状态 | 触发条件 |
|---|---|
| `complete` | 模型完整执行，且历史勾稽与交叉检查都没有超出容差的差额 |
| `partial` | 模型执行完成，但勾稽差额超容差或两条路线分歧过大（`BRIDGE_RECONCILIATION_GAP` / `CROSS_CHECK_DIVERGENCE`） |
| `insufficient_data` | 必要数据或必要证据缺失（业务证据、假设、股本、银行风险字段、桥接科目） |
| `unsupported` | 企业族首版未实现路线；或标的身份未核实、范围外 |
| `engine_error` | 快照不可用、程序故障 |

不论哪一种状态，`decision` 恒为 `undetermined`；`valuation_state` 在 `unsupported` 时为
`not_applicable`，其余为 `unknown`（研究模型不给价格位置判断）。

---

## 6. 未实现路线与所需证据

| 企业族 | 适用模型类别 | 本版不能处理的原因 | 补齐所需证据 |
|---|---|---|---|
| 保险 | 内含价值（EV）+ 新业务价值（NBV）折现 | 准备金假设、投资收益率与会计准则共同决定利润，普通 FCFF/股息会把投资收益与准备金释放错当经营现金流 | EV 报告与假设、NBV 与渠道拆分、投资资产久期与收益率假设 |
| 券商 | 正常化盈利 + 分业务估值 | 利润高度依赖市场成交与自营波动，资本中介占用大量资产负债表 | 分业务收入与利润、净资本与风险覆盖率、正常化基准 |
| 控股平台 | 分部 / 持股净资产（NAV）折价 | 合并现金流来自不同行业子公司，直接折现会混用风险与增长 | 主要子公司持股与单独财务、分部行业分类、控股折价依据 |
| 明显周期 | 周期位置判断 + 正常化现金流 | 单年现金流由价格周期主导，直接用当期外推会顺周期偏差 | 产品价格与价差多年序列、产能利用率与库存位置、跨周期正常化利润率 |
| 亏损/困境 | 持续经营判断 + 恢复路径/清算回收 | 恢复路径未确认前，股息与自由现金流都不可外推 | 债务到期结构与现金余额、亏损原因拆分、恢复或重组方案 |

这些路线返回的是**适用模型类别 + 缺什么**，不是「没有投资价值」的判断。

---

## 7. 与阶段一契约的衔接

- 研究估值只写入 `payload.research_valuation`（`payload_version = equity-research/1`）；
  正式 `payload.valuation` 仍保留给 `validated` 模型，研究结果不得占用。
- `model_results` 增加 `result_type = research_valuation`，校验要求：模型已登记且已实现、
  版本与登记一致、与 `model_plan.primary_model` 一致、整体 `model_status=research`、
  `analysis_status ∈ {complete, partial}`、`decision=undetermined`、
  `valuation_state ∈ {unknown, not_applicable}`。
- 阶段一的硬约束全部保留：阻断性原因码对所有正式价格判断一票否决；
  研究模型不得输出 `candidate`；文本与 JSON 共用同一个校验器。
- `payload` 的向后兼容：`research_valuation` / `dividend_summary` 是**可选字段**，
  阶段一的历史结果（无这两个字段）仍然通过校验。
