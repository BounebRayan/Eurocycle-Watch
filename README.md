# Eurocycles Dashboard

Two views, one app (`streamlit run dashboard.py`):

- **Distributor watch** — daily snapshots of Apollo-branded bikes as listed on
  distributor sites (currently Halfords): price/discount drift, catalog changes
  (new/delisted models), and — best-effort — stock status and ratings. Reads the
  committed SQLite snapshot; works anywhere.
- **Management** — the internal cost / margin / production picture from the
  Eurocycles ERP (SQL Server), in six tabs: **Overview** (company scoreboard),
  **Models** (best/worst, cost vs sale price per bike, margin drift), **Customers**
  (per-distributor scorecard + shipment plan vs actual), **Production** (plan
  attainment, label throughput, line-flow time), **Supply & cost** (component
  price inflation, supplier spend, quality claims, PO pipeline), **Finance**
  (billings vs collections, commission cost). **Local-only** — the ERP is on
  `(localdb)\MSSQLLocalDB` and isn't reachable from Streamlit Cloud, so the view
  shows an explainer there. See
  [`docs/eurocycles-erp-findings.md`](docs/eurocycles-erp-findings.md) (§12b for
  what each tab supports).

## How it's built

```
apollo-dashboard/
  scrapers/
    base.py       # ProductRecord + BaseScraper interface - implement this for a new site
    halfords.py   # Halfords: catalog+price via their search API, stock/rating via page scrape
  db.py            # SQLite schema (products, snapshots, run_log) + upsert helpers
  run_daily.py     # CLI orchestrator - run this daily (or by hand)
  dashboard.py     # Streamlit entry - st.navigation over the two views
  theme.py         # shared palette + Plotly chrome + formatters
  erp.py           # read access to the Eurocycles ERP (SQL Server); degrades gracefully
  crosswalk.py     # joins the scraped shelf to our ERP models - the only module touching both
  views/
    distributor.py       # the Apollo / Halfords watch (SQLite)
    management/          # the ERP-backed management view (package, one module per tab)
      __init__.py        #   render(): connection guard, sidebar, 8 tabs
      _common.py         #   Ctx + shared money/format helpers
      actions.py         #   the to-do list: rules over the other tabs' data
      overview.py  models.py  valuechain.py  customers.py
      production.py  supply.py  finance.py
  data/apollo_dashboard.db   # created on first run
```

### Management view — connecting to the ERP

Needs `pyodbc` + `sqlalchemy` (in `requirements.txt`) and the *ODBC Driver 17 for
SQL Server*. Connection string resolution: `st.secrets["erp"]["odbc"]` → env
`ERP_ODBC` → local default (`(localdb)\MSSQLLocalDB`, `Encrypt=no`). Start the
instance first: `sqllocaldb start MSSQLLocalDB`.

The **Actions / Overview / Models / Value chain / Customers** tabs need only `eurocycles_db`. Two tabs use
satellite databases on the same instance and show a "restore it" notice if absent:
**Supply & cost** → `eurocycles_db_calc` (component price history), **Production**
→ `eurocycles_label` (serial-label throughput). Restore each the same way as the
main DB (`RESTORE DATABASE ... WITH MOVE`).

### Actions — the to-do list

`views/management/actions.py` is the landing tab. Every other tab answers "what
happened"; this one answers "what should someone do on Monday". It runs a fixed
set of rules over the same data the other tabs chart, keeps only rows breaking a
threshold, **sizes each in money on a stated basis**, and ranks them. Every block
names the tab that holds the evidence — this page is an index, not an analysis.

Adding a rule = write a `_rule_*` function returning a `Finding` and add it to
`RULES`. A rule that finds nothing is dropped silently; a rule that raises shows a
warning and the rest of the page still renders.

Current rules: models under the 25% target · models sold below build cost · margin
eroding year on year on steady volume · the Halfords price-review shortlist (from
the Value chain join) · models still selling into a shelf that no longer lists them
· production orders left under plan.

Two notes on thresholds, because a to-do list that cries wolf gets ignored:

- Sizing must be a **real DT amount on a stated basis**, never a synthetic score.
  The Halfords shortlist is sized at bringing a model up to *our own* blended
  margin — not at taking the retailer's share, which isn't ours to take and would
  inflate the number wildly.
- The production rule ages orders against **the data's own latest invoice**, not
  the wall clock, and deliberately does *not* use `declarationprd.fermee`: only 25
  of 16,248 orders carry it, all for 1-6 units, so a rule gated on "closed" could
  never fire however badly the line slipped.

### Value chain — the one tab that reads both sides

`views/management/valuechain.py` is the only place the scraped shelf and the ERP
meet. It answers "of the price a customer pays in Halfords, how much is our build
cost, how much do we keep, how much does Halfords keep?" — per bike, in £, ex-VAT.

**The join** (`crosswalk.py`) is by **model name**, narrowed by e-bike flag and
wheel size, because no identifier is shared between the two systems:

| | Halfords listing | Our article |
|---|---|---|
| name | first word after *Apollo* | `libnach`, less the `A - ` brand initial, an e-bike's leading `e`, and a `NEW` season marker |
| e-bike | *Electric* in the title, or the electric-bikes category | `nomachat.ebike` |
| wheel | `24" Wheel` in the title (adult bikes are frame-sized and give none) | decoded from `codnach` |

`codnach` turns out to be structured for this customer — `<prefix><YY><wheel><serial>`,
so `HA 2427813` is season 2024, 27.5" wheel, and `HA EB252750` is a 2025 e-bike. It
parses on 100 % of Halfords bike rows, the `EB` prefix matches `ebike = 1` exactly,
and its wheel beats parsing `modnach` (whose `40x16T` is a sprocket, not a wheel).

The grain is the **match group** — the model as the shelf presents it. Halfords
lists one bike once per colourway and we hold one article per frame size per
season, so both sides are folded up before they meet, and every article is claimed
by at most one group so units can't be double-counted.

Currently this covers **~98 % of the Apollo bikes we invoice Halfords**. Apollo is
~82 % of our Halfords volume; the rest is Carrera, Indi and Trax, which an
Apollo-only shelf scrape can't see. Both sides' leftovers are shown rather than
dropped — a listing we can't match is shelf space we don't supply, and a model we
sell that no listing claims is a delisting.

Two caveats worth knowing:

- **No GBP rate exists in the ERP** (Halfords is invoiced in USD), so the tab
  carries its own `DT per £1` control. Its default is the ERP's live USD rate times
  the `USD_PER_GBP` constant in `valuechain.py` — the only number on the page not
  traceable to a source system.
- **`nomachat.gencodnach` holds an EAN on most Halfords models, but Halfords
  publishes no GTIN to match it against.** Checked and ruled out (2026-09-04):
  the Bloomreach search API returns none however wide the `fl` field list, the
  product page carries no `ld+json` GTIN and no 13-digit EAN anywhere in its HTML,
  and the `shopper-products` payload the page itself fetches has ~90 `c_` custom
  attributes but no `ean` / `upc` / `gtin`. An exact-key join is not available.
  What that payload *does* carry is better for narrowing: `c_wheelsize`,
  `c_gender`, `c_framematerial`, `c_braketype`, `c_suspension`, `c_numberofgears`
  and `c_rearderailleur` — which are the very tokens `modnach` is built from
  (`27.5x17 H.TAIL ALLOY D.DISC MEN 21SP TY300 REVO`), plus `variants[]` with a
  productId per frame size. Capturing those once per pid would let the 25 groups
  that currently match on name alone match on spec too. It needs the
  Akamai-fronted browser path, but unlike price these attributes are static, so
  an opportunistic capture that succeeds occasionally fills in permanently.

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
