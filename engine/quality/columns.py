"""全市场表格的列清单（只保留评分需要的字段，避免把整张原始报表入库）。"""

BALANCE_COLUMNS = [
    "SECUCODE",
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "NOTICE_DATE",
    "REPORT_DATE",
    "TOTAL_PARENT_EQUITY",
    "TOTAL_EQUITY",
    "MONETARYFUNDS",
    "SHORT_LOAN",
    "NONCURRENT_LIAB_1YEAR",
    "LONG_LOAN",
    "BOND_PAYABLE",
    "LEASE_LIAB",
    "TOTAL_ASSETS",
]

INCOME_COLUMNS = [
    "SECUCODE",
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "NOTICE_DATE",
    "REPORT_DATE",
    "PARENT_NETPROFIT",
    "TOTAL_PROFIT",
    "FE_INTEREST_EXPENSE",
    "FINANCE_EXPENSE",
    "OPERATE_PROFIT",
    "TOTAL_OPERATE_INCOME",
]

CASHFLOW_COLUMNS = [
    "SECUCODE",
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "NOTICE_DATE",
    "REPORT_DATE",
    "NETCASH_OPERATE",
    "CONSTRUCT_LONG_ASSET",
    "FA_IR_DEPR",
    "OILGAS_BIOLOGY_DEPR",
    "IA_AMORTIZE",
    "LPE_AMORTIZE",
    "USERIGHT_ASSET_AMORTIZE",
]

DIVIDEND_COLUMNS = [
    "SECUCODE",
    "SECURITY_CODE",
    "SECURITY_NAME_ABBR",
    "REPORT_DATE",
    "PRETAX_BONUS_RMB",
    "TOTAL_SHARES",
    "ASSIGN_PROGRESS",
    "IMPL_PLAN_PROFILE",
    "NOTICE_DATE",
    "PLAN_NOTICE_DATE",
]

UNIVERSE_COLUMNS = [
    "code",
    "name",
    "exchange",
    "board",
    "listing_date",
    "industry_csrc",
]

PLEDGE_COLUMNS = ["code", "name", "trade_date", "pledge_ratio", "pledge_shares", "pledge_deals", "industry"]

INDUSTRY_COLUMNS = ["code", "industry", "board_name"]
