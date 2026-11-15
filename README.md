# investment-system

A 股个股 / 境内宽基指数 / 境内普通被动 ETF / 三池名单的分析引擎。

- 规则化：所有结论由**已批准的规则 + 已提交的固定数据**重算，系统内部不调用大模型。
- 只前向验证：不做历史回测调参；结论账本只追加，2027-05-31 首次复盘。
- 银行池 11 家给正式结论（银行专用估值 v2.1）；指数与 ETF 只做研究/资料检查，不给买卖判断。

**交付入口：[DELIVERY.md](DELIVERY.md)**。

## 三份文档

| 文档 | 内容 |
|---|---|
| [docs/使用说明.md](docs/使用说明.md) | 怎么问（终端与 Claude Code）、支持的问法、卡片怎么读、四种结论含义 |
| [docs/规则说明.md](docs/规则说明.md) | 已批准的全部规则（个股政策 v1.1、三池、银行 v2.1、周期清单、指数位置分档）、决策①–④、已知局限与未做事项 |
| [docs/维护说明.md](docs/维护说明.md) | 每日自动更新、年度重跑三池、半年更新无风险利率与 ERP、2027-05 结论账本复盘、出错处理 |

历史批次过程报告在 [docs/archive/](docs/archive/)（保留不删）；验收截图在 `docs/evidence/`。

## 快速开始

```bash
cd /Users/comyn/Documents/投资/投资分析系统/investment-system
./ask "招商银行怎么样"          # 输出卡片（提问前尝试一次轻量自动更新）
./ask --detail "茅台怎么样"     # 展开细节
./ask --no-update "沪深300 现在什么位置"   # 完全离线
.venv/bin/python tools/update.py           # 手动刷新前向记录（只追加）
```

首次使用确认 `.venv/bin/python --version` 为 Python 3.12。每日自动更新需股东手动安装一次：
`scripts/install-daily-update.sh`。

## 目录

```text
engine/        运行时代码（不联网取数、不调用大模型）
data/          已提交的固定数据：快照、政策、账本、注册表、序列
tools/         取数、建快照、年度重跑、验收与报告工具（需要时才联网）
tests/         离线测试（694 项）
docs/          三份股东文档；docs/archive/ 为历史批次报告
scripts/       干净归档验证、账本快照校验、每日更新安装
ask / update   两个入口脚本
```

运行时只读已提交快照与政策，不读 `data/cache/` 等被 git 忽略的缓存（测试会扫描）。

## 当前状态

- 个股政策 v1.1（决策②，2026-11-12 批准，复核 2027-05-31）；
- 三池规则（决策③，2026-11-13 批准，复核 2027-11-13）；
- 银行专用估值 v2.1（决策④，2026-11-14 批准，复核 2027-05-31）；
- 指数候选 A 研究信号：规则未批准，只前向记录（决策①）；
- 已知局限：ETF 执行检查未做、券商保险不做、创业板指已移出（见《规则说明》第 7 节）。

## 复现与验证

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
scripts/verify-clean-archive.sh
.venv/bin/python scripts/verify-ledger-snapshots.py
.venv/bin/python tools/run_ask_acceptance.py
```

所有结果不构成投资建议。
