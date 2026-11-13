#!/usr/bin/env python
"""周期行业清单补全后的重跑：221 家池成员 + 30 只测试股。

只更换「明显周期」的行业线索来源（版本化清单），不改任何阈值、政策或估值代码。
输出：
- `data/pool-valuation/cyclical-rerun.json`
- `docs/phase4k-cyclical-rerun.md`

用法：
    .venv/bin/python tools/run_cyclical_rerun.py
    .venv/bin/python tools/run_cyclical_rerun.py --write-ledger   # 同时尝试写入正式结论账本
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.snapshot import SnapshotStore  # noqa: E402
from engine.evidence.applicability import SELECTION_POLICY  # noqa: E402
from engine.evidence.cyclical import list_summary, load_list  # noqa: E402
from engine.evidence.models import BusinessEvidence  # noqa: E402
from engine.policy.conclusion_ledger import (  # noqa: E402
    ConclusionConflict,
    ConclusionLedger,
    ConclusionRecord,
)
from engine.policy.equity import load_policy  # noqa: E402
from engine.policy.trial import evaluate_company  # noqa: E402
from engine.policy.valuation import load_macro_series  # noqa: E402
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
INCOME_REPORT = ROOT / "data" / "income-pool" / "report.json"
BANK_REPORT = ROOT / "data" / "bank-pool" / "report.json"
PRIOR_POOL = ROOT / "data" / "pool-valuation" / "pool-member-valuation.json"
PRIOR_TRIAL = ROOT / "data" / "policy-trial" / "trial-report.json"
POLICY_SNAPSHOTS = ROOT / "data" / "policy-trial" / "snapshots"
POLICY_EVIDENCE = ROOT / "data" / "policy-trial" / "evidence"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
OUT_JSON = ROOT / "data" / "pool-valuation" / "cyclical-rerun.json"
OUT_MD = ROOT / "docs" / "phase4k-cyclical-rerun.md"
ANALYSIS_DATE = date(2026, 11, 13)
LIST_PATH = ROOT / "data" / "policies" / "cyclical-industries-v1.json"

CONCLUSION_LABELS = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}


def _codes() -> tuple[list[str], dict[str, list[str]]]:
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
    income = json.loads(INCOME_REPORT.read_text(encoding="utf-8"))
    bank = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
    q = {str(row["code"]).zfill(6) for row in quality["in_pool"]}
    i = {str(row["code"]).zfill(6) for row in income["in_pool"]}
    banks = {str(row["code"]).zfill(6) for row in bank["in_pool"]}
    codes = sorted((q | i) - banks)
    pools: dict[str, list[str]] = {code: [] for code in codes}
    for code in q:
        if code in pools:
            pools[code].append("好公司池")
    for code in i:
        if code in pools:
            pools[code].append("稳定收息池")
    return codes, pools


def _summary(record: dict) -> dict:
    route = record.get("route") or {}
    trial = record.get("trial_conclusion") or {}
    return {
        "family": route.get("family"),
        "route_status": route.get("status"),
        "status": record.get("status"),
        "grade": (record.get("grade") or {}).get("grade"),
        "conclusion": trial.get("conclusion"),
        "reason_codes": list(record.get("reason_codes") or []),
        "status_reason": record.get("status_reason"),
        "cyclical_list": next(
            (
                reason.get("observed", {}).get("cyclical_list")
                for reason in (route.get("reasons") or [])
                if (reason.get("observed") or {}).get("cyclical_list")
            ),
            None,
        ),
        "observed": next(
            (
                reason.get("observed", {})
                for reason in (route.get("reasons") or [])
                if (reason.get("observed") or {}).get("cyclical_list")
            ),
            {},
        ),
    }


def _pool_run(prior: dict, *, write_ledger: bool) -> dict:
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    batch = BatchStore()
    codes, pools = _codes()
    prior_by_code = {str(row["code"]).zfill(6): row for row in prior["results"]}
    ledger = ConclusionLedger(LEDGER) if write_ledger else None
    added, skipped, conflicts = [], [], []
    rows = []
    changes = []
    affected = []
    for code in codes:
        snapshot = build_snapshot(batch, code)
        evidence = load_evidence(
            code, name=batch.name_for(code), industry=batch.industry_for(code), snapshot=snapshot
        )
        record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=code)
        now = _summary(record)
        old = _summary(prior_by_code[code])
        old_match = any(
            key in (evidence.industry_clue or "")
            for key in SELECTION_POLICY["cyclical_industries"]
        )
        new_match = now.get("cyclical_list") is not None
        row = {
            "code": code,
            "name": batch.name_for(code),
            "industry": batch.industry_for(code),
            "pools": pools[code],
            "old_clue_match": old_match,
            "new_clue_match": new_match,
            "before": old,
            "after": now,
            "prior_values": prior_by_code[code].get("values"),
            "prior_price": prior_by_code[code].get("price"),
            "prior_price_date": prior_by_code[code].get("price_as_of"),
            "prior_grade": (prior_by_code[code].get("grade") or {}).get("grade"),
            "changed": old["conclusion"] != now["conclusion"],
            "conclusion_changed": old["conclusion"] != now["conclusion"],
            "snapshot_id": snapshot.snapshot_id,
            "price": record.get("price"),
            "price_date": record.get("price_as_of"),
            "values": record.get("values"),
            "candidate_entry_price": ((record.get("trial_conclusion") or {}).get("thresholds") or {}).get("candidate_entry_price"),
            "ledger_eligible": bool(
                record.get("status") == "complete"
                and (record.get("grade") or {}).get("grade") in ("A", "B")
                and (record.get("trial_conclusion") or {}).get("conclusion")
                in ("candidate_buy_point", "wait", "avoid")
                and record.get("price") is not None
                and record.get("values")
            ),
        }
        rows.append(row)
        if row["conclusion_changed"]:
            changes.append(row)
        elif old["family"] != now["family"] or old_match != new_match:
            affected.append(row)
        if ledger is not None and row["ledger_eligible"]:
            values = record["values"]
            item = ConclusionRecord(
                instrument_id=record["instrument_id"],
                code=code,
                analysis_date=ANALYSIS_DATE.isoformat(),
                policy_version=policy.policy_id,
                policy_digest=policy.policy_digest,
                snapshot_id=snapshot.snapshot_id,
                price=float(record["price"]),
                price_date=str(record.get("price_as_of") or ""),
                values={key: float(values[key]) for key in ("pessimistic", "base", "optimistic")},
                grade=str((record.get("grade") or {}).get("grade")),
                margin_of_safety=(record.get("grade") or {}).get("margin_of_safety"),
                conclusion=str((record.get("trial_conclusion") or {}).get("conclusion")),
                binding_condition=str(
                    ((record.get("trial_conclusion") or {}).get("thresholds") or {}).get("binding_condition_text") or ""
                ),
                recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
            )
            try:
                outcome = ledger.append([item])
            except ConclusionConflict as exc:
                conflicts.append({"code": code, "reason": str(exc)})
                continue
            added.extend(outcome.get("added") or [])
            skipped.extend(outcome.get("skipped") or [])
    return {
        "total": len(rows),
        "conclusions_before": dict(Counter(row["before"]["conclusion"] for row in rows)),
        "conclusions_after": dict(Counter(row["after"]["conclusion"] for row in rows)),
        "changed_count": len(changes),
        "changes": changes,
        "affected_not_changed": affected,
        "rows": rows,
        "ledger": {
            "eligible": sum(1 for row in rows if row["ledger_eligible"]),
            "added": added,
            "skipped": skipped,
            "conflicts": conflicts,
        },
    }


def _test_set_run() -> dict:
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    trial = json.loads(PRIOR_TRIAL.read_text(encoding="utf-8"))
    store = SnapshotStore(POLICY_SNAPSHOTS)
    rows = []
    for prior in trial["records"]:
        code = str(prior["code"]).zfill(6)
        snapshot = store.load(prior["snapshot_id"])
        venue = "SH" if code.startswith("6") else "SZ"
        path = POLICY_EVIDENCE / f"EQUITY-{code}.{venue}.json"
        evidence = (
            BusinessEvidence.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if path.exists()
            else None
        )
        record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=code)
        after = _summary(record)
        before = {
            "family": (prior.get("route") or {}).get("family"),
            "route_status": (prior.get("route") or {}).get("status"),
            "status": prior.get("status"),
            "grade": (prior.get("grade") or {}).get("grade"),
            "conclusion": (prior.get("trial_conclusion") or {}).get("conclusion"),
            "reason_codes": list(prior.get("reason_codes") or []),
            "status_reason": prior.get("status_reason"),
            "cyclical_list": None,
        }
        rows.append(
            {
                "code": code,
                "name": prior.get("name"),
                "industry": prior.get("industry"),
                "snapshot_id": prior["snapshot_id"],
                "before": before,
                "after": after,
                "changed": before["conclusion"] != after["conclusion"],
            }
        )
    return {
        "total": len(rows),
        "changed_count": sum(1 for row in rows if row["changed"]),
        "changes": [row for row in rows if row["changed"]],
        "rows": rows,
    }


def run(*, write_ledger: bool) -> dict:
    prior = json.loads(PRIOR_POOL.read_text(encoding="utf-8"))
    pool = _pool_run(prior, write_ledger=write_ledger)
    test_set = _test_set_run()
    payload = {
        "meta": {
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "analysis_date": ANALYSIS_DATE.isoformat(),
            "policy": {
                "policy_id": load_policy(POLICY).policy_id,
                "policy_digest": load_policy(POLICY).policy_digest,
            },
            "cyclical_list": list_summary(LIST_PATH),
            "batch_snapshot_id": prior["meta"]["batch_snapshot_id"],
            "prior_run": "data/pool-valuation/pool-member-valuation.json（第四阶段第十批）",
            "scope": "只更换明显周期的行业线索来源；阈值、政策与估值代码未改。",
        },
        "pool": pool,
        "test_set": test_set,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    print(
        f"池成员 {pool['total']}：结论改变 {pool['changed_count']}；"
        f"测试股 {test_set['total']}：结论改变 {test_set['changed_count']}；"
        f"账本新增 {len(pool['ledger']['added'])}、跳过 {len(pool['ledger']['skipped'])}、"
        f"冲突 {len(pool['ledger']['conflicts'])}"
    )
    return payload


def _fmt_values(row: dict) -> str:
    values = row.get("prior_values") or row.get("values") or {}
    if not values:
        return "—"
    return (
        f"{values.get('pessimistic', 0):.2f} / {values.get('base', 0):.2f} / "
        f"{values.get('optimistic', 0):.2f}"
    )


def render(payload: dict) -> str:
    meta = payload["meta"]
    listing = meta["cyclical_list"]
    pool = payload["pool"]
    lines = [
        "# 明显周期行业清单补全与重跑",
        "",
        f"- 生成时间：{meta['generated_at']}",
        f"- 清单：`{listing['list_id']}`（{listing['version']}，{listing['effective_date']}）",
        f"- 依据：{listing['basis']['classification']}；{listing['basis']['publisher']}，"
        f"{listing['basis']['published_at']}；来源 {listing['basis']['source_url']}",
        f"- 政策：`{meta['policy']['policy_id']}`（{meta['policy']['policy_digest']}）",
        f"- 批量快照：`{meta['batch_snapshot_id']}`；分析日 {meta['analysis_date']}",
        "- 只更换行业线索来源；收入波动 ≥8% 或收入回撤 ≥15% 的既有阈值未改，政策与估值代码未改。",
        "",
        "## 1. 清单内容",
        "",
        "| 组 | 申万一级 | 精确行业名 | 保留的旧线索 |",
        "|---|---|---|---|",
    ]
    for group in listing["groups"]:
        lines.append(
            f"| {group['group']} | {group['level1']} | "
            f"{'、'.join(group['listed_names']) or '—'} | {'、'.join(group['legacy_clues']) or '—'} |"
        )
    lines += [
        "",
        "匹配规则：行业字段按「/」拆分后与精确行业名比较（忽略末尾Ⅱ/Ⅲ），"
        "旧线索做包含匹配；命中后仍须收入波动/回撤确认。",
        "",
        "## 2. 池成员结论变化",
        "",
        f"221 家池成员中，结论改变 **{pool['changed_count']}** 家；"
        "其余受影响但结论未变的公司列在 2.2。",
        "",
        "| 代码 | 名称 | 行业 | 命中组 | 改变前 | 改变后 | 说明 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in pool["changes"]:
        before = row["before"]
        after = row["after"]
        match = after.get("cyclical_list") or {}
        reason = (
            f"{CONCLUSION_LABELS.get(before['conclusion'], before['conclusion'])}"
            f"（{before['family']}）→ "
            f"{CONCLUSION_LABELS.get(after['conclusion'], after['conclusion'])}"
            f"（{after['family']}）"
        )
        lines.append(
            f"| {row['code']} | {row['name']} | {row['industry']} | {match.get('group', '—')} | "
            f"{CONCLUSION_LABELS.get(before['conclusion'], before['conclusion'])} | "
            f"{CONCLUSION_LABELS.get(after['conclusion'], after['conclusion'])} | {reason} |"
        )
    if not pool["changes"]:
        lines.append("| — | — | — | — | — | — | 无 |")
    lines += [
        "",
        "### 2.1 逐只说明（结论改变）",
        "",
    ]
    for row in pool["changes"]:
        after = row["after"]
        match = after.get("cyclical_list") or {}
        lines.append(
            f"- **{row['code']} {row['name']}**（{row['industry']}）："
            f"命中 `{match.get('list_id', '—')}` 的「{match.get('group', '—')}」组"
            f"（{match.get('matched_by', '—')}，{match.get('matched_name', '—')}）；"
            f"结论由「{CONCLUSION_LABELS.get(row['before']['conclusion'], row['before']['conclusion'])}」"
            f"改为「{CONCLUSION_LABELS.get(after['conclusion'], after['conclusion'])}」。"
            f"此前三档价值（悲观/基准/乐观）：{_fmt_values(row)} 元/股；"
            f"此前等级 {row.get('prior_grade') or '—'}；现价 "
            f"{row['price']:.2f} 元（{row['price_date']}）。"
        )
    lines += [
        "",
        "### 2.1.1 君正集团判定说明",
        "",
    ]
    junzheng = next((row for row in pool["changes"] if row["code"] == "601216"), None)
    if junzheng is not None:
        match = (junzheng["after"].get("cyclical_list") or {})
        observed = junzheng["after"].get("observed") or {}
        lines += [
            f"- 行业：`{junzheng['industry']}`，申万 2021 版「基础化工 / 化学原料」，"
            f"命中清单 `{match.get('list_id')}` 的「{match.get('group')}」组"
            f"（{match.get('matched_by')}，{match.get('matched_name')}）。",
            f"- 收入波动：{observed.get('revenue_growth_stdev', 0):.1%}（阈值 ≥8%）；"
            f"收入最大回撤：{observed.get('max_revenue_drawdown', 0):.1%}（阈值 ≥15%）。",
            "- 旧清单只有关键词「化工」，「化学原料」不含「化工」，因此旧版漏判；"
            "新清单把「化学原料」列为精确行业名，补全后按既有规则判为明显周期。",
            f"- 结论：候选买点 → 无法判断（路线不支持：明显周期企业，未实现周期位置判断与正常化口径）。"
            f"此前基准价值 {_fmt_values(junzheng)} 元/股，现价 {junzheng['price']:.2f} 元"
            f"（{junzheng['price_date']}）。",
            "- 这不是阈值变化：收入波动/回撤阈值、政策 JSON 与估值代码均未改；"
            "只是行业线索从硬编码关键词换成版本化清单，并补上漏掉的申万二级行业。",
            "",
            "### 2.2 受影响但结论未变",
        "",
        "| 代码 | 名称 | 行业 | 命中组 | 结论 | 未变原因 |",
        "|---|---|---|---|---|---|",
    ]
    for row in pool["affected_not_changed"]:
        match = row["after"].get("cyclical_list") or {}
        observed = row["after"].get("observed") or {}
        vol = observed.get("revenue_growth_stdev")
        drawdown = observed.get("max_revenue_drawdown")
        if row["before"]["family"] == row["after"]["family"]:
            note = "清单命中但未跨过收入波动/回撤阈值；保持原结论"
        elif row["before"]["conclusion"] == row["after"]["conclusion"]:
            note = (
                f"清单命中且收入波动 {vol:.1%} / 回撤 {drawdown:.1%} 已确认，改判为明显周期"
                "（路线不支持）；此前结论已是无法判断，结论文字未变"
                if vol is not None and drawdown is not None
                else "清单命中后改判为明显周期（路线不支持）；此前结论已是无法判断"
            )
        else:  # pragma: no cover - 结论改变的行已单独列出
            note = "清单命中后改判为明显周期"
        lines.append(
            f"| {row['code']} | {row['name']} | {row['industry']} | {match.get('group', '—')} | "
            f"{CONCLUSION_LABELS.get(row['after']['conclusion'], row['after']['conclusion'])} | {note} |"
        )
    if not pool["affected_not_changed"]:
        lines.append("| — | — | — | — | — | 无 |")
    lines += [
        "",
        "## 3. 30 只测试股",
        "",
        f"结论改变 **{payload['test_set']['changed_count']}** 只。",
        "",
        "| 代码 | 名称 | 行业 | 改变前 | 改变后 |",
        "|---|---|---|---|---|",
    ]
    for row in payload["test_set"]["changes"]:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['industry']} | "
            f"{CONCLUSION_LABELS.get(row['before']['conclusion'], row['before']['conclusion'])} | "
            f"{CONCLUSION_LABELS.get(row['after']['conclusion'], row['after']['conclusion'])} |"
        )
    if not payload["test_set"]["changes"]:
        lines.append("| — | — | — | — | — |")
    lines += [
        "",
        "## 4. 结论账本",
        "",
        f"- 本次可写入的正式结论 {pool['ledger']['eligible']} 条；"
        f"新写入 {len(pool['ledger']['added'])} 条，已存在跳过 {len(pool['ledger']['skipped'])} 条，"
        f"冲突 {len(pool['ledger']['conflicts'])} 条。",
        "- 结论变为「无法判断」的公司按既有规则不写入正式结论账本；旧记录不改，账本只追加。",
        "",
        "## 5. 口径与限制",
        "",
        "- 清单只补全行业线索；判定仍要求收入波动或回撤超过既有阈值，没有改任何阈值。",
        "- 重跑使用与第十批相同的批量快照与现价，只隔离周期行业线索的差异。",
        "- 30 只测试股使用各自原政策快照，未改用批量输入。",
        "- 清单依据申万 2021 版行业名称；行业字段来自批量表与业务证据，未重新做行业分类。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-ledger", action="store_true", help="同时尝试写入正式结论账本（只追加）")
    args = parser.parse_args()
    run(write_ledger=args.write_ledger)
