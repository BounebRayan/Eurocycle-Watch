"""Shared interface every per-site scraper implements.

Adding a new distributor = create scrapers/<name>.py with a class that
subclasses BaseScraper and implement fetch_catalog() (required) and
optionally enrich() (stock/rating/reviews - best effort). Then register it
in run_daily.py's SCRAPERS dict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProductRecord:
    """One normalized product observation. Every distributor scraper produces
    a list of these so the rest of the pipeline (db, dashboard) never has to
    know site-specific field names."""

    distributor: str
    pid: str                       # distributor's own product id/SKU
    title: str
    brand: Optional[str] = None
    url: str = ""
    category_path: Optional[str] = None

    price: Optional[float] = None
    sale_price: Optional[float] = None

    in_stock: Optional[bool] = None
    availability_raw: Optional[str] = None

    rating: Optional[float] = None
    review_count: Optional[int] = None

    # Merchandising badges the distributor shows on its own search tiles
    # (e.g. Sale / TopRated / New / BestSeller). Tri-state on purpose:
    #   None = not observed this run (badge pass skipped or blocked)
    #   []   = observed, product genuinely carries no badges
    # Don't collapse those two - "no badges" and "we couldn't look" mean
    # very different things to a trend chart.
    merch_badges: Optional[list[str]] = None


class BaseScraper:
    name: str = "base"

    def fetch_catalog(self, query: str, **kwargs) -> list[ProductRecord]:
        """Cheap, reliable pass: catalog presence + pricing. Should be safe
        to run daily without special-casing rate limits beyond politeness."""
        raise NotImplementedError

    def enrich(self, records: list[ProductRecord], **kwargs) -> None:
        """Optional, best-effort, in-place enrichment (stock/rating/reviews).
        Default no-op - override only if the site can be enriched safely."""
        return

    def fetch_merch_badges(self, records: list[ProductRecord], **kwargs) -> int:
        """Optional, in-place: fill in merch_badges from the distributor's own
        search/listing tiles. Separate from enrich() because it reads a listing
        page rather than one page per product, so it's cheap enough to run
        daily. Returns how many records it set badges on. Default no-op."""
        return 0
