#!/usr/bin/env python3
"""Seed deterministic demo data. Idempotent: clears CampusFlow tables first.

Run:  python backend/seed.py

Slots are generated for today plus the next 6 days so the demo always has
availability. Two historical tickets (H-1001, H-1002) are fixed, so new
tickets start at H-1003. No bookings are seeded: every booking on the
dashboard is a real transaction, and the first booking of a demo is S-01.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from database import (DAILY_SLOTS, FACILITIES, OWNERS, audit, connect,  # noqa: E402
                      init_db, sla_due)


def seed() -> None:
    conn = connect()
    init_db(conn)
    for table in ("audit_log", "bookings", "slots", "tickets",
                  "facilities", "users"):
        conn.execute(f"DELETE FROM {table}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now(timezone.utc).date()

    conn.executemany("INSERT INTO facilities (name, capacity) VALUES (?, ?)",
                     [("study room", 20), ("seminar room", 60),
                      ("sports hall", 100)])
    conn.executemany(
        "INSERT INTO users (name, block, phone) VALUES (?, ?, ?)",
        [("Aarav", "Block B", ""), ("Priya", "Block C", ""),
         ("Rahul", "Block A", ""), ("Warden", "Office", "")])
    # All slots start free. Nothing here looks like a real transaction:
    # bookings only ever come from the voice agent or API callers.
    for day in range(7):
        date = (today + timedelta(days=day)).strftime("%Y-%m-%d")
        for facility in FACILITIES:
            for slot in DAILY_SLOTS:
                conn.execute(
                    "INSERT INTO slots (facility, date, slot, is_booked)"
                    " VALUES (?, ?, ?, 0)", (facility, date, slot))
    conn.execute(
        "INSERT INTO tickets (ticket_id, reporter, category, location,"
        " description, priority, status, owner, created_at, updated_at,"
        " sla_due) VALUES ('H-1001', 'Priya', 'network',"
        " 'Block C Room 301', 'Wi-Fi dead since morning', 'P2',"
        " 'in_progress', ?, ?, ?, ?)",
        (OWNERS["network"], now, now, sla_due("P2", now)))
    conn.execute(
        "INSERT INTO tickets (ticket_id, reporter, category, location,"
        " description, priority, status, owner, created_at, updated_at,"
        " sla_due) VALUES ('H-1002', 'Rahul', 'cleaning',"
        " 'Block A corridor', 'Stained wall near lift', 'P3',"
        " 'resolved', ?, ?, ?, ?)",
        (OWNERS["cleaning"], now, now, sla_due("P3", now)))
    audit(conn, "seed", "create_ticket", "H-1001",
          "historical: network P2 at Block C Room 301")
    audit(conn, "seed", "create_ticket", "H-1002",
          "historical: cleaning P3 at Block A corridor")
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
              for t in ("tickets", "bookings", "slots", "audit_log",
                        "users")}
    conn.close()
    print(f"Seeded: {counts['tickets']} tickets, {counts['bookings']} "
          f"bookings, {counts['slots']} slots, {counts['audit_log']} "
          f"audit rows, {counts['users']} users.")


if __name__ == "__main__":
    seed()
