"""类型先行：用已批准的三池名单给个股与银行标注类型（不新增任何判定规则）。

只读 `data/*-pool/report.json` 的 `in_pool` 名单（决策③ 已批准），不重新计算条件、
不改阈值，也不参与估值与结论；结论仍由已批准政策与固定快照重算。

五种类型（施工单第六阶段 §一.1）：

| 类型 | 条件 | 一句话含义 |
|---|---|---|
| 好公司型 | 在好公司池 | 过去十年持续高回报，有护城河的财务脚印（系统不能验证护城河本身） |
| 好公司型＋收息型 | 同时在两池 | 同上，并且利润稳、分红稳 |
| 收息型 | 只在收息池 | 利润稳、分红稳；没有持续高回报，买点主要来自「便宜＋分红」 |
| 银行池 | 在银行池 | 按银行专用标准 |
| 池外 | 不在任何池 | 未通过或未参与筛选；结论只按估值规则，质量未经筛选 |

银行优先按银行池标注（银行专用估值与银行池标准单独一套）。
"""

from __future__ import annotations

from typing import Mapping

TYPE_QUALITY = "好公司型"
TYPE_QUALITY_INCOME = "好公司型＋收息型"
TYPE_INCOME = "收息型"
TYPE_BANK = "银行池"
TYPE_OUTSIDE = "池外"

# 展示顺序＝施工单表格顺序；排行按此顺序分组。
TYPE_ORDER = (TYPE_QUALITY, TYPE_QUALITY_INCOME, TYPE_INCOME, TYPE_BANK, TYPE_OUTSIDE)

TYPE_MEANINGS = {
    TYPE_QUALITY: "过去十年持续高回报，有护城河的财务脚印（系统不能验证护城河本身）",
    TYPE_QUALITY_INCOME: (
        "过去十年持续高回报，有护城河的财务脚印（系统不能验证护城河本身），并且利润稳、分红稳"
    ),
    TYPE_INCOME: "利润稳、分红稳；没有持续高回报，买点主要来自「便宜＋分红」",
    TYPE_BANK: "按银行专用标准",
    TYPE_OUTSIDE: "不在任何池：未通过或未参与筛选；结论只按估值规则，质量未经筛选",
}

# 卡片固定提示（施工单 §一.2）。
MOAT_HINT = "系统不判断护城河是否仍然存在，请自行研究其生意"


def classify(code: str, pool_sets: Mapping[str, set[str]]) -> str:
    """按已批准名单判定类型；`pool_sets` 只含 quality / income / bank 三个集合。"""
    key = str(code).zfill(6)
    bank = pool_sets.get("bank") or set()
    quality = pool_sets.get("quality") or set()
    income = pool_sets.get("income") or set()
    if key in bank:
        return TYPE_BANK
    in_quality = key in quality
    in_income = key in income
    if in_quality and in_income:
        return TYPE_QUALITY_INCOME
    if in_quality:
        return TYPE_QUALITY
    if in_income:
        return TYPE_INCOME
    return TYPE_OUTSIDE


def type_line(company_type: str) -> str:
    """卡片第一行：类型 + 一句话含义（含义取自施工单，不改写）。"""
    meaning = TYPE_MEANINGS.get(company_type, TYPE_MEANINGS[TYPE_OUTSIDE])
    return f"类型：{company_type}｜{meaning}"


def hint_line() -> str:
    """卡片固定提示行。"""
    return f"提示：{MOAT_HINT}。"


def type_sort_key(company_type: str) -> int:
    try:
        return TYPE_ORDER.index(company_type)
    except ValueError:
        return len(TYPE_ORDER)


def ordered_types(types: list[str] | tuple[str, ...]) -> list[str]:
    """按施工单顺序排列出现的类型（去重）。"""
    seen = {str(item) for item in types}
    return [item for item in TYPE_ORDER if item in seen]


__all__ = [
    "MOAT_HINT",
    "TYPE_BANK",
    "TYPE_INCOME",
    "TYPE_MEANINGS",
    "TYPE_ORDER",
    "TYPE_OUTSIDE",
    "TYPE_QUALITY",
    "TYPE_QUALITY_INCOME",
    "classify",
    "hint_line",
    "ordered_types",
    "type_line",
    "type_sort_key",
]
