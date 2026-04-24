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
import threading
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

DATA_FILE = Path(__file__).parent / "data" / "tickets.json"
WEB_DIR = Path(__file__).parent / "web"
REPO_DIR = Path(__file__).parent
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "sfpastebin")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = "zstone-collab/illegal-postings"
COMMIT_DEBOUNCE_SEC = 10  # wait this long after last edit before committing

app = Flask(__name__, static_folder=str(WEB_DIR))


# ── Auto-commit admin edits back to git so they survive Render redeploys ─────

_commit_timer = None
_commit_lock = threading.Lock()


def schedule_git_commit(reason: str):
    """Debounce admin edits into a single commit ~COMMIT_DEBOUNCE_SEC after the last write."""
    if not GITHUB_TOKEN:
        print("[git-commit] GITHUB_TOKEN not set; skipping persistence")
        return
    global _commit_timer
    with _commit_lock:
        if _commit_timer is not None:
            _commit_timer.cancel()
        _commit_timer = threading.Timer(COMMIT_DEBOUNCE_SEC, _do_git_commit, args=[reason])
        _commit_timer.daemon = True
        _commit_timer.start()


def _do_git_commit(reason: str):
    """Stage tickets.json, commit, push. Runs on a background thread."""
    if not GITHUB_TOKEN:
        return

    cwd = str(REPO_DIR)
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Illegal Postings Admin"
    env["GIT_AUTHOR_EMAIL"] = "admin@illegal-postings.com"
    env["GIT_COMMITTER_NAME"] = "Illegal Postings Admin"
    env["GIT_COMMITTER_EMAIL"] = "admin@illegal-postings.com"
    remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"

    def run(cmd, **kw):
        return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=30, **kw)

    try:
        # Configure origin fresh (Render strips it during deploy)
        run(["git", "remote", "remove", "origin"])  # best-effort
        r = run(["git", "remote", "add", "origin", remote_url])
        if r.returncode != 0:
            print(f"[git-commit] remote add failed: {r.stderr.decode()}")
            return

        # Stage the data file
        r = run(["git", "add", "data/tickets.json"])
        if r.returncode != 0:
            print(f"[git-commit] add failed: {r.stderr.decode()}")
            return

        # No-op if nothing changed
        if run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
            return

        # Commit
        r = run(["git", "commit", "-m", f"Admin edit: {reason}"])
        if r.returncode != 0:
            print(f"[git-commit] commit failed: {r.stderr.decode()}")
            return

        # Push. If remote has diverged, fetch + rebase once, then retry push.
        r = run(["git", "push", "origin", "HEAD:main"])
        if r.returncode != 0:
            print(f"[git-commit] push failed, trying rebase: {r.stderr.decode()}")
            run(["git", "fetch", "origin", "main"])
            r = run(["git", "rebase", "origin/main"])
            if r.returncode != 0:
                print(f"[git-commit] rebase failed: {r.stderr.decode()}")
                run(["git", "rebase", "--abort"])
                return
            r = run(["git", "push", "origin", "HEAD:main"])
            if r.returncode != 0:
                print(f"[git-commit] retry push failed: {r.stderr.decode()}")
                return

        print(f"[git-commit] ✓ pushed ({reason})")
    except Exception as e:
        print(f"[git-commit] EXCEPTION ({reason}): {e}")


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
    schedule_git_commit(f"delete {ticket_id}")
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
    schedule_git_commit(f"bulk-delete {deleted} tickets")
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
    schedule_git_commit(f"skip-unanalyzed ({count})")
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
    schedule_git_commit(f"bulk-skip {prefix} ({count})")
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
            schedule_git_commit(f"categorize {ticket_id} → {theme}")
            return jsonify({"id": ticket_id, "theme": theme})
    abort(404)


@app.route("/api/debug-commit", methods=["POST"])
@require_admin
def debug_commit():
    """Synchronously run the full commit flow and return every step's output.
    Makes a tiny no-op write so there's something to commit.
    """
    if not GITHUB_TOKEN:
        return jsonify({"error": "GITHUB_TOKEN not set"}), 400

    # Force a real diff: touch the last-modified in a harmless way
    tickets = load_tickets()
    save_tickets(tickets)  # rewrites file (may be identical if JSON dump is deterministic)

    cwd = str(REPO_DIR)
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Illegal Postings Admin"
    env["GIT_AUTHOR_EMAIL"] = "admin@illegal-postings.com"
    env["GIT_COMMITTER_NAME"] = "Illegal Postings Admin"
    env["GIT_COMMITTER_EMAIL"] = "admin@illegal-postings.com"
    remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"

    steps = []

    def run(cmd, ignore_fail=False):
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            step = {
                "cmd": " ".join(c if "x-access-token" not in c else c.split("@")[-1] for c in cmd),
                "rc": r.returncode,
                "stdout": (r.stdout or "").strip()[:500],
                "stderr": (r.stderr or "").strip()[:500],
            }
            steps.append(step)
            return r
        except Exception as e:
            steps.append({"cmd": " ".join(cmd[:2]), "error": str(e)})
            return None

    run(["git", "remote", "remove", "origin"], ignore_fail=True)
    run(["git", "remote", "add", "origin", remote_url])
    run(["git", "add", "data/tickets.json"])
    r = run(["git", "diff", "--staged", "--quiet"])
    has_changes = r is not None and r.returncode != 0
    steps.append({"info": f"has_staged_changes: {has_changes}"})
    if has_changes:
        run(["git", "commit", "-m", "Debug test commit"])
        run(["git", "push", "origin", "HEAD:main"])
    else:
        steps.append({"info": "No changes to commit (save_tickets wrote identical JSON)"})

    return jsonify({"steps": steps})


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
