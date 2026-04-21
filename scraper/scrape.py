"""
Scrapes SF 311 illegal postings tickets and saves to data/tickets.json.

Strategy:
  Pass 1 — list pages: get ticket_id + image_url + address together (same <li>)
  Pass 2 — individual pages: get lat/lng coordinates
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://san-francisco2-production.spotmobile.net"
LIST_URL = (
    BASE_URL
    + "/tickets?filter%5Bfacets%5D%5Bticket_type_code%5D%5B%5D=PW%3ABSES%3AIllegal+Postings"
    "&order%5Bby%5D=chronological&order%5Bdirection%5D=descending"
)
DATA_FILE = Path(__file__).parent.parent / "data" / "tickets.json"
MAX_PAGES = 80                                        # hard cap
STOP_BEFORE = datetime(2026, 1, 1, tzinfo=timezone.utc)  # don't scrape older than this
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_list_page(page: int) -> list[dict]:
    """Extract ticket stubs (id + image_url + address) from a list page."""
    soup = fetch(f"{LIST_URL}&page={page}")
    tickets = []

    for li in soup.find_all("li"):
        # Ticket link → ID
        ticket_link = li.find("a", href=re.compile(r"/tickets/\d+"))
        if not ticket_link:
            continue
        m = re.search(r"/tickets/(\d+)", ticket_link["href"])
        if not m:
            continue
        ticket_id = m.group(1)

        # Cloudinary image: href of an <a> wrapping a placeholder <img>
        image_url = None
        for a in li.find_all("a", href=True):
            if "cloudinary.com" in a["href"]:
                image_url = a["href"].split("#")[0]
                break

        # Address: <address> tag is the reliable source
        addr_tag = li.find("address")
        address = addr_tag.get_text(strip=True) if addr_tag else None

        # Date/time: <time datetime="ISO"> → use ISO for canonical date, text for relative ("12h ago")
        time_tag = li.find("time")
        date_iso = time_tag.get("datetime") if time_tag else None
        date_relative = time_tag.get_text(strip=True) if time_tag else ""

        # Status: span with bg-status-open or bg-status-closed class
        status = "UNKNOWN"
        status_span = li.find("span", class_=re.compile(r"bg-status-"))
        if status_span:
            status = status_span.get_text(strip=True).upper()

        tickets.append({
            "id": ticket_id,
            "url": f"{BASE_URL}/tickets/{ticket_id}",
            "address": address,
            "image_url": image_url,
            "status": status,
            "date": date_relative,
            "date_iso": date_iso,
            "lat": None,
            "lng": None,
            "analyzed": False,
        })

    return tickets


def fetch_coordinates(ticket_id: str) -> Optional[tuple]:
    """Fetch lat/lng from individual ticket page."""
    url = f"{BASE_URL}/tickets/{ticket_id}"
    try:
        soup = fetch(url)
        for a in soup.find_all("a", href=True):
            m = re.search(r"ll=([-\d.]+)(?:,|%2C)([-\d.]+)", a["href"], re.IGNORECASE)
            if m:
                return float(m.group(1)), float(m.group(2))
    except Exception as e:
        print(f"  Coord error for {ticket_id}: {e}")
    return None


def main():
    DATA_FILE.parent.mkdir(exist_ok=True)

    existing = {}
    if DATA_FILE.exists():
        for t in json.loads(DATA_FILE.read_text()):
            existing[t["id"]] = t

    print(f"Starting scrape. Existing: {len(existing)} tickets")
    new_count = 0

    # Pass 1: collect stubs from list pages (stop when we hit STOP_BEFORE)
    stop_reached = False
    for page in range(1, MAX_PAGES + 1):
        print(f"\nList page {page}...")
        try:
            stubs = parse_list_page(page)
        except Exception as e:
            print(f"  Error: {e}")
            break

        if not stubs:
            print("  Empty page, stopping.")
            break

        for stub in stubs:
            # Check cutoff date
            iso = stub.get("date_iso")
            if iso:
                try:
                    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                    if d < STOP_BEFORE:
                        stop_reached = True
                        print(f"  Reached cutoff at {d.date()}, stopping.")
                        break
                except Exception:
                    pass

            tid = stub["id"]
            if tid in existing:
                for k in ("address", "date", "date_iso", "status", "image_url"):
                    if stub.get(k) is not None and stub.get(k) != "":
                        existing[tid][k] = stub[k]
                continue
            existing[tid] = stub
            new_count += 1

        if stop_reached:
            break
        # Periodic save so we don't lose progress
        DATA_FILE.write_text(json.dumps(list(existing.values()), indent=2))
        time.sleep(0.3)

    print(f"\nCollected {len(existing)} tickets ({new_count} new). Fetching coordinates...")

    # Pass 2: get coordinates for tickets that need them
    needs_coords = [t for t in existing.values() if not t.get("lat")]
    print(f"{len(needs_coords)} tickets need coordinates.")

    for i, ticket in enumerate(needs_coords):
        tid = ticket["id"]
        print(f"  [{i+1}/{len(needs_coords)}] Coords for {tid}...")
        coords = fetch_coordinates(tid)
        if coords:
            existing[tid]["lat"] = coords[0]
            existing[tid]["lng"] = coords[1]
        else:
            print(f"    No coords found")
        # Save periodically
        if (i + 1) % 5 == 0:
            DATA_FILE.write_text(json.dumps(list(existing.values()), indent=2))
        time.sleep(0.4)

    total = len(existing)
    with_coords = sum(1 for t in existing.values() if t.get("lat"))
    with_img = sum(1 for t in existing.values() if t.get("image_url"))
    print(f"\nDone. {total} tickets | {with_coords} with coords | {with_img} with images")
    DATA_FILE.write_text(json.dumps(list(existing.values()), indent=2))


if __name__ == "__main__":
    main()
