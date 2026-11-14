#!/usr/bin/env python
"""第五阶段行为基线比对：before/ 与 after/ 的逐字节差异必须只来自三处修正。

用法：
    .venv/bin/python scripts/compare-capture.py ../batch5-baseline/before ../batch5-baseline/after

退出码 0 表示：
- 卡片集合一致；
- 每张差异卡片的差异行都能归入三处修正（或来源日期替换）；
- 归一化（把同一处修正替换成占位符）后，122 张卡片逐字节相同；
- 8 个账本与 16 个政策文件的 sha256 与 before/summary.json 一致。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

FIX1_MARKERS = ("未通过银行池条件", "待定银行池条件", "未检验银行池条件")
PB_LINE = re.compile(r"^(关键指标：市净率 )[0-9]+(?:\.[0-9]+)?( 倍)")
SOURCE_LINE = re.compile(r"^(来源与日期：.*?)（最新交易日）")
SOURCE_DATE_LINE = re.compile(r"^(来源与日期：.*?)（20\d\d-\d\d-\d\d）(.*)$")
HISTORY_INSERTED = (
    "东方财富市场市净率与分红数据，",
    "市净率与估值同按普通股口径（价格 ÷ 每股净资产）计算，",
)


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _card_paths(root: Path) -> dict[str, Path]:
    return {str(path.relative_to(root)): path for path in sorted(root.rglob("*.txt"))}


def _normalize_fix3(text: str) -> tuple[str, bool]:
    """把来源行的具体日期归一化；返回 (归一化文本, 是否命中旧文案「最新交易日」)。"""
    if not text.startswith("来源与日期："):
        return text, False
    if "最新交易日" in text:
        return text.replace("最新交易日", "<日期>"), True
    return SOURCE_DATE_LINE.sub(r"\1（<日期>）\2", text), False


def _normalize_pb_line(text: str) -> tuple[str, bool]:
    if not text.startswith("关键指标：市净率"):
        return text, False
    new, count = PB_LINE.subn(r"\1<市净率>\2", text)
    return new, bool(count)


def _normalize_history_line(text: str) -> tuple[str, bool]:
    if not text.startswith("历史位置："):
        return text, False
    new = text
    for piece in HISTORY_INSERTED:
        new = new.replace(piece, "")
    return new, new != text


def _split_conditions(lines: list[str], root: Path) -> tuple[list[str], list[str]]:
    """去掉非银行池银行卡片新增的条件清单，返回 (剩余行, 被去掉的行)。"""
    kept: list[str] = []
    dropped: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(FIX1_MARKERS):
            dropped.append(line)
            index += 1
            while index < len(lines) and lines[index].startswith("- "):
                dropped.append(lines[index])
                index += 1
            continue
        kept.append(line)
        index += 1
    return kept, dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args(argv)
    before = Path(args.before)
    after = Path(args.after)
    if not before.is_dir() or not after.is_dir():
        print(f"目录不存在：{before} / {after}", file=sys.stderr)
        return 2

    before_cards = _card_paths(before)
    after_cards = _card_paths(after)
    missing = sorted(set(before_cards) - set(after_cards))
    extra = sorted(set(after_cards) - set(before_cards))
    if missing or extra:
        print(f"卡片集合不一致：缺 {missing}，多 {extra}", file=sys.stderr)
        return 1

    changed: list[dict] = []
    residual: list[str] = []
    for rel in sorted(before_cards):
        old_lines = _read(before_cards[rel])
        new_lines = _read(after_cards[rel])
        if old_lines == new_lines:
            continue
        categories: list[str] = []
        old_has_latest = any("最新交易日" in line for line in old_lines)
        old_norm: list[str] = []
        for line in old_lines:
            line, _ = _normalize_pb_line(line)
            line, _ = _normalize_history_line(line)
            text, _ = _normalize_fix3(line)
            old_norm.append(text)

        new_norm: list[str] = []
        source_dates: set[str] = set()
        dropped_condition_lines: list[str] = []
        for line in new_lines:
            if line.startswith("市净率口径："):
                categories.append("修正2：市净率口径行")
                continue
            line, hit = _normalize_pb_line(line)
            if hit:
                categories.append("修正2：市净率数值")
            line, hit = _normalize_history_line(line)
            if hit:
                categories.append("修正2：历史位置口径")
            if old_has_latest and line.startswith("来源与日期："):
                date = re.search(r"（(20\d\d-\d\d-\d\d)）", line)
                if date:
                    source_dates.add(date.group(1))
                    categories.append("修正3：来源日期")
            text, _ = _normalize_fix3(line)
            new_norm.append(text)
        kept, dropped_condition_lines = _split_conditions(new_norm, after)
        if dropped_condition_lines:
            categories.append("修正1：未通过条件清单")
        new_norm = kept

        residual_old, residual_new = old_norm, new_norm
        if residual_old != residual_new:
            residual.append(rel)
            diff = [
                f"  - old: {line}"
                for line in residual_old
                if line not in residual_new
            ] + [
                f"  + new: {line}"
                for line in residual_new
                if line not in residual_old
            ]
            print(f"意外差异：{rel}\n" + "\n".join(diff), file=sys.stderr)
            continue
        changed.append(
            {
                "path": rel,
                "categories": sorted(set(categories)),
                "source_dates": sorted(source_dates),
                "condition_lines": dropped_condition_lines,
            }
        )

    print(f"卡片总数：{len(before_cards)}；变化 {len(changed)} 张；逐字节相同 {len(before_cards) - len(changed)} 张")
    category_counts = Counter()
    for item in changed:
        for category in item["categories"]:
            category_counts[category] += 1
    print("差异分类统计：")
    for category, count in sorted(category_counts.items()):
        print(f"  - {category}：{count} 张")
    print("变化卡片清单：")
    for item in changed:
        detail = "、".join(item["categories"])
        dates = f"（日期 {','.join(item['source_dates'])}）" if item["source_dates"] else ""
        print(f"  - {item['path']}：{detail}{dates}")
        for line in item["condition_lines"]:
            print(f"      条件行：{line}")

    # 账本与政策哈希
    before_summary = json.loads((before / "summary.json").read_text(encoding="utf-8"))
    after_summary = json.loads((after / "summary.json").read_text(encoding="utf-8"))
    hash_problems = []
    for label in ("ledgers", "policies"):
        before_hashes = before_summary.get(label) or {}
        after_hashes = after_summary.get(label) or {}
        if set(before_hashes) != set(after_hashes):
            hash_problems.append(f"{label} 文件集合不一致")
        for name, row in before_hashes.items():
            if after_hashes.get(name, {}).get("sha256") != row.get("sha256"):
                hash_problems.append(f"{label} 哈希变化：{name}")
    if hash_problems:
        print("哈希校验失败：\n  " + "\n  ".join(hash_problems), file=sys.stderr)
        return 1
    print(
        f"哈希：{len(before_summary['ledgers'])} 个账本、"
        f"{len(before_summary['policies'])} 个政策文件逐字节不变"
    )

    if residual:
        print(f"归一化后仍有意外差异：{residual}", file=sys.stderr)
        return 1
    print("归一化结论：把三处修正（含来源日期替换）归一化后，全部卡片逐字节相同。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
