"""公告主体判定：与好公司池 v1.2 / batch4f K3 相同的口径。

只做规则判定，不调用模型；人工核对结果（`data/policy-inputs/governance-review-v1.json`）
和已提交的建池判定（按公告 art_code）优先于自动判定，旧公告因此与建池时逐条一致。

R1 起，监测与建池共用本模块的 `classify_event`；样板句用正则去除，内幕交易、短线交易、
环保、安全生产、金融监管等非信息披露事项显式判「不计入」。正文取不到或事项无法确定时
记 `pending`（本周未完成），不计入、也不算通过。
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

# 样板句变体："不存在任何虚假记载、误导性陈述或者重大遗漏"等。先整句去掉，再找违规词，
# 否则样板句里的「虚假记载/重大遗漏」会被误当成违规事实。
INFO_BOILERPLATE_PATTERNS = (
    re.compile(
        r"(?:不(?:存在|涉及)|没有|未发现)(?:任何)?虚假记载"
        r"(?:[、，,;；]?\s*误导性陈述)?"
        r"(?:[、，,;；]?\s*(?:或者|或|及)?\s*重大遗漏)?"
    ),
    re.compile(r"(?:保证|承诺)[^。；;]{0,30}(?:真实|准确|完整)[^。；;]{0,80}"),
    re.compile(r"(?:及时|严格|按照[^。；;]{0,40})?履行信息披露义务"),
    re.compile(r"指定(?:的)?信息披露媒体[^。；;]{0,100}"),
    re.compile(r"信息披露媒体(?:为|是)[^。；;]{0,100}"),
    re.compile(r"真实[、，,]?准确[、，,]?完整"),
    re.compile(r"误导性陈述(?:或者|或|及|、)?重大遗漏"),
)
# 兼容旧调用方（测试/文档）的元组；实际去除逻辑见 `strip_boilerplate`。
INFO_BOILERPLATE = tuple(pattern.pattern for pattern in INFO_BOILERPLATE_PATTERNS)

# matter_scope：仅信息披露类事项；以下事项显式判「不计入」。
EXCLUDED_MATTERS = (
    ("内幕交易", re.compile(r"内幕交易")),
    ("短线交易", re.compile(r"短线交易")),
    ("环保", re.compile(r"(?:环保|环境保护)(?:处罚|违法|违规|事故|问题|督查)")),
    ("安全生产", re.compile(r"安全生产(?:事故|处罚|违法|违规)")),
    (
        "金融监管",
        re.compile(r"(?:金融监管|银保监|人民银行|国家金融监督管理总局)[^。；;]{0,40}(?:处罚|罚款|监管措施)"),
    ),
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


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def strip_boilerplate(blob: str) -> str:
    """去掉信息披露样板句（含「任何」「或者」等变体）。"""
    text = _compact(strip_tags(blob))
    for pattern in INFO_BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)
    return text


def info_disclosure(blob: str) -> bool:
    text = strip_boilerplate(blob)
    return any(pattern in text for pattern in INFO_PATTERNS)


def excluded_matter(blob: str) -> tuple[str, str, str] | None:
    """返回 (事项, 说明, 不计入理由)；不是排除事项时返回 None。"""
    text = strip_boilerplate(blob)
    for matter, pattern in EXCLUDED_MATTERS:
        if not pattern.search(text):
            continue
        if matter == "内幕交易":
            if re.search(r"其他公司|他公司|不涉及本公司股票", text):
                detail = "他公司股票"
            elif "本公司" in text:
                detail = "本公司股票"
            else:
                detail = ""
            reason = f"内幕交易（{detail}），非信息披露类" if detail else "内幕交易，非信息披露类"
            return matter, detail, reason
        return matter, "", f"{matter}，非信息披露类"
    return None


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


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "是")
    return bool(value)


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
    committed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """返回与 `build_governance_data.py` 相同字段的判定结果。

    `review` 是人工核对表（按 art_code）；`committed` 是已提交建池快照里的同一公告行。
    两者优先于自动判定，保证监测与建池逐条一致。
    """
    blob = f"{title} {content}"
    clean = strip_boilerplate(blob)
    review_override = (review or {}).get(art_code) or {}
    prior = committed or {}
    title_role, title_subject = penalty_role(title, company_name)
    prior_role = str(prior.get("subject_role") or prior.get("role") or "")
    role = str(review_override.get("role") or prior_role or title_role or "unknown")
    subject = review_override.get("subject") or prior.get("subject") or title_subject
    if subject is None:
        subject = company_name if role == "company" else ""
    excluded = excluded_matter(blob)

    decision_source = "rule"
    pending = False
    if review_override:
        decision_source = "review"
        info = _as_bool(review_override.get("info_disclosure"))
        basis = str(review_override.get("basis") or "人工核对")
        public_censure = _as_bool(review_override.get("public_censure", "公开谴责" in blob))
        is_regulator = (
            _as_bool(review_override["regulator"])
            if "regulator" in review_override
            else regulator(blob, event_type)
        )
        counted_override = _as_bool(review_override.get("counted", False)) if "counted" in review_override else None
        unknown_override = _as_bool(review_override.get("subject_unknown", False)) if "subject_unknown" in review_override else None
    elif prior:
        decision_source = "snapshot"
        info = _as_bool(prior.get("info_disclosure"))
        basis = f"沿用建池快照判定（{prior.get('subject_basis') or '已提交'}）"
        public_censure = _as_bool(prior.get("public_censure", "公开谴责" in blob))
        is_regulator = _as_bool(prior.get("regulator"))
        counted_override = _as_bool(prior.get("counted"))
        unknown_override = _as_bool(prior.get("subject_unknown"))
    else:
        info = info_disclosure(blob)
        basis = "正文关键词：信息披露类" if info else "正文关键词：非信息披露类"
        public_censure = "公开谴责" in blob
        is_regulator = regulator(blob, event_type)
        counted_override = None
        unknown_override = None

    if excluded:
        info = False
        basis = excluded[2]

    # R2：没有正文（也没有人工/已提交判定）时，主体或事项无法确定，记待定。
    if not (review_override or prior):
        if not strip_tags(content):
            pending = True
            basis = "正文未取得，主体/事项待定"
        elif not info and not excluded:
            pending = True
            basis = "正文未说明信息披露事项，主体/事项待定"
    if role == "unknown" and not (review_override or prior):
        pending = True
        basis = "主体身份不明，待定"

    final = public_censure if event_type in ("公开谴责", "纪律处分") else True
    computed_counted = bool(info and is_regulator and final and role in COUNTED_ROLES)
    counted = counted_override if counted_override is not None else computed_counted
    if excluded or pending:
        counted = False
    unknown = (
        unknown_override
        if unknown_override is not None
        else bool(info and is_regulator and final and role == "unknown")
    )
    if pending:
        unknown = False
    doc_no = extract_doc_no(blob) or str(prior.get("doc_no") or "")
    evidence_basis = basis
    return {
        "date": date,
        "event_type": event_type,
        "title": strip_tags(title),
        "subject": str(subject or ""),
        "role": role,
        "counted": bool(counted),
        "subject_unknown": bool(unknown),
        "info_disclosure": bool(info),
        "regulator": bool(is_regulator),
        "pending": bool(pending),
        "matter": excluded[0] if excluded else ("信息披露" if info else ""),
        "basis": evidence_basis,
        "decision_source": decision_source,
        "doc_no": doc_no,
        "url": url,
        "evidence": f"{strip_tags(title)}（{evidence_basis}）",
    }


__all__ = [
    "COUNTED_ROLES",
    "EXCLUDED_MATTERS",
    "INFO_BOILERPLATE",
    "INFO_BOILERPLATE_PATTERNS",
    "INFO_PATTERNS",
    "ROLE_LABELS",
    "classify_event",
    "excluded_matter",
    "extract_doc_no",
    "info_disclosure",
    "load_review",
    "penalty_role",
    "reason_snippet",
    "regulator",
    "strip_boilerplate",
    "strip_tags",
]
