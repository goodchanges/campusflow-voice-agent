#!/usr/bin/env python3
"""CampusFlow backend tests. Standard library only.

Run:  python backend/test_backend.py

Starts server.py as a subprocess on 127.0.0.1:18001 with a temp database,
seeds it, and exercises all five endpoints plus both demo scenarios.
Exit 0 = all pass, 1 = any failure.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import database  # noqa: E402
import queries  # noqa: E402
import seed  # noqa: E402
PORT = 18001
BASE = f"http://127.0.0.1:{PORT}"
TOMORROW = (datetime.now(timezone.utc).date()
            + timedelta(days=1)).strftime("%Y-%m-%d")

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"[{PASS if cond else FAIL}] {name}"
          + (f" — {detail}" if detail and not cond else ""))


def get_raw(path: str) -> tuple:
    req = urllib.request.Request(BASE + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return (res.status,
                    res.headers.get_content_type(),
                    res.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get_content_type(), err.read().decode()


def call(method: str, path: str, body=None) -> tuple:
    parts = urllib.parse.urlsplit(path)
    selector = parts.path + ("?" + urllib.parse.quote(parts.query, safe="=&")
                             if parts.query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + selector, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode())


def main() -> int:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["CAMPUSFLOW_DB"] = tmp.name
    env = dict(os.environ, CAMPUSFLOW_DB=tmp.name,
               CAMPUSFLOW_PORT=str(PORT))
    seeded_proc = subprocess.run([sys.executable, str(HERE / "seed.py")],
                                 capture_output=True, text=True, env=env,
                                 cwd=ROOT)
    if seeded_proc.returncode != 0:
        print(seeded_proc.stderr)
        return 1
    server = subprocess.Popen([sys.executable, str(HERE / "server.py")],
                              env=env, cwd=ROOT,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                if call("GET", "/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            print("server did not start")
            return 1

        # 10. deterministic seed data
        s, seeded = call("GET", "/tickets?ticket_id=H-1001")
        check("seed ticket H-1001", s == 200
              and seeded["ticket"]["status"] == "in_progress")
        s, b = call("GET", "/slots?facility=study room&date=" + TOMORROW)
        check("seed booking blocks one slot", s == 200
              and b["available_slots"] == ["11:00-12:00", "17:00-18:00",
                                           "19:00-20:00"],
              json.dumps(b))

        # 1. create_ticket (P1)
        s, created = call("POST", "/tickets", {
            "reporter": "Aarav", "category": "plumbing",
            "location": "Block B second floor water cooler",
            "description": "Water spreading near stairs", "priority": "P1"})
        t = created.get("ticket", {})
        check("create_ticket", s == 200 and t.get("ticket_id") == "H-1003"
              and t.get("status") == "open"
              and t.get("owner") == "plumber on duty"
              and t.get("priority") == "P1"
              and all(k in t for k in ("sla_due", "created_at")),
              json.dumps(created))

        # 2. get_ticket_status
        s, got = call("GET", "/tickets?ticket_id=H-1003")
        check("get_ticket_status", s == 200
              and got["ticket"]["status"] == "open"
              and got["ticket"]["owner"] == "plumber on duty",
              json.dumps(got))

        # 3. list_slots
        s, slots = call("GET", "/slots?facility=study room&date=" + TOMORROW)
        check("list_slots", s == 200 and len(slots["available_slots"]) == 3,
              json.dumps(slots))

        # 4. book_facility
        pick = slots["available_slots"][0]
        s, booked = call("POST", "/bookings", {
            "facility": "study room", "date": TOMORROW, "slot": pick,
            "requester": "Aarav"})
        bk = booked.get("booking", {})
        check("book_facility", s == 200 and bk.get("booking_id") == "S-02"
              and bk.get("status") == "confirmed"
              and len(bk.get("pass_code", "")) == 4, json.dumps(booked))

        # list again: booked slot gone (scenario B tail)
        s, slots2 = call("GET", "/slots?facility=study room&date=" + TOMORROW)
        check("slot no longer listed", s == 200 and pick not in
              slots2["available_slots"], json.dumps(slots2))

        # 5. booking conflict
        s, conflict = call("POST", "/bookings", {
            "facility": "study room", "date": TOMORROW, "slot": pick,
            "requester": "Raju"})
        check("booking conflict", s == 409 and conflict["error"]["code"]
              == "BOOKING_CONFLICT", json.dumps(conflict))

        # 6. escalate_ticket (P1 -> warden)
        s, esc = call("POST", "/tickets/escalate", {
            "ticket_id": "H-1003", "reason": "water spreading near stairs"})
        e = esc.get("escalation", {})
        check("escalate_ticket", s == 200
              and e.get("previous_status") == "open"
              and e.get("new_status") == "escalated"
              and e.get("escalated_to") == "warden"
              and "escalated_at" in e, json.dumps(esc))

        # scenario A tail: status reads escalated
        s, got2 = call("GET", "/tickets?ticket_id=H-1003")
        check("status after escalate", s == 200
              and got2["ticket"]["status"] == "escalated", json.dumps(got2))

        # 7. ticket not found
        s, nf = call("GET", "/tickets?ticket_id=H-9999")
        check("ticket not found", s == 404 and nf["error"]["code"]
              == "TICKET_NOT_FOUND", json.dumps(nf))
        s, nf2 = call("POST", "/tickets/escalate", {
            "ticket_id": "H-9999", "reason": "x"})
        check("escalate unknown ticket", s == 404 and nf2["error"]["code"]
              == "TICKET_NOT_FOUND", json.dumps(nf2))

        # 8. invalid input
        s, bad = call("POST", "/tickets", {"reporter": "Aarav"})
        check("missing fields", s == 400 and bad["error"]["code"]
              == "INVALID_INPUT", json.dumps(bad))
        s, bad2 = call("POST", "/tickets", {
            "reporter": "A", "category": "rocket", "location": "B",
            "description": "d", "priority": "P9"})
        check("bad category/priority", s == 400 and bad2["error"]["code"]
              == "INVALID_INPUT", json.dumps(bad2))
        s, bad3 = call("GET", "/tickets")
        check("missing ticket_id", s == 400 and bad3["error"]["code"]
              == "INVALID_INPUT", json.dumps(bad3))
        s, bad4 = call("GET", "/slots?facility=nope&date=" + TOMORROW)
        check("unknown facility", s == 404 and bad4["error"]["code"]
              == "FACILITY_NOT_FOUND", json.dumps(bad4))

        # P1 escalation language: recorded, not "notified"
        s, p2 = call("POST", "/tickets", {
            "reporter": "Raju", "category": "electrical",
            "location": "Block A Room 102",
            "description": "Socket sparking lightly", "priority": "P2"})
        p2id = p2["ticket"]["ticket_id"]
        s, p2esc = call("POST", "/tickets/escalate", {
            "ticket_id": p2id, "reason": "needs manager sign-off"})
        check("P2 escalates to facility manager",
              s == 200 and p2esc["escalation"]["escalated_to"]
              == "facility manager" and "No external notification"
              in p2esc["escalation"]["note"], json.dumps(p2esc))

        # escalate from terminal states is rejected
        s, r = call("POST", "/tickets/escalate", {
            "ticket_id": "H-1002", "reason": "please reopen"})
        check("escalate resolved rejected", s == 400 and r["error"]["code"]
              == "INVALID_INPUT", json.dumps(r))
        s, r2 = call("POST", "/tickets/escalate", {
            "ticket_id": "H-1003", "reason": "again"})
        check("escalate escalated rejected", s == 400 and r2["error"]["code"]
              == "INVALID_INPUT", json.dumps(r2))

        # invalid requester
        s, ir = call("POST", "/tickets", {
            "reporter": "  ", "category": "plumbing", "location": "B",
            "description": "d", "priority": "P3"})
        check("empty reporter", s == 400 and ir["error"]["code"]
              == "INVALID_REQUESTER", json.dumps(ir))
        s, ir2 = call("POST", "/bookings", {
            "facility": "study room", "date": TOMORROW,
            "slot": "17:00-18:00", "requester": ""})
        check("empty requester", s == 400 and ir2["error"]["code"]
              == "INVALID_REQUESTER", json.dumps(ir2))

        # nonexistent facility / slot
        s, nf3 = call("POST", "/bookings", {
            "facility": "pool", "date": TOMORROW,
            "slot": "17:00-18:00", "requester": "Aarav"})
        check("book unknown facility", s == 404 and nf3["error"]["code"]
              == "FACILITY_NOT_FOUND", json.dumps(nf3))
        s, ns = call("POST", "/bookings", {
            "facility": "study room", "date": TOMORROW,
            "slot": "25:00-26:00", "requester": "Aarav"})
        check("book unknown slot", s == 404 and ns["error"]["code"]
              == "SLOT_UNAVAILABLE", json.dumps(ns))

        # concurrent double-booking: exactly one winner
        outcomes = []

        def race(i):
            outcomes.append(call("POST", "/bookings", {
                "facility": "seminar room", "date": TOMORROW,
                "slot": "11:00-12:00", "requester": f"Racer{i}"})[0])

        threads = [threading.Thread(target=race, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        check("concurrent double-book", outcomes.count(200) == 1
              and outcomes.count(409) == 7, str(sorted(outcomes)))

        # lifecycle rules, exercised directly on a fresh ticket
        s, fresh = call("POST", "/tickets", {
            "reporter": "Test", "category": "furniture",
            "location": "Block D Room 001", "description": "Wobbly chair",
            "priority": "P3"})
        fresh_id = fresh["ticket"]["ticket_id"]
        conn = database.connect()
        try:
            try:
                database.transition_ticket(conn, "H-1002", "open", "test",
                                           "test")
                check("closed->open rejected", False, "no error raised")
            except database.TicketTransitionError:
                check("closed->open rejected", True)
            chain = ["assigned", "in_progress", "resolved", "closed"]
            prev = "open"
            ok = True
            try:
                for nxt in chain:
                    got_prev = database.transition_ticket(
                        conn, fresh_id, nxt, "test", "test")
                    ok = ok and got_prev == prev
                    prev = nxt
                conn.commit()
            except database.TicketTransitionError as exc:
                ok = False
                print("chain failed:", exc)
            check("valid lifecycle chain", ok and prev == "closed")
        finally:
            conn.close()

        # 9. audit log, per action
        conn = sqlite3.connect(tmp.name)
        tools = [r[0] for r in conn.execute(
            "SELECT tool FROM audit_log WHERE ref_id IN ('H-1003', 'S-02')"
            " ORDER BY id")]
        conn.close()
        check("audit log rows", tools == ["create_ticket", "book_facility",
                                          "escalate_ticket"], str(tools))
        conn = sqlite3.connect(tmp.name)
        per_action = {t: conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE tool = ?", (t,)
            ).fetchone()[0] for t in ("create_ticket", "book_facility",
                                      "escalate_ticket")}
        conn.close()
        check("audit row after create_ticket", per_action["create_ticket"]
              >= 1, str(per_action))
        check("audit row after booking", per_action["book_facility"] >= 1,
              str(per_action))
        check("audit row after escalation", per_action["escalate_ticket"]
              >= 1, str(per_action))

        # foreign-key integrity at the DB layer
        conn = database.connect()
        try:
            conn.execute(
                "INSERT INTO bookings (booking_id, facility, date, slot,"
                " requester, pass_code, status, created_at)"
                " VALUES ('S-99', 'pool', '2026-01-01', 'x', 't', 'y',"
                " 'confirmed', 'z')")
            check("booking FK rejects bad facility", False, "no error")
        except sqlite3.IntegrityError:
            check("booking FK rejects bad facility", True)
        finally:
            conn.rollback()
            conn.close()
        conn = database.connect()
        try:
            conn.execute("INSERT INTO slots (facility, date, slot)"
                         " VALUES ('pool', '2026-01-01', 'x')")
            check("slot FK rejects bad facility", False, "no error")
        except sqlite3.IntegrityError:
            check("slot FK rejects bad facility", True)
        finally:
            conn.rollback()
            conn.close()

        # dashboard-ready reads
        conn = database.connect()
        try:
            check("find_user", (queries.find_user(conn, "aarav") or {})
                  .get("block") == "Block B")
            check("open_tickets", any(
                t["ticket_id"] == "H-1001"
                for t in queries.open_tickets(conn)))
            check("escalated_tickets", any(
                t["ticket_id"] == p2id
                for t in queries.escalated_tickets(conn)))
            check("recent_audit", len(queries.recent_audit(conn)) >= 3)
            check("recent_bookings", any(
                b["booking_id"] == "S-01"
                for b in queries.recent_bookings(conn)))
        finally:
            conn.close()

        # dashboard: static routes
        s, ctype, html = get_raw("/dashboard")
        check("dashboard loads", s == 200 and ctype == "text/html"
              and "CampusFlow Ops" in html and "app.js" in html, ctype)
        s, ctype, _ = get_raw("/dashboard/style.css")
        check("dashboard css", s == 200 and ctype == "text/css", ctype)
        s, ctype, js = get_raw("/dashboard/app.js")
        check("dashboard js", s == 200 and "javascript" in ctype
              and "/api/overview" in js, ctype)

        # dashboard: read API
        ov = call("GET", "/api/overview")[1]
        check("overview shape",
              all(k in ov for k in ("ok", "summary", "tickets",
                                    "bookings", "audit", "slots"))
              and all(k in ov["summary"] for k in ("open_tickets",
                      "escalated_tickets", "todays_bookings",
                      "free_slots_today")), str(sorted(ov)))
        check("overview data",
              any(t["ticket_id"] == "H-1001" for t in ov["tickets"])
              and any(b["booking_id"] == "S-01" for b in ov["bookings"])
              and len(ov["audit"]) >= 3
              and len(ov["slots"]["study room"]) >= 1, json.dumps(
                  ov["summary"]))

        # polling refresh: new ticket appears on next poll
        call("POST", "/tickets", {"reporter": "Dash", "category": "hvac",
                                  "location": "Block D",
                                  "description": "No cooling",
                                  "priority": "P2"})
        ov2 = call("GET", "/api/overview")[1]
        check("overview refreshes",
              any(t["reporter"] == "Dash" for t in ov2["tickets"])
              and ov2["summary"]["open_tickets"]
              == ov["summary"]["open_tickets"] + 1,
              json.dumps(ov2["summary"]))

        # bad date + unknown routes stay clean JSON, no traceback
        s, bad_date = call("GET", "/api/overview?date=tomorrow")
        check("overview bad date", s == 400 and bad_date["error"]["code"]
              == "INVALID_INPUT", json.dumps(bad_date))
        s, _, trav = get_raw("/dashboard/../backend/server.py")
        check("no path traversal", s == 404 and "def " not in trav, trav[:80])
        s, nope = call("GET", "/nope")
        check("unknown route clean", s == 404 and nope["success"] is False
              and "Traceback" not in json.dumps(nope), json.dumps(nope))

        # seed reset is deterministic (runs last: it wipes test data)
        seed.seed()
        seed.seed()
        conn = sqlite3.connect(tmp.name)
        n_t = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        ids = [r[0] for r in conn.execute(
            "SELECT ticket_id FROM tickets ORDER BY ticket_id")]
        has_s01 = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE booking_id = 'S-01'"
            ).fetchone()[0]
        n_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        check("seed reset deterministic",
              n_t == 2 and ids == ["H-1001", "H-1002"] and has_s01 == 1
              and n_users == 4, f"tickets={ids} users={n_users}")
    finally:
        server.terminate()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
