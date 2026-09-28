from __future__ import annotations

import argparse
import contextlib
import json
import sqlite3
import sys
import threading
import traceback
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, quote, urlparse

import core

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
DB_PATH = BASE_DIR / "stats.db"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PORT_ATTEMPTS = 12
MAX_BODY_BYTES = 8_000_000
IMPORT_MODES = ("append", "replace")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    side       TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
    trade_date TEXT    NOT NULL,
    price      REAL    NOT NULL CHECK (price > 0),
    note       TEXT    NOT NULL DEFAULT '',
    seq        INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_pair
    ON trades (code, side, trade_date, seq);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextlib.contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def fetch_trades(conn: sqlite3.Connection) -> list[core.Trade]:
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY trade_date DESC, seq DESC, id DESC"
    ).fetchall()
    return core.load_trades(rows)


def earliest_buy_date(conn: sqlite3.Connection, code: str) -> str | None:
    row = conn.execute(
        "SELECT MIN(trade_date) AS d FROM trades WHERE code = ? AND side = 'buy'",
        (code,),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def date_warning(conn: sqlite3.Connection, code: str, side: str, trade_date: str) -> str | None:
    if side != core.SELL:
        return None
    first_buy = earliest_buy_date(conn, code)
    if first_buy is None:
        return f"「{code}」尚无买入记录，该笔卖出将无法配对，也不会计入盈亏统计。"
    if trade_date < first_buy:
        return (
            f"「{code}」最早买入日期为 {first_buy}，"
            f"早于该日期的卖出无法配对，也不会计入盈亏统计。"
        )
    return None


def insert_trade(conn: sqlite3.Connection, data: dict) -> tuple[int, str | None]:
    next_seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM trades"
    ).fetchone()["n"]
    warning = date_warning(conn, data["code"], data["side"], data["trade_date"])
    cursor = conn.execute(
        "INSERT INTO trades (code, name, side, trade_date, price, note, seq, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            data["code"],
            data["name"],
            data["side"],
            data["trade_date"],
            data["price"],
            data["note"],
            next_seq,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    return int(cursor.lastrowid), warning


def update_trade(conn: sqlite3.Connection, trade_id: int, data: dict) -> str | None:
    exists = conn.execute("SELECT id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not exists:
        raise core.ValidationError(f"未找到要修改的交易记录（id={trade_id}）")
    warning = date_warning(conn, data["code"], data["side"], data["trade_date"])
    conn.execute(
        "UPDATE trades SET code = ?, name = ?, side = ?, trade_date = ?, price = ?,"
        " note = ? WHERE id = ?",
        (
            data["code"],
            data["name"],
            data["side"],
            data["trade_date"],
            data["price"],
            data["note"],
            trade_id,
        ),
    )
    return warning


def import_trades(
    conn: sqlite3.Connection,
    records: Sequence[dict],
    mode: str,
) -> None:
    """写入导入记录。mode=replace 时先清空现有数据。"""
    if mode == "replace":
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'trades'")

    start = conn.execute("SELECT COALESCE(MAX(seq), 0) AS n FROM trades").fetchone()["n"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO trades (code, name, side, trade_date, price, note, seq, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                item["code"],
                item["name"],
                item["side"],
                item["trade_date"],
                item["price"],
                item["note"],
                start + offset + 1,
                now,
            )
            for offset, item in enumerate(records)
        ],
    )


def split_import_body(body: Any) -> tuple[str, Any]:
    """拆出导入模式与数据体。

    两种写法都支持：
      {"mode": "replace", "payload": <导出文件内容>}
      <导出文件内容>            —— 整个请求体就是数据，模式默认 append
    """
    if isinstance(body, dict) and "payload" in body:
        mode = str(body.get("mode") or "").strip()
        return mode or "append", body["payload"]
    return "append", body


class Handler(BaseHTTPRequestHandler):
    server_version = "StockSim/1.0"
    protocol_version = "HTTP/1.1"
    verbose = False

    STATIC = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "application/javascript; charset=utf-8"),
        "/style.css": ("style.css", "text/css; charset=utf-8"),
    }

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def log_message(self, fmt: str, *args) -> None:
        if self.verbose:
            sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _dispatch(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if method == "GET":
                self._handle_get(path)
            elif method == "POST" and path == "/api/trades":
                self._handle_create()
            elif method == "POST" and path == "/api/import":
                self._handle_import()
            elif method == "PUT" and path.startswith("/api/trades/"):
                self._handle_update(self._trade_id(path))
            elif method == "DELETE" and path.startswith("/api/trades/"):
                self._handle_delete(self._trade_id(path))
            else:
                self._send_json({"error": "接口不存在"}, 404)
        except core.ValidationError as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                traceback.print_exc()
            self._send_json({"error": f"服务器内部错误：{exc}"}, 500)

    @staticmethod
    def _trade_id(path: str) -> int:
        raw = path.rsplit("/", 1)[-1]
        try:
            return int(raw)
        except ValueError:
            raise core.ValidationError("交易记录 id 必须是整数") from None

    def _handle_get(self, path: str) -> None:
        if path in self.STATIC:
            self._send_static(*self.STATIC[path])
        elif path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/api/trades":
            with db() as conn:
                trades = fetch_trades(conn)
            self._send_json({"trades": [core.trade_to_dict(t) for t in trades]})
        elif path == "/api/summary":
            raw_as_of = self._query_param("as_of")
            as_of = core.parse_date(raw_as_of) if raw_as_of else date.today()
            with db() as conn:
                trades = fetch_trades(conn)
            self._send_json(core.build_summary(trades, as_of))
        elif path == "/api/export":
            with db() as conn:
                trades = fetch_trades(conn)
            stamp = date.today().strftime("%Y%m%d")
            self._send_attachment(core.export_text(trades), f"trades-{stamp}.json")
        elif path == "/api/import-template":
            self._send_attachment(core.export_template_text(), "trades-template.json")
        else:
            self._send_json({"error": "页面不存在"}, 404)

    def _query_param(self, name: str) -> str | None:
        values = parse_qs(urlparse(self.path).query).get(name)
        return values[0] if values else None

    def _handle_create(self) -> None:
        data = core.normalize_trade(self._read_json())
        with db() as conn:
            trade_id, warning = insert_trade(conn, data)
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            trade = core.trade_from_mapping(row)
        self._send_json(
            {"trade": core.trade_to_dict(trade), "warning": warning}, 201
        )

    def _handle_update(self, trade_id: int) -> None:
        data = core.normalize_trade(self._read_json())
        with db() as conn:
            warning = update_trade(conn, trade_id, data)
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            trade = core.trade_from_mapping(row)
        self._send_json({"trade": core.trade_to_dict(trade), "warning": warning})

    def _handle_delete(self, trade_id: int) -> None:
        with db() as conn:
            cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            if cursor.rowcount == 0:
                raise core.ValidationError(f"未找到要删除的交易记录（id={trade_id}）")
        self._send_json({"deleted_id": trade_id})

    def _handle_import(self) -> None:
        mode, data = split_import_body(self._read_json(allow_list=True))
        if mode not in IMPORT_MODES:
            raise core.ValidationError("导入模式必须是 append（追加）或 replace（覆盖）")

        result = core.parse_import(data)
        if result.errors:
            self._send_json(
                {
                    "error": f"有 {len(result.errors)} 条记录无法解析，本次未导入任何数据",
                    "errors": result.errors,
                },
                400,
            )
            return

        with db() as conn:
            import_trades(conn, result.records, mode)
            trades = fetch_trades(conn)
        self._send_json(
            {
                "mode": mode,
                "imported": len(result.records),
                "total": len(trades),
                "errors": [],
                "warnings": core.collect_warnings(core.pair_trades(trades)),
            }
        )

    def _read_json(self, allow_list: bool = False):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise core.ValidationError("请求头 Content-Length 无效") from None
        if length <= 0:
            raise core.ValidationError("请求体不能为空")
        if length > MAX_BODY_BYTES:
            raise core.ValidationError("请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise core.ValidationError("请求体不是合法的 JSON") from exc
        if isinstance(payload, list):
            if not allow_list:
                raise core.ValidationError("请求体必须是 JSON 对象")
            return payload
        if not isinstance(payload, dict):
            raise core.ValidationError("请求体必须是 JSON 对象")
        return payload

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_attachment(self, text: str, filename: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename)}',
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename: str, content_type: str) -> None:
        target = (WEB_DIR / filename).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            self._send_json({"error": "静态资源缺失"}, 404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str, port: int, attempts: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    for candidate in range(port, port + attempts):
        try:
            return ThreadingHTTPServer((host, candidate), Handler), candidate
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"无法在 {host}:{port}-{port + attempts - 1} 绑定端口：{last_error}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="股票模拟交易系统（本地）")
    parser.add_argument("--host", default=DEFAULT_HOST, help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="起始端口，默认 8765")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--verbose", action="store_true", help="打印访问日志")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    Handler.verbose = args.verbose
    init_db()
    httpd, port = create_server(args.host, args.port, PORT_ATTEMPTS)
    url = f"http://{args.host}:{port}/"

    print("=" * 56)
    print("  股票模拟交易系统已启动")
    print(f"  访问地址：{url}")
    print(f"  数据文件：{DB_PATH}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)

    if not args.no_browser:
        opener = threading.Timer(0.5, webbrowser.open, (url,))
        opener.daemon = True
        opener.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
