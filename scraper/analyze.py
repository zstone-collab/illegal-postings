"""
Runs Claude Vision on each ticket's image to:
- Extract visible text (OCR)
- Assign a theme with emoji
- Generate a short funny commentary
- Flag boring signs (street signs, stop signs) to skip

Requires: ANTHROPIC_API_KEY env var set.
Run after scrape.py.
"""

import base64
import json
import os
import time
from pathlib import Path
from typing import Optional

import anthropic
import requests

DATA_FILE = Path(__file__).parent.parent / "data" / "tickets.json"

# Text phrases that auto-skip a posting (case-insensitive substring match)
TEXT_BLOCKLIST = [
    "curandera",
]

# Text phrases that force a posting into the Political category (overrides Claude)
POLITICAL_KEYWORDS = [
    "gaza", "israel", "palestine", "palestin", "iran",
]

THEMES = """
Available themes (pick the single best fit):
- 🗳️ Political (activism, social justice, environment, protest)
- 🎨 Art & Culture (murals, poetry, creative expression)
- 🎵 Events (shows, concerts, parties, gatherings)
- 🚀 Startups (apps, tech companies, hustle culture)
- 🔧 Services (flyers for moving, plumbers, tutors, tarot readers, handymen, etc.)
- 💊 Drugs (dispensaries, delivery services, dealers, drug-related flyers, harm reduction, etc.)
- 💕 Dating (personals, missed connections, hookup flyers, escort ads, matchmaking)
- 🐾 Lost & Found (missing pets, lost items)
- 👁️ Weird & Unexplained (conspiracy, cults, very odd, comedy, satire)
- 🚫 Skip (BORING/INSTITUTIONAL or NO TEXT — set skip=true for ANY of:
    • NO READABLE TEXT in the image at all — must have at least some words/letters visible
    • Street signs, stop signs, speed limit signs, parking signs, utility markers
    • Permanent business signage (storefront signs, hotel signs, restaurant awnings)
    • HOUSE SIGNS / real estate: For-Sale, For-Rent, Open House, realtor yard signs, address plaques
    • House numbers, building plaques, historical markers, dedication plaques
    • Blank walls, unreadable photos, empty scenes, generic graffiti tags with no message
    • Official city/government signs (including the SF city seal)
    • Any permanent fixed signage that isn't a flyer, poster, sticker, or piece of ephemera stuck onto something)
"""

SYSTEM_PROMPT = """You are a witty urban archivist cataloging the unauthorized postings and flyers of San Francisco.
Your job is to look at photos of illegal postings reported to 311 and analyze them.
Be funny, irreverent, and insightful. SF has a rich tradition of street-level political and artistic expression.
Keep commentary short (1-2 sentences max). If the image is just a boring infrastructure sign or unreadable, say so honestly."""

ANALYSIS_PROMPT = f"""Analyze this image of a posting reported as "illegal" to SF 311.

{THEMES}

Return ONLY valid JSON with these fields:
{{
  "theme": "<emoji + theme name from list above>",
  "skip": <true if theme is 🚫 Skip, false otherwise>,
  "extracted_text": "<all visible text from the posting, or empty string if none>",
  "commentary": "<1-2 sentence funny/insightful comment about this posting, or empty string if skip>",
  "confidence": <0.0-1.0 how confident you are in the text extraction>
}}

Be thorough with text extraction - get every word visible.
For skip items: set skip=true and leave extracted_text and commentary empty."""


def detect_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def fetch_image_b64(url: str) -> Optional[tuple]:
    """Returns (base64_data, mime_type) or None."""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        mime = detect_mime(r.content)
        return base64.standard_b64encode(r.content).decode("utf-8"), mime
    except Exception as e:
        print(f"    Error fetching image: {e}")
        return None


def analyze_ticket(client: anthropic.Anthropic, ticket: dict) -> dict:
    if not ticket.get("image_url"):
        return {**ticket, "analyzed": True, "skip": True, "theme": "🚫 Skip",
                "extracted_text": "", "commentary": "", "confidence": 0}

    print(f"  Fetching image for {ticket['id']}...")
    result = fetch_image_b64(ticket["image_url"])
    if not result:
        return {**ticket, "analyzed": True, "skip": True, "theme": "🚫 Skip",
                "extracted_text": "", "commentary": "", "confidence": 0}
    img_b64, mime_type = result

    print(f"  Analyzing with Claude ({mime_type})...")
    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": ANALYSIS_PROMPT},
                ],
            }],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        extracted = (result.get("extracted_text") or "").strip()
        lower_text = extracted.lower()
        no_text = len(extracted) == 0
        blocked = any(phrase in lower_text for phrase in TEXT_BLOCKLIST)
        skip = result.get("skip", True) or no_text or blocked

        theme = result.get("theme", "🚫 Skip")
        if not skip and any(kw in lower_text for kw in POLITICAL_KEYWORDS):
            theme = "🗳️ Political"

        return {
            **ticket,
            "analyzed": True,
            "skip": skip,
            "theme": "🚫 Skip" if skip else theme,
            "extracted_text": extracted,
            "commentary": result.get("commentary", ""),
            "confidence": result.get("confidence", 0),
        }

    except Exception as e:
        print(f"    Claude error: {e}")
        return {**ticket, "analyzed": True, "skip": True, "theme": "🚫 Skip",
                "extracted_text": "", "commentary": "", "confidence": 0}


def main():
    if not DATA_FILE.exists():
        print("No tickets.json found. Run scrape.py first.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set ANTHROPIC_API_KEY environment variable.")
        return

    client = anthropic.Anthropic(api_key=api_key)
    tickets = json.loads(DATA_FILE.read_text())

    pending = [t for t in tickets if not t.get("analyzed")]
    print(f"Analyzing {len(pending)} unanalyzed tickets (of {len(tickets)} total)...")

    for i, ticket in enumerate(tickets):
        if ticket.get("analyzed"):
            continue
        print(f"\n[{i+1}/{len(tickets)}] Ticket {ticket['id']} @ {ticket.get('address', 'unknown')}")
        tickets[i] = analyze_ticket(client, ticket)
        DATA_FILE.write_text(json.dumps(tickets, indent=2))
        time.sleep(0.3)  # Be gentle with the API

    kept = sum(1 for t in tickets if not t.get("skip"))
    print(f"\nDone. {kept} postings kept, {len(tickets) - kept} skipped.")


if __name__ == "__main__":
    main()
