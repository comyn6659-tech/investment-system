# 对话入口端到端验收（batch4k）

- 生成时间：2026-11-14T20:43:53+08:00
- 政策：`equity-policy-v1.1`（已批准）
- 固定问题集：67 条，通过 67 条；端到端全部通过：是
- 问题集测试使用批量快照固定价格；30 只测试股 / 指数 / 银行 / ETF 使用当日实时行情。

## 1. 固定问题集

| # | 类别 | 问题 | 期望意图 | 实际意图 | 结论 | 行数 | 结果 |
|---|---|---|---|---|---|---:|---|
| q01 | 单只个股 | 茅台怎么样 | stock | stock | wait | 10 | 通过 |
| q02 | 单只个股 | 600519 贵不贵 | stock | stock | wait | 10 | 通过 |
| q03 | 单只个股 | 美的能买吗 | stock | stock | wait | 10 | 通过 |
| q04 | 单只个股 | 五粮液值得买吗 | stock | stock | undetermined | 10 | 通过 |
| q05 | 单只个股 | 格力电器现在能买吗 | stock | stock | undetermined | 10 | 通过 |
| q06 | 单只个股 | 京东方A怎么样 | stock | stock | undetermined | 10 | 通过 |
| q07 | 银行 | 招商银行怎么样 | bank | bank | candidate_buy_point | 15 | 通过 |
| q08 | 银行 | 工商银行贵不贵 | bank | bank | wait | 15 | 通过 |
| q09 | 银行 | 招行股息率多少 | bank | bank | candidate_buy_point | 15 | 通过 |
| q10 | 银行 | 平安银行怎么样 | bank | bank | undetermined | 16 | 通过 |
| q11 | 指数 | 沪深300 现在什么位置 | index | index | undetermined | 13 | 通过 |
| q12 | 指数 | 中证500 贵不贵 | index | index | undetermined | 13 | 通过 |
| q13 | 指数 | 中证1000 什么位置 | index | index | undetermined | 13 | 通过 |
| q14 | 指数 | 恒生科技 | index | index | undetermined | 11 | 通过 |
| q15 | ETF | 510300 能买吗 | etf | etf | undetermined | 9 | 通过 |
| q16 | ETF | 510500 怎么样 | etf | etf | undetermined | 9 | 通过 |
| q17 | ETF | 512100 能买吗 | etf | etf | undetermined | 9 | 通过 |
| q18 | ETF | 159915 能买吗 | out_of_scope | out_of_scope | — | 10 | 通过 |
| q19 | 池名单 | 好公司池有哪些 | pool_list | pool_list | — | 9 | 通过 |
| q20 | 池名单 | 收息池里的国企 | pool_list | pool_list | — | 8 | 通过 |
| q21 | 池名单 | 银行池 | pool_list | pool_list | — | 18 | 通过 |
| q22 | 池名单 | 稳定收息池有哪些 | pool_list | pool_list | — | 9 | 通过 |
| q23 | 是否在池 | 格力在不在好公司池 | membership | membership | — | 9 | 通过 |
| q24 | 是否在池 | 为什么比亚迪不在 | membership | membership | — | 10 | 通过 |
| q25 | 是否在池 | 茅台在不在收息池 | membership | membership | — | 8 | 通过 |
| q26 | 是否在池 | 招商银行在不在银行池 | membership | membership | — | 8 | 通过 |
| q27 | 是否在池 | 中国平安在不在好公司池 | membership | membership | — | 8 | 通过 |
| q28 | 排行 | 好公司里谁离买点最近 | ranking | ranking | — | 15 | 通过 |
| q29 | 排行 | 现在有哪些候选买点 | ranking | ranking | — | 22 | 通过 |
| q30 | 排行 | 收息池里谁离买点最近 | ranking | ranking | — | 16 | 通过 |
| q31 | 排行 | 离买点最近的股票有哪些 | ranking | ranking | — | 22 | 通过 |
| q32 | 历史 | 茅台以前的结论 | history | history | — | 10 | 通过 |
| q33 | 历史 | 600519 历史结论 | history | history | — | 10 | 通过 |
| q34 | 历史 | 招行的结论记录 | history | history | — | 7 | 通过 |
| q35 | 状态 | 数据是哪天的 | status | status | — | 12 | 通过 |
| q36 | 状态 | 规则什么时候复核 | status | status | — | 12 | 通过 |
| q37 | 状态 | 数据更新到哪天 | status | status | — | 12 | 通过 |
| q38 | 状态 | 政策版本是什么 | status | status | — | 12 | 通过 |
| q39 | 范围外/歧义 | 创业板 | ambiguous | ambiguous | — | 7 | 通过 |
| q40 | 范围外/歧义 | 创业板指 | out_of_scope | out_of_scope | — | 10 | 通过 |
| q41 | 范围外/歧义 | 中国平安 | stock | stock | undetermined | 10 | 通过 |
| q42 | 范围外/歧义 | 苹果公司 | out_of_scope | out_of_scope | — | 11 | 通过 |
| q43 | 范围外/歧义 | 000001 | ambiguous | ambiguous | — | 10 | 通过 |
| q44 | 范围外/歧义 | 平安 | ambiguous | ambiguous | — | 11 | 通过 |
| q45 | 历史 | 君正集团以前的结论 | history | history | — | 8 | 通过 |
| q46 | 历史 | 扬农化工以前的结论 | history | history | — | 8 | 通过 |
| q47 | 排行 | A股有哪些值得投资的公司 | ranking | ranking | — | 22 | 通过 |
| q48 | 排行 | 有什么好股票 | ranking | ranking | — | 22 | 通过 |
| q49 | 排行 | 推荐几只股票 | ranking | ranking | — | 22 | 通过 |
| q50 | 是否在池 | 为什么比亚迪不是好公司 | membership | membership | — | 10 | 通过 |
| q51 | 是否在池 | 比亚迪算不算好公司 | membership | membership | — | 10 | 通过 |
| q52 | 是否在池 | 比亚迪是好公司吗 | membership | membership | — | 10 | 通过 |
| q53 | 排行 | 收息池有哪些公司现在的价格很合适 | ranking | ranking | — | 16 | 通过 |
| q54 | 排行 | 好公司池里哪些现在便宜 | ranking | ranking | — | 15 | 通过 |
| q55 | 排行 | 好公司池哪些划算 | ranking | ranking | — | 15 | 通过 |
| q56 | 排行 | 收息池里哪些低估 | ranking | ranking | — | 16 | 通过 |
| q57 | 排行 | 好公司池里现在哪些值得买 | ranking | ranking | — | 15 | 通过 |
| q58 | 排行 | 收息池哪些能买 | ranking | ranking | — | 16 | 通过 |
| q59 | 排行 | 好公司池里价格合适的公司 | ranking | ranking | — | 15 | 通过 |
| q60 | 排行 | 收息池离买点最近的公司 | ranking | ranking | — | 16 | 通过 |
| q61 | 排行 | 银行池里哪些便宜 | ranking | ranking | — | 18 | 通过 |
| q62 | 银行 | 招商银行怎么样 | bank | bank | candidate_buy_point | 15 | 通过 |
| q63 | 银行 | 工商银行怎么样 | bank | bank | wait | 15 | 通过 |
| q64 | 银行 | 平安银行怎么样 | bank | bank | undetermined | 16 | 通过 |
| q65 | 排行 | 银行池里谁离买点最近 | ranking | ranking | — | 18 | 通过 |
| q66 | 排行 | 银行里有哪些买点 | ranking | ranking | — | 18 | 通过 |
| q67 | 排行 | 现在有哪些候选买点 | ranking | ranking | — | 22 | 通过 |

## 2. 30 只测试股

| 代码 | 名称 | 意图 | 结论 | 现价 | 价格日期 | 等级 | 状态 | 结果 |
|---|---|---|---|---:|---|---|---|---|
| 600519 | 贵州茅台 | stock | wait | 1435.26 | 2026-11-13 | B | complete | 通过 |
| 000858 | 五粮液 | stock | undetermined | 73.38 | 2026-11-13 | C | partial | 通过 |
| 600887 | 伊利股份 | stock | undetermined | 31.74 | 2026-11-13 | C | complete | 通过 |
| 603288 | 海天味业 | stock | avoid | 34.89 | 2026-11-13 | A | complete | 通过 |
| 000895 | 双汇发展 | stock | avoid | 24.67 | 2026-11-13 | A | complete | 通过 |
| 600436 | 片仔癀 | stock | undetermined | 130.4 | 2026-11-13 | C | partial | 通过 |
| 000333 | 美的集团 | stock | wait | 90.55 | 2026-11-13 | A | complete | 通过 |
| 000651 | 格力电器 | stock | undetermined | 39.96 | 2026-11-13 | A | partial | 通过 |
| 600660 | 福耀玻璃 | stock | undetermined | 56.86 | 2026-11-13 | C | complete | 通过 |
| 600031 | 三一重工 | stock | undetermined | 18.32 | 2026-11-13 | C | unsupported | 通过 |
| 600309 | 万华化学 | stock | undetermined | 68.09 | 2026-11-13 | C | insufficient_data | 通过 |
| 002415 | 海康威视 | stock | undetermined | 34.45 | 2026-11-13 | C | complete | 通过 |
| 300750 | 宁德时代 | stock | wait | 237.7 | 2026-11-13 | B | complete | 通过 |
| 002594 | 比亚迪 | stock | undetermined | 79.99 | 2026-11-13 | C | partial | 通过 |
| 600276 | 恒瑞医药 | stock | avoid | 48.07 | 2026-11-13 | B | complete | 通过 |
| 600900 | 长江电力 | stock | undetermined | 29.91 | 2026-11-13 | C | complete | 通过 |
| 601088 | 中国神华 | stock | undetermined | 50.0 | 2026-11-13 | C | unsupported | 通过 |
| 601006 | 大秦铁路 | stock | undetermined | 4.84 | 2026-11-13 | C | partial | 通过 |
| 600941 | 中国移动 | stock | avoid | 101.46 | 2026-11-13 | B | complete | 通过 |
| 601668 | 中国建筑 | stock | undetermined | 4.62 | 2026-11-13 | C | insufficient_data | 通过 |
| 601919 | 中远海控 | stock | undetermined | 17.03 | 2026-11-13 | C | unsupported | 通过 |
| 600019 | 宝钢股份 | stock | undetermined | 6.18 | 2026-11-13 | C | unsupported | 通过 |
| 601899 | 紫金矿业 | stock | undetermined | 26.38 | 2026-11-13 | C | unsupported | 通过 |
| 601857 | 中国石油 | stock | undetermined | 12.39 | 2026-11-13 | C | unsupported | 通过 |
| 000002 | 万科Ａ | stock | undetermined | 3.06 | 2026-11-13 | C | unsupported | 通过 |
| 601012 | 隆基绿能 | stock | undetermined | 11.7 | 2026-11-13 | C | unsupported | 通过 |
| 600036 | 招商银行 | bank | candidate_buy_point | 42.93 | 2026-11-13 | — | — | 通过 |
| 601398 | 工商银行 | bank | wait | 8.85 | 2026-11-13 | — | — | 通过 |
| 601318 | 中国平安 | stock | undetermined | 53.8 | 2026-11-13 | C | unsupported | 通过 |
| 600030 | 中信证券 | stock | undetermined | 25.0 | 2026-11-13 | C | unsupported | 通过 |

## 3. 三只指数 + 恒生科技

| 问题 | 意图 | 结论 | 行数 | 结果 |
|---|---|---|---:|---|
| 沪深300 现在什么位置 | index | undetermined | 13 | 通过 |
| 中证500 现在什么位置 | index | undetermined | 13 | 通过 |
| 中证1000 现在什么位置 | index | undetermined | 13 | 通过 |
| 恒生科技 | index | undetermined | 11 | 通过 |

## 4. 两家银行

| 问题 | 意图 | 结论 | 行数 | 结果 |
|---|---|---|---:|---|
| 招商银行怎么样 | bank | candidate_buy_point | 15 | 通过 |
| 工商银行怎么样 | bank | wait | 15 | 通过 |

## 5. 三只 ETF

| 问题 | 意图 | 结论 | 行数 | 结果 |
|---|---|---|---:|---|
| 510300 能买吗 | etf | undetermined | 9 | 通过 |
| 510500 能买吗 | etf | undetermined | 9 | 通过 |
| 512100 能买吗 | etf | undetermined | 9 | 通过 |

## 6. 输出扫描

- 禁用词：逐条扫描「建议买入 / 建议卖出 / 推荐买入 / 推荐卖出 / 强烈推荐 / 建议加仓 / 建议减仓」，0 命中。
- 日期与来源：每张卡片都有日期（行情/财务/规则/账本），并有来源或快照/账本/规则指路。
- 研究模型：指数、ETF、银行卡片均未出现「正式结论」字样；结论统一为「无法判断」。
- 结构：默认卡片 ≤ 25 行；第一行是结论；末行是「不构成投资建议」。

## 7. 限制

- 卡片里的「现在」指本次运行时的最新行情/序列；非交易时段取上一交易日收盘。
- 指数分位来自供应商历史序列，官方最新值不参与第三方历史排名。
- 银行估值未启用；银行卡片只列公开报表指标，不给买点。
