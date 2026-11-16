"""公告主体判定：与好公司池 v1.2 / batch4f K3 相同的口径。

只做规则判定，不调用模型；人工核对结果（`data/policy-inputs/governance-review-v1.json`）
按公告 art_code 覆盖自动判定，旧公告因此与建池时逐条一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_PATH = ROOT / "data" / "policy-inputs" / "governance-review-v1.json"

COUNTED_ROLES = frozenset(
    {"company", "controller", "actual_controller", "concert_party", "director_officer"}
)
ROLE_LABELS = {
    "company": "公司本身",
    "controller": "控股股东",
    "actual_controller": "实际控制人",
    "concert_party": "一致行动人",
    "director_officer": "时任董监高",
    "other_shareholder": "其他股东（非控股、非一致行动人）",
    "subsidiary": "子公司/分公司",
    "other_entity": "其他主体",
    "unknown": "主体身份不明",
}
INFO_PATTERNS = (
    "信息披露违法违规",
    "涉嫌信息披露违法",
    "信息披露违法",
    "虚假记载",
    "重大遗漏",
    "未按规定披露",
    "未及时披露",
    "未准确披露",
    "信息披露不准确",
    "未真实披露",
    "违规披露",
)
INFO_BOILERPLATE = (
    "保证信息披露的内容真实",
    "保证公告内容的真实",
    "履行信息披露义务",
    "指定信息披露媒体",
    "信息披露媒体为",
    "及时履行信息披露义务",
    "按照有关法律法规的规定和要求，及时履行信息披露义务",
    "没有虚假记载",
    "不存在虚假记载",
    "误导性陈述或重大遗漏",
    "真实、准确、完整",
)
DUTY_PATTERN = re.compile(r"董事|监事|高级管理人员|高管|总经理|副总经理|董事会秘书|财务总监")


def load_review(path: Path | None = None) -> dict[str, Any]:
    node = Path(path or REVIEW_PATH)
    if not node.exists():
        return {}
    payload = json.loads(node.read_text(encoding="utf-8"))
    return payload.get("penalty_events") or {}


def strip_tags(value: str | None) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def info_disclosure(blob: str) -> bool:
    text = re.sub(r"\s+", "", blob)
    for phrase in INFO_BOILERPLATE:
        text = text.replace(re.sub(r"\s+", "", phrase), "")
    return any(pattern in text for pattern in INFO_PATTERNS)


def penalty_role(title: str, company_name: str) -> tuple[str, str | None]:
    if "实际控制人" in title:
        return "actual_controller", "实际控制人（公告所述）"
    if DUTY_PATTERN.search(title):
        match = DUTY_PATTERN.search(title)
        return "director_officer", f"时任{match.group(0)}（公告所述）" if match else "时任董监高（公告所述）"
    if "间接控股股东" in title:
        return "other_entity", "间接控股股东"
    if "控股股东" in title:
        return "controller", "控股股东"
    if "股东" in title:
        match = re.search(r"股东[^，。；)）]{0,20}", title)
        return "other_shareholder", match.group(0) if match else "股东（公告所述）"
    if "子公司" in title or "分公司" in title:
        return "subsidiary", "子公司/分公司"
    if title.strip():
        return "company", company_name
    return "unknown", None


def regulator(blob: str, event_type: str) -> bool:
    if event_type in ("立案", "行政处罚"):
        return any(word in blob for word in ("中国证券监督管理委员会", "证监会", "证监局"))
    return any(
        word in blob
        for word in ("上交所", "上海证券交易所", "深交所", "深圳证券交易所", "证券交易所")
    )


def extract_doc_no(text: str) -> str:
    match = re.search(
        r"〔\d{4}〕\s*\d+\s*号|\[\d{4}\]\s*\d+\s*号|（\d{4}）\s*\d+\s*号|\(\d{4}\)\s*\d+\s*号",
        text,
    )
    return match.group(0) if match else ""


def reason_snippet(text: str, words: tuple[str, ...] = ("信息披露", "虚假记载", "重大遗漏", "立案"), width: int = 140) -> str:
    compact = re.sub(r"\s+", " ", text)
    for word in words:
        index = compact.find(word)
        if index >= 0:
            start = max(0, index - width // 2)
            return compact[start : start + width]
    return compact[:width] if compact else ""


def classify_event(
    *,
    code: str,
    company_name: str,
    event_type: str,
    title: str,
    date: str,
    url: str,
    content: str = "",
    art_code: str = "",
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """返回与 `build_governance_data.py` 相同字段的判定结果。"""
    blob = f"{title} {content}"
    override = (review or {}).get(art_code) or {}
    if override:
        role = str(override.get("role") or "unknown")
        subject = override.get("subject")
        info = bool(override.get("info_disclosure"))
        basis = override.get("basis")
        public_censure = bool(override.get("public_censure", "公开谴责" in blob))
        is_regulator = override.get("regulator") if "regulator" in override else regulator(blob, event_type)
    else:
        role, subject = penalty_role(title, company_name)
        info = info_disclosure(blob)
        basis = "标题/正文关键词：" + ("信息披露类" if info else "非信息披露类")
        public_censure = "公开谴责" in blob
        is_regulator = regulator(blob, event_type)
    if subject is None:
        subject = company_name if role == "company" else ""
    final = public_censure if event_type in ("公开谴责", "纪律处分") else True
    counted = bool(info and is_regulator and final and role in COUNTED_ROLES)
    unknown = bool(info and is_regulator and final and role == "unknown")
    doc_no = extract_doc_no(blob)
    return {
        "date": date,
        "event_type": event_type,
        "title": strip_tags(title),
        "subject": str(subject or ""),
        "role": role,
        "counted": counted,
        "subject_unknown": unknown,
        "info_disclosure": bool(info),
        "regulator": bool(is_regulator),
        "doc_no": doc_no,
        "url": url,
        "evidence": f"{strip_tags(title)}（{basis or '公告所述'}）",
    }


__all__ = [
    "COUNTED_ROLES",
    "ROLE_LABELS",
    "classify_event",
    "extract_doc_no",
    "info_disclosure",
    "load_review",
    "penalty_role",
    "reason_snippet",
    "regulator",
    "strip_tags",
]
