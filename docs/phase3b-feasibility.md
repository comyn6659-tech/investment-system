# 策略数据可行性调查（第三阶段第二批 B）

生成时间：2026-11-11T23:47:14+08:00｜生成脚本：`tools/probe_strategy_data.py`

范围：只用免费、无需账号的来源（已安装 akshare 版本中与需求明确对应的接口）。
不购买、不注册、不爬登录页。**结论只针对下列已测接口与参数**，
不代表「所有免费来源都没有」。

## 1. 结论

| 编号 | 需求 | 结论 | 说明 |
|---|---|---|---|
| D1 | ≥5 年、周度或更高频、同口径 PE/PB | **部分** | 日频长历史已可取到 PE（d1_csindex_hist_pe_300、d1_csindex_hist_pe_905、d1_csindex_hist_pe_852）；另一项仍只有月度采样，原周度策略所需的双指标同频历史未齐备。 |
| D2 | 公布时刻或可复现的发布规律 | **未解锁** | 所有已测返回字段里都没有公布时刻或发布时间戳，因此无法在不额外假设的前提下确定信号在当时的可得时点。 |
| D3 | 全收益指数或可重建总收益的分红/再投资数据 | **已解锁** | 已测来源中存在官方全收益指数日频历史（d3_csindex_tr_300 2010-01-01~2026-11-10、d3_csindex_tr_905 2010-01-01~2026-11-10、d3_csindex_tr_852 2010-01-01~2026-11-10），可直接作为全收益口径使用；仍需注意它来自官方文件、与价格指数分属不同序列。 |
| D4 | 三只 ETF 历史日线价格与历史单位净值 | **部分** | 已实测到价格 3/3、净值 0/3 满足 ≥5 年日频（有的来自早前一次成功调用）；其余 6 条本次调用失败：d4_etf_price_510300、d4_etf_price_510500、d4_etf_price_512100、d4_etf_nav_510300、d4_etf_nav_510500、d4_etf_nav_512100。失败原因是接口限流/超时，属于本次调用环境问题，不是「数据不存在」，需择时重测后才能定论。 |

## 1.1 本次调用环境

- 观察到限流/超时的主机：push2.eastmoney.com、push2his.eastmoney.com、api.fund.eastmoney.com
- 说明：东方财富系列接口在本次调查期间返回限流或读超时（首次限流提示约需 5 分钟，反复触发可能 40 分钟以上）。失败记录属于本次调用环境问题，**不代表数据不存在**。同一批接口在 2026-11-11T16:53 的采集任务中成功：data/snapshots/ 的三只 ETF 快照即由 fund_etf_hist_em 与 fund_etf_fund_info_em 构建。
- 重测策略：每条候选单次调用；失败后按 probe_id 重测，重测失败不会抹掉早前的成功记录（见 previous_success）。

## 2. 逐条调用记录

| probe | 需求 | 接口 | 参数 | 状态 | 行数 | 日期范围 | 频率 | 返回字段 | 错误 |
|---|---|---|---|---|---|---|---|---|---|
| `d1_lg_pe_300` | D1 | `stock_index_pe_lg` | symbol=沪深300 | ok | 260 | 2005-04-29 → 2026-11-11 | monthly | 日期、指数、等权静态市盈率、静态市盈率、静态市盈率中位数、等权滚动市盈率、滚动市盈率、滚动市盈率中位数 | — |
| `d1_lg_pb_300` | D1 | `stock_index_pb_lg` | symbol=沪深300 | ok | 260 | 2005-04-29 → 2026-11-11 | monthly | 日期、指数、市净率、等权市净率、市净率中位数 | — |
| `d1_csindex_value_300` | D1 | `stock_zh_index_value_csindex` | symbol=000300 | ok | 20 | 2026-10-15 → 2026-11-11 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、市盈率1、市盈率2… | — |
| `d1_csindex_hist_pe_300` | D1 | `stock_zh_index_hist_csindex` | symbol=000300、start_date=20100101、end_date=20261110 | ok | 4092 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d1_csindex_hist_pe_905` | D1 | `stock_zh_index_hist_csindex` | symbol=000905、start_date=20100101、end_date=20261110 | ok | 4093 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d1_csindex_hist_pe_852` | D1 | `stock_zh_index_hist_csindex` | symbol=000852、start_date=20100101、end_date=20261110 | ok | 4093 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d1_market_pe_lg` | D1 | `stock_market_pe_lg` | symbol=上证 | ok | 336 | 1999-01-29 → 2026-11-11 | monthly | 日期、指数、平均市盈率 | — |
| `d2_csindex_value_fields` | D2 | `stock_zh_index_value_csindex` | symbol=000300 | ok | 20 | 2026-10-15 → 2026-11-11 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、市盈率1、市盈率2… | — |
| `d2_csindex_hist_fields` | D2 | `stock_zh_index_hist_csindex` | symbol=000300、start_date=20100101、end_date=20261110 | ok | 4092 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d3_tr_shH00300` | D3 | `stock_zh_index_daily_em` | symbol=shH00300 | error | — | — | — | — | Push2RateLimitError: 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口 |
| `d3_tr_H00300` | D3 | `stock_zh_index_daily_em` | symbol=H00300 | empty | 0 | — | — | — | — |
| `d3_csindex_tr_300` | D3 | `stock_zh_index_hist_csindex` | symbol=H00300、start_date=20100101、end_date=20261110 | ok | 4093 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d3_csindex_tr_905` | D3 | `stock_zh_index_hist_csindex` | symbol=H00905、start_date=20100101、end_date=20261110 | ok | 4093 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d3_csindex_tr_852` | D3 | `stock_zh_index_hist_csindex` | symbol=H00852、start_date=20100101、end_date=20261110 | ok | 4093 | 2010-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |
| `d3_csindex_yield_300` | D3 | `stock_zh_index_value_csindex` | symbol=000300 | ok | 20 | 2026-10-15 → 2026-11-11 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、市盈率1、市盈率2… | — |
| `d4_etf_price_510300` | D4 | `fund_etf_hist_em` | symbol=510300、period=daily、adjust= | error | — | — | — | — | Push2RateLimitError: 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口 |
| `d4_etf_price_510500` | D4 | `fund_etf_hist_em` | symbol=510500、period=daily、adjust= | error | — | — | — | — | Push2RateLimitError: 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口 |
| `d4_etf_price_512100` | D4 | `fund_etf_hist_em` | symbol=512100、period=daily、adjust= | error | — | — | — | — | Push2RateLimitError: 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口 |
| `d4_etf_price_sina_510300` | D4 | `fund_etf_hist_sina` | symbol=sh510300 | ok | 3513 | 2012-05-28 → 2026-11-11 | daily | date、open、high、low、close、volume、amount、postVol… | — |
| `d4_etf_price_sina_510500` | D4 | `fund_etf_hist_sina` | symbol=sh510500 | ok | 3316 | 2013-03-15 → 2026-11-11 | daily | date、open、high、low、close、volume、amount、postVol… | — |
| `d4_etf_price_sina_512100` | D4 | `fund_etf_hist_sina` | symbol=sh512100 | ok | 2432 | 2016-11-04 → 2026-11-11 | daily | date、open、high、low、close、volume、amount、postVol… | — |
| `d4_etf_nav_510300` | D4 | `fund_etf_fund_info_em` | fund=510300、start_date=20100101、end_date=20261110 | error | — | — | — | — | ReadTimeout: HTTPSConnectionPool(host='api.fund.eastmoney.com', port=443): Read timed out. (read timeout=10) |
| `d4_etf_nav_510500` | D4 | `fund_etf_fund_info_em` | fund=510500、start_date=20100101、end_date=20261110 | error | — | — | — | — | ConnectTimeout: HTTPSConnectionPool(host='api.fund.eastmoney.com', port=443): Max retries exceeded with url: /f10/lsjz?f |
| `d4_etf_nav_512100` | D4 | `fund_etf_fund_info_em` | fund=512100、start_date=20100101、end_date=20261110 | error | — | — | — | — | ReadTimeout: HTTPSConnectionPool(host='api.fund.eastmoney.com', port=443): Read timed out. (read timeout=10) |
| `d3_tr_csiH00300` | D3 | `stock_zh_index_daily_em` | symbol=csiH00300 | error | — | — | — | — | Push2RateLimitError: 东方财富 push2 行情服务器断开了连接，通常是短时间内请求过多，当前 IP 被临时限流。首次限流一般约 5 分钟后恢复，反复触发后可能需要 40 分钟以上；等待期间不要重复调用东方财富的行情接口 |
| `d3_csindex_hist_H00300` | D3 | `stock_zh_index_hist_csindex` | symbol=H00300、start_date=20190101、end_date=20261110 | ok | 1905 | 2019-01-01 → 2026-11-10 | daily | 日期、指数代码、指数中文全称、指数中文简称、指数英文全称、指数英文简称、开盘、最高… | — |

## 3. 对本批结论的影响

- 原设计的**周度** A/B 候选策略仍无法做可执行回测：D1 只拿到月度、D2 拿不到公布时刻；
- 本批因此只做月度的事后诊断（见 [phase3b-diagnostics.md](phase3b-diagnostics.md)），
  不做状态机、不做模拟持仓、不做回测；
- D4 已解锁的部分可以支持「历史价格与净值序列」，但仍缺净值披露时刻与交易状态，
  因此不能据此断言当日盘中可成交。

## 4. 未做的事

- 没有遍历所有供应商，也没有尝试付费或需要登录的来源；
- 没有为了让 D1 通过而把月度数据插值成周度；
- 没有为了让 D3 通过而用价格指数冒充全收益指数。

