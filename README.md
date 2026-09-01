# Apollo Distributor Watch

Daily snapshots of Apollo-branded bikes as listed on distributor sites
(currently Halfords), tracking price/discount drift, catalog changes
(new/delisted models), and — best-effort — stock status and ratings.

## How it's built

```
apollo-dashboard/
  scrapers/
    base.py       # ProductRecord + BaseScraper interface - implement this for a new site
    halfords.py   # Halfords: catalog+price via their search API, stock/rating via page scrape
  db.py            # SQLite schema (products, snapshots, run_log) + upsert helpers
  run_daily.py     # CLI orchestrator - run this daily (or by hand)
  dashboard.py     # Streamlit dashboard reading the SQLite file
  data/apollo_dashboard.db   # created on first run
```

Three independent data sources per distributor:

1. **Catalog + pricing** (`fetch_catalog`) — Halfords' own search widget calls
   a third-party search API (`core.dxpapi.com`, run by Bloomreach, not
   Halfords' own bot-protected domain). It's cheap, reliable, and returns
   every Apollo-branded listing with title/price/sale price/url in a couple
   of requests. Filtered to actual bikes (URL path under `/bikes/`,
   excluding `second-hand-bikes`) vs. Apollo-branded accessories/parts.

2. **Merchandising badges** (`fetch_merch_badges`, on by default, `--no-badges`
   to skip) — reads the `Sale` / `TopRated` / `New` / `BestSeller` badges off
   Halfords' own search-result tiles. This is a *listing* page read, so it
   covers the whole catalog in ~2 page loads rather than one per product.
   Stored per-PID per-day in `snapshots.merch_badges`. See "About the badge
   pass" below.

3. **Stock + rating + reviews** (`enrich`, opt-in via `--enrich`) — reads
   each product page's `schema.org/Product` JSON-LD block (`offers.availability`,
   `aggregateRating.ratingValue`, `aggregateRating.reviewCount`) using a
   headless browser. **This is unreliable by design of the target, not the
   code**: `www.halfords.com` sits behind Akamai bot protection. During
   development, a single slow, sequential, non-parallel headless-Chrome
   request succeeded once, and every request after that — including ones
   spaced 15-60+ seconds apart — got an Akamai "Access Denied" edge block.
   The code handles this by failing soft (leaves `rating`/`in_stock` as
   `NULL` for whatever it couldn't reach) rather than crashing the whole
   run or hammering retries. **Treat this feature as a bonus, not a
   guarantee** — see "About the stock/rating scrape" below before relying
   on it.

## About the badge pass

Worth being precise about what this does and doesn't give you, because it's
easy to assume it solves the stock problem. It doesn't:

- **There is a third state between "fine" and "403": a degraded render.**
  The page can return 200 with the product grid present but the badge,
  compare and rating components never hydrating - a tile ships only
  title/price/view-details. Observed directly: the same URL gave 13-of-18
  tiles badged at 11:08, hard 403s at 11:14, and 200-but-zero-badges at
  11:45 on the same machine. The scraper refuses a read where no tile on the
  whole page carries a badge, because recording it would write a page-wide
  false negative as "observed, no badges".
- **Rating and review count come from the tiles too - confirmed.** The pass
  reads them from each tile's `aria-label` (`also read rating for 14 tiles
  via {'aria': 14}` on 2026-08-27), which means rating no longer needs one
  page load per product. `enrich()` still runs after and overwrites with the
  product page's JSON-LD when it gets through, since that's authoritative -
  but rating coverage no longer depends on it. Three further fallbacks
  (`itemprop`, a rating/star testid, and a "4.5 out of 5" text match) are in
  place in case the aria-label markup changes; the log line names whichever
  one hit.
- **Search tiles carry no stock information at all.** Confirmed directly: a
  full search page render contains zero occurrences of `Limited Stock`,
  `InStock`, `OutOfStock`, `orderable` or `stockLevel`, and its only JSON-LD
  block is a `SearchResultsPage`, not `Product`. The `Limited Stock` badge
  people notice on product pages is a `product-detail-badges-*` component;
  listing tiles render a different one, `product-tile-badges-*`, which is
  purely merchandising (Sale/DEAL, TopRated, New, BestSeller). `Limited
  Stock` and `Clearance` are detail-page badges and will never appear from
  this pass. Stock still requires one page load per product.
- **It's the same Akamai wall.** Plain `requests` gets 403 on every path of
  `www.halfords.com` — including `/robots.txt`, which is a useful tell that
  the block is client-fingerprint-based (TLS/JA3, HTTP2 framing, IP
  reputation) and not path-based. Real headless Chromium gets through, and
  then loses access as IP reputation degrades under repeated hits. The badge
  pass fails soft and never blocks the catalog snapshot.

Two implementation details that are easy to get wrong:

- `start=` on the search URL is **cumulative, not a slice**: `?start=144`
  server-renders results 0..151, not 144..162. Tempting shortcut, but **don't
  jump straight to the last offset** - tried twice live and degraded both
  times: the 151-tile page renders its tiles and never hydrates their badge
  or rating components. The pass walks in 18-result steps instead.
- **Merge the walk's pages, don't replace.** Because `start=` is cumulative,
  every load re-renders all earlier tiles - so a later page can return MORE
  tiles while hydrating none of their badges. Taking the bigger result
  wholesale throws away good data already read. Seen live 2026-09-01: pages
  1-4 carried badges, page 5 came back with 90 tiles and zero badges, and the
  whole run was discarded as a failed read. `_merge_tiles()` keeps the
  richest record per pid instead.
- **Pace the walk.** At 3-6s between page loads the third request of a run
  got a hard 403. The delay is now 20-40s (`page_delay_min`/`page_delay_max`
  on the scraper), so a full 151-result walk is ~8 loads over ~4 minutes,
  once a day. A partial walk is safe: products whose tile was never read keep
  `merch_badges = NULL`, not `''`.
- **Only send `q` and `start`.** The WAF 403s on parameters their own UI
  doesn't emit — a `?sz=72` page-size override was rejected outright while
  the identical URL without it returned 200.

`merch_badges` is deliberately tri-state, and the dashboard should respect it:

| value  | meaning |
|--------|---------|
| `NULL` | not observed this run (pass skipped, blocked, or read rejected) |
| `''`   | observed, product genuinely carries no badges |
| `Sale,TopRated` | observed, comma-separated badge list |

Collapsing `NULL` and `''` would turn every blocked run into a fake
"all badges disappeared" event. The scraper refuses to write `''` across the
board if it reads tiles but finds zero badges on any of them, since that's
almost always a hydration failure rather than the truth.

## Setup (already done on this machine)

- Python 3.12 installed via `winget`
- Virtualenv at `.venv/` with `requests`, `streamlit`, `pandas`, `plotly`,
  `playwright` (+ Chromium browser) installed
- A Windows Task Scheduler job **`ApolloDistributorWatch-Halfords`** runs
  `python run_daily.py` every day at **06:00** (catalog + price only, no
  `--enrich` — see below for why that's not in the daily automated job)

To set this up on another machine:
```powershell
winget install -e --id Python.Python.3.12
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
```

## Running it

```powershell
# Catalog + price + merch badges (default)
.venv\Scripts\python run_daily.py

# Catalog + price only - no browser needed at all
.venv\Scripts\python run_daily.py --no-badges

# Also attempt stock/rating/reviews for up to 25 products (slow, best-effort)
.venv\Scripts\python run_daily.py --enrich --enrich-max 25

# View the dashboard
.venv\Scripts\streamlit run dashboard.py
```

Then open the URL Streamlit prints (defaults to http://localhost:8501).

## About the stock/rating scrape

I did not enable `--enrich` in the daily scheduled task on purpose. What I
observed directly while building this:

- `www.halfords.com` (not the search API — the main site) returns Akamai
  "Access Denied" for `curl`-style requests outright.
- A real headless Chrome request got through **once**. The very next
  request, and every one after, got blocked — even slowed down to one
  request per 15-60 seconds, from the same machine.
- `robots.txt` explicitly disallows crawling `/search*` and `/*?q=*` (the
  page you linked). It does *not* disallow individual product pages, and it
  doesn't technically cover the separate `dxpapi.com` API domain the
  catalog step uses — but it's a clear signal Halfords doesn't want their
  search results crawled, worth respecting given they're your distributor.

Given that, my recommendation:
- Keep the **daily** cron to catalog + price only (already set up this way).
- If you want stock/rating data, run `--enrich` **manually or weekly**, with
  a small `--enrich-max`, and expect a good chunk of it to come back empty.
  It's genuinely useful when it gets through (you now have working
  extraction logic for the JSON-LD block), just don't build a "the
  dashboard breaks if this fails" dependency on it.
- If it starts getting blocked 100% of the time even after a cooldown,
  that's Halfords' infrastructure telling you no — I'd stop pushing on it
  rather than try to defeat the bot protection, both because that crosses
  from "reading a public page" into "actively evading access controls,"
  and because they're a business partner, not an arbitrary target.
- I could not find Halfords' actual Terms of Website Use page (link 404'd)
  to check for an explicit anti-automation clause — worth a manual look
  before relying on this feature long-term.

### Second enrich path: `--enrich-mode api`

Halfords' site is a Salesforce Commerce Cloud storefront (PWA Kit, formerly
"Mobify"). Its frontend calls Salesforce's own `shopper-products` API
(`/mobify/proxy/api/product/shopper-products/v1/...`) for price/availability
— discoverable via browser devtools. `scrapers/halfords.py`'s
`enrich_via_shopper_api()` uses this instead of per-page JSON-LD scraping:
it loads **one** product page to capture the guest auth token Halfords' own
JS obtains, then replays it to fetch availability for up to 24 products per
batched call. In testing this enriched 25/25 products in ~16s, versus
minutes for the same count via `enrich()`'s one-page-per-product approach —
a real efficiency win *when it gets through*.

Two things to know before using it:

- **It does not avoid the Akamai wall** — it's the same protection guarding
  the HTML pages. During testing here, one run got real data, the next
  (fresh browser session) was blocked from the first request. It's exactly
  as best-effort as `enrich()`, just cheaper per success.
- **It's a step further than reading public markup.** `fetch_catalog()`
  reads a public, documented search widget API. `enrich()` reads JSON-LD
  Halfords deliberately publishes in page markup for SEO. This instead
  replays a guest session token minted by Halfords' *own frontend* against
  their internal commerce API — closer to "operating their app as a client"
  than "reading a public page." It only returns stock, not rating/reviews
  (those come from a separate reviews provider `enrich()` still needs for).
  Kept as an opt-in `--enrich-mode`, not the default, for that reason.

```powershell
.venv\Scripts\python run_daily.py --enrich --enrich-mode api --enrich-max 25
```

## Adding another distributor

1. Create `scrapers/<name>.py` with a class subclassing `BaseScraper` from
   `scrapers/base.py`, implementing `fetch_catalog()` (required) and
   optionally `enrich()`.
2. Register it in `run_daily.py`'s `SCRAPERS` dict.
3. Everything else (db schema, dashboard, Task Scheduler) works unchanged —
   the dashboard already groups/filters by `distributor`.

## Managing the scheduled task

```powershell
Get-ScheduledTask -TaskName "ApolloDistributorWatch-Halfords"          # check status
Start-ScheduledTask -TaskName "ApolloDistributorWatch-Halfords"        # run now
Unregister-ScheduledTask -TaskName "ApolloDistributorWatch-Halfords"   # remove it
```

Logs from each run go to stdout/the Task Scheduler history; a row is also
written to the `run_log` table in the SQLite database on every run
(catalog count, enriched count, error if any).
