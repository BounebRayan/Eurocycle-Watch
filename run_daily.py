"""Daily orchestrator: run each registered distributor scraper, write a
snapshot row per product for today. Meant to be run once/day via Windows
Task Scheduler (see README.md), but safe to run by hand any time.

Examples:
    python run_daily.py                          # catalog + price + merch badges, all distributors
    python run_daily.py --no-badges               # skip the badge pass (no browser needed at all)
    python run_daily.py --enrich                  # also attempt stock/rating (slow, best-effort)
    python run_daily.py --distributor halfords --enrich --enrich-max 40
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import db
from scrapers.halfords import HalfordsScraper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_daily")

# Register new distributors here: name -> zero-arg factory returning a BaseScraper.
SCRAPERS = {
    "halfords": HalfordsScraper,
}


def run_one(name: str, query: str, bikes_only: bool, enrich: bool, enrich_max: int,
            enrich_mode: str, badges: bool) -> None:
    scraper = SCRAPERS[name]()
    started = datetime.now(timezone.utc).isoformat()
    error = None
    records = []
    enriched_count = 0
    badge_count = 0

    try:
        records = scraper.fetch_catalog(query=query, bikes_only=bikes_only)
        log.info("%s: fetched %d catalog records for query=%r", name, len(records), query)

        if badges:
            # One extra browser pass over the search listing (2 page loads,
            # not one per product). Wrapped separately so a block here can
            # never cost us the catalog+price snapshot we already have.
            try:
                badge_count = scraper.fetch_merch_badges(records, query=query)
            except Exception:
                log.exception("%s: merch badge pass failed - continuing without badges", name)

        if enrich:
            if enrich_mode == "api" and hasattr(scraper, "enrich_via_shopper_api"):
                scraper.enrich_via_shopper_api(records, max_items=enrich_max)
            else:
                scraper.enrich(records, max_items=enrich_max)
            enriched_count = sum(1 for r in records if r.rating is not None or r.in_stock is not None)
            log.info("%s: enriched %d/%d records via mode=%s (rest left as None - see scrapers/%s.py docstring)",
                      name, enriched_count, len(records), enrich_mode, name)

        db.upsert_products_and_snapshot(records)
        log.info("%s: snapshot written (%d records, %d with merch badges)", name, len(records), badge_count)
    except Exception as e:
        error = str(e)
        log.exception("%s: run failed", name)

    finished = datetime.now(timezone.utc).isoformat()
    db.log_run(started, finished, name, len(records), enriched_count, error)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily distributor catalog/price/stock snapshot")
    parser.add_argument("--distributor", choices=list(SCRAPERS.keys()) + ["all"], default="all")
    parser.add_argument("--query", default="apollo", help="Search keyword (brand name)")
    parser.add_argument("--all-categories", action="store_true",
                         help="Don't restrict to bikes - include accessories/parts too")
    parser.add_argument("--no-badges", dest="badges", action="store_false",
                         help="Skip the merchandising-badge pass (Sale/TopRated/New/BestSeller "
                              "read off Halfords' own search tiles). On by default: it's 2 page "
                              "loads total regardless of catalog size, and fails soft.")
    parser.add_argument("--enrich", action="store_true",
                         help="Also scrape stock/rating/reviews via headless browser (slow, best-effort, can get blocked)")
    parser.add_argument("--enrich-max", type=int, default=25,
                         help="Max products to enrich per run - keep this modest to stay polite")
    parser.add_argument("--enrich-mode", choices=["jsonld", "api"], default="jsonld",
                         help="jsonld (default): one page load per product, reads schema.org JSON-LD "
                              "(rating+availability). api: one seed page load + batched Salesforce "
                              "shopper-products calls (availability only, no rating) - see "
                              "scrapers/halfords.py docstring point 3 before relying on this.")
    args = parser.parse_args()

    db.init_db()
    names = list(SCRAPERS.keys()) if args.distributor == "all" else [args.distributor]
    for name in names:
        run_one(name, args.query, not args.all_categories, args.enrich, args.enrich_max,
                args.enrich_mode, args.badges)


if __name__ == "__main__":
    main()
