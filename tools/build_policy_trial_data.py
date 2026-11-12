#!/usr/bin/env python
"""采集「个股政策 v1 草案」试运行所需的数据（30 只固定测试集）。

范围（第四阶段第一批 A 节）：
- 每只标的尽量取 10 个完整财年（=44 个报告期，含同比基期与 2026 年三季报），
  最少 7 个财年；取不到的字段如实记 missing，不用其他数据凑；
- 快照写入独立目录 `data/policy-trial/snapshots/`，**不覆盖**已签收的阶段二快照；
- 业务证据写入 `data/policy-trial/evidence/`，供政策试运行判断路线（与 CLI 无关）；
- 规范化无风险利率所需的中债 10 年期收益率历史单独入库，带来源与内容哈希。

用法：
    .venv/bin/python tools/build_policy_trial_data.py --as-of 2026-11-11
    .venv/bin/python tools/build_policy_trial_data.py --only 600519,000858
    .venv/bin/python tools/build_policy_trial_data.py --macro-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from build_business_evidence import build_evidence  # noqa: E402
from build_equity_snapshots import build_equity_snapshot  # noqa: E402
from engine.data.snapshot import SnapshotStore, stable_digest  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
TRIAL_ROOT = ROOT / "data" / "policy-trial"
SNAPSHOT_DIR = TRIAL_ROOT / "snapshots"
EVIDENCE_DIR = TRIAL_ROOT / "evidence"
CACHE_DIR = TRIAL_ROOT / "cache"
MACRO_DIR = TRIAL_ROOT / "macro"

# 固定测试集（施工单原文顺序；工程样本，不是推荐）
CODES = (
    "600519", "000858", "600887", "603288", "000895", "600436", "000333",
    "000651", "600660", "600031", "600309", "002415", "300750", "002594",
    "600276", "600900", "601088", "601006", "600941", "601668", "601919",
    "600019", "601899", "601857", "000002", "601012", "600036", "601398",
    "601318", "600030",
)

POLICY_TRIAL_PERIODS = 44  # 11 年 × 4 个报告期：覆盖 10 个财年 + 2026 三季报与基期


def _venue(code: str) -> str:
    return "SH" if code.startswith(("6", "9")) else "SZ"


def _retry(label: str, fn, *, attempts: int = 3, sleep: float = 3.0):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < attempts:
                time.sleep(sleep * (attempt + 1))
    raise RuntimeError(f"{label} 连续 {attempts} 次失败：{type(last).__name__}: {last}") from last


# --- 宏观：中债 10 年期收益率 ------------------------------------------------
def build_macro_series(*, as_of: str, now: datetime) -> dict:
    """抓取中债 10 年期国债收益率日频历史，落库月末值与最新值。

    规范化无风险利率 = max(最新值, 近 10 年月末值均值)，由政策模块计算；
    本文件只负责把**观测值**带来源地固定下来，不在采集端做政策判断。
    """
    start = f"{int(as_of[:4]) - 11}0101"
    frame = _retry(
        "中债 10 年期收益率",
        lambda: ak.bond_zh_us_rate(start_date=start),
    )
    frame = frame[["日期", "中国国债收益率10年"]].dropna()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.strftime("%Y-%m-%d")
    frame = frame[frame["日期"] <= as_of].sort_values("日期")
    if frame.empty:
        raise RuntimeError("中债 10 年期收益率序列为空")

    latest_date = str(frame["日期"].iloc[-1])
    latest = float(frame["中国国债收益率10年"].iloc[-1])

    # 近 10 年「完整月份」的月末值：截至 as_of 所在月之前
    cutoff = (pd.Timestamp(as_of) - pd.offsets.MonthBegin(1)).strftime("%Y-%m-%d")
    monthly = frame[frame["日期"] <= cutoff].copy()
    monthly["month"] = monthly["日期"].str[:7]
    month_end = monthly.groupby("month", as_index=False).tail(1)
    month_end = month_end.sort_values("日期").tail(120)
    if len(month_end) < 120:
        raise RuntimeError(f"月末观测不足 120 个（实际 {len(month_end)}），无法计算近 10 年均值")

    payload = {
        "schema_version": "macro-series/1",
        "series": "cn_10y_government_yield",
        "label": "中国 10 年期国债到期收益率",
        "unit": "percent",
        "frequency": "daily_observation_month_end_series",
        "as_of": as_of,
        "latest": {"date": latest_date, "value_pct": latest},
        "month_end_observations": [
            {"date": str(row["日期"]), "value_pct": float(row["中国国债收益率10年"])}
            for _, row in month_end.iterrows()
        ],
        "month_end_start": str(month_end["日期"].iloc[0]),
        "month_end_end": str(month_end["日期"].iloc[-1]),
        "source": {
            "source_id": "em.bond_zh_us_rate",
            "publisher": "东方财富（汇总中债收益率）",
            "interface": "AKShare bond_zh_us_rate",
            "url": "https://data.eastmoney.com/cjsj/globalRateBond.html",
            "retrieved_at": now.isoformat(timespec="seconds"),
            "definition_note": (
                "中国国债到期收益率，日频；月末值取每月最后一个有观测的交易日。"
                "来源未说明报价口径（中债估值/收盘），按同源同列使用。"
            ),
        },
        "notes": [
            "本文件只保存观测值；max(最新值, 近 10 年月末均值) 由政策模块计算并报告。",
            "近 10 年定义为截至分析日所在月之前的 120 个完整月份。",
        ],
    }
    payload["series_id"] = "macro-" + stable_digest(payload)
    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    path = MACRO_DIR / "cn-10y-yield.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "file": str(path.relative_to(ROOT)),
        "series_id": payload["series_id"],
        "latest_date": latest_date,
        "latest_pct": latest,
        "month_end_mean_pct": sum(o["value_pct"] for o in payload["month_end_observations"])
        / len(payload["month_end_observations"]),
        "months": len(payload["month_end_observations"]),
    }


# --- 单只标的 ---------------------------------------------------------------
def build_one(code: str, *, as_of: str, now: datetime) -> dict:
    stamp = now.isoformat(timespec="seconds")
    venue = _venue(code)
    instrument_id = f"EQUITY:{code}.{venue}"

    evidence = build_evidence(code, as_of=as_of, now=now)
    evidence_path = EVIDENCE_DIR / f"{instrument_id.replace(':', '-')}.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    snapshot = build_equity_snapshot(
        code,
        expected_as_of=as_of,
        now=now,
        periods=POLICY_TRIAL_PERIODS,
        cache_dir=CACHE_DIR,
        price_source="sina",
    )
    store = SnapshotStore(SNAPSHOT_DIR)
    store.save(snapshot)

    annual = sorted(
        {
            fact.fact_id.split(".")[1][:4]
            for fact in snapshot.facts
            if fact.fact_id.startswith("fin.")
            and fact.fact_id.split(".")[1].endswith("12-31")
        }
    )
    return {
        "code": code,
        "instrument_id": instrument_id,
        "snapshot_id": snapshot.snapshot_id,
        "as_of": snapshot.as_of,
        "facts": len(snapshot.facts),
        "annual_years": annual,
        "annual_year_count": len(annual),
        "evidence_file": str(evidence_path.relative_to(ROOT)),
        "evidence_id": evidence["evidence_id"],
        "industry": evidence.get("industry_clue"),
        "built_at": stamp,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采集个股政策 v1 草案试运行数据")
    parser.add_argument("--as-of", dest="as_of", default="2026-11-11", help="预期市场日期")
    parser.add_argument("--only", default=None, help="只构建指定代码，逗号分隔")
    parser.add_argument("--macro-only", action="store_true", help="只采集宏观收益率序列")
    parser.add_argument("--workers", type=int, default=1, help="线程并发数（默认 1；akshare 的 mini_racer 非线程安全）")
    parser.add_argument("--skip-macro", action="store_true", help="跳过宏观序列（多进程分组采集时只在一个进程里做）")
    parser.add_argument("--no-manifest", action="store_true", help="不重建快照 manifest（多进程采集结束后统一重建）")
    parser.add_argument("--no-summary", action="store_true", help="不写 build-summary.json")
    args = parser.parse_args(argv)

    now = datetime.now(TZ)
    for directory in (SNAPSHOT_DIR, EVIDENCE_DIR, CACHE_DIR, MACRO_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "built_at": now.isoformat(timespec="seconds"),
        "as_of": args.as_of,
        "periods": POLICY_TRIAL_PERIODS,
        "codes": [],
        "failures": [],
    }

    if args.skip_macro:
        summary["macro"] = {"skipped": True}
    else:
        print("[1/2] 采集宏观收益率序列…", file=sys.stderr)
        macro = build_macro_series(as_of=args.as_of, now=now)
        summary["macro"] = macro
        print(json.dumps(macro, ensure_ascii=False), file=sys.stderr)
        if args.macro_only:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else CODES
    print(f"[2/2] 采集 {len(codes)} 只标的（并发 {args.workers}）…", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(build_one, code, as_of=args.as_of, now=now): code for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                row = future.result()
                summary["codes"].append(row)
                print(
                    f"  完成 {code}: {row['snapshot_id']} 年报 {row['annual_year_count']} 个 "
                    f"({row['annual_years'][0]}–{row['annual_years'][-1]})",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001
                summary["failures"].append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
                print(f"  失败 {code}: {type(exc).__name__}: {exc}", file=sys.stderr)

    summary["codes"].sort(key=lambda r: CODES.index(r["code"]))
    if not args.no_manifest:
        store = SnapshotStore(SNAPSHOT_DIR)
        store.rebuild_manifest()
    if not args.no_summary:
        (TRIAL_ROOT / "build-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
