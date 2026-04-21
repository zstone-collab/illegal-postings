"""
SF Pastebin — lightweight Flask server.
Serves the static frontend and provides password-protected admin delete API.

Usage:
    ADMIN_PASSWORD=yourpassword python3 server.py

Endpoints:
    GET  /                          → serves web/index.html
    GET  /data/tickets.json         → serves current ticket data
    DELETE /api/tickets/<id>        → delete one ticket (requires ?pw=PASSWORD)
    POST /api/tickets/bulk-delete   → delete many tickets (requires ?pw=PASSWORD)
    GET  /api/tickets               → list all non-skipped ticket IDs
"""

import json
import os
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

DATA_FILE = Path(__file__).parent / "data" / "tickets.json"
WEB_DIR = Path(__file__).parent / "web"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "sfpastebin")

app = Flask(__name__, static_folder=str(WEB_DIR))


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        pw = request.args.get("pw") or (request.get_json(silent=True) or {}).get("pw")
        if pw != ADMIN_PASSWORD:
            abort(401, "Unauthorized")
        return f(*args, **kwargs)
    return wrapper


def load_tickets():
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text())


def save_tickets(tickets):
    DATA_FILE.write_text(json.dumps(tickets, indent=2))


# ── Static frontend ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(WEB_DIR, path)


@app.route("/data/tickets.json")
def tickets_json():
    tickets = load_tickets()
    return jsonify(tickets)


# ── Admin API ─────────────────────────────────────────────────────────────────

@app.route("/api/tickets", methods=["GET"])
@require_admin
def list_tickets():
    tickets = load_tickets()
    return jsonify([
        {"id": t["id"], "address": t.get("address"), "theme": t.get("theme"), "skip": t.get("skip")}
        for t in tickets
    ])


@app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
@require_admin
def delete_ticket(ticket_id):
    tickets = load_tickets()
    before = len(tickets)
    tickets = [t for t in tickets if t["id"] != ticket_id]
    if len(tickets) == before:
        abort(404, f"Ticket {ticket_id} not found")
    save_tickets(tickets)
    return jsonify({"deleted": ticket_id, "remaining": len(tickets)})


@app.route("/api/tickets/bulk-delete", methods=["POST"])
@require_admin
def bulk_delete():
    body = request.get_json(silent=True) or {}
    ids_to_delete = set(body.get("ids", []))
    if not ids_to_delete:
        abort(400, "Provide a list of ids to delete")
    tickets = load_tickets()
    before = len(tickets)
    tickets = [t for t in tickets if t["id"] not in ids_to_delete]
    deleted = before - len(tickets)
    save_tickets(tickets)
    return jsonify({"deleted": deleted, "remaining": len(tickets)})


@app.route("/api/tickets/skip-all-unanalyzed", methods=["POST"])
@require_admin
def skip_unanalyzed():
    tickets = load_tickets()
    count = 0
    for t in tickets:
        if not t.get("analyzed"):
            t["skip"] = True
            count += 1
    save_tickets(tickets)
    return jsonify({"marked_skip": count})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3456))
    print(f"SF Pastebin running on http://localhost:{port}")
    print(f"Admin password: {ADMIN_PASSWORD}")
    app.run(host="0.0.0.0", port=port, debug=False)
