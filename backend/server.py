#!/usr/bin/env python3
"""CampusFlow tool backend: serves the five AssemblyAI http-tool contracts.

    python backend/server.py

Listens on 127.0.0.1:8000 (CAMPUSFLOW_PORT to change). Local dev uses
CAMPUSFLOW_BASE_URL=http://localhost:8000; publishing the agent needs the
public https URL of the deployed backend instead (see agents/campusflow.jsonc).
Standard library only.
"""

import json
import os
import sys
import traceback
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from database import connect, init_db  # noqa: E402
from handlers import (DATE_RE, book_facility, create_ticket,  # noqa: E402
                      escalate_ticket, get_ticket_status, get_time,
                      list_slots)
from queries import (escalated_tickets, open_tickets,  # noqa: E402
                     recent_audit, recent_bookings, recent_tickets)

DASHBOARD_DIR = HERE.parent / "dashboard"

# Exact paths only: no directory listing, no traversal.
STATIC_FILES = {
    "/dashboard": ("index.html", "text/html; charset=utf-8"),
    "/dashboard/": ("index.html", "text/html; charset=utf-8"),
    "/dashboard/style.css": ("style.css", "text/css; charset=utf-8"),
    "/dashboard/app.js": ("app.js",
                           "application/javascript; charset=utf-8"),
}


def send_json(handler: BaseHTTPRequestHandler, status: int,
              payload: dict) -> None:
    send_bytes(handler, status, json.dumps(payload).encode(),
               "application/json")


def send_bytes(handler: BaseHTTPRequestHandler, status: int, body: bytes,
               content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler: BaseHTTPRequestHandler):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return None
    if length <= 0 or length > 1_000_000:
        return None
    try:
        return json.loads(handler.rfile.read(length).decode())
    except (ValueError, UnicodeDecodeError):
        return None


def overview(conn, date: str) -> tuple:
    """Read-only bundle for the ops dashboard. One poll returns summary,
    tickets, bookings, audit, and slot availability. Never mutates."""
    from queries import available_slots
    from database import FACILITIES
    today = datetime.now(timezone.utc).date().strftime("%Y-%m-%d")
    slot_date = date.strip() if date else today
    if not DATE_RE.match(slot_date):
        return 400, {"success": False, "error": {
            "code": "INVALID_INPUT", "message": "date must be YYYY-MM-DD."}}
    tickets = recent_tickets(conn, 50)
    bookings = recent_bookings(conn, 50)
    audit = recent_audit(conn, 30)
    slots = {f: available_slots(conn, f, slot_date) for f in FACILITIES}
    free_today = sum(len(available_slots(conn, f, today))
                     for f in FACILITIES)
    return 200, {
        "ok": True,
        "slot_date": slot_date,
        "summary": {
            "open_tickets": len(open_tickets(conn)),
            "escalated_tickets": len(escalated_tickets(conn)),
            "todays_bookings": sum(1 for b in bookings
                                   if b["date"] == today),
            "free_slots_today": free_today,
        },
        "tickets": tickets,
        "bookings": bookings,
        "audit": audit,
        "slots": slots,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CampusFlow/1.0"

    def log_message(self, fmt, *args):  # noqa: N802
        sys.stderr.write("backend: " + fmt % args + "\n")

    def _route(self, method: str, path: str, query: dict,
               body, conn) -> tuple:
        if method == "GET" and path == "/health":
            return 200, {"ok": True}
        if method == "GET" and path == "/time":
            return get_time()
        if method == "GET" and path == "/api/overview":
            return overview(conn, query.get("date", ""))
        if method == "POST" and path == "/tickets":
            if not isinstance(body, dict):
                return 400, {"success": False, "error": {
                    "code": "INVALID_INPUT",
                    "message": "Request body must be JSON."}}
            return create_ticket(conn, body)
        if method == "GET" and path == "/tickets":
            return get_ticket_status(conn, query.get("ticket_id", ""))
        if method == "GET" and path == "/slots":
            return list_slots(conn, query.get("facility", ""),
                              query.get("date", ""))
        if method == "POST" and path == "/bookings":
            if not isinstance(body, dict):
                return 400, {"success": False, "error": {
                    "code": "INVALID_INPUT",
                    "message": "Request body must be JSON."}}
            return book_facility(conn, body)
        if method == "POST" and path == "/tickets/escalate":
            if not isinstance(body, dict):
                return 400, {"success": False, "error": {
                    "code": "INVALID_INPUT",
                    "message": "Request body must be JSON."}}
            return escalate_ticket(conn, body)
        return 404, {"success": False, "error": {
            "code": "INVALID_INPUT", "message": "Unknown endpoint."}}

    def _handle(self, method: str) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if method == "GET" and parsed.path in STATIC_FILES:
            name, content_type = STATIC_FILES[parsed.path]
            try:
                data = (DASHBOARD_DIR / name).read_bytes()
            except OSError:
                traceback.print_exc()
                data = b"Dashboard file missing."
                try:
                    send_bytes(self, 500, data, "text/plain")
                except BrokenPipeError:
                    pass
                return
            try:
                send_bytes(self, 200, data, content_type)
            except BrokenPipeError:
                pass
            return
        query = dict(urllib.parse.parse_qsl(parsed.query))
        body = read_json(self) if method == "POST" else None
        conn = connect()
        try:
            status, payload = self._route(method, parsed.path, query,
                                          body, conn)
        except BrokenPipeError:
            return
        except Exception:  # never leak internals to the client
            traceback.print_exc()
            status, payload = 500, {"success": False, "error": {
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong. Try again."}}
        finally:
            conn.close()
        try:
            send_json(self, status, payload)
        except BrokenPipeError:
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


def main() -> None:
    conn = connect()
    init_db(conn)
    # Ephemeral disks (Render free tier, containers) start empty: seed the
    # deterministic demo dataset on boot when asked and no tickets exist.
    # CAMPUSFLOW_SEED=1 enables it; existing data is never wiped.
    if os.environ.get("CAMPUSFLOW_SEED") == "1":
        count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        conn.close()
        if count == 0:
            import seed as seed_module
            seed_module.seed()
            conn = connect()
    conn.close()
    # Render injects PORT; local dev uses CAMPUSFLOW_PORT (default 8000).
    # Bind 127.0.0.1 locally, 0.0.0.0 when CAMPUSFLOW_HOST says so.
    port = int(os.environ.get("CAMPUSFLOW_PORT",
                              os.environ.get("PORT", "8000")))
    host = os.environ.get("CAMPUSFLOW_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"CampusFlow backend on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
