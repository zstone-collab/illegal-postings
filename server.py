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
import tempfile
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

DATA_FILE = Path(__file__).parent / "data" / "tickets.json"
WEB_DIR = Path(__file__).parent / "web"
REPO_DIR = Path(__file__).parent
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "sfpastebin")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = "zstone-collab/illegal-postings"
COMMIT_POLL_SEC = 3        # worker wakes up this often
COMMIT_MAX_AGE_SEC = 10    # an edit will be committed within this many seconds, guaranteed

app = Flask(__name__, static_folder=str(WEB_DIR))


# ── Data-file lock: serialize load-modify-save across concurrent admin requests ──

_data_lock = threading.Lock()


# ── Auto-commit: every mark_dirty kicks a commit thread immediately.
#    If one's already running, skip — it'll pick up our pending edit before finishing.
#    No polling loop that can silently die. ─────────────────────────────────────

_dirty = False
_dirty_at = None
_last_reason = ""
_dirty_lock = threading.Lock()
_commit_mutex = threading.Lock()       # ensures only one git operation runs at a time
_last_commit = {"ok": None, "ts": None, "reason": None, "error": None}


def mark_dirty(reason: str):
    """Record edit + commit synchronously in the request thread.

    Background threads were getting starved on Render free tier — never ran.
    Synchronous commit adds ~1-2s per admin action but gives absolute certainty
    that the edit is in git before the HTTP response returns.
    """
    global _dirty, _dirty_at, _last_reason
    with _dirty_lock:
        if not _dirty:
            _dirty_at = time.time()
        _dirty = True
        _last_reason = reason

    # Serialize concurrent admin requests so we don't race on git ops
    with _commit_mutex:
        # Drain loop: grab latest reason, commit, then check if MORE edits arrived
        while True:
            with _dirty_lock:
                if not _dirty:
                    return
                reason = _last_reason
                _dirty = False
            _do_git_commit(reason)


def _do_git_commit(reason: str):
    """Stage tickets.json, commit, push. Records result in _last_commit."""
    global _last_commit
    if not GITHUB_TOKEN:
        _last_commit = {"ok": False, "ts": time.time(), "reason": reason,
                        "error": "GITHUB_TOKEN not set"}
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

    def fail(msg):
        global _last_commit
        print(f"[git-commit] FAIL ({reason}): {msg}")
        _last_commit = {"ok": False, "ts": time.time(), "reason": reason, "error": msg}

    try:
        # Configure origin fresh (Render strips it during deploy)
        run(["git", "remote", "remove", "origin"])  # best-effort
        r = run(["git", "remote", "add", "origin", remote_url])
        if r.returncode != 0:
            return fail(f"remote add failed: {r.stderr.decode()[:200]}")

        # Stage
        r = run(["git", "add", "data/tickets.json"])
        if r.returncode != 0:
            return fail(f"add failed: {r.stderr.decode()[:200]}")

        # No-op if nothing changed (file was rewritten identically)
        if run(["git", "diff", "--staged", "--quiet"]).returncode == 0:
            _last_commit = {"ok": True, "ts": time.time(), "reason": reason,
                            "error": None, "note": "no changes"}
            return

        # Commit
        r = run(["git", "commit", "-m", f"Admin edit: {reason}"])
        if r.returncode != 0:
            return fail(f"commit failed: {r.stderr.decode()[:200]}")

        # Push. If remote diverged (I pushed from my laptop), rebase + retry.
        r = run(["git", "push", "origin", "HEAD:main"])
        if r.returncode != 0:
            print(f"[git-commit] push conflict, rebasing: {r.stderr.decode()[:200]}")
            run(["git", "fetch", "origin", "main"])
            r = run(["git", "rebase", "origin/main"])
            if r.returncode != 0:
                run(["git", "rebase", "--abort"])
                return fail(f"rebase failed: {r.stderr.decode()[:200]}")
            r = run(["git", "push", "origin", "HEAD:main"])
            if r.returncode != 0:
                return fail(f"retry push failed: {r.stderr.decode()[:200]}")

        _last_commit = {"ok": True, "ts": time.time(), "reason": reason, "error": None}
        print(f"[git-commit] ✓ pushed ({reason})")
    except Exception as e:
        return fail(f"exception: {e}")


def _startup_recovery():
    """On process start: if tickets.json differs from git HEAD, commit + push.
    This catches the 'edits written to disk but never committed before crash' case.
    Only relevant when the filesystem survives restart; on Render deploys the
    filesystem is reset anyway so this is a no-op in that case — but a cheap no-op.
    """
    if not GITHUB_TOKEN or not DATA_FILE.exists():
        return
    try:
        r = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "data/tickets.json"],
            cwd=str(REPO_DIR), capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            print("[startup] tickets.json differs from HEAD, recovering...")
            mark_dirty("startup recovery: uncommitted edits on disk")
    except Exception as e:
        print(f"[startup] recovery check failed: {e}")


# Run startup recovery immediately — it calls mark_dirty which kicks its own thread
_startup_recovery()


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
    """Atomic write: temp file + rename. Prevents corruption if process dies mid-write."""
    # NamedTemporaryFile in same dir so os.rename is atomic (same filesystem)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(DATA_FILE.parent),
        prefix=".tickets-", suffix=".json.tmp", delete=False,
    )
    try:
        json.dump(tickets, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(DATA_FILE))  # atomic
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


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
    with _data_lock:
        tickets = load_tickets()
        before = len(tickets)
        tickets = [t for t in tickets if t["id"] != ticket_id]
        if len(tickets) == before:
            abort(404, f"Ticket {ticket_id} not found")
        save_tickets(tickets)
    mark_dirty(f"delete {ticket_id}")
    return jsonify({"deleted": ticket_id, "remaining": len(tickets)})


@app.route("/api/tickets/bulk-delete", methods=["POST"])
@require_admin
def bulk_delete():
    body = request.get_json(silent=True) or {}
    ids_to_delete = set(body.get("ids", []))
    if not ids_to_delete:
        abort(400, "Provide a list of ids to delete")
    with _data_lock:
        tickets = load_tickets()
        before = len(tickets)
        tickets = [t for t in tickets if t["id"] not in ids_to_delete]
        deleted = before - len(tickets)
        save_tickets(tickets)
    mark_dirty(f"bulk-delete {deleted} tickets")
    return jsonify({"deleted": deleted, "remaining": len(tickets)})


@app.route("/api/tickets/skip-all-unanalyzed", methods=["POST"])
@require_admin
def skip_unanalyzed():
    with _data_lock:
        tickets = load_tickets()
        count = 0
        for t in tickets:
            if not t.get("analyzed"):
                t["skip"] = True
                count += 1
        save_tickets(tickets)
    mark_dirty(f"skip-unanalyzed ({count})")
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
    with _data_lock:
        tickets = load_tickets()
        count = 0
        for t in tickets:
            if (t.get("theme") or "").startswith(prefix) and not t.get("skip"):
                t["skip"] = True
                count += 1
        save_tickets(tickets)
    mark_dirty(f"bulk-skip {prefix} ({count})")
    return jsonify({"skipped": count, "theme_prefix": prefix})


@app.route("/api/tickets/<ticket_id>/categorize", methods=["POST"])
@require_admin
def categorize_ticket(ticket_id):
    body = request.get_json(silent=True) or {}
    theme = body.get("theme")
    if not theme:
        abort(400, "Provide a theme")
    with _data_lock:
        tickets = load_tickets()
        target = next((t for t in tickets if t["id"] == ticket_id), None)
        if not target:
            abort(404)
        target["theme"] = theme
        target["skip"] = theme == "🚫 Skip"
        target["confidence"] = 1.0
        target["reviewed_by_human"] = True
        save_tickets(tickets)
    mark_dirty(f"categorize {ticket_id} → {theme}")
    return jsonify({"id": ticket_id, "theme": theme})


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


@app.route("/api/admin-status", methods=["GET"])
@require_admin
def admin_status():
    """Pending state + last commit result. Hit any time to check persistence health."""
    with _dirty_lock:
        pending = _dirty
        pending_age = (time.time() - _dirty_at) if (_dirty and _dirty_at) else 0
        pending_reason = _last_reason if _dirty else None
    return jsonify({
        "pending_commit": pending,
        "pending_age_sec": round(pending_age, 1),
        "pending_reason": pending_reason,
        "last_commit": _last_commit,
        "token_configured": bool(GITHUB_TOKEN),
    })


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
