#!/usr/bin/env python3
"""CampusFlow storage: SQLite schema, id generation, and shared constants.

Standard library only (sqlite3). The database file lives next to this
module; set CAMPUSFLOW_DB to point elsewhere (used by the test script).
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

CATEGORIES = ("plumbing", "electrical", "hvac", "network",
              "cleaning", "furniture", "other")
PRIORITIES = ("P1", "P2", "P3")
STATUSES = ("open", "assigned", "in_progress", "resolved", "closed",
            "escalated")
FACILITIES = ("study room", "seminar room", "sports hall")
DAILY_SLOTS = ("09:00-10:00", "11:00-12:00", "17:00-18:00", "19:00-20:00")

# Deterministic owner per category, spoken back to the caller.
OWNERS = {
    "plumbing": "plumber on duty",
    "electrical": "electrician on duty",
    "hvac": "HVAC technician",
    "network": "network support",
    "cleaning": "housekeeping",
    "furniture": "facilities",
    "other": "facilities desk",
}

# SLA per priority, in hours.
SLA_HOURS = {"P1": 4, "P2": 24, "P3": 72}

TICKET_PREFIX = "H-"
TICKET_START = 1001
BOOKING_PREFIX = "S-"


def db_path() -> Path:
    return Path(os.environ.get("CAMPUSFLOW_DB", HERE / "campusflow.db"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    block TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    reporter TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sla_due TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facilities (
    name TEXT PRIMARY KEY,
    capacity INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facility TEXT NOT NULL REFERENCES facilities(name),
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    is_booked INTEGER NOT NULL DEFAULT 0,
    UNIQUE (facility, date, slot)
);
CREATE TABLE IF NOT EXISTS bookings (
    booking_id TEXT PRIMARY KEY,
    facility TEXT NOT NULL REFERENCES facilities(name),
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    requester TEXT NOT NULL,
    pass_code TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    tool TEXT NOT NULL,
    ref_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sla_due(priority: str, now: str) -> str:
    base = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    due = base + timedelta(hours=SLA_HOURS[priority])
    return due.strftime("%Y-%m-%dT%H:%M:%SZ")


def next_ticket_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT ticket_id FROM tickets ORDER BY ticket_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return f"{TICKET_PREFIX}{TICKET_START}"
    try:
        num = int(row["ticket_id"].split("-", 1)[1])
    except (IndexError, ValueError):
        num = TICKET_START - 1
    return f"{TICKET_PREFIX}{num + 1}"


def next_booking_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT booking_id FROM bookings ORDER BY booking_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return f"{BOOKING_PREFIX}01"
    try:
        num = int(row["booking_id"].split("-", 1)[1])
    except (IndexError, ValueError):
        num = 0
    return f"{BOOKING_PREFIX}{num + 1:02d}"


def audit(conn: sqlite3.Connection, actor: str, tool: str,
          ref_id: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, actor, tool, ref_id, detail)"
        " VALUES (?, ?, ?, ?, ?)",
        (utcnow(), actor, tool, ref_id, detail),
    )


# --- ticket lifecycle -------------------------------------------------------
# open -> assigned -> in_progress -> resolved -> closed, with escalation
# allowed from open / assigned / in_progress. Closed is terminal; there is
# no reopen workflow yet, so closed -> anything is rejected.

TRANSITIONS = {
    "open": ("assigned", "in_progress", "resolved", "escalated", "closed"),
    "assigned": ("in_progress", "resolved", "escalated", "closed"),
    "in_progress": ("resolved", "escalated", "closed"),
    "escalated": ("assigned", "in_progress", "resolved", "closed"),
    "resolved": ("closed",),
    "closed": (),
}


class TicketTransitionError(ValueError):
    """Raised when a ticket status change breaks the lifecycle."""


def transition_ticket(conn: sqlite3.Connection, ticket_id: str,
                      new_status: str, actor: str, tool: str,
                      detail: str = "") -> str:
    """Move a ticket to new_status after validating the lifecycle.

    Writes an audit row. Returns the previous status. Committing is left
    to the caller. Raises LookupError for unknown tickets and
    TicketTransitionError for illegal moves."""
    if new_status not in STATUSES:
        raise TicketTransitionError(f"Unknown status '{new_status}'.")
    row = conn.execute("SELECT status, reporter FROM tickets"
                       " WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise LookupError(ticket_id)
    previous = row["status"]
    if new_status not in TRANSITIONS[previous]:
        raise TicketTransitionError(
            f"Cannot move ticket {ticket_id} from '{previous}'"
            f" to '{new_status}'.")
    now = utcnow()
    conn.execute("UPDATE tickets SET status = ?, updated_at = ?"
                 " WHERE ticket_id = ?", (new_status, now, ticket_id))
    audit(conn, actor or row["reporter"], tool, ticket_id,
          detail or f"{previous} -> {new_status}")
    return previous


# --- transactions -----------------------------------------------------------

@contextmanager
def immediate(conn: sqlite3.Connection):
    """SERIALIZE a read-check-write sequence (e.g. slot booking) so two
    concurrent requests cannot both win. Commits on success, rolls back
    on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
