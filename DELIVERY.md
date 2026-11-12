# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第一批**收尾**（M1–M5；验收对象为 `9bc31ac` / 入口 `b7ef983`） |
| 代码版本 | `phase4-0.1.1` |
| 政策版本 | 引擎 `unapproved-phase2`；个股草案 `equity-policy-v1-draft`（`draft_unapproved`，未批准） |
| 模型版本 | `research-0.4.0`（个股 3 个 + 指数画像 + 候选 A 研究信号，全部 `research`） |
| 本轮提交 | `3a910c0`（M1–M5 收尾） |
| 工作区 | 干净（`git status --short` 为空） |
| 上一轮 | `b7ef983`（第四阶段第一批交付入口）、`9bc31ac`（批次实现） |

## 1. 本轮范围：只做 M1–M4，M5 回归

| 编号 | 问题 | 修复 | 结果 |
|---|---|---|---|
| M1 | 指数报告「历史覆盖」说官方文件只有 20 个交易日、无全收益、不支持 5 年周度，却同时给出候选 A 信号 | `payload.history_coverage` 拆成 `snapshot_path`（实时抓取路径，字段全部加 `snapshot_fetch_` 前缀）与 `series_library`（候选 A 使用的固定序列，逐条给起止与观测数）；文本按同一结构分两段 | 三只指数文本/JSON 均不再出现矛盾；序列库显示「支持 5 年周度历史：是」 |
| M2 | 周度分位显示 67.70%，真实值 67.7165%（先舍入到一位再按两位格式化） | `percentile_display` 改为两位；文本从未舍入值格式化 | 沪深300 现显示 **67.72%** |
| M3 | 000858 FY2025 营收 −55%、归母净利润 −72%，需核对；报告缺基期异常清单 | 用同花顺年度摘要独立核对；报告新增 §4.2「基期异常变动清单」，阈值写入政策 | **已核对，数据属实**（405.29 亿元 / 89.54 亿元）；清单只列 000858，不改结论 |
| M4 | 高杠杆企业 WACC 被账面债务权重压低（601668 WACC 3.11%） | 政策新增 50% 杠杆护栏；超过时 partial / C / 无法判断，价值标注「仅供研究参考」，新增原因码 | 仅 601668（83.3%）触发；其余标的最多 33.3%，结论不变 |

**不做**：未改 WACC 算法；未调任何阈值；未增加银行数据；未动指数阈值与回测；未改 ETF；未升级 `validated`。

## 2. M5 回归（不得变化的项）

| 检查项 | 结果 |
|---|---|
| 30 只预演结论逐只相同（含 601668 仍为无法判断） | **0 差异** |
| 账本既有 3 条记录哈希不变 | `update_signal_ledger.py --check` 0 问题；文件字节未变 |
| CLI 仍为 `research` / `undetermined` | 沪深300 / 中证500 / 中证1000 / 510300 / 个股均验证 |
| 全部测试通过 | **442 passed** |

## 3. 试运行结论（与验收批次一致）

| 维度 | 分布（30 只） |
|---|---|
| 预演结论 | 候选买点 **0**、等待 7、回避 4、无法判断 19 |
| 确定性等级 | A **5**、B **6**、C 19 |
| 状态 | complete 16、partial 2、insufficient_data 2、unsupported 10 |

达到 A/B 级的 11 只：贵州茅台（B，等待）、双汇发展（A，回避）、片仔癀（A，回避）、
福耀玻璃（A，等待）、海康威视（A，等待）、中国移动（A，等待）、伊利股份（B，回避）、
美的集团（B，等待）、万华化学（B，等待）、宁德时代（B，等待）、恒瑞医药（B，回避）。

候选买点为 0 是按政策 25%/40% 安全边际得到的事实，没有调规则；安全边际若一律按
20–30% 计，600309、300750 会进入候选买点，按 35–45% 计则为 0。

## 4. 指数候选 A 研究信号与账本（行为未变）

| 指数 | 观察周 | 观察日 | 官方滚动 PE | 5 年周度分位 | 信号 |
|---|---|---|---|---|---|
| 沪深300 | 2026-W45 | 2026-11-06 | 13.13 | 67.72% | 退出条件成立 |
| 中证500 | 2026-W45 | 2026-11-06 | 22.18 | 68.90% | 退出条件成立 |
| 中证1000 | 2026-W45 | 2026-11-06 | 26.01 | 62.01% | 退出条件成立 |

账本仍是 3 条（2026-W45），只追加、未改写。信号只描述条件是否成立，不跟踪持仓，
不构成买卖建议，也不给目标点位。

## 5. 数据（未变）

| 项目 | 内容 |
|---|---|
| 个股快照 | `data/policy-trial/snapshots/` 30 只，`as_of=2026-11-11`，本轮未重建 |
| 业务证据 | `data/policy-trial/evidence/` 30 份 |
| 无风险利率 | `data/policy-trial/macro/cn-10y-yield.json`（120 个月末 + 最新值） |
| M3 独立核对 | 同花顺年度财务摘要（AKShare `stock_financial_abstract_ths`）：000858 FY2025 营收 405.29 亿元、净利润 89.54 亿元，与东方财富一致 |
| 旧项目 | 未修改、未删除（`/Users/comyn/Documents/deepseek-harness/投资/valuation/` 只读参考） |

## 6. 可复现命令

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system
.venv/bin/python tools/run_equity_policy_trial.py                     # 试运行报告 + JSON
.venv/bin/python tools/update_signal_ledger.py --check                # 账本完整性
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

（数据采集命令见 `data/policy-trial/build-summary.json` 与 `tools/build_policy_trial_data.py`；
本轮未重建快照。）

## 7. 测试命令与实际结果

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
# 442 passed
```

| 文件 | 项数 | 内容 |
|---|---|---|
| `tests/test_equity_policy.py` | 38 | L1–L5 + M3/M4：政策集中/无散落阈值、折现率/终值/再投资/情景手算、600519 分歧、等级与结论边界、基期异常清单与独立核对、护栏边界 |
| `tests/test_index_signal.py` | 17 | 信号与账本 + M1/M2/M4：历史覆盖拆分、分位两位精度、账本幂等/冲突/篡改、护栏边界、CLI 不可成为价格判断 |
| 既有回归 | 387 | 阶段一/二/三全部保留，全部通过 |

**未通过项：无。**

## 8. 与批准的关系

收尾完成后交设计方复验 M1–M5 并签收；之后政策草案与试运行报告交 Fable 5.2 只读审查，
设计方逐条判断后再带证据提请股东决策②。批准之前，系统仍不输出正式结论，
`decision` 恒为 `undetermined`。

## 9. 提交记录

| 提交 | 内容 |
|---|---|
| `3a910c0` | **本轮**：M1–M5 收尾 + 7 项新测试 + 文档/示例 |
| `b7ef983` | 第四阶段第一批交付入口（记录 `9bc31ac`） |
| `9bc31ac` | 第四阶段第一批：政策草案试运行 + 候选 A 信号与账本 + 30 只数据 |
| `1866e2b` / `106a9b8` | 第三阶段收尾（已签收） |
| `698bd50` / `a5620ae` / `2f824df` / `1bb379c` | 第三阶段各批（已签收） |
| `82b8d4c` / `317506e` / `5c4c1c9` 等 | 阶段二、阶段一（已签收） |

## 10. 详细材料

| 内容 | 文件 |
|---|---|
| 本批实现与验收报告（含 M1–M5 与数据核对） | [docs/phase4a-delivery.md](docs/phase4a-delivery.md) |
| 个股政策试运行报告（自动生成，含基期异常清单与护栏） | [docs/phase4a-equity-policy-trial.md](docs/phase4a-equity-policy-trial.md) |
| 政策文件（版本化、每参数带依据、含 50% 护栏） | [data/policies/equity-policy-v1-draft.json](data/policies/equity-policy-v1-draft.json) |
| 试运行机器可读结果 | [data/policy-trial/trial-report.json](data/policy-trial/trial-report.json) |
| 试运行数据来源汇总 | [data/policy-trial/build-summary.json](data/policy-trial/build-summary.json) |
| 信号账本与规则 | [data/ledger/candidate-a-signals.jsonl](data/ledger/candidate-a-signals.jsonl)、[data/ledger/README.md](data/ledger/README.md) |
| 指数画像规格（含历史覆盖拆分与候选 A 信号） | [docs/index-profile-spec.md](docs/index-profile-spec.md) |
| 候选 A 回测记录 | [docs/phase3c-backtest.md](docs/phase3c-backtest.md)、[data/backtest/candidate-a.json](data/backtest/candidate-a.json) |
| 验收清单（阶段一/二/三 + L1–L8 + M1–M5） | [docs/acceptance-checklist.md](docs/acceptance-checklist.md) |
| 个股研究估值（阶段二） | [docs/phase2-delivery.md](docs/phase2-delivery.md)、[docs/equity-model-spec.md](docs/equity-model-spec.md) |
| 示例请求与输出 | [examples/](examples/) |
