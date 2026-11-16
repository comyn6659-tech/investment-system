"""认不出名字时的提示：从问题里取出公司名，给出「没找到叫『X』的上市公司」的固定说法。

施工单第六阶段 §一.4：认不出名字时，提示改为
「没找到叫『X』的上市公司，请用证券简称或 6 位代码」。

只做文本提取，不猜股票、不模糊匹配、不改识别规则。
"""

from __future__ import annotations

import re
import unicodedata

QUESTION_TAILS = (
    "值不值得买",
    "值得买吗",
    "可以买吗",
    "怎么样",
    "贵不贵",
    "能买吗",
    "好不好",
    "值多少钱",
    "便宜吗",
    "怎么走",
    "买不买",
    "如何",
    "怎样",
    "贵吗",
    "能买",
    "买吗",
)

LEADING_FILLERS = (
    "帮我看看",
    "帮我分析",
    "帮我查一下",
    "请问一下",
    "麻烦问一下",
    "分析一下",
    "看一下",
    "查一下",
    "我想问",
    "想问问",
    "我想知道",
    "请问",
    "看看",
    "查查",
)

TRAILING_WORDS = ("这只股票", "这只", "股票", "公司", "个股")

GENERIC_NAMES = {
    "数据",
    "规则",
    "政策",
    "现在",
    "今天",
    "明天",
    "银行",
    "银行池",
    "好公司",
    "好公司池",
    "收息池",
    "稳定收息池",
    "股票",
    "基金",
    "指数",
    "大盘",
    "市场",
    "A股",
    "板块",
    "行业",
    "系统",
    "账本",
    "结论",
    "买点",
}

_NAME_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,12})$")


def _strip_prefix(text: str) -> str:
    current = text
    changed = True
    while changed and current:
        changed = False
        for filler in LEADING_FILLERS:
            if current.startswith(filler) and len(current) > len(filler) + 1:
                current = current[len(filler) :]
                changed = True
    return current


def _strip_suffix(text: str) -> str:
    current = text
    changed = True
    while changed and current:
        changed = False
        for word in TRAILING_WORDS:
            if current.endswith(word) and len(current) > len(word) + 1:
                current = current[: -len(word)]
                changed = True
    return current


def unknown_subject(question: str) -> str | None:
    """问题像在问一家公司、但名字没被识别出来时，返回那个名字；否则返回 None。"""
    text = unicodedata.normalize("NFKC", question or "").strip()
    if not text:
        return None
    for tail in QUESTION_TAILS:
        index = text.find(tail)
        if index <= 0:
            continue
        head = text[:index].strip()
        match = _NAME_RE.search(head)
        if match is None:
            continue
        name = _strip_prefix(_strip_suffix(match.group(1)))
        if not (2 <= len(name) <= 10):
            continue
        if name.upper() in {item.upper() for item in GENERIC_NAMES}:
            continue
        return name
    return None


__all__ = ["GENERIC_NAMES", "QUESTION_TAILS", "unknown_subject"]
