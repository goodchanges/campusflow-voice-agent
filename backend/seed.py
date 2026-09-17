#!/usr/bin/env python3
"""Seed deterministic demo data. Idempotent: clears CampusFlow tables first.

Run:  python backend/seed.py

Seeds lookup data only: facilities, users, and slots for today plus the
next 6 days (84 total, all free) so the demo always has availability.
Zero tickets and zero bookings are seeded on purpose: every transaction on
the dashboard is a real one. The first ticket of a demo is H-1001 and the
first booking is S-01.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from database import (DAILY_SLOTS, FACILITIES, connect,  # noqa: E402
                      init_db)


def seed() -> None:
    conn = connect()
    init_db(conn)
    for table in ("audit_log", "bookings", "slots", "tickets",
                  "facilities", "users"):
        conn.execute(f"DELETE FROM {table}")
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
    conn.commit()
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
