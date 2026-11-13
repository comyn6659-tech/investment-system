# investment-system

A 股个股 / 境内 A 股宽基指数 / 境内普通被动 ETF 的分析引擎。

建设方式：独立新项目，从零搭建，不读取、不导入、不依赖旧项目
`../valuation/` 的代码、缓存或投资政策。旧目录仅作可选的只读查证资料。

**当前阶段：第四阶段第十一批（对话入口 + 端到端验收；周期行业清单补全；前向记录 update）。**

- 阶段一：身份、结果契约、固定快照与数据可用性调查（已签收 `5c4c1c9`）。
- 阶段二：三条个股研究路线（FCFF / 股息折现 / 银行专用），全部 `research`
  （已签收 `317506e`）。见 [docs/phase2-delivery.md](docs/phase2-delivery.md)。
- 阶段三首轮：三只首批宽基指数的 5 年月度描述性画像 + 三只 ETF 的产品资料完整性检查。
  见 [docs/phase3-delivery.md](docs/phase3-delivery.md)、
  [docs/index-profile-spec.md](docs/index-profile-spec.md)、
  [docs/phase3-samples.md](docs/phase3-samples.md)。
- 阶段四：个股政策 v1.1 正式结论（第二批）；创业板指接入（第三批，官方历史估值不可得）；
  好公司池草案试运行（第四批）；补治理检验（第五批）；收息池（第七批）；银行池与预演（第八批）；
  三池批准与恒生科技（第九批）；池成员批量估值（第十批）；对话入口与周期清单补全（第十一批）。
  见 [docs/phase4b-delivery.md](docs/phase4b-delivery.md) 至
  [docs/phase4k-delivery.md](docs/phase4k-delivery.md)。

**交付入口：[DELIVERY.md](DELIVERY.md)。**

所有研究结果 `decision=undetermined`、模型状态 `research`：
研究估值与指数分位**不是**买点、不是目标价、不是已验证策略；参数未经设计方批准。
`unsupported` 表示「本版未实现该路线」，`insufficient_data` 表示「数据不足」，
都不等于对该标的投资价值的判断。

## 1. 阶段一做了什么

| 能力 | 状态 |
|---|---|
| 标的识别（个股 / 宽基指数 / 被动 ETF / 范围外对象 / 歧义 / 非法输入） | 已实现 |
| ETF → 跟踪指数的核实映射 | 已实现（5 只有来源的 ETF） |
| 统一结果契约（analysis_status / model_status / valuation_state / decision / reason_codes） | 已实现，文本与 JSON 共用一套出口校验 |
| 研究护栏（research 模型不能产生正式 candidate） | 已实现 |
| 固定快照、内容哈希、离线重放 | 已实现（快照绑定请求标的/资产类别，加载即校验） |
| 时点语义（带时区比较、日期精度按当日结束） | 已实现 |
| 硬规则出口校验（research/未完成不得给正式价格判断） | 已实现，文本与 JSON 共用 |
| 数据可用性调查（个股 / 三只宽基 / ETF） | 已实现，见 `data/coverage/coverage-report.json` |
| 个股研究估值（FCFF / 股息折现 / 银行专用） | 已实现，`research`，见阶段二文档 |
| 指数 5 年月度估值画像（PE/PB 分位） | 已实现，`research`，见阶段三文档 |
| ETF 产品资料完整性检查 + 事后同日折溢价 | 已实现，`research`，见阶段三文档 |
| 分位进出策略、状态机、历史回测 | **未实现**（下一批） |

## 2. 目录结构

```text
engine/                 运行时代码（不 import akshare）
  identity/             标的身份：注册表、规范化、解析器
  contracts/            结果契约：枚举、原因码、载荷、校验
  data/                 事实、数值卫生、快照
  models/               模型登记表（阶段一全部 implemented=false）
  decision/             决策护栏（唯一允许写 decision 的地方）
  reporting/            JSON 出口与文本摘要
  evidence/             结构化业务证据与模型适用性判定
  series/               指数估值序列（内容哈希）、月度窗口与分位、画像组装
  financials/           报告期/TTM、FCFF 桥接、分红归集
  assumptions/          研究假设配置（内容哈希 + 未批准状态）
  valuation/            折现内核、FCFF、股息、银行约束、编排
  execution/            ETF 执行检查（阶段三）
  research/             研究与上线边界
  pipeline.py           解析 → 快照 → 结果 → 护栏 → 校验
  cli.py                命令行入口
data/registry/          已核实身份注册表 + 交易所A股清单（含来源与抓取时间）
data/evidence/          结构化业务证据 + 宏观利率基准（含来源与抓取时间）
data/assumptions/       研究假设配置（每组都带来源、推导与未批准状态）
data/series/            版本化指数估值序列（series_id = 内容哈希）
data/snapshots/         固定快照（小体量，入版本控制）
data/cache/             大体积原始数据（不入版本控制）
data/coverage/          数据可用性调查报告
tools/                  取数、建快照、建清单、探针（需要 akshare）
tests/                  离线测试
docs/                   交付文档、验收清单、数据覆盖表
examples/               请求与示例输出
```

## 3. 安装与运行

需要 Python ≥ 3.11（本机使用 3.12.16）。运行时（`engine`）只用标准库；
`tools/` 下的取数脚本需要 akshare。

```bash
cd /Users/comyn/Documents/deepseek-harness/投资/investment-system

# 1) 虚拟环境（任意 3.11+ 解释器均可）
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-data.txt pytest

# 2) 运行（默认离线，只读仓库内固定快照）
PY=.venv/bin/python
$PY -m engine.cli '工商银行'
$PY -m engine.cli --json '沪深300'
$PY -m engine.cli --json '510300'
$PY -m engine.cli --json '512880'      # 行业 ETF：范围外
$PY -m engine.cli --json '000001'      # 代码冲突：需要澄清

# 阶段二：个股研究估值（必须提供假设文件）
$PY -m engine.cli '600519' \
  --assumptions data/assumptions/600519-fcff-base.json \
  --cross-check-assumptions data/assumptions/600519-ddm-crosscheck.json
$PY -m engine.cli '600900' --assumptions data/assumptions/600900-ddm-base.json
$PY -m engine.cli '601398'             # 银行：资本/资产质量证据缺失 → insufficient_data
$PY -m engine.cli '601318'             # 保险：首版未实现 → unsupported

# 第十一批：对话入口（不调用大模型；卡片可供 Claude 原样呈现）
./ask "茅台怎么样"
./ask --detail "招商银行怎么样"
./ask --no-update "数据是哪天的"

# 第十一批：前向记录只追加刷新（行情/周信号/官方当日估值/恒生科技月度）
./update
./update --check

# 阶段三：指数画像与 ETF 产品资料（无需额外参数）
$PY -m engine.cli '沪深300'            # 5 年月度 PE/PB 分位
$PY -m engine.cli '510300'             # ETF 产品资料 + 关联同一份指数画像
```

退出码：`0` 正常产出结果 / `2` 需要澄清或输入非法 / `3` 引擎错误（快照不可用等）/
`4` 结果契约校验失败（实现缺陷，必须修复）。

其他参数：

```bash
$PY -m engine.cli --snapshot snap-xxxxxxxxxxxxxxxx '中证500'   # 指定快照重放
$PY -m engine.cli --request examples/requests/index_000300.json
$PY -m engine.cli --json --out examples/outputs/index_000300.json '沪深300'
$PY -m engine.cli --evidence-dir data/evidence '600519' --assumptions <file>
```

## 4. 测试

```bash
.venv/bin/python -m pytest -q
```

测试全部离线：不访问网络，只读 `data/registry/` 与 `data/snapshots/`。

## 5. 数据与快照

- **快照 id 由内容决定**：`snap-` + 规范化 JSON 的 SHA-256 前 16 位。读取时重新计算并比对，
  不一致即报错（`analysis_status=engine_error`），不降级成更宽松的结论。
- **重建快照会改变 id**：抓取时间进入内容哈希。同一份已提交快照的重放完全一致。
- **可获得时间**：每条事实单独记录 `available_at`，并解析为带时区时刻后比较；
  只有日期的公告按「当日结束」处理（精度 `date`），不会被当成当天 00:00 已知；
  无法确定发布时点的事实保持 `None`，不伪造成「当时已知」。
  快照 `coverage.point_in_time_granularity` / `availability_precision_counts` 记录精度分布。
- **同日多版本**：`latest_for` 按 `(as_of, fetched_at, snapshot_id)` 取最新，哈希只作身份与完整性校验；
  指定旧 ID 仍可精确重放。
- **快照绑定**：加载时核对请求 ID、文件声明 ID 与内容哈希，并校验快照所属标的与资产类别；
  串号直接报 `engine_error`，不会把别的标的价格挂到当前标的。
- **零与缺失分离**：`known_zero` / `missing` / `not_applicable` / `stale` / `conflict` 是不同状态，
  缺失不会变成 0，NaN/Infinity 会在构造与出口两次被拒绝。

## 6. 版本

| 字段 | 值 | 含义 |
|---|---|---|
| `schema_version` | `analysis-result/1` | 结果契约版本 |
| `code_version` | `phase3-0.3.0` | 代码语义版本（阶段三首轮） |
| `policy_version` | `unapproved-phase2` | 研究与正式决策分离，没有任何投资门槛获批 |
| `model_version` | `research-0.3.0` | 个股 3 个 + 指数画像 1 个，全部 `research` |

## 7. 明确不做（阶段三首轮）

不做指数回测、不做 A/B 进出状态机、不生成正式波段买点、不给目标点位；
不做网页、不做自动交易与仓位管理、不做每日任务、不接入新 skill；
不替设计方批准折现率、股权风险溢价、终值增长或银行风险参数；
不新增「低 ROE 一律不能估值」这类一刀切规则；不改旧系统、不购买数据、不申请账户。

## 8. 阶段一文档索引

- [docs/phase1-delivery.md](docs/phase1-delivery.md)：阶段一交付
- [docs/phase1-rework.md](docs/phase1-rework.md)：R1–R10 返工
- [docs/phase1-rework-f1f3.md](docs/phase1-rework-f1f3.md)：F1–F3 收尾
- [docs/data-coverage.md](docs/data-coverage.md)：数据覆盖表
- [docs/acceptance-checklist.md](docs/acceptance-checklist.md)：历史风险验收清单
