# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十一批第二次收尾：**股东试用可用性修正** |
| 本轮实现提交 | `（本次收尾提交，见 git log）` |
| 上一轮 | `193736e`（第十一批第一次收尾，实现 `de013ee`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| Claude Code 新会话实测 | 两句均实际执行 `./ask`，卡片逐字呈现（2 张截图 + 会话记录） |
| `ask` 固定问题集 | **52 / 52**（新增推荐类 3 条、比亚迪“好不好”3 条） |
| 默认卡片术语扫描 | **0 命中**（52 条问题 + 30 只测试股） |
| 原因码中文解释 | 全部出现的 reason code 均有大白话解释；比亚迪卡片不重复原因 |
| 更新时限 | 已更新 / 非交易日 / 接口超时三种情形均 < 15 秒并注明数据日期 |
| 干净归档（`git archive`，无 `data/cache`） | **673 passed + 1 skipped**（无 `.git` 的 diff 测试跳过）；问题集 **52/52** |
| 正式结论账本 | **99 条**（98 条原有不变 + 1 条试用新增），0 问题 |
| 本地测试 | **674 passed** |

## 2. V1–V5

- **V1 `CLAUDE.md`**：项目根目录新增硬规则——任何投资/股票/指数/ETF/银行/三池问题必须先运行
  `./ask "<原问题>"`，逐字原样呈现，不得用自身知识回答或补充买卖意见，不支持的问题列出可用问法；
  规则详见 `SKILL.md`。
- **V2 意图补充**：“A股有哪些值得投资的公司”“有什么好股票”“推荐几只股票”→ 排行卡片并写
  “入池不等于买入”；“为什么比亚迪不是好公司”“比亚迪算不算好公司”“比亚迪是好公司吗”→
  是否在池成绩单，不再走个股估值卡。
- **V3 更新不让人等**：当天已成功更新则跳过；非交易日（周末 + 2026 交易所休市日）跳过自动更新；
  单次更新总时限 10 秒，超时用已有数据作答并注明“数据截至 X，本次未能更新”。
  交易日历：`data/calendar/cn-trading-holidays-2026.json`（上交所 2026 休市安排）。
- **V4 大白话**：默认卡片不出现 FCFF、主模型、机制默认落入、终值连续性护栏、EBITDA 等内部术语；
  每个原因码有中文解释；同一原因不重复（结论原因 → 其他原因；局限不再重复）；术语与原始数字
  只在 `--detail`。
- **V5 周期属性**：非周期公司显示“非周期”，不再显示“非金融企业”；周期公司“明显周期”；
  银行“非周期（银行）”。

## 3. W1 证据

新会话（Claude Code，2026-11-14 15:39，项目 `investment-system`）实际执行：

```bash
./ask "A股有哪些值得投资的公司"
./ask "茅台怎么样"
```

- 会话记录：`docs/phase4k-r2-claude-session.md`（含会话 ID 与两条 Bash 调用）
- 截图：[Q1](docs/evidence/claude-code-q1.png)、[Q2](docs/evidence/claude-code-q2.png)

## 4. 验收自检（W1–W6）

| 编号 | 结果 |
|---|---|
| W1 | Claude Code 新会话两句均调用 `./ask` 并呈现卡片；截图 + 会话记录已入库 |
| W2 | 推荐/值得投资问法 → 排行卡 + “入池不等于买入”；比亚迪三个问法 → 成绩单与未通过条件 |
| W3 | 非交易日、已更新、接口超时三情形 < 15 秒并注明数据日期（测试） |
| W4 | 默认卡片术语扫描 0；原因码中文解释齐全；比亚迪原因不重复（测试） |
| W5 | `git archive` 干净目录 673 passed + 1 skipped、问题集 52/52；账本前 98 条不变 |
| W6 | 本文件一页以内 |

## 5. 复现

```bash
scripts/verify-clean-archive.sh                  # 干净目录：全部测试 + 52 条问题集
./ask "A股有哪些值得投资的公司"
./ask "为什么比亚迪不是好公司"
./ask "茅台怎么样"
.venv/bin/python tools/run_ask_acceptance.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 详细交付：[docs/phase4k-delivery.md](docs/phase4k-delivery.md)｜端到端验收：[docs/phase4k-acceptance.md](docs/phase4k-acceptance.md)｜会话证据：[docs/phase4k-r2-claude-session.md](docs/phase4k-r2-claude-session.md)
- Claude 规则：[CLAUDE.md](CLAUDE.md)、[SKILL.md](SKILL.md)｜问题集：[data/ask/questions.json](data/ask/questions.json)｜验收清单：[docs/acceptance-checklist.md](docs/acceptance-checklist.md)
