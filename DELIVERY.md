# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 阶段二首轮收尾（S1 / S2 修复） |
| 代码版本 | `phase2-0.2.1` |
| 政策版本 | `unapproved-phase2`（没有任何模型或参数升级为 `validated`） |
| 模型版本 | `equity-research-0.2.0`（三条个股路线均为 `research`） |
| 本轮提交 | `PENDING`（S1/S2 修复提交，见 §7） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮被审查提交 | `a8c8f7a` |

## 1. 本轮完成范围

只按复验报告修 S1、S2 两项，并更新直接相关的测试、样例与文档。
未扩大施工范围、未重做基础设施、未调参追求估值变化、未改旧系统、未进入阶段三。

- **S1**：权益桥接里「少数股东权益缺失 → 当成零」的缺陷。现在每个桥接项只有
  「有来源的数值（含来源确认的零）」或「该项独立零声明 + 依据」两种合法来源，
  否则 `insufficient_data`；已删除可以一次授权所有桥接项归零的 `absent_as_zero` 全局开关。
- **S2**：没有持续资金来源的超额分配被零增长终值无限延续的缺陷。末期 DPS 超过当年 EPS
  且启用终值时，没有显式可持续终值就拒绝；有依据时改用 `sustainable_terminal_dps`，
  超额部分只进明确预测期。限制与终值增长正负无关，也不静默截断派息率。

真实样本研究估值**没有变化**（600519 `1341.1280337369838`、600900 `20.967224587337128`），
两项修复只收紧了「缺证据」路径。

## 2. 对复验报告的逐项答复

| 复验项 | 根因 | 修复位置 | 修复后结果 |
|---|---|---|---|
| S1 少数股东权益缺失当成零（P0） | `build_equity_bridge` 用 `annual.number(...) or 0.0`，把缺失与「确认无此项」合并；同函数有 `absent_as_zero` 全局开关 | `engine/financials/bridge.py` | 缺失 → `insufficient_data`、无研究估值、原因码 `MISSING_MODEL_INPUT`；已知零仍计算（`sourced_zero`）；冲突拒绝 |
| S2 无资金来源的超额分配被永续（P1） | `check_growth_consistency` 只在 `terminal_growth > 0` 时检查留存；`value_dividend` 直接把末期 DPS 外推为终值首期 | `engine/valuation/ddm.py` | 超额 DPS + 终值 → 拒绝 `TERMINAL_DISTRIBUTION_UNFUNDED`；显式可持续终值时 1075.8157 元/股；100% 派息零增长仍为 1000.0 |

复验脚本（只读、内存操作）现在的输出：

```text
PASS S1 missing minority interest: {'baseline_value': 1341.1280337369838,
     'missing_value': None, 'missing_status': 'insufficient_data', ...}
PASS S2 unsupported excess payout perpetuity: {'rejected': 'TERMINAL_DISTRIBUTION_UNFUNDED: ...'}
exit=0
```

## 3. 测试命令与实际结果

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 286 passed in 2.99s
```

| 文件 | 项数 | 本轮相关覆盖 |
|---|---|---|
| `tests/test_valuation_kernel.py` | 61 | 桥接项逐项零声明（缺失/已知零/冲突/无全局开关）；S2 终值可持续性五类情形；银行路线同步受约束 |
| `tests/test_phase2_pipeline.py` | 45 | 真实样本单字段缺失/已知零/冲突的完整运行；S2 端到端（拒绝 + 显式拆分）；文本渲染与契约 |
| `tests/test_applicability.py` | 25 | 适用性、证据哈希、报告期与 TTM |
| `tests/test_assumptions.py` | 13 | 假设哈希、状态、来源必填 |
| 阶段一原有 | 142 | 身份、契约、快照、时点、出口护栏（全部保留通过） |

**未通过项：无。** 复验脚本 `phase2-review-checks.py` 两项均 PASS，退出码 0。

## 4. 可复现命令与关键示例结果

```bash
# 1) 复验脚本（只读）
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  /Users/comyn/Documents/Codex/2026-11-10/users-comyn-documents-deepseek-harness-valuation/outputs/phase2-review-checks.py

# 2) 真实样本：非金融 FCFF 主 + 股息交叉检查
.venv/bin/python -m engine.cli '600519' \
  --assumptions data/assumptions/600519-fcff-base.json \
  --cross-check-assumptions data/assumptions/600519-ddm-crosscheck.json
# complete / research / undetermined；基准 1,341.13 元/股；区间 1,061.55 – 1,555.57；现价 1,439.00

# 3) 真实样本：成熟非金融股息主
.venv/bin/python -m engine.cli '600900' --assumptions data/assumptions/600900-ddm-base.json
# complete / research / undetermined；基准 20.97 元/股；区间 16.53 – 25.57；现价 29.67

# 4) 反例：银行缺资本/资产质量证据
.venv/bin/python -m engine.cli '601398'
# insufficient_data / undetermined；缺失字段含 bank.cet1_ratio、bank.npl_ratio 等

# 5) 反例：首版未实现路线
.venv/bin/python -m engine.cli '601318'   # unsupported（保险）
.venv/bin/python -m engine.cli '601088'   # unsupported（明显周期）
.venv/bin/python -m engine.cli '600030'   # unsupported（券商）

# 6) 重新生成本轮样例与证据表
.venv/bin/python tools/render_examples.py
.venv/bin/python tools/render_phase2_report.py
```

## 5. 详细材料

| 内容 | 文件 |
|---|---|
| 本轮交付与 S1/S2 修复说明 | [docs/phase2-delivery.md](docs/phase2-delivery.md) |
| 三条模型规格、桥接口径、失败状态 | [docs/equity-model-spec.md](docs/equity-model-spec.md) |
| 参数来源、敏感性、待设计方决定事项 | [docs/assumptions-and-sensitivity.md](docs/assumptions-and-sensitivity.md) |
| 真实运行样本、桥接表、敏感性表（自动生成） | [docs/phase2-samples.md](docs/phase2-samples.md) |
| 历史风险验收清单（含 S1/S2 行） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 数据覆盖表（阶段一调查） | [docs/data-coverage.md](docs/data-coverage.md) |
| 研究假设配置（内容哈希 + 未批准状态） | [data/assumptions/](data/assumptions/) |
| 结构化业务证据与宏观基准 | [data/evidence/](data/evidence/) |
| 固定快照与目录规则 | [data/snapshots/README.md](data/snapshots/README.md) |
| 示例请求与输出 | [examples/](examples/) |
| 阶段一交付入口 | [docs/phase1-delivery.md](docs/phase1-delivery.md) |

## 6. 尚未解决的问题与需要设计方决定的事项

### 6.1 已知局限（本轮未扩大范围）

| # | 事项 | 影响 |
|---|---|---|
| L1 | 银行资本充足率/不良率/拨备覆盖率/风险加权资产无免费来源 | 601398 只能 `insufficient_data`，本轮无银行出价 |
| L2 | 600900 缺 FCFF 交叉检查 | 目标资本结构假设未获批准 |
| L3 | 600519 两条路线差 24.3%（阈值 25%） | 终值增长与可持续增长口径差异，已解释不平均 |
| L4 | 非经营性资产按 0、受限资金未拆分、少数股东按账面 | 均为逐项声明、随结果可审计的近似，但仍是正式使用前的限制 |
| L5 | 合并范围含金融子公司时其存款/贷款变动进入经营现金流 | 用 OCF 勾稽差额暴露；本轮两样本 1.91% / 8.05% 在容差内 |
| L6 | 超额分配若确有现金流证据支持长期延续 | 本轮只支持「显式可持续终值 + 依据」的拆分；未实现融资/资产消耗模型 |
| L7 | 业务证据为脚本整理，未做财报文本阅读 | 可能漏掉业务结构变化 |

### 6.2 需要设计方决定

| # | 事项 | 现状 |
|---|---|---|
| P1–P3 | 股权风险溢价、公司特定溢价、终值增长 | 维持研究假设，未批准为全市场默认政策 |
| P4 | 五年预测期 | 接受为可配置研究基线 |
| P5 | 派息路径 | 接受显式情景，须通过 S2 的可持续性检查 |
| P6–P9 | 模型选择阈值、分歧阈值、勾稽容差、展示带宽 | 保留研究版本，未批准为正式质量门槛 |
| P10–P11 | 银行监管下限、RWA 增速 | 无证据时继续缺数阻断 |
| P12 | 当前总股本 / 回购稀释口径 | 维持「与估值时点一致的股数」研究口径 |
| P13 | 超额分配的现金流证明口径（S2 扩展） | 需定义可分配现金口径、维持性资本开支、受限资金与完整周期支付记录 |

## 7. 提交记录

```bash
git log --oneline
```

本轮提交与范围见 `git log`；每条提交信息都写明改动范围与测试结果。
历史交付报告（阶段一与阶段二首轮）保留在 `docs/` 中，本文件始终指向最新一轮。
