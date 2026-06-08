# AGENTS.md — Leasehackr Scraper

> **This is the single source of truth for anyone (human or AI) touching this repo.**
> Read it fully before changing code or "fixing" anything. The repo previously had **no
> docs at all** (only `requirements.txt`); this file was authored from the source. If
> something here is wrong, fix *this* file.

---

## 1. What this is

A tiny Python scraper (no web app, no server) that pulls the latest lease deals from
**Leasehackr's "Pick'n'Drive" board** (`https://pnd.leasehackr.com/`), scores each deal,
writes them to a **Google Sheet**, and fires a **Telegram alert** for standout deals.

It runs **entirely on GitHub Actions cron** — there is nothing to deploy. Two scheduled
workflows run once per day against the same source page but write to two different tabs of
the same spreadsheet:

| Workflow file | Display name | Entry point | Cron (UTC) | Sheet tab written | Behaviour |
|---|---|---|---|---|---|
| `.github/workflows/weekly_scraper.yml` | **Historical Scraper** | `scraper.py` | `4 7 * * *` (07:04 daily) | `sheet1` (first/default tab, "Historical") | **Cumulative** — merges scraped deals into all prior rows, dedups, sorts by score, rewrites the whole tab |
| `.github/workflows/daily_scraper.yml` | **Daily Scraper** | `scraper_daily.py` | `6 7 * * *` (07:06 daily) | `Daily` tab | **Snapshot** — wipes the tab and writes only today's deals, sorted by score |
| `.github/workflows/keepalive.yml` | Keepalive | (inline shell) | `17 3 1,15 * *` (1st & 15th) | n/a (commits to repo) | Empty commit if repo idle ≥40 days, to stop GitHub auto-disabling the crons |

> ⚠️ **Naming is misleading — verify against this table, not the filenames.**
> `weekly_scraper.yml` is **not** weekly; it runs **daily** at 07:04 UTC and is the
> *Historical* (cumulative) scraper. `daily_scraper.yml` runs daily at 07:06 UTC. Both run
> every day, 2 minutes apart, plus the Daily one sleeps 5 minutes after install (see §5).
> The "weekly" filename is a historical artifact (git: "Fix: Historical scraper runs daily").

**Stack:** Python 3.9 (CI pin) · [`scrapling`](https://pypi.org/project/scrapling/)
`StealthyFetcher` (Camoufox headless browser) for fetching · BeautifulSoup4/lxml for
parsing · `gspread` + `google-auth` for Sheets · `requests` for the Telegram API.

---

## 2. Architecture / data flow

```
GitHub Actions cron (daily)
   │
   ├─ scraper.py (Historical, 07:04 UTC)        ├─ scraper_daily.py (Daily, 07:06 UTC)
   │   1. read existing rows from sheet1         │   1. open/create "Daily" tab
   │   2. scrape pnd.leasehackr.com              │   2. clear tab, re-write headers
   │   3. score deals (1% rule)                  │   3. scrape pnd.leasehackr.com  ──┐
   │   4. filter deals NOT already in sheet      │   4. dedup within today's scrape   │ shared
   │   5. Telegram-alert NEW deals ≥98           │   5. write today's deals, sorted   │ helpers
   │   6. merge+dedup+sort all, rewrite sheet1   │   6. Telegram-alert ANY deal ≥98  ─┘ from
   ▼                                             ▼                                       scraper.py
Google Sheet (SPREADSHEET_ID)                Telegram (TELEGRAM_TOKEN → TELEGRAM_CHAT_ID)
```

Both scrapers share one fetch+parse+score pipeline. `scraper_daily.py` **imports
`scraper`** and reuses `get_google_client`, `get_spreadsheet_id`, `scrape_deals`, and the
`LeaseDeal` dataclass (via `deal.signature`/`deal.to_list()`). Only the persistence
strategy and the alert trigger differ.

### The scrape itself (`scraper.scrape_deals`)
- Fetches `https://pnd.leasehackr.com/` with `StealthyFetcher().fetch(...,
  wait_selector='.deal_card', timeout=60000)` — it waits for `.deal_card` to appear (the
  page is JS-rendered) rather than network-idle, with a 60s timeout.
- Parses the rendered HTML with BeautifulSoup, finds all `div.deal_card`, and for each card
  reads CSS-class fields: `.make_val .model_val .model_yr_val .trim_val .msrp_val
  .monthly_val .das_val .term_val .mileage_val` and the calculator link `.calc_val`.
- Extra fields come from the **query string of the `.calc_val` href**: `sales_price`, `mf`
  (money factor), `resP` (residual %), `sales_tax`.
- **Interest rate % = `mf * 2400`** (standard lease MF→APR conversion).
- `model` is stored as the concatenation `"{year} {make} {model} {trim}"`.

### Scoring (`scraper.calculate_score`) — the "1% rule"
```
effective_monthly = monthly + (das / months)
ratio             = effective_monthly / msrp
score             = 100 - ((ratio - 0.008) / 0.010) * 100   # clamped 0..100
```
So **0.8% of MSRP → score 100**, **1.8% → score 0**, linear between, clamped. Any parse
error (bad number, div-by-zero) → score `0`.

### Dedup signature
A deal's identity is the 4-tuple **(make, model, msrp, monthly_payment)** —
`LeaseDeal.signature`. In the sheet, the equivalent columns are indices `0,1,2,6`.

---

## 3. The Google Sheet — 13-column layout (do not reorder)

Both tabs use this exact header order; the row written by `LeaseDeal.to_list()` and the
dedup index math both depend on it:

```
0 Make | 1 Model | 2 MSRP | 3 Sales Price | 4 Months | 5 Miles/Year |
6 Monthly Payment | 7 Due at Signing | 8 Sales Tax | 9 Money Factor |
10 Interest Rate % | 11 Residual % | 12 Score
```

- **Historical (`sheet1`)** is rewritten in full each run: `worksheet.clear()` →
  `append_row(headers)` → `append_rows(all_deals)`, sorted by Score (col 12) descending.
  `fetch_existing_rows` back-fills a Score for any legacy row that has <13 columns and
  normalizes every row to exactly 13 columns.
- **Daily (`Daily` tab)** is wiped and rewritten with only today's deals (headers kept).
  The tab is auto-created (13 cols) if missing.

---

## 4. Run it locally

```bash
pip install -r requirements.txt
scrapling install          # downloads the Camoufox browser (large; needed by StealthyFetcher)

# credentials: either drop a service-account file as credentials.json in the repo root,
# or set GOOGLE_CREDENTIALS to the JSON string (CI uses the env var).
export SPREADSHEET_ID=...        # the target Google Sheet's ID
export TELEGRAM_TOKEN=...        # optional locally; alert is skipped if unset
export TELEGRAM_CHAT_ID=...      # optional locally

python scraper.py          # Historical (cumulative → sheet1)
python scraper_daily.py    # Daily (snapshot → "Daily" tab)
```

`inspect_structure.py` is a **debug-only** helper (not run by CI): point it at a saved HTML
dump to verify the `.deal_card` / `.calc_val` selectors still match the live site.
`python inspect_structure.py -f page_source.html` (default file `page_source.html`, which
is git-ignored). Use it first when a scrape suddenly returns 0 deals.

### Environment / secrets (where they live)
All four are GitHub **repository secrets** (referenced as `${{ secrets.NAME }}` and validated
at the top of each workflow before any scraping):

| Var | Purpose | Local fallback |
|---|---|---|
| `SPREADSHEET_ID` | Target Google Sheet ID | required |
| `GOOGLE_CREDENTIALS` | Service-account JSON (full string) | falls back to `credentials.json` file |
| `TELEGRAM_TOKEN` | Bot token for alerts | optional — alert skipped if unset |
| `TELEGRAM_CHAT_ID` | Destination chat | optional — alert skipped if unset |

**Never commit secret values.** `credentials.json` and `.env*` are git-ignored. The repo is
public — keep it that way.

### CI install nuances (already handled — don't "simplify" away)
- Each scraper workflow runs `scrapling install` in a **3-attempt retry loop** with
  `GITHUB_TOKEN` set in env. Camoufox is downloaded from the GitHub Releases API; the token
  lifts the rate limit from 60/hr (shared-runner IP) to 1000/hr and dodges flaky **403
  rate-limit** failures. Keep both the token and the retry loop.
- `pip install` uses `--retries 5 --timeout 60` for the same flakiness reasons.
- Both scraper jobs `timeout-minutes: 15`; both pin Python `3.9`.

---

## 5. Gotchas / hard rules

1. **Scheduling is staggered on purpose.** Historical (07:04) runs before Daily (07:06).
   The Daily job additionally **sleeps 300s** (`Sleep 5 minutes before running daily
   scraper`) after the browser install — this serializes the two runs so they don't hammer
   the source site or the Sheets API simultaneously, and gives Historical time to finish.
   (See git: "Sequentialize workflow", "2-min offset".) Don't remove the sleep/offset.

2. **Telegram alert triggers differ between the two scrapers** (same threshold value 98,
   different *scope*):
   - **Historical** alerts only on deals that are **brand-new** (not already in `sheet1`)
     scoring ≥98 — `send_telegram_alert` over `filter_hot_deals(new_deals)`.
   - **Daily** alerts on **any** of today's scraped deals scoring ≥98 (regardless of
     novelty) — `send_daily_telegram_alert` over `filter_hot_deals(sorted_deals)`.
   - **Doc-vs-code note:** the comment in `scraper.py` ("Mirrors scraper_daily.py so both
     scrapers use the same bar") refers to the *threshold value* (both `98.0`), **not** the
     alert behaviour, which is genuinely different per the above.

3. **`scraper_daily.py` defines its own `TELEGRAM_ALERT_THRESHOLD`, `filter_hot_deals`, and
   `_fmt_money`** that shadow the ones in `scraper.py` — they are duplicated, not imported.
   If you change the threshold or money formatting, **change it in both files** or they
   drift. (`scrape_deals`, `LeaseDeal`, `get_google_client`, `get_spreadsheet_id` *are*
   imported from `scraper`, so those are single-sourced.)

4. **Column order is load-bearing.** The 13-column layout drives `to_list()`, the sort key
   (`x[12]`), and the dedup signature (cols `0,1,2,6`). Reordering headers silently breaks
   dedup and scoring of existing rows. Add new columns only at the **end**, and update the
   header lists in *both* files plus the index math.

5. **Telegram send is best-effort.** `scraper.py`'s `send_telegram_alert` does not check
   the HTTP status (fire-and-forget); `scraper_daily.py`'s `send_daily_telegram_alert`
   prints the status. Neither failure aborts the run / fails the workflow — a failed alert
   is silent. If alerts stop arriving, the scrape can still be succeeding.

6. **Scrape can legitimately return 0 deals** if Leasehackr changes its markup (the
   `.deal_card` / `.calc_val` CSS classes) or blocks the headless browser. The scripts won't
   error — they'll just write an empty/unchanged sheet. Use `inspect_structure.py` to
   diagnose selector drift before "fixing" anything else.

7. **Keepalive exists to fight GitHub's 60-day cron auto-disable.** Scrapers never push to
   the repo, so without commits GitHub suspends the schedules. `keepalive.yml` makes an
   empty `chore: keepalive [skip ci]` commit only when the repo has been idle ≥40 days
   (runs 1st & 15th; has `contents: write`). Don't delete it or the crons will eventually
   stop. `workflow_dispatch` with `force: true` forces a commit.

8. **No tests, no CI lint gate.** The only "CI" is the scheduled scrapers themselves. There
   is nothing that validates a code change before it runs in production at 07:04/07:06 UTC.
   Test changes locally (§4) before pushing.

---

## 6. Known issues / open items

- **No README / human landing page** — only this AGENTS.md. (Acceptable for a personal
  scraper; add a README if the repo is shared.)
- **Misleading workflow filename** `weekly_scraper.yml` (it's the *daily Historical*
  scraper) — kept as-is to avoid breaking history; documented in §1.
- **Duplicated alert/format code** across the two files (see §5.3) is a latent drift risk.
- **Pinned deps** (`requirements.txt`): `scrapling[all]==0.2.99`, `gspread==5.12.0`,
  `google-auth==2.35.0`, `beautifulsoup4==4.12.3`, `requests==2.32.3`, `lxml==5.2.2`,
  `certifi==2024.6.2`. Bumping `scrapling` may change the Camoufox install behaviour — test
  the CI `scrapling install` step after any bump.

---

## 7. File / module map

| File | What it does |
|---|---|
| `scraper.py` | **Historical scraper** + shared library. `LeaseDeal` dataclass, `calculate_score` (1% rule), `scrape_deals` (StealthyFetcher fetch + BS4 parse), `get_google_client`/`get_spreadsheet_id`, dedup/merge/sort helpers, `send_telegram_alert`, `main()` (cumulative rewrite of `sheet1`). |
| `scraper_daily.py` | **Daily scraper.** Imports `scraper` for fetch/score/auth; owns the `Daily` tab (create/clear/keep-headers), dedups within today's scrape, `send_daily_telegram_alert`, `main()`. |
| `inspect_structure.py` | Debug helper (CLI, `-f/--file`): inspect `.deal_card`/`.calc_val` structure from a saved HTML file. Not used by CI. |
| `requirements.txt` | Pinned Python deps (see §6). |
| `.github/workflows/weekly_scraper.yml` | **Historical** cron (07:04 UTC daily) → runs `scraper.py`. |
| `.github/workflows/daily_scraper.yml` | **Daily** cron (07:06 UTC daily, +5min sleep) → runs `scraper_daily.py`. |
| `.github/workflows/keepalive.yml` | Empty-commit keepalive (1st & 15th) to prevent cron auto-disable. |
| `.gitignore` | Ignores `credentials.json`, `page_source.html`, venvs, caches, `.env*`. |

External endpoints: source = `https://pnd.leasehackr.com/`; alerts =
`https://api.telegram.org/bot<token>/sendMessage`; Sheets via gspread (Google API).
Repo: `github.com/jalalchowdhury1/leasehackr-scraper` (public).
