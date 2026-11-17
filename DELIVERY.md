# 交付入口

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/投资/投资分析系统/investment-system/` |
| 本轮阶段 | 第七阶段补丁：**行情日期、过期提示、定时任务自检（H1–H6）** |
| 本轮实现提交 | `5d22e2c`（验收兼容 `0508a28`；本文档单独提交） |
| 上一阶段 | 第七阶段 M1–M7 交付见 `7e202b1`；本补丁只改运行与提示，不改规则/阈值/账本旧记录 |

## 1. 修了什么（H1–H6）

| 编号 | 修复 | 测试与验证 |
|---|---|---|
| H1 行情日期 | `fetch_quotes` 改为逐行按腾讯行情时间戳判断：行情日早于今天 → 当前价、日期=行情日；行情日=今天且 15:00 前 → 昨收、日期=上一交易日；15:00 后 → 当前价、日期=今天。`expected_price_date` 同步修正（凌晨/开盘前不再落到前前一天）。 | `test_h1_quote_price_date_by_timestamp`（周二 00:12、13:06、18:30，周六 10:00，国庆后开盘前 5 个时点）；`test_h1_expected_date_before_open_is_previous_trading_day`。真实 15:01 `./update` 新写 `quotes-2026-11-17.json`（5,225 行），此前 11-16 不再被拒。 |
| H2 过期提醒 | 最新 `price_date` < 应取日期时，`./ask` 每个带价格回答的第一行显示「价格停在 X，已落后 N 个交易日，请运行 ./update 或检查定时任务」；页面顶部与「数据状态」同一提示。N 只数交易日。 | `test_h2_stale_price_is_first_line_of_ask`（价格 11-16、应取 11-17、落后 1 日；不带价格的变动回答不提示）；`test_h2_page_top_has_stale_banner`。15:01 更新前真实 `./ask` 首行即为该提示；更新后消失。 |
| H3 定时任务自检 | `./update --check` 读取 `~/Library/LaunchAgents/com.investment-system.forward-update.plist`，核对 `WorkingDirectory` / `ProgramArguments` / 日志路径；plist 不存在或指向旧路径时明确报错并给出 `修复：cd … && scripts/install-daily-update.sh`。安装脚本安装后打印 plist、解释器、脚本与工作目录实际路径。 | `test_h3_launchd_check_ok_and_missing`；`test_h3_launchd_check_reports_old_path_with_fix`；`test_h3_install_script_prints_actual_plist_paths`。本机 `./update --check` → `launchd.status=ok`、`problems=[]`。 |
| H4 卸载脚本 | `scripts/uninstall-daily-update.sh` 第 23 行 `$LABEL（` 改为 `${LABEL}`；测试扫描所有 shell 入口，禁止变量后紧跟中文。 | `test_h4_uninstall_reports_label_without_unbound_variable`（临时 HOME 真实执行，rc=0，输出「已卸载定时任务」）；`test_h4_no_shell_variable_glued_to_chinese`。 |
| H5 财报季崩溃 | 复现：顶层 `data/pool-valuation/manifest.json` 的 `tables` 是数量（int），`annual_periods()` 却当表清单迭代 → `TypeError: 'int' object is not iterable`。修复：`applied_seasons` 从 `current` 快照目录的 manifest 读报告期；`tables` 非 list 时报错写清字段；单季失败带季节/报告期/报表，失败步骤不再拖垮整次更新。 | `test_h5_applied_seasons_reads_current_snapshot_manifest`（指针 `tables` 为 int 时能算出已应用季；2026 半年报/三季报可进入刷新）。真实 15:01 `./update` 状态 **ok**，`earnings_update` 正确 skipped（无到期季），不再 failed。 |
| H6 页面缓存 | `WebApp` 的个股/总览/三池/指数缓存按「最新行情快照 + 结论账本 + 批量快照指针」签名自动失效，并清空 `AskService` 的行情/快照派生缓存；页面每 60 秒检查数据状态，行情日期变化时顶部提示「数据已更新，点击刷新」。 | `test_h6_web_cache_invalidates_on_quote_and_ledger_change`（同一签名只评估 1 次；换行情快照后 2 次；改结论账本后 3 次；缓存重置 1→2 次）。 |

## 2. 验收数字与真实运行

- **本地测试：789 passed**（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider`，约 4 分 17 秒）。
- **干净归档：788 passed + 1 skipped**（`tests/test_pool_valuation.py:161` 无 `.git` 跳过）；`git archive` 后问题集 **68/68**、测试股 30/30、指数 4/4、银行 2/2、ETF 3/3，`全部通过=True`。
- **H5 财报季实跑**：真实数据源试跑 2026 三季报刷新，取数 **5,287 家**，2 家未取得（`002731`、`688613`）；主快照未切换（正式 `./update` 判定无到期季，避免重复切换/重复表）。同次真实更新结论变动 **2 家**（`002690` 美亚光电、`600993` 马应龙，等待→回避）；14:06 真实运行另有 1 家（`600406` 国电南瑞）。
- **真实 15:01 `./update`**：状态 `ok`；行情 appended 11-17（5,225 行）；财报季 skipped；红线扫描本周已扫描；结论检查 98 家、变化 2 家。
- **数据完整性**：`data/policies` 相对 `7e202b1` **零差异**；`data/ledger` 只有新增追加行（删除行 0），旧记录逐字节不变；`quotes-2026-11-13.json` sha256 `b3e0786b…caa0d` 与 HEAD 一致。新增的每日行情/序列/账本追加按既有「按月提交」约定留在工作树，不混入本补丁提交。

## 3. 说明

- H1 只改价格日期判断与补数逻辑，不改估值/结论规则；H5 只改报告期读取与错误信息，不切换主快照。
- 页面提示只影响展示；不静默使用旧价格，也不自动改账本。
- 定时任务仍需股东手动安装；本次未自动安装、未改 plist。

## 4. 复现命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider   # 789 passed
scripts/verify-clean-archive.sh                                                # git archive：788 passed + 1 skipped；68/68；全部通过=True
./update --check                                                               # 账本 + launchd 路径自检
.venv/bin/python tools/run_monitoring.py check
```
