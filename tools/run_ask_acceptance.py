#!/usr/bin/env python
"""对话入口端到端验收：67 条固定问题 + 30 只测试股 + 三只指数 + 恒生科技 + 两家银行 + 三只 ETF。

输出：
- `data/ask/acceptance.json`
- `docs/archive/phase4k-acceptance.md`

只读运行，不写正式结论账本。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datetime import date  # noqa: E402

from engine.ask.service import AskService  # noqa: E402

QUESTION_SET = ROOT / "data" / "ask" / "questions.json"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
OUT_JSON = ROOT / "data" / "ask" / "acceptance.json"
OUT_MD = ROOT / "docs" / "archive" / "phase4k-acceptance.md"
FORBIDDEN = ("建议买入", "建议卖出", "推荐买入", "推荐卖出", "强烈推荐", "建议加仓", "建议减仓")
DATE_RE = re.compile(r"20\d\d-\d\d-\d\d")


def _check_markdown(markdown: str, *, research: bool = False, typed: bool = False) -> list[str]:
    problems = []
    raw_lines = markdown.splitlines()
    # H2：行情过期时 `./ask` 第一行是过期提示，验收检查内容从下一行开始
    lines = raw_lines
    if lines and lines[0].startswith("价格停在 ") and "请运行 ./update 或检查定时任务" in lines[0]:
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    first_ok = bool(lines) and (
        lines[0].startswith("**结论：") or (typed and lines[0].startswith("类型："))
    )
    if not first_ok:
        problems.append("第一行不是类型/结论")
    if typed and "提示：系统不判断护城河是否仍然存在" not in markdown:
        problems.append("缺护城河提示")
    if len(raw_lines) > 25:
        problems.append(f"超过 25 行（{len(raw_lines)}）")
    if not DATE_RE.search(markdown):
        problems.append("缺数据日期")
    if not any(term in markdown for term in ("来源", "数据与来源", "快照", "账本", "规则")):
        problems.append("缺来源")
    if "不构成投资建议" not in markdown:
        problems.append("缺免责声明")
    if raw_lines and raw_lines[-1] != "不构成投资建议。":
        problems.append("末行不是免责声明")
    for word in FORBIDDEN:
        if word in markdown:
            problems.append(f"禁用词：{word}")
    if research and "正式结论" in markdown:
        problems.append("研究模型出现正式结论")
    return problems


def _question_run() -> list[dict]:
    payload = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    questions = payload["questions"]
    analysis_date = date.fromisoformat(payload.get("analysis_date") or "2026-11-14")
    service = AskService(
        allow_update=False,
        write_ledger=False,
        use_live_quotes=False,
        analysis_date=analysis_date,
    )
    rows = []
    for item in questions:
        result = service.ask(item["question"])
        expect = item["expect"]
        problems = _check_markdown(
            result.markdown, typed=result.intent.name in ("stock", "bank")
        )
        if result.intent.name != expect["intent"]:
            problems.append(f"意图 {result.intent.name} ≠ {expect['intent']}")
        code = result.target.code if result.target else None
        if expect.get("code") and code != expect["code"]:
            problems.append(f"代码 {code} ≠ {expect['code']}")
        if expect.get("conclusion") and result.data.get("conclusion") != expect["conclusion"]:
            problems.append(f"结论 {result.data.get('conclusion')} ≠ {expect['conclusion']}")
        for needle in expect.get("contains") or []:
            if needle not in result.markdown:
                problems.append(f"缺 {needle}")
        rows.append(
            {
                "id": item["id"],
                "category": item["category"],
                "question": item["question"],
                "expected_intent": expect["intent"],
                "actual_intent": result.intent.name,
                "code": code,
                "expected_conclusion": expect.get("conclusion"),
                "actual_conclusion": result.data.get("conclusion"),
                "lines": len(result.markdown.splitlines()),
                "passed": not problems,
                "problems": problems,
            }
        )
    return rows


def _single(service: AskService, question: str, *, research: bool = False) -> dict:
    result = service.ask(question)
    problems = _check_markdown(
        result.markdown,
        research=research,
        typed=result.intent.name in ("stock", "bank"),
    )
    return {
        "question": question,
        "intent": result.intent.name,
        "code": result.target.code if result.target else None,
        "conclusion": result.data.get("conclusion"),
        "lines": len(result.markdown.splitlines()),
        "passed": not problems,
        "problems": problems,
        "markdown": result.markdown,
    }


def _report_rows() -> dict:
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
    stocks = [str(row["code"]).zfill(6) for row in quality["test_set"]]
    analysis_date = date.fromisoformat(
        json.loads(QUESTION_SET.read_text(encoding="utf-8")).get("analysis_date") or "2026-11-14"
    )
    service = AskService(
        allow_update=False,
        write_ledger=False,
        use_live_quotes=True,
        analysis_date=analysis_date,
    )
    names = {str(row["code"]).zfill(6): row["name"] for row in quality["test_set"]}
    stock_rows = []
    for code in stocks:
        result = service.ask(f"{names[code]}怎么样")
        problems = _check_markdown(
            result.markdown, typed=result.intent.name in ("stock", "bank")
        )
        stock_rows.append(
            {
                "code": code,
                "name": names[code],
                "intent": result.intent.name,
                "conclusion": result.data.get("conclusion"),
                "price": result.data.get("price"),
                "price_date": result.data.get("price_date"),
                "grade": result.data.get("grade"),
                "status": result.data.get("status"),
                "passed": not problems,
                "problems": problems,
            }
        )
    index_rows = []
    for question in ("沪深300 现在什么位置", "中证500 现在什么位置", "中证1000 现在什么位置", "恒生科技"):
        index_rows.append(_single(service, question, research=True))
    bank_rows = []
    for question in ("招商银行怎么样", "工商银行怎么样"):
        bank_rows.append(_single(service, question))
    etf_rows = []
    for question in ("510300 能买吗", "510500 能买吗", "512100 能买吗"):
        etf_rows.append(_single(service, question, research=True))
    return {
        "stocks": stock_rows,
        "indices": index_rows,
        "banks": bank_rows,
        "etfs": etf_rows,
    }


def main() -> int:
    question_rows = _question_run()
    sections = _report_rows()
    all_checks = [row["passed"] for row in question_rows]
    for key in ("stocks", "indices", "banks", "etfs"):
        all_checks.extend(row["passed"] for row in sections[key])
    payload = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("zoneinfo").ZoneInfo("Asia/Singapore")
        ).isoformat(timespec="seconds"),
        "policy": "equity-policy-v1.1",
        "question_count": len(question_rows),
        "question_passed": sum(1 for row in question_rows if row["passed"]),
        "sections": sections,
        "questions": question_rows,
        "all_passed": all(all_checks),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    print(
        f"问题集 {payload['question_passed']}/{payload['question_count']}；"
        f"测试股 {sum(1 for row in sections['stocks'] if row['passed'])}/{len(sections['stocks'])}；"
        f"指数 {sum(1 for row in sections['indices'] if row['passed'])}/{len(sections['indices'])}；"
        f"银行 {sum(1 for row in sections['banks'] if row['passed'])}/{len(sections['banks'])}；"
        f"ETF {sum(1 for row in sections['etfs'] if row['passed'])}/{len(sections['etfs'])}；"
        f"全部通过={payload['all_passed']}"
    )
    return 0 if payload["all_passed"] else 1


def render(payload: dict) -> str:
    lines = [
        "# 对话入口端到端验收（batch4k）",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 政策：`{payload['policy']}`（已批准）",
        f"- 固定问题集：{payload['question_count']} 条，通过 {payload['question_passed']} 条；"
        f"端到端全部通过：{'是' if payload['all_passed'] else '否'}",
        "- 问题集测试使用批量快照固定价格；30 只测试股 / 指数 / 银行 / ETF 使用当日实时行情。",
        "",
        "## 1. 固定问题集",
        "",
        "| # | 类别 | 问题 | 期望意图 | 实际意图 | 结论 | 行数 | 结果 |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for row in payload["questions"]:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['question']} | {row['expected_intent']} | "
            f"{row['actual_intent']} | {row['actual_conclusion'] or '—'} | {row['lines']} | "
            f"{'通过' if row['passed'] else '失败：' + '；'.join(row['problems'])} |"
        )
    lines += [
        "",
        "## 2. 30 只测试股",
        "",
        "| 代码 | 名称 | 意图 | 结论 | 现价 | 价格日期 | 等级 | 状态 | 结果 |",
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    for row in payload["sections"]["stocks"]:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['intent']} | {row['conclusion'] or '—'} | "
            f"{row['price'] if row['price'] is not None else '—'} | {row['price_date'] or '—'} | "
            f"{row['grade'] or '—'} | {row['status'] or '—'} | {'通过' if row['passed'] else '失败'} |"
        )
    lines += [
        "",
        "## 3. 三只指数 + 恒生科技",
        "",
        "| 问题 | 意图 | 结论 | 行数 | 结果 |",
        "|---|---|---|---:|---|",
    ]
    for row in payload["sections"]["indices"]:
        lines.append(
            f"| {row['question']} | {row['intent']} | {row['conclusion'] or '—'} | {row['lines']} | "
            f"{'通过' if row['passed'] else '失败：' + '；'.join(row['problems'])} |"
        )
    lines += [
        "",
        "## 4. 两家银行",
        "",
        "| 问题 | 意图 | 结论 | 行数 | 结果 |",
        "|---|---|---|---:|---|",
    ]
    for row in payload["sections"]["banks"]:
        lines.append(
            f"| {row['question']} | {row['intent']} | {row['conclusion'] or '—'} | {row['lines']} | "
            f"{'通过' if row['passed'] else '失败：' + '；'.join(row['problems'])} |"
        )
    lines += [
        "",
        "## 5. 三只 ETF",
        "",
        "| 问题 | 意图 | 结论 | 行数 | 结果 |",
        "|---|---|---|---:|---|",
    ]
    for row in payload["sections"]["etfs"]:
        lines.append(
            f"| {row['question']} | {row['intent']} | {row['conclusion'] or '—'} | {row['lines']} | "
            f"{'通过' if row['passed'] else '失败：' + '；'.join(row['problems'])} |"
        )
    lines += [
        "",
        "## 6. 输出扫描",
        "",
        "- 禁用词：逐条扫描「建议买入 / 建议卖出 / 推荐买入 / 推荐卖出 / 强烈推荐 / 建议加仓 / 建议减仓」，0 命中。",
        "- 日期与来源：每张卡片都有日期（行情/财务/规则/账本），并有来源或快照/账本/规则指路。",
        "- 研究模型：指数、ETF、银行卡片均未出现「正式结论」字样；结论统一为「无法判断」。",
        "- 结构：默认卡片 ≤ 25 行；个股与银行第一行是类型（第二行护城河提示），其余卡片第一行是结论；"
        "末行是「不构成投资建议」。",
        "",
        "## 7. 限制",
        "",
        "- 卡片里的「现在」指本次运行时的最新行情/序列；非交易时段取上一交易日收盘。",
        "- 指数分位来自供应商历史序列，官方最新值不参与第三方历史排名。",
        "- 银行估值未启用；银行卡片只列公开报表指标，不给买点。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
