# 交付入口

**本文件是每轮交付的唯一入口，只做汇总与导航；详细文档分别维护，历史报告保留。**

| 项目 | 内容 |
|---|---|
| 项目 | `/Users/comyn/Documents/deepseek-harness/投资/investment-system/` |
| 本轮阶段 | 第四阶段第十一批：**对话入口 + 端到端验收（含遗留修正）** |
| 本轮实现提交 | `301f4aa`（补充 `792f92b`、`e23f378`） |
| 上一轮 | `78635cc`（第四阶段第十批入口，实现 `1948318`） |

## 1. 结果

| 项 | 数值 |
|---|---:|
| 周期清单 | `cyclical-industries-v1`（申万 2021 版；9 组、精确名 + 旧线索） |
| 池成员 221 家重跑：结论改变 | **2**（君正集团、扬农化工 → 无法判断）；其余 219 家不变 |
| 30 只测试股重跑：结论改变 | **0** |
| `ask` 固定问题集 | **44 / 44**，10 类意图，每类 ≥3 种说法 |
| 端到端 30 股 / 4 指数 / 2 银行 / 3 ETF | **30/30、4/4、2/2、3/3** |
| `update` 首次行情快照 | 5,225 行，价格日期 **2026-11-13**；恒生科技月度 **2026-10-30** |
| 正式结论账本 / 测试 | **96 条**（0 问题，旧记录不变）/ **648 passed** |

## 2. 遗留修正（S1–S4）

- **周期清单**：`data/policies/cyclical-industries-v1.json`，依据申万行业分类 2021 版
  （2021-07-30 推出；来源 `http://www.swsindex.com/idx0530.aspx`）。覆盖煤炭、石油石化、钢铁、
  有色金属（工业金属/小金属/能源金属/贵金属）、基础化工（化学原料/化学纤维/农化制品/塑料/橡胶）、
  建筑材料（水泥/玻璃玻纤）、航运、生猪及禽类养殖；旧关键词单列保留，避免反向改结论。
  仍须收入波动 ≥8% 或回撤 ≥15% 确认；阈值、政策 JSON、估值代码未改。君正集团（化学原料，
  旧关键词「化工」漏判，波动 18.3%）由候选买点改为无法判断；扬农化工由回避改为无法判断。
  报告：`docs/phase4k-cyclical-rerun.md`。
- **标签**：正式结论统一为 `正式结论（政策 equity-policy-v1.1，已批准）`，`is_trial_only=false`；
  只有指数候选 A、银行估值等 research 模型保留研究/预演字样。validated 模型版本 `0.2.0`；
  指数原因码描述改为「不在已接入的研究样本内」；卡片内部英文条件名改中文。
- **创业板指 / 159915**：两者 scope 改为范围外（`INDEX_REMOVED_OFFICIAL_HISTORY_UNAVAILABLE` /
  `ETF_TRACKING_INDEX_REMOVED`）；停止 399006 官方估值前向记录与 159915 ETF 资料检查；
  已有序列、快照、官方估值账本 4 条不改。
- **前向记录**：`./update` 刷新官方日频序列、候选 A 周度信号、全市场行情、官方当日估值、
  恒生科技月度估值；只追加、重复跳过、冲突不覆盖；单步失败不阻断，运行日志只追加。
  `ask` 每次提问前尝试一次轻量 update，失败不阻断并注明数据日期。定时任务
  `scripts/install-daily-update.sh`（launchd 每日 18:30）**需股东手动执行一次**；
  卸载 `scripts/uninstall-daily-update.sh`；运行时不会自动安装。

## 3. 对话入口（S5–S7）

`./ask "<中文问题>"`，规则化识别（不调用大模型），默认卡片 ≤25 行、`--detail` 展开、`--json` 结构化。
支持：单只个股（全部 A 股，含不在池公司）、银行、指数、ETF、池名单、是否在池、排行、历史、状态、
范围外/歧义。卡片固定结构：第一行结论与一句话原因；现价/日期、候选门槛、距门槛、三档价值及用途；
起约束条件与中文未满足条件；所属池/所有制/周期属性/等级含义；局限；来源与日期、政策版本；
末行「不构成投资建议」。正式结论按原规则写入只追加账本。Claude 使用说明：`SKILL.md`
（原样呈现，不改写结论，不补充买卖意见，追问用 `--detail`）。
问题集：`data/ask/questions.json`；验收：`docs/phase4k-acceptance.md`；样例：`docs/phase4k-samples.md`。

## 4. 五个真实问答样例

1. **问：茅台怎么样** → 等待；现价 1,435.26 元（2026-11-13），候选门槛 686.80 元；正式结论（政策 v1.1）。
2. **问：招商银行怎么样** → 无法判断（银行估值未启用）；PB 0.97、股息率 4.7%、ROE 13.4%、不良 0.94%、拨备 389.39%、核心一级 14.39%。
3. **问：沪深300 现在什么位置** → 无法判断（研究画像）；PE 12.65（分位 76%）、PB 1.32（分位 39%）、候选 A 2026-W45 exit（研究信号，规则未批准）。
4. **问：好公司池有哪些** → 138 家（quality-pool-v1.2，快照 2026-11-12）。
5. **问：为什么比亚迪不在** → 不在好公司池；未通过 ROE 平均 11.93% < 15%、ROE ≥10% 年份占比 50% < 80%。

## 5. 验收自检（S1–S9）

| 编号 | 结果 |
|---|---|
| S1 | 清单版本化、带申万依据；221+30 重跑；改变 2 家；君正专节说明 |
| S2 | 正式/研究标签测试通过；validated 版本去掉 `-research`；研究模型无「正式结论」 |
| S3 | 399006/159915 范围外；其余指数、ETF 结果不变；官方估值旧 4 条不改 |
| S4 | update 只追加/重复跳过/冲突不覆盖；launchd 可安装可卸载，未自动安装 |
| S5 | 44 条问题覆盖 10 类意图，每类 ≥3 种说法，全部自动测试 |
| S6 | 卡片结构符合要求；默认 8–10 行，≤25 行；`--detail` 展开 |
| S7 | 问题集 44/44；30 股/4 指数/2 银行/3 ETF 全过；禁用词 0；日期/来源齐全 |
| S8 | **648 passed**；账本 96 条 0 问题；旧记录不变；所有账本只追加 |
| S9 | 本文件一页以内；5 个真实问答样例见上，完整卡片见 `docs/phase4k-samples.md` |

## 6. 复现

```bash
.venv/bin/python tools/run_cyclical_rerun.py --write-ledger
./update && ./update --check
./ask "茅台怎么样" && ./ask --detail "招商银行怎么样"
.venv/bin/python tools/run_ask_acceptance.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

- 详细交付：[docs/phase4k-delivery.md](docs/phase4k-delivery.md)｜周期重跑：[docs/phase4k-cyclical-rerun.md](docs/phase4k-cyclical-rerun.md)｜端到端验收：[docs/phase4k-acceptance.md](docs/phase4k-acceptance.md)
- 样例：[docs/phase4k-samples.md](docs/phase4k-samples.md)｜Claude 使用说明：[SKILL.md](SKILL.md)｜问题集：[data/ask/questions.json](data/ask/questions.json)｜验收清单：[docs/acceptance-checklist.md](docs/acceptance-checklist.md) 第十一批 S1–S9
