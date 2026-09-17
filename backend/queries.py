#!/usr/bin/env python3
"""CampusFlow read queries. Reusable by the future dashboard (Phase 7) and
any other reader. All return plain dicts/lists, safe to json.dumps.
Standard library only.
"""

import sqlite3

OPEN_STATUSES = ("open", "assigned", "in_progress")


def _all(cur) -> list:
    return [dict(row) for row in cur.fetchall()]


def recent_tickets(conn: sqlite3.Connection, limit: int = 20) -> list:
    return _all(conn.execute(
        "SELECT * FROM tickets ORDER BY created_at DESC, ticket_id DESC"
        " LIMIT ?", (limit,)))


def open_tickets(conn: sqlite3.Connection) -> list:
    return _all(conn.execute(
        "SELECT * FROM tickets WHERE status IN ('open', 'assigned',"
        " 'in_progress') ORDER BY created_at DESC"))


def escalated_tickets(conn: sqlite3.Connection) -> list:
    return _all(conn.execute(
        "SELECT * FROM tickets WHERE status = 'escalated'"
        " ORDER BY created_at DESC"))


def recent_bookings(conn: sqlite3.Connection, limit: int = 20) -> list:
    return _all(conn.execute(
        "SELECT * FROM bookings ORDER BY date DESC, slot DESC"
        " LIMIT ?", (limit,)))


def available_slots(conn: sqlite3.Connection, facility: str,
                    date: str) -> list:
    return [r["slot"] for r in conn.execute(
        "SELECT slot FROM slots WHERE facility = ? AND date = ?"
        " AND is_booked = 0 ORDER BY slot", (facility, date))]


def recent_audit(conn: sqlite3.Connection, limit: int = 50) -> list:
    return _all(conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)))


def find_user(conn: sqlite3.Connection, name: str):
    row = conn.execute("SELECT * FROM users WHERE lower(name) = lower(?)",
                       (name.strip(),)).fetchone()
    return dict(row) if row else None


def list_users(conn: sqlite3.Connection) -> list:
    return _all(conn.execute("SELECT * FROM users ORDER BY name"))
