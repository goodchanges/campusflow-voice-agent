/* CampusFlow Ops dashboard. Read-only: polls /api/overview every 2.5s
   and re-renders. Rows never seen before are highlighted briefly so the
   audience can tell the voice agent just did something. No frameworks. */
(function () {
  "use strict";

  var POLL_MS = 2500;
  var seenTickets = {};
  var seenBookings = {};
  var seenAudit = {};
  var firstLoad = true;

  var els = {};
  ["status-dot", "status-text", "clock", "updated-pill", "offline-banner",
   "card-open", "card-escalated", "card-bookings", "card-slots",
   "ticket-count", "booking-count", "ticket-table", "booking-table",
   "audit-feed", "slot-facility", "slot-date", "slot-list"
  ].forEach(function (id) { els[id] = document.getElementById(id); });

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function shortTs(iso) {
    // "2026-09-17T07:17:38Z" -> "17 Sep 07:17"
    var d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getUTCDate() + " " + months[d.getUTCMonth()] + " " +
           p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
  }

  function priBadge(p) {
    return '<span class="badge ' + p.toLowerCase() + '">' + esc(p) + "</span>";
  }

  function statusBadge(s) {
    var cls = "st-muted";
    if (s === "open") cls = "st-open";
    else if (s === "escalated") cls = "st-escalated";
    else if (s === "resolved" || s === "closed") cls = "st-ok";
    return '<span class="badge ' + cls + '">' + esc(s) + "</span>";
  }

  function setOnline(online) {
    els["status-dot"].className = "dot" + (online ? "" : " offline");
    els["status-text"].textContent = online ? "Backend Online" : "Backend Offline";
    els["offline-banner"].hidden = online;
  }

  function flashUpdated() {
    els["updated-pill"].hidden = false;
    setTimeout(function () { els["updated-pill"].hidden = true; }, 4000);
  }

  function renderTickets(tickets) {
    var tb = els["ticket-table"].querySelector("tbody");
    var now = Date.now();
    var html = tickets.map(function (t) {
      var fresh = !firstLoad && !seenTickets[t.ticket_id];
      seenTickets[t.ticket_id] = true;
      var overdue = ["open", "assigned", "in_progress", "escalated"]
        .indexOf(t.status) >= 0 && Date.parse(t.sla_due) < now;
      return '<tr class="' + (fresh ? "fresh" : "") + '">' +
        '<td class="id">' + esc(t.ticket_id) + "</td>" +
        "<td>" + priBadge(t.priority) + "</td>" +
        "<td>" + esc(t.category) + "</td>" +
        "<td>" + esc(t.location) + "</td>" +
        "<td>" + statusBadge(t.status) + "</td>" +
        "<td>" + esc(t.owner) + "</td>" +
        '<td class="' + (overdue ? "sla-overdue" : "") + '">' +
          shortTs(t.sla_due) + (overdue ? " OVERDUE" : "") + "</td>" +
        "<td>" + shortTs(t.created_at) + "</td></tr>";
    }).join("");
    tb.innerHTML = html ||
      '<tr><td colspan="8" class="empty-note">No tickets yet.</td></tr>';
    els["ticket-count"].textContent = "(" + tickets.length + ")";
    return !firstLoad && html.indexOf('class="fresh"') >= 0;
  }

  function renderBookings(bookings) {
    var tb = els["booking-table"].querySelector("tbody");
    var html = bookings.map(function (b) {
      var fresh = !firstLoad && !seenBookings[b.booking_id];
      seenBookings[b.booking_id] = true;
      return '<tr class="' + (fresh ? "fresh" : "") + '">' +
        '<td class="id">' + esc(b.booking_id) + "</td>" +
        "<td>" + esc(b.facility) + "</td>" +
        "<td>" + esc(b.date) + "</td>" +
        "<td>" + esc(b.slot) + "</td>" +
        "<td>" + esc(b.requester) + "</td>" +
        "<td>" + esc(b.pass_code) + "</td>" +
        "<td>" + statusBadge(b.status) + "</td></tr>";
    }).join("");
    tb.innerHTML = html ||
      '<tr><td colspan="7" class="empty-note">No bookings yet.</td></tr>';
    els["booking-count"].textContent = "(" + bookings.length + ")";
    return !firstLoad && html.indexOf('class="fresh"') >= 0;
  }

  function renderAudit(audit) {
    var html = audit.map(function (a) {
      var fresh = !firstLoad && !seenAudit[a.id];
      seenAudit[a.id] = true;
      return '<li class="' + (fresh ? "fresh" : "") + '">' +
        '<span class="tool">' + esc(a.tool) + "</span> &rarr; " +
        '<span class="ref">' + esc(a.ref_id) + "</span> &mdash; " +
        esc(a.detail) +
        "<time>" + esc(a.actor) + " &middot; " + shortTs(a.ts) + "</time></li>";
    }).join("");
    els["audit-feed"].innerHTML = html || "<li>No activity yet.</li>";
    return !firstLoad && html.indexOf('class="fresh"') >= 0;
  }

  function renderSlots(slots, slotDate) {
    var fac = els["slot-facility"].value;
    var list = slots[fac] || [];
    els["slot-list"].innerHTML = list.length
      ? list.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("")
      : '<li class="empty">No free slots for ' + esc(fac) +
        " on " + esc(slotDate) + ".</li>";
  }

  function apiUrl() {
    var fac = els["slot-facility"].value;
    var date = els["slot-date"].value;
    var q = date ? "?date=" + encodeURIComponent(date) : "";
    return "/api/overview" + q + (q ? "&" : "?") +
      "facility=" + encodeURIComponent(fac);
  }

  var lastSlots = null;
  var lastSlotDate = null;

  function poll() {
    fetch(apiUrl(), { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("http " + res.status);
        return res.json();
      })
      .then(function (data) {
        setOnline(true);
        var changed = false;
        els["card-open"].textContent = data.summary.open_tickets;
        els["card-escalated"].textContent = data.summary.escalated_tickets;
        els["card-bookings"].textContent = data.summary.todays_bookings;
        els["card-slots"].textContent = data.summary.free_slots_today;
        changed = renderTickets(data.tickets) || changed;
        changed = renderBookings(data.bookings) || changed;
        changed = renderAudit(data.audit) || changed;
        lastSlots = data.slots;
        lastSlotDate = data.slot_date;
        renderSlots(data.slots, data.slot_date);
        var d = new Date();
        function p(n) { return (n < 10 ? "0" : "") + n; }
        els.clock.textContent = "Updated " + p(d.getHours()) + ":" +
          p(d.getMinutes()) + ":" + p(d.getSeconds());
        if (changed) flashUpdated();
        firstLoad = false;
      })
      .catch(function () { setOnline(false); });
  }

  function todayStr() {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }

  els["slot-date"].value = todayStr();
  els["slot-facility"].addEventListener("change", poll);
  els["slot-date"].addEventListener("change", poll);
  poll();
  setInterval(poll, POLL_MS);
})();
