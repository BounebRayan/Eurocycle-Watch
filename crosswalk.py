"""Bridge between the distributor's shelf and the models we build for it.

The Distributor view knows what Halfords *charges*; the Management view knows
what a bike *costs us* and what we *invoice*. Nothing joined them, so the one
question management actually asks — "of the price the end customer pays, how
much do we keep?" — had no answer. This module is that join.

**There is no shared identifier.** Halfords' `pid` is theirs; our `codnach` is
ours; `nomachat.gencodnach` holds an EAN on ~60 % of Halfords models but the
scrape doesn't capture one to match it against. So the join is by **model
name**, narrowed by e-bike flag and wheel size:

    "Apollo Gridlok Junior Mountain Bike - 24\" Wheel"   (retail listing)
     →  GRIDLOK · not-electric · 24"
    "A - GRIDLOK 24 BOY ALLOY H.TAIL BLUE"  `HA 2424330`  (our article)
     →  GRIDLOK · not-electric · 24"   (wheel decoded from the code)

Grain is the **match group** — one model as the shelf presents it — not the
listing and not the article. Halfords lists the same bike several times (one
per colour) and we hold one article per frame size per season, so both sides
are many; joining listing-to-article would multiply units by both fan-outs.

Every group carries a `confidence`, and both sides' leftovers are returned
rather than dropped: a retail listing we can't match is a bike Halfords sells
that we don't build, and that is worth seeing on its own.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

SHELF_DB = Path(__file__).parent / "data" / "apollo_dashboard.db"

# Scraped distributor -> `customer.codcust` in the ERP. Halfords is codcust 13
# (`abrev` HA, `codecli` 2005). Adding a distributor to the watch means adding
# it here too, or its shelf has nothing to join to.
DISTRIBUTOR_CUSTOMER = {"halfords": 13}

# The distributor's own brand words, stripped before the model name is read.
# "Apollo Gridlok ..." -> GRIDLOK. A title that starts with something else
# (Halfords stocks a few non-Apollo bikes the Apollo search returns) keeps its
# first word and simply won't match anything of ours — which is the truth.
_RETAIL_BRANDS = ("APOLLO",)

_WHEEL_IN_TITLE = re.compile(r'(\d{1,2}(?:\.\d)?)\s*(?:"|”|IN\b|INCH\b)')
_FIRST_WORD = re.compile(r"[A-Z]+")
_ERP_BRAND_PREFIX = re.compile(r"^[A-Z]\s*-\s*")
_SEASON_MARKER = re.compile(r"^(?:NEW|NEW MODEL|SAMPLE)\s+")


# ------------------------------------------------------------- the shelf ---
@st.cache_data(ttl=300, show_spinner=False)
def load_shelf(distributor: str = "halfords") -> tuple[pd.DataFrame, str]:
    """One row per live listing on the distributor's newest snapshot, with the
    model key parsed out of the title. Returns `(listings, snapshot_date)`.

    `shelf_price` is what the shelf charges today (`sale_price`, falling back to
    `price`); `rrp` is the un-discounted price beside it. Both are the
    distributor's retail price and so **include VAT** — the caller strips it
    before any comparison with our ex-works invoice."""
    if not SHELF_DB.exists():
        return pd.DataFrame(), ""
    with sqlite3.connect(SHELF_DB) as conn:
        latest = pd.read_sql_query(
            "SELECT MAX(snapshot_date) AS d FROM snapshots WHERE distributor = ?",
            conn, params=(distributor,))["d"].iloc[0]
        if not latest:
            return pd.DataFrame(), ""
        df = pd.read_sql_query("""
            SELECT p.pid, p.title, p.category_path, p.url,
                   s.price AS rrp, s.sale_price, s.discount_pct, s.in_stock
            FROM products p
            JOIN snapshots s ON s.distributor = p.distributor AND s.pid = p.pid
            WHERE p.distributor = ? AND s.snapshot_date = ?
        """, conn, params=(distributor, latest))

    df["shelf_price"] = df["sale_price"].fillna(df["rrp"])
    df = df[df["shelf_price"] > 0].copy()
    keys = df.apply(lambda r: _retail_key(r["title"], r["category_path"]), axis=1)
    df["model"] = [k[0] for k in keys]
    df["ebike"] = [k[1] for k in keys]
    df["wheel_in"] = [k[2] for k in keys]
    return df, str(latest)


def _retail_key(title: str, category: str | None) -> tuple[str, int, float | None]:
    """`("Apollo Phaze-E Electric Mountain Bike - M, L Frames", ...)`
    -> `("PHAZE", 1, None)`.

    Wheel size is only present on the titles of bikes sized by wheel (kids,
    junior, folding); adult bikes are sized by frame ("S, M, L Frames") and
    return `None`, which the matcher reads as "don't narrow on wheel"."""
    t = (title or "").upper().strip()
    for brand in _RETAIL_BRANDS:
        t = re.sub(rf"^{brand}\s+", "", t)

    ebike = int("ELECTRIC" in t or bool(re.search(r"\bE-?BIKE\b", t))
                or (category or "") == "electric-bikes"
                or bool(re.match(r"^[A-Z]+-E\b", t)))

    wheel = None
    m = _WHEEL_IN_TITLE.search(t)
    if m:
        wheel = float(m.group(1))

    m = _FIRST_WORD.match(t)
    return (m.group(0) if m else "", ebike, wheel)


# -------------------------------------------------------------- our side ---
def erp_model_key(libnach: str, ebike: int) -> str:
    """Model name out of `nomachat.libnach`.

    Three conventions to undo, all confirmed across the Halfords rows:
      `"A - VALIER 17'' ALLOY ..."`  the brand initial (A=Apollo, C=Carrera),
                                     written `A - ` or `A-`
      `"A - eEVADE 27.5x17 ..."`     an e-bike's name is the model prefixed `e`
      `"A - NEW GRIDLOK HARDTAIL"`   a season marker, not part of the name —
                                     and a costly one to miss: `NEW` was the
                                     most common "model" in the raw data, on
                                     GRIDLOK, VIVID, KINX, CREED and more.

    The `e` is only stripped when `ebike = 1`, or every ENTICE and ELYSE would
    lose its first letter."""
    s = _ERP_BRAND_PREFIX.sub("", (libnach or "").strip())
    if ebike and re.match(r"^e[A-Z]", s):
        s = s[1:]
    s = _SEASON_MARKER.sub("", s.upper())
    m = _FIRST_WORD.match(s)
    return m.group(0) if m else ""


def prepare_models(models: pd.DataFrame) -> pd.DataFrame:
    """Add the matching keys to `erp.load_customer_models()` output.

    `brand_norm` collapses the free-text `brandnach` to its first word, which is
    enough to separate the brands Halfords buys from us — APOLLO, CARRERA, INDI,
    TRAX, VOODOO — through the noise of `APOLLO 2016`, `APOLLO/BLACK FRIDAY` and
    `APOLLO VARIANT`. It matters because the shelf we scrape is Apollo only: a
    Carrera we build is *correctly* unmatched, and counting it as a miss would
    make the join look far worse than it is."""
    out = models.copy()
    out["model"] = [erp_model_key(lib, eb)
                    for lib, eb in zip(out["libnach"], out["ebike"])]
    out["brand_norm"] = (out["brandnach"].str.upper()
                         .str.extract(r"([A-Z][A-Z\-]*)", expand=False)
                         .fillna(""))
    return out


# --------------------------------------------------------------- matching ---
# Ordered best-first; `confidence` is one of these and the UI colours by it.
CONF_WHEEL = "model + wheel"
CONF_MODEL = "model name"
CONF_WHEEL_MISS = "model name (no article at that wheel size)"


def _group_id(model: str, ebike, wheel) -> str:
    """Stable key for a match group. Wheel is blank for the frame-sized bikes."""
    w = "" if pd.isna(wheel) else f"{wheel:g}"
    return f"{model}|{int(ebike)}|{w}"


def match(shelf: pd.DataFrame, models: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Join the shelf to our articles.

    Returns `(groups, links)`:
      `groups` one row per match group — the model as the shelf presents it,
               with its retail prices and how confident the match is. Groups
               with no article of ours are kept, `n_articles = 0`: those are
               the bikes Halfords sells that we don't build.
      `links`  `(group_id, article)` — every article claimed by a group, so
               sales can be aggregated up. **Each article appears at most
               once**, so summing units over `links` can't double-count.

    Within one (model, e-bike) bucket, groups that name a wheel size claim the
    articles at that wheel first; whatever is left goes to the group that
    doesn't name one (the adult bikes, sized by frame). A wheel group left with
    nothing falls back to the bucket's leftovers only when it is the sole
    claimant — an ambiguous fallback would be a guess, not a match."""
    if shelf.empty:
        return pd.DataFrame(), pd.DataFrame(columns=["group_id", "article"])

    grouped = (shelf.groupby(["model", "ebike", "wheel_in"], dropna=False)
               .agg(listings=("pid", "count"),
                    shelf_price=("shelf_price", "median"),
                    shelf_price_min=("shelf_price", "min"),
                    shelf_price_max=("shelf_price", "max"),
                    rrp=("rrp", "median"),
                    on_sale=("discount_pct", lambda s: float((s.fillna(0) > 0).mean())),
                    categories=("category_path", lambda s: ", ".join(sorted(set(s.dropna())))),
                    example=("title", "first"),
                    url=("url", "first"))
               .reset_index())
    grouped["group_id"] = [_group_id(m, e, w) for m, e, w in
                           zip(grouped["model"], grouped["ebike"], grouped["wheel_in"])]

    links: list[tuple[str, str]] = []
    conf: dict[str, str] = {}

    for (model, ebike), bucket in grouped.groupby(["model", "ebike"], dropna=False):
        cand = models[(models["model"] == model) & (models["ebike"] == ebike)]
        unclaimed = set(cand["article"])

        with_wheel = bucket[bucket["wheel_in"].notna()]
        without = bucket[bucket["wheel_in"].isna()]

        starved = []
        for _, g in with_wheel.iterrows():
            hit = cand[(cand["wheel_in"] == int(g["wheel_in"]))
                       & cand["article"].isin(unclaimed)]
            if hit.empty:
                starved.append(g["group_id"])
                continue
            links += [(g["group_id"], a) for a in hit["article"]]
            unclaimed -= set(hit["article"])
            conf[g["group_id"]] = CONF_WHEEL

        # The frame-sized groups take what the wheel-sized ones didn't want.
        for _, g in without.iterrows():
            if not unclaimed:
                continue
            links += [(g["group_id"], a) for a in unclaimed]
            conf[g["group_id"]] = CONF_MODEL
            unclaimed = set()

        # A wheel group that found nothing can still be right — the article may
        # simply carry a code whose wheel digits didn't decode. Give it the
        # leftovers only when nothing else could claim them.
        if len(starved) == 1 and unclaimed and without.empty:
            links += [(starved[0], a) for a in unclaimed]
            conf[starved[0]] = CONF_WHEEL_MISS

    link_df = pd.DataFrame(links, columns=["group_id", "article"])
    counts = link_df.groupby("group_id").size().rename("n_articles")
    groups = grouped.join(counts, on="group_id")
    groups["n_articles"] = groups["n_articles"].fillna(0).astype(int)
    groups["confidence"] = groups["group_id"].map(conf).fillna("no match")
    return groups, link_df
