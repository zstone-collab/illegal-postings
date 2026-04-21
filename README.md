# Illegal Postings

A living catalog of San Francisco's unauthorized walls — scraped from 311 complaints, read by Claude Vision.

## What it does

1. **Scrapes** SF's 311 complaint listings for "Illegal Postings"
2. **Extracts** ticket ID, address, lat/lng, image URL, date, status
3. **Analyzes** each image with Claude Vision — OCRs text, categorizes, filters boring signs
4. **Displays** the results on a map + gallery, filterable by category

## Run locally

```bash
pip3 install -r requirements.txt
python3 scraper/scrape.py                            # fetch tickets
ANTHROPIC_API_KEY=xxx python3 scraper/analyze.py     # analyze images
ADMIN_PASSWORD=secret python3 server.py              # serve site at :3456
```

Admin panel: `http://localhost:3456/?admin=secret`

## Deploy

See [DEPLOY.md](DEPLOY.md).
