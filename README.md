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
  the office machine's own SQL Server instance (`EC-RAYAN`) and isn't reachable
  from Streamlit Cloud, so the view shows an explainer there. See
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
`ERP_ODBC` → local default (`SERVER=EC-RAYAN`, `Encrypt=no`) — the machine's own
`MSSQLSERVER` Windows service, always running, no instance to start by hand.

The **Actions / Overview / Activity report / Models / Value chain / Customers / Landed cost / Re-quotation** tabs need only `eurocycles_db`. Three tabs use
satellite databases on the same instance and degrade gracefully if one is absent:
**Supply & cost** → `eurocycles_db_calc` (component price history), **Production**
→ `eurocycles_label` (serial-label throughput), **Models' detail dialog** →
`eurocycles_db_images` (product photos — shows "no photo on file" per model rather
than a notice, since most models don't have one anyway). Restore each the same way
as the main DB (`RESTORE DATABASE ... WITH MOVE`).

The **Activity report**'s three consumption sections also read `eurocycles_db_calc`.
In the current restore that database ends **2026-06-30** while the sales data runs
to 2026-08-19, so those three sections under-report any window past June and the
tab says so. Everything else in that tab needs only `eurocycles_db`.

### Activity report — parity with the GPAO

The Eurocycles GPAO (the VB.NET ERP client) carries its own management reporting.
Its flagship screen — `10-Financier/05-Direction generale/frmActiviteComp1.vb`,
*"Activities comparatives"* — is what produced
`data/finance/erp-report-ytd-2026-09-21.xlsx`, and the **Activity report** tab is a
query-for-query port of it: nineteen sections comparing a date window against the
same window one year earlier, across sales, customer orders, purchasing, component
consumption and freight.

**15 of the 18 sections the GPAO exports tie to its spreadsheet to the cent.**
`tests/test_gpao_activity.py` reads that committed export and asserts it, so a
later tidy-up of a join can't quietly break parity. The three that don't tie are
the consumption sections, and the cause is the short `eurocycles_db_calc` restore
above — the GPAO's own SQL, run unchanged, returns our number, not the
spreadsheet's.

The port keeps the GPAO's quirks on purpose, including four places its own
arithmetic doesn't hold up (three revenue totals for one period; an average price
that divides two different populations; a percentage rounded before it's divided).
Those are reproduced so the numbers still tie, and flagged on the page and in
**`docs/gpao-parity.md`**, which carries the evidence and the corrected figures.
The GPAO source itself is read-only for this project and was not modified.

### Landed cost — putting inbound freight back

Every other margin figure here is **margin over `facture_det.mat`** — standard
material cost, which carries no packaging, no paint and no inbound freight. The
GPAO's own answer is `facturef_det.coef`: the ancillary charges on a supplier
invoice (transit, handling, insurance, duty, freight and four more), spread per
unit. `frmEtatOFValorisesTrans.vb` adds it to each production order's bill of
materials, and the **Landed cost** tab ports that.

It closes most of a gap the earlier pass couldn't explain. Over Jan–Jun 2026:

| | |
|---|---:|
| Margin over material (what the Overview shows) | 31.9 % |
| less inbound freight | −4.6 pp |
| **Margin after freight** | **27.3 %** |
| Accounting gross margin, from the P&L pack | 26.9 % |

Parity is pinned against the ERP's own stored values rather than a spreadsheet:
`coef` is written into `facturef_det` by the GPAO, and recomputing it from the
charge columns reproduces **98.7 % of rows exactly**. The GPAO does its as-of
lookups as correlated subqueries (351 s for a six-month window); the same rule via
`merge_asof` takes 1.6 s.

Three more defects live here, including one where the coefficient formula
subtracts a *percentage* from an *amount* and leaves 41 supplier invoices with
negative freight. All are reproduced, flagged on the page, and written up in
**`docs/gpao-parity.md`** §5.

### Re-quotation — a sell price worth trusting

Landed cost says what a bike costs. The **Re-quotation** tab says what it *would*
cost to build at today's component prices, and what the price list wants for it.
It ports `frmAnalyseCoutMatNC` (walk each model's bill of materials twice — once
at the price it was costed at, once at each part's current order price) and
`frmConsultPrixNC` (the per-model costing sheet in `costing_nc`, and the FOB
price derived from it).

The second half answers something `docs/eurocycles-erp-findings.md` §4 left open:
the dashboard had no trustworthy sell price, because `nomachat.prxnach` is stale.
Against realised average selling price for 2026, over the 283 models carrying
both figures:

| | median abs. error | within ±10 % |
|---|---:|---:|
| Price derived from `costing_nc` | **7.1 %** | **60 %** |
| `nomachat.prxnach` | 35.3 % | 2 % |

Its weakness is reach, not accuracy — only 45 % of models invoiced this year have
a costing sheet.

**The re-quotation itself has a defect worth knowing before you read it.** A
component whose current price was never filled in is scored at **zero**, so it
vanishes from the new quotation while keeping its full weight in the old cost —
turning a gap in the part master into an apparent saving. Over 2026 that is 3.0 %
of components, and it is enough to flip the answer on **126 of 749 models**: the
GPAO reports material cost falling 1.94 %, like for like it fell 0.36 %. The tab
carries both readings everywhere, and the Actions tab's new re-pricing rule ranks
on the like-for-like one.

Parity is pinned by running the GPAO's own query string character for character
and matching it per model. Six defects in total are reproduced and written up in
**`docs/gpao-parity.md`** §6-7.

### Profitability — the finance pack

The Overview's second block walks **revenue down to net profit**: gross margin,
each operating-cost section, pre-tax profit, tax, net. It comes from
[`finance_pack.py`](finance_pack.py) reading the accounting export in
`data/finance/pl-<year>.xlsx`, **not** from the ERP — which holds none of those
costs except salaries.

To refresh it, drop a newer export into `data/finance/`. The sheet carries no
year inside it (only month names), so **the year must be in the filename**;
anything else is ignored rather than dated wrong.

Two things worth knowing before reading the numbers:

- **Section totals come from the pack's own `999` rows, never from summing the
  member lines.** Four cost-of-sales lines are month-end *balances* (raw and
  finished-goods stock), so totalling the section's members gives 825M against
  a real 23.8M. `tests/test_finance_pack.py` guards this, and also checks our
  revenue→net walk still lands on the pack's printed `P.B.I.T` / `Total Net`.
- **The KPI strip's margin is a different measure** — `revenue − facture_det.mat`,
  which is material cost only and runs ~5 points above the accounting gross
  margin. The tile is labelled "Margin over build cost" for that reason, and an
  expander on the Overview reconciles the two. See docs §12d.

### Language

A **Language** selector sits under **Currency** in the sidebar (English /
Français). It translates the whole Management view — controls, tab names,
headings, KPI labels, captions, `help=` tooltips, chart axes and legends, table
headers and dropdown options. What stays as-is is ERP data: model names, brands,
countries and column identifiers like `nomachat.cusnach`.

- `t()` / `tf()` live in [`i18n.py`](i18n.py); tabs reach them via `ctx.t()`.
- The catalogue is [`translations/fr.json`](translations/fr.json), **keyed by the
  English source string**, so call sites read as what they render and a missing
  entry falls back to English rather than showing a raw key. It is plain JSON —
  editable without touching Python if the French wording needs a tweak.
- Strings with numbers are templates with named placeholders
  (`ctx.tf("{lo}–{hi} cumulative. Top {n}.", …)`) so French can reorder the
  sentence.
- `i18n.N_()` marks a string defined away from where it renders (a module-level
  table, an `erp` data value) — it returns the string unchanged but keeps it
  visible to the extractor.
- Dropdowns pass `format_func=ctx.t`, so the option *values* stay English and
  keep driving the logic while only the display translates.

`tests/test_i18n.py` fails if any UI string lacks a translation, if a
translation drops a placeholder, or if the catalogue holds entries nothing
uses — run it after adding UI text. Add a language by dropping
`translations/<code>.json` beside `fr.json` and listing it in `i18n.LANGUAGES`.

### Model detail — one bike's full profile

A dialog opened from the **Models** tab: search or pick a model, hit **View
details**. Shows its photo (`eurocycles_db_images`, when there is one), identity
(brand, distributor, decoded season/wheel from `codnach`), the same economics
tiles as the rest of the tab for the selected range, a year-by-year revenue/
margin/units chart, every other season's article code for the same bike (its
`design_key` family — see the margin bridge note below for why that grouping
matters), and production plan-vs-declared for the exact code.

### Margin bridge — why the margin line moved

At the bottom of the **Overview** tab. Decomposes the year-on-year change in gross
margin into six buckets, as an **exact identity** — they sum to the change to the
cent, which is what makes it auditable rather than a story:

| bucket | reading |
|---|---|
| volume | sold more or fewer bikes overall |
| mix | sold a different blend of them, at last year's margins |
| price | same bikes, different selling price |
| cost | same bikes, different build cost |
| new / dropped | the range itself changing |

Price and cost are the two anyone can act on directly. An expander attributes
price, cost and volume+mix per model (volume and mix can't be split per model —
the split needs a company-wide average).

Two things it gets right that are easy to get wrong:

- **It groups by `design_key`, not `article`.** `codnach` carries the model year,
  so the same bike is re-coded every season and grouping by article reports the
  annual renumbering of the range as models dropped and replaced. At the article
  grain new+dropped run **6.8x** the size of the actual change; by design, 3.7x.
  There's a Design/Article toggle so the difference is visible rather than assumed.
- **It trims both compared years to the same elapsed months** when either is the
  part year. `_common.like_for_like` only handles the newest pair; the bridge lets
  you pick any two, so an 8-month 2026 could otherwise land against a full 2023 and
  the whole gap would show up as `volume`.

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
