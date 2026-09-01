"""Halfords scraper.

Two independent passes, deliberately kept separate:

1. fetch_catalog() - hits Halfords' Bloomreach Discovery search API
   (core.dxpapi.com, a third-party search vendor's domain, not behind
   Halfords' own bot protection). Cheap, reliable, gives catalog presence +
   pricing for every matching product in a couple of requests. Safe for a
   daily cron.

2. enrich() - visits individual Halfords product pages with a real headless
   browser to read stock status + rating + review count out of each page's
   schema.org JSON-LD block. www.halfords.com sits behind Akamai bot
   protection: during development, a legitimate slow, sequential headless
   Chrome request got an "Access Denied" edge block within minutes of light
   testing. This is real and not a matter of trying harder - so enrich() is
   deliberately slow (long randomized delays, small per-run cap) and always
   fails soft: a blocked/timed-out product is just left with
   stock/rating = None rather than aborting the run or retrying aggressively.
   Treat this as a nice-to-have, not a load-bearing daily guarantee - and if
   you see it getting blocked consistently, turn it off (drop --enrich) or
   run it weekly instead of daily rather than pushing harder against it.

3. enrich_via_shopper_api() - alternative to enrich(). Loads ONE product
   page to let Halfords' own frontend (a Salesforce Commerce Cloud / PWA
   Kit storefront) perform its normal guest SLAS auth exchange, captures
   the Bearer token their own JS obtains, then replays it against
   Salesforce's shopper-products API directly - batching up to 24 product
   IDs per call instead of one page load per product. This is the same
   internal API discovered via browser devtools network tab
   (`/mobify/proxy/api/product/shopper-products/v1/...`).

   IMPORTANT CAVEATS, confirmed by direct testing while building this:
   - This does NOT get around Akamai. A bare `requests`/curl call to this
     endpoint gets an Akamai edge "Access Denied" exactly like the HTML
     pages do. A real Playwright browser can sometimes get through to the
     Salesforce API gateway itself - but in testing, one attempt succeeded
     and the very next attempt, from a brand-new browser context (not even
     a reused session), was blocked on the first request. Treat this as
     equally best-effort as enrich(), not a fix for the blocking.
   - It only returns availability/stock (`inventory.orderable`,
     `.stockLevel`) - not rating/reviews, which come from a separate
     provider Halfords' pages embed via JSON-LD. It does not replace
     enrich() for rating data.
   - Unlike fetch_catalog()'s dxpapi.com call (a public, documented search
     widget API), this hits Salesforce Commerce Cloud's shopper API using
     a guest auth token minted by Halfords' own frontend JS for its own
     use - it's a step further into "replaying an internal system's
     session" than reading data already public in page markup. Kept
     separate/opt-in from enrich() rather than replacing it for that
     reason - see README for the fuller discussion.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
import random
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from .base import BaseScraper, ProductRecord

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.halfords.com/search"

SEARCH_API = "https://core.dxpapi.com/api/v1/core/"
ACCOUNT_ID = "7940"
DOMAIN_KEY = "halfords_uk"

SHOPPER_PRODUCTS_API = (
    "https://www.halfords.com/mobify/proxy/api/product/shopper-products/v1/"
    "organizations/f_ecom_bcrp_prd/products"
)
SITE_ID = "halfords-uk"

BIKE_PATH_PREFIX = "/bikes/"
EXCLUDE_PATH_SEGMENTS = ("second-hand-bikes",)  # used bikes - exclude from new-model tracking

FL_FIELDS = "pid,title,brand,price,sale_price,low_sale_price,price_range,url,thumb_image"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class HalfordsScraper(BaseScraper):
    name = "halfords"

    def __init__(self, session: Optional[requests.Session] = None,
                 page_delay_min: float = 20.0, page_delay_max: float = 40.0):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        # Delay between listing page loads in the badge/rating walk. See
        # _walk_grid - too short and the third request of a run gets a 403.
        self.page_delay_min = page_delay_min
        self.page_delay_max = page_delay_max
        # A page that renders tiles but no badges gets retried at the same
        # offset with an exponentially longer wait before we give up on it.
        self.degraded_retries = 2
        self.degraded_backoff = 2.5

    def fetch_catalog(self, query: str = "apollo", bikes_only: bool = True) -> list[ProductRecord]:
        records: dict[str, ProductRecord] = {}
        rows, start, total = 50, 0, None

        while total is None or start < total:
            params = {
                "q": query,
                "account_id": ACCOUNT_ID,
                "domain_key": DOMAIN_KEY,
                "fl": FL_FIELDS,
                "start": start,
                "rows": rows,
                "facet.version": "3.0",
                "url": f"https://www.halfords.com/search?q={query}",
                "ref_url": f"https://www.halfords.com/search?q={query}",
                "request_type": "search",
                "search_type": "keyword",
            }
            resp = self.session.get(SEARCH_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            total = data["response"]["numFound"]
            docs = data["response"]["docs"]
            if not docs:
                break

            for d in docs:
                pid = str(d.get("pid"))
                url = d.get("url", "")
                path = urlparse(url).path
                is_bike = path.startswith(BIKE_PATH_PREFIX) and not any(
                    seg in path for seg in EXCLUDE_PATH_SEGMENTS
                )
                if bikes_only and not is_bike:
                    continue

                parts = path.strip("/").split("/")
                category_path = parts[1] if len(parts) >= 2 else None

                records[pid] = ProductRecord(
                    distributor=self.name,
                    pid=pid,
                    title=d.get("title", ""),
                    brand=d.get("brand"),
                    url=url,
                    category_path=category_path,
                    price=d.get("price"),
                    sale_price=d.get("sale_price"),
                )
            start += rows

        return list(records.values())

    # --- merchandising badges (search listing tiles) -----------------------

    # PID lives directly in the grid item's testid: product-item-287195-GridItem
    #
    # Rating markup on the tile has NOT been confirmed yet - every attempt to
    # look has hit either a 403 or a degraded render where the tile ships only
    # title/price/view-details. So this tries several shapes rather than
    # committing to one selector, and reports which one hit (see the
    # "rating via" log line) so the guess can be replaced with the real thing
    # the first time a healthy page is seen. Everything is optional: a tile
    # with no rating markup yields rating=None, never 0.
    _TILE_EXTRACT_JS = r"""() => {
      const num = (s) => { const m = String(s ?? '').match(/(\d+(?:\.\d+)?)/); return m ? parseFloat(m[1]) : null; };
      const out = {};
      document.querySelectorAll('li[data-testid^="product-item-"][data-testid$="-GridItem"]').forEach(li => {
        const m = (li.getAttribute('data-testid') || '').match(/^product-item-(.+)-GridItem$/);
        if (!m) return;

        const badges = [...li.querySelectorAll('[data-testid^="product-tile-badges-"]')]
          .map(e => {
            const b = (e.getAttribute('data-testid') || '').match(/^product-tile-badges-(.+?)-badge-/);
            return b ? b[1] : null;
          })
          .filter(Boolean);

        let rating = null, reviews = null, via = null;

        // 1. an explicit aria-label, the most stable thing a rating widget has
        const aria = [...li.querySelectorAll('[aria-label]')]
          .map(e => e.getAttribute('aria-label'))
          .find(a => /out of\s*5|star/i.test(a || ''));
        if (aria) { rating = num(aria); via = 'aria'; }

        // 2. schema.org microdata, if the tile carries it
        if (rating === null) {
          const rv = li.querySelector('[itemprop="ratingValue"]');
          if (rv) { rating = num(rv.getAttribute('content') || rv.textContent); via = 'itemprop'; }
        }

        // 3. a testid naming rating/star/score
        if (rating === null) {
          const el = [...li.querySelectorAll('[data-testid]')]
            .find(e => /rat(ing)?|star|score/i.test(e.getAttribute('data-testid') || ''));
          if (el) { rating = num(el.getAttribute('aria-label') || el.textContent); via = 'testid:' + el.getAttribute('data-testid'); }
        }

        // 4. "4.5 out of 5" anywhere in the tile text
        if (rating === null) {
          const t = li.innerText || '';
          const mm = t.match(/(\d(?:\.\d)?)\s*(?:out of|\/)\s*5/i);
          if (mm) { rating = parseFloat(mm[1]); via = 'text'; }
        }

        // review count: the "(19)" that sits beside the stars
        const rc = [...li.querySelectorAll('[data-testid]')]
          .find(e => /review|count/i.test(e.getAttribute('data-testid') || ''));
        if (rc) reviews = num(rc.textContent);
        if (reviews === null) {
          const mm = (li.innerText || '').match(/\((\d+)\)/);
          if (mm) reviews = parseInt(mm[1], 10);
        }

        out[m[1]] = { badges: [...new Set(badges)], rating, reviews, via };
      });
      return out;
    }"""

    # Kept as an alias so the badge-only callers and tests keep working.
    _BADGE_EXTRACT_JS = _TILE_EXTRACT_JS

    def fetch_merch_badges(
        self,
        records: list[ProductRecord],
        query: str = "apollo",
        page_size: int = 18,
        max_pages: int = 10,
    ) -> int:
        """Read Sale/TopRated/New/BestSeller badges off Halfords' own search
        tiles and attach them to matching records in place.

        `start` on the search URL is CUMULATIVE, not a slice - ?start=144
        server-renders results 0..151, not just 144..162. So when we can read
        the result total we jump straight to the final offset and get the whole
        grid in 2 page loads. If the total can't be read (the count element is
        client-rendered and isn't always present when we look) we fall back to
        walking start= forward until the tile count stops growing.

        Only `q` and `start` are ever sent. Halfords' WAF 403s on params their
        own UI doesn't emit - ?sz=72 was rejected outright while the identical
        URL without it returned 200. Don't add any.

        Like enrich(), this needs a real browser to get past Akamai and fails
        soft: on a block, records keep merch_badges=None ("not observed")
        rather than [] ("observed, no badges"). Returns the number of records
        it set badges on.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed - skipping merch badges")
            return 0

        by_pid = {r.pid: r for r in records}
        if not by_pid:
            return 0

        base = f"{SEARCH_URL}?q={query}"
        scraped: dict[str, list[str]] = {}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=USER_AGENT, viewport={"width": 1366, "height": 900}, locale="en-GB"
            )
            page = context.new_page()
            try:
                scraped = self._load_grid(page, base)
                if not scraped:
                    log.warning("merch badges: first search page returned nothing "
                                "(blocked, or markup changed) - leaving all records unobserved")
                    browser.close()
                    return 0

                total = self._parse_result_total(page)
                # The jump-to-last-offset shortcut is gone. Tried twice live,
                # degraded both times: ?start=144 renders 151 tiles but the
                # badge/rating components never hydrate on a page that big.
                # Walking in page_size steps is slower but actually returns data.
                scraped = self._walk_grid(page, base, scraped, page_size,
                                          max_pages, total)
            except Exception as e:
                log.warning("merch badges: scrape failed (%s) - keeping whatever was read", e)
            finally:
                browser.close()

        if not scraped:
            return 0

        # Tiles found but not a single badge anywhere is far more likely a
        # hydration failure than the truth (some product in a 100+ catalog is
        # always on Sale). Confirmed live: the grid can render 200 OK with only
        # title/price/view-details while the badge, compare and rating
        # components never hydrate. Writing that would record a page-wide false
        # negative as "observed, no badges", so refuse it and leave all None.
        if not any(t.get("badges") for t in scraped.values()):
            log.warning("merch badges: read %d tiles but zero badges on any of them - "
                        "treating as a failed read, not as 'no badges' (the page "
                        "renders in a degraded state when the client is flagged)",
                        len(scraped))
            return 0

        hits = rated = 0
        vias: Counter[str] = Counter()
        for pid, tile in scraped.items():
            rec = by_pid.get(str(pid))
            if rec is None:
                continue  # accessory/non-bike listing we don't track
            rec.merch_badges = sorted(tile.get("badges") or [])
            hits += 1

            # Rating/reviews are a bonus from the same tiles - see the note on
            # _TILE_EXTRACT_JS. enrich() runs after this and overwrites with the
            # product page's JSON-LD when it gets through, which is authoritative.
            rating = tile.get("rating")
            if rating is not None:
                rec.rating = float(rating)
                rated += 1
                if tile.get("via"):
                    vias[tile["via"]] += 1
            reviews = tile.get("reviews")
            if reviews is not None:
                rec.review_count = int(reviews)

        if rated:
            log.info("merch badges: also read rating for %d tiles via %s", rated, dict(vias))
        else:
            log.info("merch badges: no rating markup found on any tile - "
                     "rating still needs enrich()")

        unmatched = len(records) - hits
        log.info("merch badges: read %d tiles, matched %d/%d tracked records%s",
                 len(scraped), hits, len(records),
                 f" ({unmatched} not on the listing pages we read)" if unmatched else "")
        return hits

    def _load_grid(self, page, url) -> dict[str, list[str]]:
        """Navigate and extract pid -> badges. Returns {} on a block/timeout so
        callers can distinguish 'nothing there' from 'never looked'."""
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.info("merch badges: navigation to %s failed: %s", url, e)
            return {}
        if resp is None or resp.status >= 400:
            log.info("merch badges: %s returned %s", url, resp.status if resp else "no-response")
            return {}
        try:
            page.wait_for_selector("li[data-testid^='product-item-']", timeout=20000)
        except Exception:
            pass  # extraction below just returns {} if the grid never rendered

        # Tiles and their badges hydrate separately, and off-screen tiles on a
        # long cumulative page don't render badges until scrolled near. Without
        # this the extract returns every pid with an EMPTY badge list, which is
        # indistinguishable from "genuinely unbadged" and silently poisons the
        # data. Scroll the grid through, then wait for at least one badge.
        try:
            self._scroll_through(page)
            page.wait_for_selector("[data-testid^='product-tile-badges-']", timeout=15000)
        except Exception:
            log.info("merch badges: no badge element rendered on %s", url)
        page.wait_for_timeout(2000)

        try:
            return page.evaluate(self._BADGE_EXTRACT_JS) or {}
        except Exception as e:
            log.info("merch badges: extraction failed on %s: %s", url, e)
            return {}

    @staticmethod
    def _scroll_through(page, step: int = 1200, max_steps: int = 60) -> None:
        """Scroll to the bottom in increments so every tile enters the viewport
        once and hydrates its badges, then return to the top."""
        try:
            for _ in range(max_steps):
                at_end = page.evaluate(
                    "(s) => { const y = window.scrollY; window.scrollBy(0, s);"
                    " return window.scrollY === y; }", step
                )
                page.wait_for_timeout(180)
                if at_end:
                    break
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
        except Exception as e:
            log.debug("merch badges: scroll pass failed: %s", e)

    @staticmethod
    def _tile_score(tile) -> int:
        """How much usable data a tile carries. Used to pick a winner when the
        same pid is read twice."""
        return (1 if tile.get("badges") else 0) + (1 if tile.get("rating") is not None else 0)

    @classmethod
    def _merge_tiles(cls, base: dict, incoming: dict) -> dict:
        """Keep the richest record per pid rather than taking the newer page
        wholesale.

        `start=` is cumulative, so every load re-renders all earlier tiles. A
        later page can therefore return MORE tiles while hydrating none of
        their badge/rating components - taking it wholesale then discards good
        data already read. Observed live 2026-09-01: pages 1-4 carried badges,
        page 5 came back degraded with 90 tiles and zero badges, and the whole
        run was thrown away as a failed read.
        """
        out = dict(base)
        for pid, tile in incoming.items():
            old = out.get(pid)
            if old is None or cls._tile_score(tile) > cls._tile_score(old):
                out[pid] = tile
        return out

    @staticmethod
    def _is_degraded(tiles: dict) -> bool:
        """Tiles rendered but not one badge among them. On a page whose first
        rows are known-badged products that means the components never
        hydrated - not that nothing is on sale."""
        return bool(tiles) and not any(t.get("badges") for t in tiles.values())

    def _walk_grid(self, page, base, scraped, page_size, max_pages, total=None):
        """Step start= forward until no new pids appear.

        Two distinct failure modes, handled differently:

        - **403** - we're rate-limited. Stop; more requests make it worse.
        - **Degraded** (tiles render, no badges hydrate) - retry the SAME
          offset after a long backoff rather than walking on, because walking
          on just requests an even bigger page. If it degrades again we
          re-probe start=0: if that page is healthy the problem is page size,
          not our standing, and the log says so.

        Partial results are kept: products we never saw a tile for keep
        merch_badges=None ("not observed").
        """
        for _ in range(max_pages):
            if total and len(scraped) >= total:
                break

            nxt = None
            for attempt in range(self.degraded_retries + 1):
                delay = random.uniform(self.page_delay_min, self.page_delay_max)
                if attempt:
                    delay *= self.degraded_backoff ** attempt
                    log.info("merch badges: offset %d degraded - backing off %.0fs "
                             "and retrying the same page", len(scraped), delay)
                time.sleep(delay)
                nxt = self._load_grid(page, f"{base}&start={len(scraped)}")
                if not nxt or not self._is_degraded(nxt):
                    break

            if not nxt:
                log.info("merch badges: walk stopped at %d tiles (blocked) - "
                         "keeping what we have", len(scraped))
                break

            if self._is_degraded(nxt):
                self._diagnose_degraded(page, base, len(nxt))
                break

            merged = self._merge_tiles(scraped, nxt)
            if len(merged) <= len(scraped):
                break  # no new products on that page - we're done
            scraped = merged
        return scraped

    def _diagnose_degraded(self, page, base, tile_count) -> None:
        """Re-probe page one to tell 'page too big' apart from 'we're flagged'.
        Costs one small request and turns an ambiguous stall into a fact."""
        time.sleep(random.uniform(self.page_delay_min, self.page_delay_max))
        probe = self._load_grid(page, base)
        if not probe:
            log.warning("merch badges: stopped at %d tiles - page one is blocked too, "
                        "so this is our standing with the edge, not page size. "
                        "Leave it longer before the next run.", tile_count)
        elif self._is_degraded(probe):
            log.warning("merch badges: stopped at %d tiles - page one is degraded too, "
                        "so the whole session is being served stripped pages. "
                        "Leave it longer before the next run.", tile_count)
        else:
            log.warning("merch badges: stopped at %d tiles - but page one still renders "
                        "badges fine, so this is PAGE SIZE, not rate limiting. Waiting "
                        "longer will not help; the cumulative grid is simply too big to "
                        "hydrate past ~%d tiles.", tile_count, tile_count)

    @staticmethod
    def _parse_result_total(page) -> Optional[int]:
        """Pull N out of the grid's "Showing 18 of N results" line. Reads whole
        page text rather than a testid - the count element is client-rendered
        and its testid isn't reliably present when we look."""
        for getter in ("() => document.body.innerText",
                       "() => document.body.textContent"):
            try:
                text = page.evaluate(getter) or ""
            except Exception:
                continue
            m = re.search(r"of\s+([\d,]+)\s+results", text)
            if m:
                return int(m.group(1).replace(",", ""))
        return None

    def enrich(
        self,
        records: list[ProductRecord],
        max_items: Optional[int] = 25,
        min_delay: float = 6.0,
        max_delay: float = 15.0,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed - skipping enrichment (pip install playwright && playwright install chromium)")
            return

        targets = records if max_items is None else records[:max_items]
        if not targets:
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})

            for i, rec in enumerate(targets):
                if i > 0:
                    time.sleep(random.uniform(min_delay, max_delay))
                page = None
                try:
                    page = context.new_page()
                    resp = page.goto(rec.url, wait_until="domcontentloaded", timeout=30000)
                    if resp is None or resp.status >= 400:
                        log.info("enrich: got status %s for pid=%s - leaving stock/rating unset",
                                  resp.status if resp else "no-response", rec.pid)
                        continue

                    try:
                        page.wait_for_selector("script[type='application/ld+json']", timeout=10000)
                    except Exception:
                        pass  # fine, we'll just find zero blocks below

                    blocks = page.eval_on_selector_all(
                        "script[type='application/ld+json']", "els => els.map(e => e.textContent)"
                    )
                    for raw in blocks:
                        try:
                            data = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if not isinstance(data, dict) or data.get("@type") != "Product":
                            continue

                        offer = data.get("offers") or {}
                        avail = offer.get("availability", "") or ""
                        rec.availability_raw = avail
                        rec.in_stock = ("InStock" in avail) if avail else None

                        agg = data.get("aggregateRating") or {}
                        if agg:
                            rec.rating = agg.get("ratingValue")
                            rec.review_count = agg.get("reviewCount")
                except Exception as e:
                    log.warning("enrich failed for pid=%s: %s", rec.pid, e)
                finally:
                    if page is not None:
                        try:
                            page.close()
                        except Exception:
                            pass

            browser.close()

    def enrich_via_shopper_api(
        self,
        records: list[ProductRecord],
        max_items: Optional[int] = 25,
        batch_size: int = 24,
        batch_min_delay: float = 4.0,
        batch_max_delay: float = 9.0,
    ) -> None:
        """Alternative to enrich() - see module docstring point 3 for the
        full explanation and caveats. Only fills in in_stock/availability_raw;
        does not touch rating/review_count."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed - skipping shopper-api enrichment")
            return

        targets = records if max_items is None else records[:max_items]
        if not targets:
            return

        captured_token: dict[str, str] = {}

        def on_request(req):
            if "value" in captured_token:
                return
            if "shopper-products/v1" not in req.url:
                return
            headers = req.headers
            auth = headers.get("authorization") or headers.get("Authorization")
            if auth and auth.lower().startswith("bearer "):
                captured_token["value"] = auth

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 900})
            context.on("request", on_request)

            page = context.new_page()
            try:
                # Load one real product page so Halfords' own frontend does its
                # normal guest SLAS auth exchange - we just capture the token
                # it obtains rather than requesting one ourselves.
                page.goto(targets[0].url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(6000)
            except Exception as e:
                log.warning("enrich_via_shopper_api: seed page load failed: %s", e)

            if "value" not in captured_token:
                log.warning(
                    "enrich_via_shopper_api: never captured a bearer token (blocked, or "
                    "Halfords changed how their frontend authenticates) - leaving all records unset"
                )
                browser.close()
                return

            token = captured_token["value"]
            by_pid = {r.pid: r for r in targets}

            # NOTE: batches are issued as real in-page fetch() calls via
            # page.evaluate(), not context.request.get(). Confirmed by direct
            # testing that the two are NOT equivalent here even with the same
            # cookies/token: Playwright's out-of-band APIRequestContext got a
            # 403 on this endpoint while an identical call run as genuine
            # in-page JS got 200 - the API gateway (Akamai and/or Salesforce's
            # own WAF) distinguishes real browser-originated requests from
            # Playwright's separate request-context network stack.
            fetch_js = """async ({url, params, token}) => {
                const qs = new URLSearchParams(params).toString();
                const resp = await fetch(url + '?' + qs, { headers: { Authorization: token } });
                const status = resp.status;
                let body = null;
                try { body = await resp.json(); } catch (e) { /* non-JSON error body */ }
                return { status, body };
            }"""

            for i in range(0, len(targets), batch_size):
                chunk = targets[i:i + batch_size]
                ids = ",".join(r.pid for r in chunk)
                if i > 0:
                    time.sleep(random.uniform(batch_min_delay, batch_max_delay))
                try:
                    result = page.evaluate(fetch_js, {
                        "url": SHOPPER_PRODUCTS_API,
                        "params": {"ids": ids, "siteId": SITE_ID, "locale": "en-GB", "currency": "GBP"},
                        "token": token,
                    })
                    if result["status"] >= 400:
                        log.info("enrich_via_shopper_api: batch status %s for ids=%s - leaving unset",
                                  result["status"], ids)
                        continue
                    data = result["body"] or {}
                    for item in data.get("data", []):
                        rec = by_pid.get(str(item.get("id")))
                        if rec is None:
                            continue
                        inv = item.get("inventory") or {}
                        if inv:
                            rec.in_stock = inv.get("orderable")
                            rec.availability_raw = json.dumps(inv)
                except Exception as e:
                    log.warning("enrich_via_shopper_api: batch failed for ids=%s: %s", ids, e)

            browser.close()
