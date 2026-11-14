"""规则化意图识别（不使用大模型）。

输入：中文问题 + 已识别的标的（Target）。
输出：Intent（意图、池范围、所有制筛选、说明），供数据层与卡片层使用。

规则顺序：状态 → 历史 → 排行 → 是否在池 → 池名单 → 标的类型。
识别不了时返回 unrecognized，由调用方列出支持的问法。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from engine.ask.resolve import Target, board_only, overseas

INTENT_LABELS = {
    "stock": "单只个股",
    "bank": "银行",
    "index": "指数",
    "etf": "ETF",
    "pool_list": "池名单",
    "membership": "是否在池",
    "ranking": "排行",
    "history": "历史结论",
    "status": "数据与规则状态",
    "out_of_scope": "范围外",
    "ambiguous": "歧义",
    "unrecognized": "未识别",
}

SUPPORTED_QUESTIONS = [
    "单只个股：「茅台怎么样」「600519 贵不贵」「美的能买吗」",
    "银行：「招商银行怎么样」（银行池 11 家给候选买点/等待/回避；非银行池只给价值数字与无法判断）",
    "指数：「沪深300 现在什么位置」「恒生科技」",
    "ETF：「510300 能买吗」（产品资料 + 所跟踪指数的研究信号）",
    "池名单：「好公司池有哪些」「收息池里的国企」「银行池」",
    "是否在池：「格力在不在好公司池」「为什么比亚迪不是好公司」「比亚迪算不算好公司」",
    "排行：「好公司里谁离买点最近」「银行池里谁离买点最近」「现在有哪些候选买点」「推荐几只股票」「A股有哪些值得投资的公司」",
    "历史：「茅台以前的结论」",
    "状态：「数据是哪天的」「规则什么时候复核」",
]

_STATUS_PATTERNS = (
    r"数据.*哪(天|日)",
    r"数据日期",
    r"数据.*到.*(天|日)",
    r"更新.*(时间|日期|哪天)",
    r"规则.*复核",
    r"复核.*(时间|日期|哪天|日)",
    r"政策.*版本",
    r"什么时候复核",
)
_HISTORY_PATTERNS = (
    r"以前",
    r"历史",
    r"过去",
    r"上次",
    r"曾经",
    r"结论.*记录",
    r"记录.*结论",
    r"账本",
)
_RANKING_PATTERNS = (
    r"离买点",
    r"离.*门槛",
    r"排行",
    r"排名",
    r"候选买点",
    r"哪些候选",
    r"谁.*(最近|近|便宜)",
    r"买点.*(最近|排行|哪些)",
    r"有哪些买点",
    r"银行.*买点",
)
_MEMBERSHIP_PATTERNS = (
    r"在不在",
    r"是不是.*池",
    r"是否.*池",
    r"为什么.*不在",
    r"为什么.*没",
    r"属于.*池",
    r"在.*池.*吗",
    r"在.*池里吗",
    r"不是好公司",
    r"算不算",
    r"是不是好公司",
    r"是好公司吗",
    r"好公司吗",
    r"算好公司",
    r"好不好",
)
_PRICE_PATTERNS = (
    r"便宜",
    r"合适",
    r"划算",
    r"低估",
    r"值得买",
    r"能买",
    r"价格",
    r"离买点",
)
_RECOMMEND_PATTERNS = (
    r"值得投资",
    r"好股票",
    r"推荐.*股票",
    r"买什么股票",
    r"哪些股票.*(好|值得)",
    r"哪只股票",
)
_POOL_LIST_PATTERNS = (
    r"有哪些",
    r"名单",
    r"成员",
    r"包含.*(哪些|什么)",
    r"列出",
    r"池里",
    r"池.*(哪些|有什么)",
    r"池$",
)
_POOL_QUALITY = ("好公司池", "好公司", "质量池")
_POOL_INCOME = ("收息池", "稳定收息池", "收息")
_POOL_BANK = ("银行池", "银行里")


@dataclass(frozen=True)
class Intent:
    name: str
    target: Target | None = None
    pool: str | None = None  # quality / income / bank / all
    ownership: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.name,
            "intent_label": INTENT_LABELS.get(self.name, self.name),
            "target": self.target.to_dict() if self.target else None,
            "pool": self.pool,
            "ownership": self.ownership,
            "reason": self.reason,
        }


def _has(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _pool(text: str) -> str | None:
    if any(key in text for key in _POOL_BANK):
        return "bank"
    if any(key in text for key in _POOL_INCOME):
        return "income"
    if any(key in text for key in _POOL_QUALITY):
        return "quality"
    if "池" in text:
        return "all"
    return None


def _ownership(text: str) -> str | None:
    for key in ("央企", "地方国企", "国企", "民企", "外资"):
        if key in text:
            return key
    return None


def parse(question: str, target: Target | None) -> Intent:
    text = unicodedata.normalize("NFKC", question or "").strip()
    if not text:
        return Intent("unrecognized", reason="问题为空")

    # 池名 + 价格类词：优先走该池排行，即使句子里混进了股票名（如「值得买」）
    pool_with_price = _pool(text)
    if pool_with_price is not None and _has(_PRICE_PATTERNS, text):
        return Intent("ranking", pool=pool_with_price)

    # 标的已识别：先看是否在问状态/历史/排行/是否在池
    if target is not None and target.ambiguous:
        return Intent("ambiguous", target=target, reason="同一名称或代码对应多个标的")
    if target is not None:
        scope = target.scope or {}
        if scope.get("status") == "out_of_scope":
            return Intent(
                "out_of_scope",
                target=target,
                reason=scope.get("detail") or "该标的不在已接入的研究范围内",
            )
        if _has(_STATUS_PATTERNS, text):
            return Intent("status", target=target)
        if _has(_HISTORY_PATTERNS, text):
            return Intent("history", target=target)
        if _has(_RANKING_PATTERNS, text):
            return Intent("ranking", target=target)
        if _has(_MEMBERSHIP_PATTERNS, text) or (
            _pool(text) is not None and re.search(r"[在属]", text) and "吗" in text
        ):
            return Intent("membership", target=target, pool=_pool(text) or "quality")
        if target.kind == "index":
            return Intent("index", target=target)
        if target.kind == "etf":
            return Intent("etf", target=target)
        return Intent("stock", target=target)

    # 无标的：检查状态/排行/池名单/范围外/歧义
    if _has(_STATUS_PATTERNS, text):
        return Intent("status")
    if _has(_RANKING_PATTERNS, text) or _has(_RECOMMEND_PATTERNS, text):
        return Intent("ranking", pool=_pool(text) or "all")
    if _pool(text) is not None and _has(_PRICE_PATTERNS, text):
        return Intent("ranking", pool=_pool(text))
    if _pool(text) is not None and _has(_POOL_LIST_PATTERNS, text):
        return Intent("pool_list", pool=_pool(text), ownership=_ownership(text))
    if any(marker in text for marker in ("创业板50", "创业板100", "创业板200", "创业板300", "创业板综", "创业板R")):
        return Intent(
            "out_of_scope",
            reason="该创业板指数不在已接入范围；创业板指（399006）已于 2026-11-13 移出（官方历史估值不可得）。",
        )
    if overseas(text):
        return Intent("out_of_scope", reason="境外市场个股/基金：本版只覆盖境内 A 股、境内宽基指数与境内普通被动 ETF")
    board = board_only(text)
    if board is not None:
        if board == "创业板":
            return Intent(
                "ambiguous",
                reason=(
                    "「创业板」是交易板块，不是指数；创业板指（399006）已移出：官方历史估值不可得"
                    "（2026-11-13 股东决定）。创业板50 等其他创业板指数也在范围外。"
                ),
            )
        return Intent("out_of_scope", reason=f"「{board}」是交易板块，不是本版接入的宽基指数")
    if _has(_HISTORY_PATTERNS, text):
        return Intent("unrecognized", reason="想查历史结论时，请带上标的名称或代码")
    if _pool(text) is not None:
        return Intent("pool_list", pool=_pool(text), ownership=_ownership(text))
    return Intent(
        "unrecognized",
        reason="没有识别出标的或问法；可以说「茅台怎么样」「好公司池有哪些」「数据是哪天的」等",
    )


__all__ = ["INTENT_LABELS", "SUPPORTED_QUESTIONS", "Intent", "parse"]
