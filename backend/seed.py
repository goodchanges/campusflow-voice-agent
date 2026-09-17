#!/usr/bin/env python3
"""Seed deterministic demo data. Idempotent: clears CampusFlow tables first.

Run:  python backend/seed.py

Slots are generated for today plus the next 6 days so the demo always has
availability. Two historical tickets (H-1001, H-1002) and one booking (S-01)
are fixed, so new tickets start at H-1003 and bookings at S-02.
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
    # One booking lives on tomorrow's first study-room slot.
    booked_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    for day in range(7):
        date = (today + timedelta(days=day)).strftime("%Y-%m-%d")
        for facility in FACILITIES:
            for slot in DAILY_SLOTS:
                taken = (facility == "study room" and date == booked_date
                         and slot == DAILY_SLOTS[0])
                conn.execute(
                    "INSERT INTO slots (facility, date, slot, is_booked)"
                    " VALUES (?, ?, ?, ?)", (facility, date, slot,
                                             1 if taken else 0))
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
    conn.execute(
        "INSERT INTO bookings (booking_id, facility, date, slot,"
        " requester, pass_code, status, created_at)"
        " VALUES ('S-01', 'study room', ?, ?, 'Priya', 'A1B2',"
        " 'confirmed', ?)", (booked_date, DAILY_SLOTS[0], now))
    audit(conn, "seed", "create_ticket", "H-1001",
          "historical: network P2 at Block C Room 301")
    audit(conn, "seed", "create_ticket", "H-1002",
          "historical: cleaning P3 at Block A corridor")
    audit(conn, "seed", "book_facility", "S-01",
          f"study room {booked_date} {DAILY_SLOTS[0]}")
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
