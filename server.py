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
import shutil
import subprocess
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
    resp = send_from_directory(WEB_DIR, "index.html")
    # Prevent the HTML from being cached so users always get the latest asset versions
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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


@app.route("/api/tickets/review-queue", methods=["GET"])
@require_admin
def review_queue():
    """Tickets that need review: low confidence OR themed ❓ Unclear.
    Filter with ?only=unclear to get only Unclear items.
    """
    only = request.args.get("only")
    tickets = load_tickets()

    def needs_review(t):
        if not (t.get("analyzed") and not t.get("skip") and t.get("image_url")):
            return False
        if t.get("reviewed_by_human"):
            return False
        theme = t.get("theme") or ""
        is_unclear = theme.startswith("❓")
        if only == "unclear":
            return is_unclear
        low_conf = (t.get("confidence") or 0) < 0.6
        return is_unclear or low_conf

    queue = [t for t in tickets if needs_review(t)]
    # Unclear items first, then by confidence ascending
    queue.sort(key=lambda t: (
        0 if (t.get("theme") or "").startswith("❓") else 1,
        t.get("confidence") or 0,
    ))
    return jsonify(queue)


@app.route("/api/tickets/bulk-skip-by-theme", methods=["POST"])
@require_admin
def bulk_skip_by_theme():
    """Mark every ticket matching a theme prefix (e.g. '❓') as skip.
    Body: {"theme_prefix": "❓"}  -> skips all Unclear items.
    """
    body = request.get_json(silent=True) or {}
    prefix = body.get("theme_prefix")
    if not prefix:
        abort(400, "Provide theme_prefix")
    tickets = load_tickets()
    count = 0
    for t in tickets:
        if (t.get("theme") or "").startswith(prefix) and not t.get("skip"):
            t["skip"] = True
            count += 1
    save_tickets(tickets)
    return jsonify({"skipped": count, "theme_prefix": prefix})


@app.route("/api/tickets/<ticket_id>/categorize", methods=["POST"])
@require_admin
def categorize_ticket(ticket_id):
    body = request.get_json(silent=True) or {}
    theme = body.get("theme")
    if not theme:
        abort(400, "Provide a theme")
    tickets = load_tickets()
    for t in tickets:
        if t["id"] == ticket_id:
            t["theme"] = theme
            t["skip"] = theme == "🚫 Skip"
            t["confidence"] = 1.0
            t["reviewed_by_human"] = True
            save_tickets(tickets)
            return jsonify({"id": ticket_id, "theme": theme})
    abort(404)


@app.route("/api/admin-probe", methods=["GET"])
@require_admin
def admin_probe():
    """Tests whether this environment can support git auto-commit persistence.
    Runs only read-only / dry-run git operations (no commits, no pushes).
    """
    repo_dir = Path(__file__).parent
    out = {
        "repo_dir": str(repo_dir),
        "git_binary": shutil.which("git"),
        "git_dir_present": (repo_dir / ".git").is_dir(),
        "github_token_set": bool(os.environ.get("GITHUB_TOKEN")),
        "ticket_file_writable": os.access(str(DATA_FILE), os.W_OK),
    }

    def run(cmd):
        try:
            r = subprocess.run(
                cmd, cwd=str(repo_dir), capture_output=True, text=True, timeout=15
            )
            return {
                "ok": r.returncode == 0,
                "returncode": r.returncode,
                "stdout": (r.stdout or "").strip()[:500],
                "stderr": (r.stderr or "").strip()[:500],
            }
        except FileNotFoundError as e:
            return {"ok": False, "error": f"binary not found: {e}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if not out["git_binary"]:
        out["verdict"] = "FAIL — git CLI not installed on this Render instance"
        return jsonify(out)

    if not out["git_dir_present"]:
        out["verdict"] = "FAIL — .git directory not preserved in deploy"
        return jsonify(out)

    # Read-only probes
    out["git_version"] = run(["git", "--version"])
    out["git_status"] = run(["git", "status", "--porcelain"])
    out["git_log_head"] = run(["git", "log", "-1", "--oneline"])
    out["git_remote"] = run(["git", "remote", "-v"])

    # Dry-run push to test auth (only if token set). --dry-run doesn't actually push.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        remote_url = f"https://x-access-token:{token}@github.com/zstone-collab/illegal-postings.git"
        out["git_push_dry_run"] = run(
            ["git", "push", "--dry-run", remote_url, "HEAD:main"]
        )
    else:
        out["git_push_dry_run"] = {"skipped": "set GITHUB_TOKEN env var to test auth"}

    # Overall verdict
    needed = [
        out["git_version"].get("ok"),
        out["git_status"].get("ok"),
        out["git_log_head"].get("ok"),
    ]
    if all(needed):
        if token and out["git_push_dry_run"].get("ok"):
            out["verdict"] = "PASS — git available, repo healthy, token works. Auto-commit will work."
        elif not token:
            out["verdict"] = "PASS-PENDING-TOKEN — git works. Set GITHUB_TOKEN and re-probe to verify auth."
        else:
            out["verdict"] = "FAIL — git works but push auth broken. Check token permissions."
    else:
        out["verdict"] = "FAIL — basic git commands not working in this environment"

    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3456))
    print(f"SF Pastebin running on http://localhost:{port}")
    print(f"Admin password: {ADMIN_PASSWORD}")
    app.run(host="0.0.0.0", port=port, debug=False)
