#!/usr/bin/env python3
"""CampusFlow endpoint logic: the five tool contracts as pure functions.

Each handler takes a connection and validated inputs, and returns
(status_code, response_dict). The HTTP layer in server.py stays thin.
Error envelope is always {"success": False, "error": {"code", "message"}}.
"""

import re
import secrets
import sqlite3

from database import (CATEGORIES, FACILITIES, OWNERS, PRIORITIES,  # noqa: E402
                      TicketTransitionError, audit, immediate,
                      next_booking_id, next_ticket_id, sla_due,
                      transition_ticket, utcnow)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def err(code: str, message: str, status: int = 400) -> tuple:
    return status, {"success": False, "error": {"code": code,
                                               "message": message}}


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def create_ticket(conn: sqlite3.Connection, body: dict) -> tuple:
    if not _nonempty(body.get("reporter")):
        return err("INVALID_REQUESTER",
                   "A reporter name is required to log a ticket.", 400)
    required = ("category", "location", "description", "priority")
    for field in required:
        if not _nonempty(body.get(field)):
            return err("INVALID_INPUT", f"{field} is required.")
    category = body["category"].strip().lower()
    priority = body["priority"].strip().upper()
    if category not in CATEGORIES:
        return err("INVALID_INPUT",
                   f"category must be one of: {', '.join(CATEGORIES)}.")
    if priority not in PRIORITIES:
        return err("INVALID_INPUT", "priority must be P1, P2, or P3.")
    now = utcnow()
    ticket_id = next_ticket_id(conn)
    owner = OWNERS[category]
    with immediate(conn):
        conn.execute(
            "INSERT INTO tickets (ticket_id, reporter, category, location,"
            " description, priority, status, owner, created_at, updated_at,"
            " sla_due) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (ticket_id, body["reporter"].strip(), category,
             body["location"].strip(), body["description"].strip(),
             priority, owner, now, now, sla_due(priority, now)),
        )
        audit(conn, body["reporter"].strip(), "create_ticket", ticket_id,
              f"{category} {priority} at {body['location'].strip()}")
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?",
                       (ticket_id,)).fetchone()
    return 200, {"success": True, "ticket": {
        "ticket_id": row["ticket_id"], "status": row["status"],
        "owner": row["owner"], "priority": row["priority"],
        "sla_due": row["sla_due"], "created_at": row["created_at"]}}


def get_ticket_status(conn: sqlite3.Connection, ticket_id) -> tuple:
    if not _nonempty(ticket_id):
        return err("INVALID_INPUT", "ticket_id is required.")
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?",
                       (ticket_id.strip(),)).fetchone()
    if row is None:
        return err("TICKET_NOT_FOUND",
                   f"No ticket {ticket_id.strip()} was found.", 404)
    return 200, {"success": True, "ticket": {
        "ticket_id": row["ticket_id"], "status": row["status"],
        "owner": row["owner"], "priority": row["priority"],
        "updated_at": row["updated_at"], "sla_due": row["sla_due"]}}


def list_slots(conn: sqlite3.Connection, facility, date) -> tuple:
    if not _nonempty(facility) or not _nonempty(date):
        return err("INVALID_INPUT", "facility and date are required.")
    facility = facility.strip().lower()
    date = date.strip()
    if facility not in FACILITIES:
        return err("FACILITY_NOT_FOUND",
                   f"Unknown facility. Choose: {', '.join(FACILITIES)}.",
                   404)
    if not DATE_RE.match(date):
        return err("INVALID_INPUT", "date must be YYYY-MM-DD.")
    rows = conn.execute(
        "SELECT slot FROM slots WHERE facility = ? AND date = ?"
        " AND is_booked = 0 ORDER BY slot",
        (facility, date)).fetchall()
    return 200, {"success": True, "facility": facility, "date": date,
                 "available_slots": [r["slot"] for r in rows]}


def book_facility(conn: sqlite3.Connection, body: dict) -> tuple:
    if not _nonempty(body.get("requester")):
        return err("INVALID_REQUESTER",
                   "A requester name is required to book a facility.", 400)
    required = ("facility", "date", "slot")
    for field in required:
        if not _nonempty(body.get(field)):
            return err("INVALID_INPUT", f"{field} is required.")
    facility = body["facility"].strip().lower()
    date = body["date"].strip()
    slot = body["slot"].strip()
    requester = body["requester"].strip()
    if facility not in FACILITIES:
        return err("FACILITY_NOT_FOUND",
                   f"Unknown facility. Choose: {', '.join(FACILITIES)}.",
                   404)
    if not DATE_RE.match(date):
        return err("INVALID_INPUT", "date must be YYYY-MM-DD.")
    row = conn.execute(
        "SELECT is_booked FROM slots WHERE facility = ? AND date = ?"
        " AND slot = ?", (facility, date, slot)).fetchone()
    if row is None:
        return err("SLOT_UNAVAILABLE",
                   "That slot is not offered for this facility and date.",
                   404)
    if row["is_booked"]:
        return err("BOOKING_CONFLICT", "That slot is already booked.", 409)
    booking_id = next_booking_id(conn)
    pass_code = secrets.token_hex(2).upper()
    now = utcnow()
    with immediate(conn):
        locked = conn.execute(
            "SELECT is_booked FROM slots WHERE facility = ? AND date = ?"
            " AND slot = ?", (facility, date, slot)).fetchone()
        if locked is None:
            conn.rollback()
            return err("SLOT_UNAVAILABLE",
                       "That slot is not offered for this facility and date.",
                       404)
        if locked["is_booked"]:
            conn.rollback()
            return err("BOOKING_CONFLICT",
                       "That slot is already booked.", 409)
        conn.execute(
            "INSERT INTO bookings (booking_id, facility, date, slot,"
            " requester, pass_code, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'confirmed', ?)",
            (booking_id, facility, date, slot, requester, pass_code, now))
        conn.execute(
            "UPDATE slots SET is_booked = 1 WHERE facility = ? AND date = ?"
            " AND slot = ?", (facility, date, slot))
        audit(conn, requester, "book_facility", booking_id,
              f"{facility} {date} {slot}")
    return 200, {"success": True, "booking": {
        "booking_id": booking_id, "facility": facility, "date": date,
        "slot": slot, "pass_code": pass_code, "status": "confirmed"}}


def escalate_ticket(conn: sqlite3.Connection, body: dict) -> tuple:
    if not _nonempty(body.get("ticket_id")):
        return err("INVALID_INPUT", "ticket_id is required.")
    if not _nonempty(body.get("reason")):
        return err("INVALID_INPUT", "reason is required.")
    ticket_id = body["ticket_id"].strip()
    reason = body["reason"].strip()
    row = conn.execute("SELECT priority, reporter FROM tickets"
                       " WHERE ticket_id = ?", (ticket_id,)).fetchone()
    if row is None:
        return err("TICKET_NOT_FOUND", f"No ticket {ticket_id} was found.",
                   404)
    escalated_to = "warden" if row["priority"] == "P1" else "facility manager"
    now = utcnow()
    try:
        with immediate(conn):
            previous = transition_ticket(
                conn, ticket_id, "escalated", row["reporter"],
                "escalate_ticket",
                f"{row['priority']} escalation to {escalated_to}: {reason}")
    except TicketTransitionError as exc:
        return err("INVALID_INPUT", str(exc))
    return 200, {"success": True, "escalation": {
        "ticket_id": ticket_id, "previous_status": previous,
        "new_status": "escalated", "escalated_to": escalated_to,
        "escalated_at": now,
        "note": f"Escalation recorded for {escalated_to}."
                " No external notification is sent in this prototype."}}
