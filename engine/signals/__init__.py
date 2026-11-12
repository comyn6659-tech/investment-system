"""指数研究信号（候选 A）：最新周度观察点与只追加账本。"""

from engine.signals.candidate_a import (
    MODEL_ID,
    build_candidate_a_signal,
    classify_signal,
    load_backtest_summary,
)
from engine.signals.ledger import (
    LedgerConflict,
    LedgerError,
    LedgerRecord,
    SignalLedger,
)

__all__ = [
    "LedgerConflict",
    "LedgerError",
    "LedgerRecord",
    "MODEL_ID",
    "SignalLedger",
    "build_candidate_a_signal",
    "classify_signal",
    "load_backtest_summary",
]
