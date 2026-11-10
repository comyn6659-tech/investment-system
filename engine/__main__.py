"""`python -m engine` 入口，等价于 `python -m engine.cli`。"""

from __future__ import annotations

from engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
