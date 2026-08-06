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
| `.github/workflows/weekly_scraper.yml` | **Historical Scraper** | `scraper.py` | `54 3 * * *` (03:54 daily) | `sheet1` (first/default tab, "Historical") | **Cumulative** — merges scraped deals into all prior rows, dedups, sorts by score, rewrites the whole tab |
| `.github/workflows/daily_scraper.yml` | **Daily Scraper** | `scraper_daily.py` | `56 3 * * *` (03:56 daily) | `Daily` tab | **Snapshot** — wipes the tab and writes only today's deals, sorted by score |
| `.github/workflows/tests.yml` | Tests | pytest | on push to main | n/a | Runs `tests/` (parser/score/fetcher; no network) |
| `.github/workflows/keepalive.yml` | Keepalive | (inline shell) | `17 3 1,15 * *` (1st & 15th) | n/a (commits to repo) | Empty commit if repo idle ≥40 days, to stop GitHub auto-disabling the crons |

> ⚠️ **Naming is misleading — verify against this table, not the filenames.**
> `weekly_scraper.yml` is **not** weekly; it runs **daily** at 03:54 UTC and is the
> *Historical* (cumulative) scraper. `daily_scraper.yml` runs daily at 03:56 UTC. Both run
> every day, 2 minutes apart.
> The "weekly" filename is a historical artifact (git: "Fix: Historical scraper runs daily").

**Stack:** Python 3.9 (CI pin) · plain `requests` GET for fetching (see the fetch
chain below — the page is server-rendered; verified 2026-07-25) · BeautifulSoup4/lxml
for parsing · `gspread` + `google-auth` for Sheets · `requests` for the Telegram API.

> **2026-07-25 fetch redesign (Camoufox retired from the default path).** The site
> needs no stealth browser: plain `requests` returns the full server-rendered page
> (129 deals parsed identically to the browser fetch, no UA gating). Fetching now
> lives in `fetcher.py` as a three-tier chain — **requests → Lightpanda (on-demand
> static binary, covers future JS-rendering) → scrapling/Camoufox (only if
> installed, covers future bot-blocking)** — and raises if all tiers fail instead
> of writing an empty sheet. CI no longer installs any browser; scraper runs went
> from ~10+ min to ~1 min. **Rollback:** branch `camoufox-backup` / tag
> `v1-camoufox` hold the original implementation exactly as it was.

> **2026-08-05 regional boards (both workflows were failing since 08-04).** The
> site split the board into **seven regions** and `/` is now geo-routed to the
> *visitor's* region. A GitHub runner in Azure us-east therefore got the
> Mid-Atlantic board — which had **zero** deals — so the `deal_card` validation
> marker was missing, every tier "failed", and `fetch_html()` raised. The same
> URL from a Rhode Island laptop showed the Northeast board fine, which is why
> this looked like markup drift and wasn't.
> Two fixes: scrape **all seven `/r/<Region>` routes and union them**
> (`fetcher.fetch_all_regions()`, deduped by `deal.signature`), and validate on
> **`PAGE_MARKER` = the region filter bar** instead of `deal_card`, so a region
> with no listings is a healthy page rather than a dead source. Never scrape `/`
> — the sheet silently becomes "whatever region the runner landed in".
> This also explains the wild pre-fix deal counts (117 → 93 → 7 → 0): those were
> regions, not the board draining.

---

## 2. Architecture / data flow

```
GitHub Actions cron (daily)
   │
   ├─ scraper.py (Historical, 03:54 UTC)        ├─ scraper_daily.py (Daily, 03:56 UTC)
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
- Fetches **all seven regional boards** — `https://pnd.leasehackr.com/r/<Region>`
  for `California, Northeast, Mid-Atlantic, South, West, Northwest, Midwest` —
  via `fetcher.fetch_all_regions()`, and unions them, deduping on
  `deal.signature`. There is no "all regions" route; the union **is** the board.
  Bare `/` is geo-routed to the caller's own region and must not be scraped.
  If **any** region fails, the whole run raises (a partial board written to the
  sheet would read as "those deals are gone" and poison dedup/history).
- Each region goes through a three-tier fallback chain, every tier validated by
  the presence of `fetcher.PAGE_MARKER` (`portal_filter region deals`, the
  region filter bar) — **not** `deal_card`, so a region with zero listings is a
  healthy page. A blocked/redesigned page still falls through:
  1. **`requests` GET** (3 attempts, 30s timeout, desktop Chrome UA) — the normal
     path; the page is fully server-rendered.
  2. **Lightpanda** ([lightpanda-io/browser](https://github.com/lightpanda-io/browser),
     single static binary downloaded on demand from the nightly release into the
     temp dir, cached across same-runner invocations; `LIGHTPANDA_BIN` env
     overrides the path) — run as `fetch --dump html --wait-selector
     .portal_filter` (waiting on `.deal_card` timed out for 60s on every
     dealless region). Covers the site ever becoming JS-rendered. Both tiers
     verified live 2026-07-25: 129/129 deals each.
  3. **scrapling `StealthyFetcher`** (the original Camoufox path) — lazy import;
     silently skipped unless `scrapling[all]` is installed (it is NOT in
     `requirements.txt` any more). Covers the site ever adding bot detection.
  - If **all** tiers fail for a region, `fetch_html()` **raises** → the workflow
    fails loudly (see §5.6 — this replaced the old "silently write an empty
    sheet" behaviour). If every region fetches cleanly but nothing is listed,
    `scrape_deals()` prints a WARNING and returns `[]` — that is a real state of
    the board, not a scrape failure.
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
pip install -r requirements.txt      # no browser install needed any more

# credentials: either drop a service-account file as credentials.json in the repo root,
# or set GOOGLE_CREDENTIALS to the JSON string (CI uses the env var).
export SPREADSHEET_ID=...        # the target Google Sheet's ID
export TELEGRAM_TOKEN=...        # optional locally; alert is skipped if unset
export TELEGRAM_CHAT_ID=...      # optional locally

python scraper.py          # Historical (cumulative → sheet1)
python scraper_daily.py    # Daily (snapshot → "Daily" tab)

python -m pytest tests/ -q # 31 tests: parser (real-card fixture), score, fetch chain + regions
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

### CI install nuances
- **HISTORICAL (retired 2026-07-25):** the Camoufox-era workflows carried a
  3-attempt `scrapling install` retry loop plus a `sed`-patch of
  `camoufox/pkgman.py` in site-packages (the pip package ≤0.4.11 called the
  GitHub Releases API unauthenticated, so shared runners randomly hit the 60/hr
  anonymous 403 limit — killed the 2026-07-05 Daily run). All of that is gone
  from the workflows along with the browser install itself; if you ever need it
  back, it lives intact on the `camoufox-backup` branch.
- `pip install` still uses `--retries 5 --timeout 60` (shared-runner network flakiness).
- Both scraper jobs `timeout-minutes: 10` (typical run ~1 min); both pin Python `3.9`.

---

## 5. Gotchas / hard rules

1. **Scheduling is staggered on purpose.** Historical (03:54) runs before Daily (03:56);
   keep the 2-minute cron offset. The old extra **300s sleep** in the Daily job was
   removed 2026-07-25 — it existed to serialize the two ~10-minute Camoufox-install
   runs; now that a run is a single HTTP GET finishing in ~1 min, the cron offset
   alone provides the ordering it was buying.

   **The crons were moved 07:04/07:06 → 03:54/03:56 UTC on 2026-08-06, and the reason
   is not obvious from this repo.** Two facts drive it:
   - **GitHub starts these ~2h35m LATE, consistently.** Measured over 4 runs on both
     workflows (2026-08-04→06): cron 07:04 → actual 09:35–09:38 UTC, cron 07:06 →
     actual 09:39–09:42 UTC. Free-tier scheduled runs queue behind paid ones; the
     cron time is a *floor*, never a start time. **Never reason about when these
     land from the cron alone — check `gh run list --json createdAt`.**
   - **`fleet-health` (a separate repo, `github-notion-sync`) grades both of these
     workflows at 09:00 UTC** (5:00 AM EDT, launchd `com.jalal.fleet-health`). At
     07:04/07:06 the real runs landed 09:35–09:42, i.e. **38–42 minutes AFTER the
     grader**, so the daily fleet digest was passing/failing this repo on
     *yesterday's* run. The 48h `max_age_h` window hid it — nothing ever alerted.

   New expected real start is ~06:29/06:31 UTC, leaving ~2.5h of slack before the
   09:00 UTC check. **If you retime these again, keep the real (delayed) finish
   before 09:00 UTC**, and keep the 2-minute offset.

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

6. **A dead source fails loudly instead of writing an empty sheet — but an empty
   *board* does not.** Every fetch tier validates `PAGE_MARKER`
   (`portal_filter region deals`); if no tier produces it for some region,
   `fetcher.fetch_html()` raises and the workflow fails (you get GitHub's failure
   email). It deliberately does **not** validate `deal_card`: that conflated "the
   source is dead" with "this region has nothing listed today", and on 2026-08-04
   took both workflows down for two days once the runner's region emptied out.
   A valid page whose *inner* field classes (`.calc_val`, `.make_val`, …) drifted
   can still parse to 0/garbled deals without erroring — use
   `inspect_structure.py` on saved HTML to diagnose selector drift before
   "fixing" anything else.

7. **Keepalive exists to fight GitHub's 60-day cron auto-disable.** Scrapers never push to
   the repo, so without commits GitHub suspends the schedules. `keepalive.yml` makes an
   empty `chore: keepalive [skip ci]` commit only when the repo has been idle ≥40 days
   (runs 1st & 15th; has `contents: write`). Don't delete it or the crons will eventually
   stop. `workflow_dispatch` with `force: true` forces a commit.

8. **Tests exist (2026-07-25) but only cover pure logic.** `tests/` pins the parser
   (against a saved real-card fixture), the 1%-rule scoring, and the fetch chain's
   tier ordering/validation — `tests.yml` runs them on every push to main. They do
   NOT touch the network or Sheets, so a green test run does not prove the live
   site still parses; the scheduled scrapers remain the only end-to-end check.
   Update `tests/fixtures/deal_cards_sample.html` from a fresh page save if the
   markup ever changes.

---

## 6. Known issues / open items

- **No README / human landing page** — only this AGENTS.md. (Acceptable for a personal
  scraper; add a README if the repo is shared.)
- **Misleading workflow filename** `weekly_scraper.yml` (it's the *daily Historical*
  scraper) — kept as-is to avoid breaking history; documented in §1.
- **Duplicated alert/format code** across the two files (see §5.3) is a latent drift risk.
- **Pinned deps** (`requirements.txt`): `gspread==5.12.0`, `google-auth==2.35.0`,
  `beautifulsoup4==4.12.3`, `requests==2.32.3`, `lxml==5.2.2`, `certifi==2024.6.2`.
  `scrapling` was removed 2026-07-25 (fetch chain tier 3 lazy-imports it only if
  someone installs it manually; the pinned Camoufox world lives on `camoufox-backup`).
- **Lightpanda tier pins nothing:** tier 2 downloads the `nightly` release binary,
  which moves. If a future nightly breaks flags this repo uses (`fetch --dump html
  --wait-selector`), the tier degrades to a logged failure and the chain falls
  through — the requests tier keeps carrying the daily runs regardless.

---

## 7. File / module map

| File | What it does |
|---|---|
| `fetcher.py` | **Fetch layer.** `REGIONS` + `fetch_all_regions()` (union of the seven `/r/<Region>` boards; raises if any region fails). `fetch_html()` three-tier chain: requests → Lightpanda (on-demand binary, `LIGHTPANDA_BIN` override) → scrapling-if-installed; validates `PAGE_MARKER` (the region filter bar, **not** `deal_card`) per tier; raises if all fail. |
| `scraper.py` | **Historical scraper** + shared library. `LeaseDeal` dataclass, `calculate_score` (1% rule), `scrape_deals` (`fetcher.fetch_html()` + BS4 parse), `get_google_client`/`get_spreadsheet_id`, dedup/merge/sort helpers, `send_telegram_alert`, `main()` (cumulative rewrite of `sheet1`). |
| `scraper_daily.py` | **Daily scraper.** Imports `scraper` for fetch/score/auth; owns the `Daily` tab (create/clear/keep-headers), dedups within today's scrape, `send_daily_telegram_alert`, `main()`. |
| `inspect_structure.py` | Debug helper (CLI, `-f/--file`): inspect `.deal_card`/`.calc_val` structure from a saved HTML file. Not used by CI. |
| `requirements.txt` | Pinned Python deps (see §6). |
| `tests/` | pytest suite (31 tests): `test_parser.py` (real-card fixture in `tests/fixtures/`), `test_score.py` (1% rule), `test_fetcher.py` (tier ordering/validation, region fan-out, mocked — no network). |
| `.github/workflows/weekly_scraper.yml` | **Historical** cron (03:54 UTC daily) → runs `scraper.py`. |
| `.github/workflows/daily_scraper.yml` | **Daily** cron (03:56 UTC daily) → runs `scraper_daily.py`. |
| `.github/workflows/tests.yml` | pytest on every push to main / PR / manual. |
| `.github/workflows/keepalive.yml` | Empty-commit keepalive (1st & 15th) to prevent cron auto-disable. |
| `.gitignore` | Ignores `credentials.json`, `page_source.html`, venvs, caches, `.env*`. |

External endpoints: source = `https://pnd.leasehackr.com/`; alerts =
`https://api.telegram.org/bot<token>/sendMessage`; Sheets via gspread (Google API).
Repo: `github.com/jalalchowdhury1/leasehackr-scraper` (public).
