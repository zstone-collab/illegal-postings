"""
Pull the live tickets.json from Render and merge it INTO the local copy so that
admin-panel changes (deletes, skips, recategorizations) survive our git pushes.

Rules:
  - For every ticket in LOCAL:
      * If the same id exists in LIVE:
          - If LIVE has reviewed_by_human=True → LIVE wins (keep human edits)
          - Else → LOCAL wins (we have fresh analyzer output)
      * If the ticket is MISSING from LIVE → it was deleted on the live site;
        drop it from local too.
  - Ignore tickets in LIVE that aren't in LOCAL (never happens today, but safe).

Run with: python3 scripts/sync_from_live.py
"""

import json
import sys
from pathlib import Path

import requests

LIVE_URL = "https://illegal-postings.onrender.com/data/tickets.json"
LOCAL_PATH = Path(__file__).parent.parent / "data" / "tickets.json"


def main():
    print(f"Fetching {LIVE_URL}...")
    live = requests.get(LIVE_URL, timeout=30).json()
    local = json.loads(LOCAL_PATH.read_text())

    live_by_id = {t["id"]: t for t in live}
    print(f"  live: {len(live_by_id)} tickets")
    print(f"  local: {len(local)} tickets")

    merged = []
    human_wins = 0
    deleted_on_live = 0
    fresh_analyzer_wins = 0

    for t in local:
        tid = t["id"]
        if tid not in live_by_id:
            # Deleted on the live site; drop from local too.
            deleted_on_live += 1
            continue

        live_t = live_by_id[tid]
        if live_t.get("reviewed_by_human"):
            # Human made a call; preserve it verbatim.
            merged.append(live_t)
            human_wins += 1
        else:
            # No human review yet; our local (analyzer) output is authoritative.
            # But still carry forward any skip flag the human may have set.
            if live_t.get("skip") and not t.get("skip"):
                merged.append({**t, "skip": True, "theme": live_t.get("theme", t.get("theme"))})
                human_wins += 1
            else:
                merged.append(t)
                fresh_analyzer_wins += 1

    print()
    print(f"  preserved human edits: {human_wins}")
    print(f"  dropped (deleted live): {deleted_on_live}")
    print(f"  kept local analyzer:   {fresh_analyzer_wins}")
    print(f"  final: {len(merged)} tickets")

    if deleted_on_live > 50 or human_wins == 0:
        resp = input("\nSanity check numbers. Continue writing to disk? [y/N] ")
        if resp.strip().lower() != "y":
            print("Aborted.")
            sys.exit(1)

    LOCAL_PATH.write_text(json.dumps(merged, indent=2))
    print(f"\nWrote {LOCAL_PATH}")
    print("Now: git add data/tickets.json && git commit && git push")


if __name__ == "__main__":
    main()
