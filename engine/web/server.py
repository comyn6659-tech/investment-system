"""本地页面服务：只监听 127.0.0.1，不访问外网，不引入新依赖（标准库 http.server）。

用法：
    ./web                 # 默认 http://127.0.0.1:8765/（端口被占用时自动换端口），自动打开浏览器
    ./web --no-browser    # 不自动打开浏览器
    ./web --no-update     # 与 ./ask --no-update 一致：回答前不尝试联网更新
关闭终端即停止；不部署到外网、不加登录。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from engine.web.app import WebApp

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 6
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/static/app.js": "app.js",
    "/static/style.css": "style.css",
}
CONTENT_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
MAX_BODY = 64 * 1024


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def make_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "InvestmentSystemLocal/1.0"
        sys_version = ""

        # 静默：本机页面，不把访问日志刷到终端。
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        # --- 输出 -------------------------------------------------------
        def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status)

        def _send_error_json(self, message: str, status: int) -> None:
            self._send_json({"error": message}, status)

        # --- 读取请求 ---------------------------------------------------
        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                raise ValueError("请求内容过大")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"请求不是合法 JSON：{exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return payload

        # --- GET --------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path in STATIC_FILES:
                return self._static(STATIC_FILES[path])
            if path == "/favicon.ico":
                return self._send_bytes(b"", "image/x-icon", 204)
            try:
                if path == "/api/health":
                    return self._send_json({"status": "ok", "host": HOST})
                if path == "/api/overview":
                    return self._send_json(app.overview())
                if path == "/api/pools":
                    return self._send_json(app.pools())
                if path == "/api/company":
                    code = (query.get("code") or [""])[0].strip()
                    pool = (query.get("pool") or [""])[0].strip() or None
                    detail = (query.get("detail") or ["0"])[0] in ("1", "true", "yes")
                    if not code:
                        return self._send_error_json("缺少 code", 400)
                    payload = app.company(code, pool=pool, detail=detail)
                    return self._send_json(payload, 200 if payload.get("ok") else 404)
                if path == "/api/indices":
                    return self._send_json(app.indices())
                if path == "/api/ledger":
                    return self._send_json(app.ledger((query.get("q") or [None])[0]))
                if path == "/api/status":
                    return self._send_json(app.status())
            except Exception as exc:  # noqa: BLE001 - 失败也不返回假数据
                print(f"[web] {path} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
                return self._send_error_json(f"{type(exc).__name__}: {exc}", 500)
            return self._send_error_json("没有这个地址", 404)

        # --- POST -------------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._body()
                if path == "/api/ask":
                    question = str(payload.get("question") or "").strip()
                    if not question:
                        return self._send_error_json("缺少问题", 400)
                    detail = bool(payload.get("detail"))
                    return self._send_json(app.ask(question, detail=detail))
                if path == "/api/recompute":
                    record_id = str(payload.get("record_id") or "").strip()
                    if not record_id:
                        return self._send_error_json("缺少 record_id", 400)
                    return self._send_json(app.recompute(record_id))
            except ValueError as exc:
                return self._send_error_json(str(exc), 400)
            except Exception as exc:  # noqa: BLE001
                print(f"[web] {path} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
                return self._send_error_json(f"{type(exc).__name__}: {exc}", 500)
            return self._send_error_json("没有这个地址", 404)

        # --- 静态文件 ---------------------------------------------------
        def _static(self, name: str) -> None:
            path = STATIC_DIR / name
            try:
                body = path.read_bytes()
            except OSError:
                return self._send_error_json(f"缺少页面文件 {name}", 500)
            return self._send_bytes(body, CONTENT_TYPES.get(name, "application/octet-stream"))

    return Handler


def create_server(
    app: WebApp, *, host: str = HOST, port: int = DEFAULT_PORT, attempts: int = PORT_ATTEMPTS
) -> LocalHTTPServer:
    """只绑定本机地址；首选端口被占用时依次尝试后面的端口，最后交给系统分配。"""
    if host != HOST:
        raise ValueError(f"本地页面只允许监听 {HOST}，收到 {host!r}")
    handler = make_handler(app)
    candidates = [port + offset for offset in range(attempts)] if port else [0]
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            return LocalHTTPServer((host, candidate), handler)
        except OSError as exc:  # 端口被占用：换下一个
            last_error = exc
            continue
    try:
        return LocalHTTPServer((host, 0), handler)
    except OSError:  # pragma: no cover - 只在端口彻底不可用时
        if last_error is not None:
            raise last_error
        raise


def start_in_thread(
    app: WebApp, *, host: str = HOST, port: int = 0
) -> tuple[LocalHTTPServer, str, threading.Thread]:
    """测试/嵌入用：在后台线程启动服务，返回 (server, url, thread)。"""
    server = create_server(app, host=host, port=port, attempts=1)
    thread = threading.Thread(target=server.serve_forever, name="web-serve", daemon=True)
    thread.start()
    return server, url_for(server), thread


def url_for(server: LocalHTTPServer) -> str:
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}/"


def build_app(args: argparse.Namespace) -> WebApp:
    analysis_date = date.fromisoformat(args.analysis_date) if args.analysis_date else None
    return WebApp(allow_update=not args.no_update, analysis_date=analysis_date)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="web", description="投资分析系统本地页面（只监听 127.0.0.1，不访问外网）"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--no-update", action="store_true", help="与 ./ask --no-update 一致：回答前不尝试联网更新"
    )
    parser.add_argument(
        "--analysis-date", default=None, help="分析日 YYYY-MM-DD（复现用；默认今天）"
    )
    args = parser.parse_args(argv)

    app = build_app(args)
    server = create_server(app, port=args.port)
    url = url_for(server)
    print(f"投资分析系统页面：{url}", flush=True)
    print("只监听本机 127.0.0.1；不访问外网；关闭终端即停止。", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器也不影响服务
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


__all__ = [
    "DEFAULT_PORT",
    "HOST",
    "STATIC_DIR",
    "LocalHTTPServer",
    "build_app",
    "create_server",
    "main",
    "make_handler",
    "start_in_thread",
    "url_for",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
