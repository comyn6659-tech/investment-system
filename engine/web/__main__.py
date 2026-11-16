"""`python -m engine.web`：本地页面入口（与 `./web` 相同）。"""

from engine.web.server import main

if __name__ == "__main__":
    raise SystemExit(main())
