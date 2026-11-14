# 30 只测试股：批量输入路径回归

- 政策：`equity-policy-v1.1`（pol-6d1a5387780fe6a2）
- 批量快照：`qps-f140a9790e4015ea`
- 通过：30 / 30；最大基准价值相对差：0.0000%

回归时批量路径使用原快照的现价/价格日期/总股本，只替换财务与分红输入来源，以隔离字段差异。

| 代码 | 名称 | 状态 | 等级 | 结论 | 基准价值（原 / 批） | 相对差 | 通过 |
|---|---|---|---|---|---:|---:|---|
| 600519 | 贵州茅台 | complete / complete | B / B | wait / wait | 1144.6630 / 1144.6630 | 0.0000% | ✓ |
| 000858 | 五 粮 液 | partial / partial | C / C | undetermined / undetermined | 62.5987 / 62.5987 | 0.0000% | ✓ |
| 600887 | 伊利股份 | complete / complete | C / C | undetermined / undetermined | 15.9132 / 15.9132 | 0.0000% | ✓ |
| 603288 | 海天味业 | complete / complete | A / A | avoid / avoid | 17.4084 / 17.4084 | 0.0000% | ✓ |
| 000895 | 双汇发展 | complete / complete | A / A | avoid / avoid | 14.9501 / 14.9501 | 0.0000% | ✓ |
| 600436 | 片仔癀 | partial / partial | C / C | undetermined / undetermined | 28.9502 / 28.9502 | 0.0000% | ✓ |
| 000333 | 美的集团 | complete / complete | A / A | wait / wait | 76.9853 / 76.9853 | 0.0000% | ✓ |
| 000651 | 格力电器 | partial / partial | A / A | undetermined / undetermined | 41.2267 / 41.2267 | 0.0000% | ✓ |
| 600660 | 福耀玻璃 | complete / complete | C / C | undetermined / undetermined | 52.4749 / 52.4749 | 0.0000% | ✓ |
| 600031 | 三一重工 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 600309 | 万华化学 | partial / partial | C / C | undetermined / undetermined | 60.4616 / 60.4616 | 0.0000% | ✓ |
| 002415 | 海康威视 | complete / complete | C / C | undetermined / undetermined | 26.8392 / 26.8392 | 0.0000% | ✓ |
| 300750 | 宁德时代 | complete / complete | B / B | wait / wait | 242.0450 / 242.0450 | 0.0000% | ✓ |
| 002594 | 比亚迪 | partial / partial | C / C | undetermined / undetermined | 23.7673 / 23.7673 | 0.0000% | ✓ |
| 600276 | 恒瑞医药 | complete / complete | B / B | avoid / avoid | 19.4523 / 19.4523 | 0.0000% | ✓ |
| 600900 | 长江电力 | complete / complete | C / C | undetermined / undetermined | 17.5985 / 17.5985 | 0.0000% | ✓ |
| 601088 | 中国神华 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601006 | 大秦铁路 | partial / partial | C / C | undetermined / undetermined | 3.5395 / 3.5395 | 0.0000% | ✓ |
| 600941 | 中国移动 | complete / complete | B / B | avoid / avoid | 76.8180 / 76.8180 | 0.0000% | ✓ |
| 601668 | 中国建筑 | partial / partial | C / C | undetermined / undetermined | 20.1823 / 20.1823 | 0.0000% | ✓ |
| 601919 | 中远海控 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 600019 | 宝钢股份 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601899 | 紫金矿业 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601857 | 中国石油 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 000002 | 万 科Ａ | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601012 | 隆基绿能 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 600036 | 招商银行 | insufficient_data / insufficient_data | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601398 | 工商银行 | insufficient_data / insufficient_data | C / C | undetermined / undetermined | — / — | — | ✓ |
| 601318 | 中国平安 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
| 600030 | 中信证券 | unsupported / unsupported | C / C | undetermined / undetermined | — / — | — | ✓ |
