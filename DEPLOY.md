# Deployment — Free Hosting Options

## Recommended: Render.com (free, full functionality)

**Keeps the admin delete feature working.** Free tier spins down after 15 min of inactivity (wakes up in ~30s on first hit after).

1. Push this folder to a new GitHub repo (public or private):
   ```bash
   cd /Users/zarastone/postings
   git init && git add . && git commit -m "initial"
   gh repo create illegal-postings --public --source=. --push
   ```
2. Go to [render.com](https://render.com) → sign up with GitHub
3. **New +** → **Web Service** → pick your repo
4. Settings:
   - **Runtime:** Docker (it auto-detects `Dockerfile`)
   - **Environment variable:** `ADMIN_PASSWORD` = `your-secret-here`
   - **Instance type:** Free
5. Hit **Deploy** — you'll get a URL like `https://illegal-postings.onrender.com`
6. Admin panel: `https://illegal-postings.onrender.com/?admin=your-secret-here`

---

## Alternative: Fly.io (free, no cold starts)

Requires credit card (for verification, won't charge for small apps).

```bash
brew install flyctl
cd /Users/zarastone/postings
fly launch                 # accept defaults, pick a name
fly secrets set ADMIN_PASSWORD=your-secret-here
fly deploy
```

---

## Cheapest: Static-only on Netlify (no admin delete)

If you don't need the live admin panel, drop the `web/` folder + `data/tickets.json` onto [netlify.com/drop](https://app.netlify.com/drop) — instant free hosting with CDN. Deletes would require editing `data/tickets.json` locally and re-uploading.

---

## Keeping data fresh

To keep postings up-to-date after deploy, run the scraper+analyzer locally on a schedule and commit the refreshed `data/tickets.json`:

```bash
# ~/crontab — nightly at 2am
0 2 * * * cd /Users/zarastone/postings && python3 scraper/scrape.py && ANTHROPIC_API_KEY=xxx python3 scraper/analyze.py && git add data/tickets.json && git commit -m "refresh" && git push
```

Render auto-redeploys on push; Fly.io requires `fly deploy`.
