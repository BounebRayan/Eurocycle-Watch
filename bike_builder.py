"""What bikes could be built out of the stock that is just sitting there.

**This is not a GPAO port.** Every other `gpao_*` module in this repo
reproduces a screen and documents where its arithmetic fails. This module has
no counterpart in the ERP: it is new analysis, built on top of the unmoved
stock `gpao_stock` reports and the bill-of-materials corpus the factory has
already validated. Nothing here ties to a GPAO number because there is no GPAO
number to tie to.

**The problem.** Roughly DT 6.3M of parts have not been issued or reserved
since the start of the window, and 90 % of that value is a part some live bike
BOM already calls for. It is not scrap; it is bikes that were never assembled.

**Why it cannot simply be "re-run an existing model".** Measured across all
11,934 live bike BOMs, no model is more than 49 % coverable from unmoved stock,
and 5,216 score zero. The pile is skewed to the expensive structural parts —
frames, forks, hubs, rims, saddles — because those are what gets over-ordered
and stranded. The cheap consumable tail (paint, decals, cartons, labels,
lubricant, ties, screws) is never unmoved because it never stops moving. So
every proposal needs a bought tail, and that is fine: priced at BOM cost the
whole tail is about DT 39 against a bike that lists at DT 400. **Minimal
outside parts has to be measured by value and by lead time, never by line
count** — a proposal with forty bought lines worth DT 4 is a better answer than
one with three bought lines worth DT 200.

**Where the rules come from.** Not from a spec sheet — from the corpus. A slot
is required if nearly every live bike has one; a part fits a wheel size if it
has been used at that wheel size; two parts are interchangeable if the ERP says
so or if a real BOM has paired them with the same frame. Each of those is a
query over models the factory has actually built and shipped, so the rules
refresh themselves as the corpus does.

Every substitution carries the tier that licensed it (see `TIERS`), and both
ends of it by name — what comes off the bike and what goes on — so a proposal
can be audited line by line instead of taken on trust.

**Every price here is converted before it is used.** `prxndach` and
`prxfobfpiec` are stored in the part's own purchase currency and four fifths of
this corpus is bought abroad, so `in_dinar()` sits in front of every reading
that adds a BOM price to a stock valuation or compares one part's price with
another's. It is not a detail: uncorrected, 31.7 % of models land in a different
tier.

**Two of the questions here have no single right answer, so both are reported.**
A slot with several shelf parts that fit can be filled from the dear end, which
frees the most stock, or the cheap end, which leaves the bike costing less than
the bill of materials it came from (`PREFERENCES`). And `cheaper_equivalents()`
asks the narrower question the ERP has all the data for and never asks: of two
parts `fpieceq` already calls interchangeable, is the one gathering dust the
cheaper one?
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

import erp
import gpao_requote
import gpao_stock
from i18n import N_

# --------------------------------------------------------------------------
# Substitution tiers. Ordered best first — the engine prefers a lower number,
# and a proposal's headline confidence is the *worst* tier among its structural
# swaps, not an average. One unchecked fork pairing should not be buried under
# thirty certain decal swaps.
T_ORIGINAL = 0   # the model's own part is itself sitting in stock — no swap
T_DECLARED = 1   # `fpieceq` — the ERP declares the two interchangeable
T_PRECEDENT = 2  # a real BOM has paired this part with the same frame
T_CORPUS = 3     # same slot, and used at this wheel size — plausible, unproven
T_BUY = 9        # nothing in stock fits; the part has to be bought

TIERS = {
    T_ORIGINAL: N_("Own part, in stock"),
    T_DECLARED: N_("ERP-declared equivalent"),
    T_PRECEDENT: N_("Paired with this frame before"),
    T_CORPUS: N_("Same slot and wheel size — needs a check"),
    T_BUY: N_("Bought in"),
}

# Groups 01-07 are the bike itself — structure, wheels, drive, gears, steering,
# seating, brakes. 08-14 are instructions, decals, options, hardware, safety,
# packing and paint. A questionable swap in the first set is an engineering
# risk; in the second it is a cosmetic one, so only the first set sets a
# proposal's confidence.
STRUCTURAL_GROUPS = {"01", "02", "03", "04", "05", "06", "07"}

# A slot present on this share of live bikes is treated as required. Measured
# rather than chosen: 26 slots clear it, and the gap either side of it is wide
# (the next slot down sits in the 80s), so the exact cut is not load-bearing.
REQUIRED_AT = 0.95
COMMON_AT = 0.40

# The two ways to fill a slot, and they genuinely disagree. Clearing the shelf
# wants the dearest part available in every slot; protecting the margin wants a
# bike that costs less than its tier sells for. Left unconstrained the first
# produces a build costing DT 489 against a DT 400 list price, so neither is a
# safe default to impose — the tab asks.
STRATEGY_CLEAR = "clear"
STRATEGY_MARGIN = "margin"

STRATEGIES = {
    STRATEGY_MARGIN: N_("Protect the margin — keep the build inside its tier's price"),
    STRATEGY_CLEAR: N_("Clear the shelf — take the dearest part, whatever it costs"),
}

# The same disagreement, one level down, in the substitution engine. Where a
# slot has several shelf parts that fit, `PREFER_VALUE` takes the dearest —
# which clears the most money and is what the first cut did, without ever
# saying so — and `PREFER_SAVING` takes the cheapest, which clears less but
# leaves the bike costing less than the BOM it came from. Both are reported for
# whichever is chosen, because a swap that frees DT 400 of shelf while adding
# DT 40 to the build cost is a different decision from one that does both.
PREFER_VALUE = "value"
PREFER_SAVING = "saving"

PREFERENCES = {
    PREFER_VALUE: N_("Clear the most stock — take the dearest part that fits"),
    PREFER_SAVING: N_("Cut the build cost — take the cheapest part that fits"),
}

# Company gross-margin target — the same 25 % (`margeProduitFini`) the Overview,
# Models and Actions tabs draw their reference lines at. A new build is costed
# against it, because "clear the most stock" and "build a bike worth selling"
# are not the same objective: left unconstrained, the greedy picks the dearest
# part in every slot and produces a bike that costs more than its tier lists at.
TARGET_MARGIN_PCT = 25.0


# --------------------------------------------------------------- corpus ---
@st.cache_data(ttl=3600, show_spinner="Reading the bill-of-materials corpus…")
def load_corpus() -> pd.DataFrame:
    """Every line of every live bike BOM — the evidence base for all the rules.

    One row per BOM line, not per slot: a slot legitimately repeats within a
    model (`SKN` twice for front and rear wheel at 36 spokes each, `STK` up to
    nine times), so collapsing to one part per slot would silently drop
    quantity. 15 slot types do this.

    `typndach` is the slot as the BOM line itself records it. It agrees with
    `fpiece.typfpiec` on 99.99 % of lines, so the line classifies itself and
    the part master is not needed to read a BOM.
    """
    df = erp._q("""
        SELECT N.[codnach]   AS model,
               N.[libnach]   AS model_name,
               N.[wheelnach] AS wheel,
               N.[ebike]     AS ebike,
               DN.[typndach] AS slot,
               DN.[grpndach] AS grp,
               DN.[codendach] AS part,
               DN.[ordndach]  AS pord,
               DN.[qtendach]  AS qty,
               DN.[prxndach]  AS prx
        FROM [nomachat] N
        JOIN [nomachat_det] DN ON N.[codnach] = DN.[codndach]
        WHERE N.[isArchived] = 0 AND N.[isBike] = 1
    """)
    for c in ("part", "pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in ("qty", "prx"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["ebike"] = pd.to_numeric(df["ebike"], errors="coerce").fillna(0).astype(int)
    for c in ("slot", "grp", "wheel", "model", "model_name"):
        df[c] = df[c].fillna("").astype(str).str.strip()
    return df.dropna(subset=["part", "pord"]).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_part_master() -> pd.DataFrame:
    """The part facts a buy list needs: what it costs, and how long it takes.

    Lead time and minimum order quantity are the whole point. A proposal's
    bought tail is cheap; what makes it expensive is waiting ninety days for a
    part from Asia to finish a bike whose frame is already on the floor."""
    df = erp._q("""
        SELECT P.[Code] AS part, P.[ordfpiec] AS pord, P.[num] AS part_num,
               P.[Libfpiec] AS part_lib, P.[reffpiec] AS part_ref,
               P.[typfpiec] AS slot, P.[groupe] AS grp,
               ISNULL(P.[prxfobfpiec], 0) AS fob, ISNULL(P.[Devfpiec], '') AS ccy,
               ISNULL(P.[Cmdminifpiec], 0) AS moq,
               ISNULL(P.[del_appro], 0) + ISNULL(P.[del_transp], 0) AS lead_days,
               ISNULL(P.[frnfpiec], '') AS supplier
        FROM [fpiece] P WHERE P.[isArchived] = 0
    """)
    for c in ("part", "pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in ("fob", "moq", "lead_days"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df.dropna(subset=["part", "pord"]).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_equivalences() -> pd.DataFrame:
    """`fpieceq` — the parts the ERP itself declares interchangeable.

    This is the strongest evidence available and it did not have to be mined:
    `frmPlanningGeneral` already substitutes on it when it plans production, so
    these swaps are ones the factory makes in the normal course of work.

    Stored directed, `(code, orddree) -> (codee, orddreee)`. Returned as stored
    plus the reverse, because a substitution that is safe one way is in practice
    recorded only once; `direction` says which rows were declared and which were
    inferred, so a reader can discount the inferred half if they want to."""
    df = erp._q("""
        SELECT [code] AS part, [orddree] AS pord,
               [codee] AS alt_part, [orddreee] AS alt_pord
        FROM [fpieceq]
    """)
    for c in ("part", "pord", "alt_part", "alt_pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df = df.dropna().reset_index(drop=True)
    df["direction"] = "declared"

    rev = df.rename(columns={"part": "alt_part", "pord": "alt_pord",
                             "alt_part": "part", "alt_pord": "pord"}).copy()
    rev["direction"] = "inferred"
    both = pd.concat([df, rev], ignore_index=True)
    return both.drop_duplicates(["part", "pord", "alt_part", "alt_pord"]).reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def slot_labels() -> pd.DataFrame:
    """`typepieces` — the slot code, what it actually is, and where it fits.

    `Typetpiec` is the code a BOM line carries (`FR`, `SKN`, `BCC`); `Libtpiec`
    is the ERP's own description of it. `Ordretpiec` is a hierarchical assembly
    rank (`001`, `001.02`, `006.10`) and `Grptpiec` maps to one of the fifteen
    `groupes`, so between them they are the factory's own build order — which is
    why the tables here sort on them rather than alphabetically.

    Descriptions come back in French because that is how the ERP holds them, the
    same rule the rest of the Management view follows for ERP values.
    """
    df = erp._q("""
        SELECT T.[Typetpiec] AS slot, T.[Libtpiec] AS label,
               T.[Ordretpiec] AS build_order, T.[Grptpiec] AS grp,
               ISNULL(G.[Libgroupe], '') AS grp_name
        FROM [typepieces] T
        LEFT JOIN [groupes] G ON G.[Codegroupe] = T.[Grptpiec]
    """)
    for c in ("slot", "label", "build_order", "grp", "grp_name"):
        df[c] = df[c].fillna("").astype(str).str.strip()
    return df.drop_duplicates("slot").reset_index(drop=True)


def label_slots(df: pd.DataFrame, col: str = "slot") -> pd.DataFrame:
    """Attach `label` / `grp_name` / `build_order` to anything keyed by slot.

    A bare `FFS` means nothing to a reader who does not already know the part
    master, and every table here is read by someone deciding what to build."""
    if df.empty or col not in df.columns:
        return df
    lab = slot_labels().rename(columns={"slot": col})
    keep = [c for c in ("label", "build_order", "grp_name") if c not in df.columns]
    return df.merge(lab[[col] + keep], on=col, how="left")


# ------------------------------------------------------------- currency ---
# **`prxndach` is not dinar.** The BOM line price and the part master's
# `prxfobfpiec` are both stored in the part's own purchase currency, and
# `frmCostingTMP3` reads the pair `(prxndach, Devfpiec)` and multiplies by
# `devisesc` before it adds anything up. Measured on this corpus: 406,915 lines
# are USD and 124,695 EUR against 223,975 in dinar, so reading the raw number as
# dinar understates most of a bought tail by about three times, and the 414 yen
# lines — priced at rate 1.0 — overstate by about seventeen.
#
# Everything here that adds a BOM price to a stock valuation, or compares one
# part's price with another's, goes through `in_dinar` first.
def purchase_rates() -> dict:
    """Dinar per unit of each purchase currency — `devisesc`, its latest row.

    `gpao_requote._rates()`, deliberately, rather than `erp.load_fx()`: that one
    reads the rate off sales invoices and so knows EUR and USD only, while parts
    are also bought in yen. One rate for every leg, which is the same discipline
    `gpao_requote` applies when it prices both halves of a re-quotation — a
    price comparison must not have a currency move inside it."""
    r = dict(gpao_requote._rates())
    r.update({"DT": 1.0, "": 1.0})
    return r


@st.cache_data(ttl=3600, show_spinner=False)
def part_currency() -> pd.DataFrame:
    """`(part, pord) -> Devfpiec`, uppercased and stripped."""
    m = load_part_master()[["part", "pord", "ccy"]].copy()
    m["ccy"] = m["ccy"].fillna("").astype(str).str.upper().str.strip()
    return m.drop_duplicates(["part", "pord"]).reset_index(drop=True)


def in_dinar(df: pd.DataFrame, col: str = "prx") -> pd.Series:
    """`col`, a price in the part's own currency, converted to dinar.

    Uses the frame's own `ccy` when it has one and looks the currency up in the
    part master otherwise. A part with no master row falls through at rate 1.0 —
    the `ELSE 1` every GPAO currency CASE ends on."""
    if df.empty:
        return pd.Series(dtype=float)
    rates = purchase_rates()
    if "ccy" in df.columns:
        ccy = df["ccy"]
    else:
        ccy = pd.Series(
            df[["part", "pord"]].merge(part_currency(), on=["part", "pord"],
                                       how="left")["ccy"].to_numpy(),
            index=df.index)
    rate = ccy.fillna("").astype(str).str.upper().str.strip().map(rates).fillna(1.0)
    return pd.to_numeric(df[col], errors="coerce").fillna(0.0) * rate


# ---------------------------------------------------------------- rules ---
@dataclass
class SlotRules:
    """What the corpus says a bike of a given wheel size has to have."""
    prevalence: pd.DataFrame          # slot x wheel -> share of models carrying it
    overall: pd.DataFrame             # slot -> share, lines-per-model, group
    wheel_parts: pd.DataFrame         # (slot, part, pord, wheel) seen together

    def required(self, wheel: str | None = None) -> list[str]:
        return self._at_least(REQUIRED_AT, wheel)

    def common(self, wheel: str | None = None) -> list[str]:
        p = self._share(wheel)
        return sorted(p[(p >= COMMON_AT) & (p < REQUIRED_AT)].index)

    def _at_least(self, cut: float, wheel: str | None) -> list[str]:
        p = self._share(wheel)
        return sorted(p[p >= cut].index)

    def _share(self, wheel: str | None) -> pd.Series:
        if wheel is None:
            return self.overall.set_index("slot")["share"]
        sub = self.prevalence[self.prevalence["wheel"] == str(wheel)]
        if sub.empty:
            return self.overall.set_index("slot")["share"]
        return sub.set_index("slot")["share"]


@st.cache_data(ttl=3600, show_spinner=False)
def _rules_frames(corpus: pd.DataFrame) -> tuple:
    """Cached tuple behind `slot_rules` — Streamlit can hash frames, not dataclasses."""
    n_models = corpus["model"].nunique()

    overall = (corpus.groupby("slot")
               .agg(models=("model", "nunique"),
                    lines=("model", "size"),
                    grp=("grp", "first"))
               .reset_index())
    overall["share"] = overall["models"] / n_models
    overall["lines_per_model"] = overall["lines"] / overall["models"]
    overall = overall.sort_values("share", ascending=False).reset_index(drop=True)

    per_wheel = corpus.groupby("wheel")["model"].nunique().rename("wheel_models")
    prevalence = (corpus.groupby(["wheel", "slot"])["model"].nunique()
                  .rename("models").reset_index()
                  .merge(per_wheel, on="wheel"))
    prevalence["share"] = prevalence["models"] / prevalence["wheel_models"]

    # The wheel-compatibility rule, stated as evidence rather than as a
    # threshold: a part fits a wheel size if a bike of that wheel size has been
    # built with it. Frames and rims turn out to be near-exclusive to one size
    # (1,351 of 1,424 frame parts appear at exactly one), saddles and bars cross
    # freely — and that falls out of the data instead of being asserted.
    wheel_parts = (corpus[corpus["wheel"] != ""]
                   [["slot", "part", "pord", "wheel"]]
                   .drop_duplicates().reset_index(drop=True))

    return overall, prevalence, wheel_parts


def slot_rules(corpus: pd.DataFrame) -> SlotRules:
    overall, prevalence, wheel_parts = _rules_frames(corpus)
    return SlotRules(prevalence=prevalence, overall=overall, wheel_parts=wheel_parts)


# ---------------------------------------------------------------- tiers ---
# The marker slots that separate a cheap build from an expensive one. Read off
# `typepieces`: suspension vs rigid fork, disc vs rim brake, whether there are
# derailleurs at all, and whether it is an e-bike.
MARKERS = {
    "suspension_fork": ("FFS", "FFT", "RFT"),
    "disc_brake": ("DBR", "DIS", "ADB"),
    "derailleur": ("RD", "FD"),
    "ebike_drive": ("BAT", "CMO", "CON", "HBM", "FHM", "EBC"),
}

# Wrapped in `N_` so the extractor sees them: these are values the frames carry
# *and* the labels the tier selector shows, via `format_func=ctx.t`.
TIER_ECONOMY, TIER_MID, TIER_HIGH = N_("economy"), N_("mid"), N_("high-end")
TIER_ORDER = [TIER_ECONOMY, TIER_MID, TIER_HIGH]


@st.cache_data(ttl=3600, show_spinner=False)
def learn_tiers(corpus: pd.DataFrame) -> pd.DataFrame:
    """Economy / mid / high-end, read off the corpus instead of decided.

    Build cost is `qty * prx` summed over the BOM, through `in_dinar` because
    `prxndach` is stored in the part's own purchase currency. One FX date —
    today's — for every leg: using each line's own historical rate would mix a
    currency move into what is meant to be a statement about specification,
    which is the same discipline `gpao_requote` applies when it prices both legs
    of a re-quotation at one rate.

    Terciles are taken **within each wheel size**. A 20-inch child's bike and a
    29-inch mountain bike are not points on one scale, and pooling them would
    just rediscover wheel size.

    Returns one row per model: `build_cost_dt`, `tier`, and a boolean per marker
    so the tier can be described in the factory's own terms rather than as a
    dinar band.
    """
    df = corpus.copy()
    df["line_dt"] = df["qty"] * in_dinar(df)

    cost = (df.groupby(["model", "wheel", "model_name", "ebike"])["line_dt"]
            .sum().rename("build_cost_dt").reset_index())

    for name, slots in MARKERS.items():
        has = (corpus[corpus["slot"].isin(slots)]
               .groupby("model").size().rename(name) > 0)
        cost[name] = cost["model"].map(has).fillna(False).astype(bool)

    # Tercile within wheel size. `qcut` needs three distinct edges; a wheel size
    # with too few models or a flat cost distribution falls back to mid rather
    # than raising, because a thin size is not an error.
    def _tier(g: pd.DataFrame) -> pd.Series:
        if len(g) < 3 or g["build_cost_dt"].nunique() < 3:
            return pd.Series(TIER_MID, index=g.index)
        try:
            return pd.qcut(g["build_cost_dt"].rank(method="first"), 3,
                           labels=TIER_ORDER).astype(str)
        except ValueError:
            return pd.Series(TIER_MID, index=g.index)

    cost["tier"] = (cost.groupby("wheel", group_keys=False)[cost.columns.tolist()]
                    .apply(_tier))
    return cost


def tier_profile(tiers: pd.DataFrame) -> pd.DataFrame:
    """What each tier actually looks like, in markers rather than in dinar.

    This is the part that makes the tiers explicable: instead of "high-end means
    over DT 180", it says "high-end here means a suspension fork on 94 % of
    models, discs on 61 %, derailleurs on 99 %"."""
    rows = []
    for tier in TIER_ORDER:
        sub = tiers[tiers["tier"] == tier]
        if sub.empty:
            continue
        row = {"tier": tier, "models": len(sub),
               "median_cost_dt": float(sub["build_cost_dt"].median()),
               "p25_cost_dt": float(sub["build_cost_dt"].quantile(0.25)),
               "p75_cost_dt": float(sub["build_cost_dt"].quantile(0.75))}
        for name in MARKERS:
            row[name] = float(sub[name].mean())
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------ the pool ---
def stock_pool(asof, since, *, mode: int = gpao_stock.MODE_NEVER) -> pd.DataFrame:
    """The unmoved stock, trimmed to what can actually be allocated.

    Negative balances are dropped here rather than in `gpao_stock` — they are
    real rows and the port keeps them, but you cannot build a bike out of minus
    four hubs.

    Two prices come through, and they answer different questions. `unit_dt` is
    what the shelf is carried at — PMP where there is one, catalogue price
    otherwise — and is what a proposal *clears*. `fob_dt` is the catalogue price
    alone, at the same as-of rate the GPAO's own screen uses, and is the only
    one that may be compared with a BOM line's price: PMP is an average of what
    was actually paid, so a PMP-against-catalogue delta would be a valuation
    difference dressed up as a saving."""
    df = gpao_stock.load_unmoved(asof, since, mode=mode)
    if df.empty:
        return df
    keep = ["part", "pord", "part_num", "part_lib", "part_ref", "slot",
            "qty", "unit_dt", "value_dt", "value_basis", "grp", "supplier_name",
            "ccy", "prxfobfpiec", "cours", "pmp"]
    out = df[df["qty"] > 0][keep].copy()
    out["fob_dt"] = out["prxfobfpiec"] * out["cours"]
    return out.rename(columns={"qty": "on_hand"}).reset_index(drop=True)


# ------------------------------------------------------- the shortlist ---
@st.cache_data(ttl=1800, show_spinner=False)
def score_models(corpus: pd.DataFrame, pool: pd.DataFrame,
                 batch: int = 100) -> pd.DataFrame:
    """Cheap first pass: how much unmoved stock each model's own BOM touches.

    No substitution — this only asks which models already call for parts that
    are sitting there, so the expensive engine can be pointed at a shortlist
    rather than at all 11,934. A model scoring zero here can still score above
    zero after substitution, but not by much, and never enough to reach the top.
    """
    empty = pd.DataFrame(columns=["model", "direct_value_dt", "direct_lines"])
    if pool.empty:
        return empty

    hit = corpus.merge(pool[["part", "pord", "on_hand", "unit_dt"]],
                       on=["part", "pord"], how="inner")
    if hit.empty:
        return empty

    # Only count a line if the shelf can cover the whole batch. A part with four
    # units on hand does not help a run of a hundred.
    hit = hit[hit["on_hand"] >= hit["qty"] * batch]
    if hit.empty:
        return empty

    hit = hit.assign(line_value_dt=hit["qty"] * batch * hit["unit_dt"])
    return (hit.groupby("model")
            .agg(direct_value_dt=("line_value_dt", "sum"),
                 direct_lines=("part", "size"))
            .reset_index()
            .sort_values("direct_value_dt", ascending=False)
            .reset_index(drop=True))


# ------------------------------------------------- naming a substitution ---
def _name_both_sides(lines: pd.DataFrame) -> pd.DataFrame:
    """Attach the part master to both ends of every line.

    A substitution that names only the slot cannot be acted on. "FFS — fourche"
    tells a reader which slot changes and nothing about what to take off the
    shelf, and the two parts either side of the swap are the whole content of
    the instruction. So every line carries `orig_num` / `orig_lib` / `orig_ref`
    for what the BOM says and `part_num` / `part_lib` / `part_ref` for what
    would go in, plus the bought line's lead time, minimum order and supplier.
    """
    if lines.empty:
        return lines
    m = load_part_master().drop_duplicates(["part", "pord"])
    chosen = m[["part", "pord", "part_num", "part_lib", "part_ref",
                "lead_days", "moq", "supplier"]]
    orig = (m[["part", "pord", "part_num", "part_lib", "part_ref"]]
            .rename(columns={"part": "orig_part", "pord": "orig_pord",
                             "part_num": "orig_num", "part_lib": "orig_lib",
                             "part_ref": "orig_ref"}))
    out = lines.merge(chosen, on=["part", "pord"], how="left")
    out = out.merge(orig, on=["orig_part", "orig_pord"], how="left")
    for c in ("part_num", "part_lib", "part_ref", "orig_num", "orig_lib", "orig_ref",
              "supplier"):
        out[c] = out[c].fillna("").astype(str)
    # What a works order would read: the part coming off, then the one going on.
    out["change"] = out["orig_num"].where(~out["swap"], out["orig_num"] + " → " + out["part_num"])
    return out


# --------------------------------------------- cheaper on the shelf ---
def cheaper_equivalents(corpus: pd.DataFrame, pool: pd.DataFrame, eq: pd.DataFrame,
                        *, declared_only: bool = True) -> pd.DataFrame:
    """Parts a live BOM calls for that have a cheaper interchangeable twin sitting unmoved.

    **The GPAO has every ingredient for this and never cooks it.** `fpieceq` is
    the interchangeability table; `frmAvailableItem` will even list a part's
    equivalents beside their `prxfobfpiec`, their currency and their stock. But
    it lists them ordered by part number, in whatever currency each is bought
    in, with no conversion and no difference taken — so nothing in the ERP ever
    says which of two interchangeable parts is the cheaper one. The only screen
    that *acts* on `fpieceq` is `frmPlanningGeneral`, and it only mentions an
    equivalent on a line already in shortage (`totQte <= 0`), as a semicolon-
    joined string in a report cell. Substituting to save money, or to consume
    stock that is not moving, is not a question the GPAO asks.

    So this asks it. One row per `(specified part, shelf part)` pair where the
    two are declared interchangeable, the shelf part has not moved, and its
    catalogue price in dinar undercuts what the BOM pays for the specified one.
    Both legs are `prxfobfpiec` converted at one rate — the comparison the
    costing screen would make if it were asked to re-cost the model with the
    swap in it.

    `declared_only` keeps it to `fpieceq`'s own pairs. The looser tiers the
    retrofit engine uses (same frame before, same slot and wheel size) are
    evidence for a proposal a human is already reading line by line; they are
    too weak to headline a list that reads as "these two are the same part".
    """
    empty = pd.DataFrame(columns=[
        "part", "pord", "orig_num", "orig_lib", "slot", "alt_part", "alt_pord",
        "part_num", "part_lib", "models", "qty_per_bike", "orig_unit_cost_dt",
        "alt_unit_cost_dt", "saving_per_bike_dt", "on_hand", "bikes_covered",
        "saving_dt", "stock_freed_dt", "direction", "supplier_name"])
    if corpus.empty or pool.empty or eq.empty:
        return empty

    # What the corpus pays for each specified part, in dinar. Median, because
    # the same part carries a different `prxndach` in different BOMs and one
    # stale line should not decide whether a swap looks worthwhile.
    c = corpus.assign(prx_dt=in_dinar(corpus))
    used = (c.groupby(["part", "pord", "slot"])
            .agg(models=("model", "nunique"),
                 qty_per_bike=("qty", "median"),
                 orig_unit_cost_dt=("prx_dt", "median"))
            .reset_index())

    pairs = eq if not declared_only else eq[eq["direction"] == "declared"]
    df = used.merge(pairs[["part", "pord", "alt_part", "alt_pord", "direction"]],
                    on=["part", "pord"], how="inner")
    if df.empty:
        return empty

    shelf = (pool[["part", "pord", "part_num", "part_lib", "part_ref", "on_hand",
                   "fob_dt", "unit_dt", "supplier_name"]]
             .rename(columns={"part": "alt_part", "pord": "alt_pord",
                              "fob_dt": "alt_unit_cost_dt"}))
    df = df.merge(shelf, on=["alt_part", "alt_pord"], how="inner")
    if df.empty:
        return empty

    df = df.merge(load_part_master()[["part", "pord", "part_num", "part_lib"]]
                  .rename(columns={"part_num": "orig_num", "part_lib": "orig_lib"})
                  .drop_duplicates(["part", "pord"]),
                  on=["part", "pord"], how="left")
    for c in ("orig_num", "orig_lib", "part_num", "part_lib", "supplier_name"):
        df[c] = df[c].fillna("").astype(str)

    # A part with no catalogue price is not a cheaper part, it is a missing one.
    df = df[(df["alt_unit_cost_dt"] > 0) & (df["orig_unit_cost_dt"] > 0)]
    df = df[df["alt_unit_cost_dt"] < df["orig_unit_cost_dt"]]
    if df.empty:
        return empty

    df["qty_per_bike"] = df["qty_per_bike"].replace(0, 1.0).fillna(1.0)
    df["saving_per_bike_dt"] = ((df["orig_unit_cost_dt"] - df["alt_unit_cost_dt"])
                                * df["qty_per_bike"])
    df["bikes_covered"] = (df["on_hand"] // df["qty_per_bike"]).astype(float)
    # Bounded by the shelf, not by demand: the saving stops when the pile does.
    df["saving_dt"] = df["saving_per_bike_dt"] * df["bikes_covered"]
    df["stock_freed_dt"] = df["unit_dt"] * df["qty_per_bike"] * df["bikes_covered"]

    df = label_slots(df)
    return (df[df["bikes_covered"] >= 1]
            .sort_values("saving_dt", ascending=False)
            .reset_index(drop=True))


@st.cache_data(ttl=1800, show_spinner=False)
def cheaper_shelf_swaps(asof, since, *,
                        mode: int = gpao_stock.MODE_NEVER) -> pd.DataFrame:
    """`cheaper_equivalents` behind a key of scalars, not frames.

    Same reason `dead_stock_portfolio` has one. Everything it reads is cached
    already, but a cache key made of the frames themselves means hashing a
    763,692-row corpus on every rerun — and this sits on a tab whose currency
    selector reruns it."""
    pool = stock_pool(asof, since, mode=mode)
    corpus = load_corpus()
    if pool.empty or corpus.empty:
        return pd.DataFrame()
    return cheaper_equivalents(corpus, pool, load_equivalences())


def models_using(corpus: pd.DataFrame, part, pord) -> pd.DataFrame:
    """The live models whose BOM carries this part — the drill behind a pair."""
    hit = corpus[(corpus["part"] == part) & (corpus["pord"] == pord)]
    if hit.empty:
        return hit
    return (hit.groupby(["model", "model_name", "wheel"])
            .agg(qty_per_bike=("qty", "sum"), slot=("slot", "first"))
            .reset_index().sort_values("model"))


# ------------------------------------------------------------ retrofit ---
@dataclass
class Proposal:
    """One buildable proposal, with every line's provenance attached."""
    model: str
    model_name: str
    wheel: str
    tier: str
    batch: int
    lines: pd.DataFrame           # one row per BOM line, with source and tier
    dead_value_dt: float          # unmoved stock consumed at this batch size
    buy_value_dt: float           # what has to be bought, at BOM cost in dinar
    accounting_cost_dt: float     # dead + buy: what the books would carry
    worst_structural_tier: int
    max_lead_days: float
    swaps: int
    saving_dt: float = 0.0        # cheaper substitutions less dearer ones, net
    cheaper_swaps: int = 0        # swaps where the shelf part undercuts the BOM
    prefer: str = PREFER_VALUE    # which objective picked between the candidates

    @property
    def cash_cost_dt(self) -> float:
        """What building the batch costs in new money.

        The unmoved parts are already paid for — that is the entire argument for
        using them — so they are not cash. The accounting reading sits beside
        this one because the two answer different questions and the books use
        the other."""
        return self.buy_value_dt

    @property
    def confidence(self) -> int:
        return self.worst_structural_tier

    @property
    def swap_lines(self) -> pd.DataFrame:
        """Only the lines that actually change — what a works order would say."""
        if self.lines.empty:
            return self.lines
        return self.lines[self.lines["swap"]].sort_values("line_dt", ascending=False)


def propose_retrofit(model: str, corpus: pd.DataFrame, pool: pd.DataFrame,
                     eq: pd.DataFrame, rules: SlotRules, tiers: pd.DataFrame,
                     *, batch: int = 100,
                     prefer: str = PREFER_VALUE) -> "Proposal | None":
    """Take a real BOM and swap unmoved stock into it wherever a rule allows.

    Walks the donor's lines and, for each, tries the tiers best first: the
    model's own part if it is on the shelf, then an ERP-declared equivalent,
    then a part that a bike sharing this frame has used in this slot, then any
    part of the same slot that has been built at this wheel size. If none has
    enough on hand to cover the batch, the line is bought.

    Quantities come from the donor line and are never altered — a bike needing
    36 spokes still needs 36. A part is used only if `on_hand >= qty * batch`,
    so the proposal is buildable as stated rather than buildable in principle.

    `prefer` decides only which of the fitting candidates wins, never whether to
    swap at all: both objectives clear the same lines, they just fill them from
    opposite ends of the price list. See `PREFERENCES`.

    **Every line names both parts.** `orig_part` / `orig_num` is what the BOM
    says, `part` / `part_num` is what would go in, and `saving_dt` is the
    difference re-costed the way `frmCostingTMP3` would: the BOM's own price for
    the line against the shelf part's catalogue price, both in dinar at one
    rate. It is deliberately *not* measured against what the shelf is carried
    at — most shelf rows are valued at PMP, an average of what was actually
    paid, and subtracting a catalogue price from an average would report a
    valuation difference as a saving. Where either side has no price at all —
    26 pool rows and 14,214 corpus lines carry a zero — the swap is made and the
    comparison is left blank, because a missing price is not a free part.
    """
    bom = corpus[corpus["model"] == model]
    if bom.empty:
        return None
    bom = bom.assign(prx_dt=in_dinar(bom))

    meta = bom.iloc[0]
    wheel = str(meta["wheel"])
    tier_row = tiers[tiers["model"] == model]
    tier = str(tier_row.iloc[0]["tier"]) if not tier_row.empty else TIER_MID

    avail = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}
    unit = {(r.part, r.pord): r.unit_dt for r in pool.itertuples(index=False)}
    # The catalogue price of each shelf part, in dinar — the leg that may be
    # compared with a BOM line. `unit` above is the carrying value and may not.
    cat = {(r.part, r.pord): r.fob_dt for r in pool.itertuples(index=False)}
    pool_keys = set(avail)

    # Candidate sets, built once per proposal rather than once per line.
    eq_map: dict = {}
    for p, o, ap, ao in eq[["part", "pord", "alt_part", "alt_pord"]].itertuples(index=False):
        eq_map.setdefault((p, o), []).append((ap, ao))

    frame = bom[bom["slot"] == "FR"]
    precedent: dict = {}
    if not frame.empty:
        fk = (frame.iloc[0]["part"], frame.iloc[0]["pord"])
        sibs = corpus[(corpus["part"] == fk[0]) & (corpus["pord"] == fk[1])]["model"].unique()
        for slot, grp in corpus[corpus["model"].isin(sibs)].groupby("slot"):
            precedent[slot] = set(zip(grp["part"], grp["pord"]))

    wp = rules.wheel_parts
    at_wheel = wp[wp["wheel"] == wheel] if wheel else wp.iloc[0:0]
    corpus_ok: dict = {}
    for slot, grp in at_wheel.groupby("slot"):
        corpus_ok[slot] = set(zip(grp["part"], grp["pord"]))

    rows = []
    for line in bom.itertuples(index=False):
        need = line.qty * batch
        key = (line.part, line.pord)

        def fits(k) -> bool:
            return need > 0 and k in pool_keys and avail.get(k, 0) >= need

        chosen, tier_used = None, T_BUY
        if fits(key):
            chosen, tier_used = key, T_ORIGINAL
        else:
            for cands, t in ((eq_map.get(key, []), T_DECLARED),
                             (precedent.get(line.slot, set()), T_PRECEDENT),
                             (corpus_ok.get(line.slot, set()), T_CORPUS)):
                fitting = [k for k in cands if fits(k)]
                if fitting:
                    # Which end of the price list to fill from. Both break the
                    # tie on the key so a rerun cannot move the answer.
                    chosen = (min(fitting, key=lambda k: (cat.get(k, 0.0), k))
                              if prefer == PREFER_SAVING
                              else max(fitting, key=lambda k: (unit.get(k, 0.0), k)))
                    tier_used = t
                    break

        src = "stock" if chosen is not None else "buy"
        use = chosen if chosen is not None else key
        # Bought lines carry the BOM's own price, converted; stock lines carry
        # what the shelf is valued at, because that is what gets cleared.
        px = unit.get(chosen, 0.0) if chosen is not None else line.prx_dt
        swap = chosen is not None and chosen != key
        # A price of zero on either side is a hole in the part master, not a
        # free part. Costing a swap against it would report the whole of the
        # other leg as a saving, so the comparison is left blank instead.
        new_cost = cat.get(chosen, 0.0) if swap else line.prx_dt
        priced = swap and new_cost > 0 and line.prx_dt > 0
        saving = (line.prx_dt - new_cost) * need if priced else 0.0
        if chosen is not None:
            avail[chosen] = avail.get(chosen, 0) - need
        rows.append({
            "slot": line.slot, "grp": line.grp, "qty_per_bike": line.qty,
            "orig_part": line.part, "orig_pord": line.pord,
            "part": use[0], "pord": use[1], "source": src, "tier": tier_used,
            "swap": swap, "qty_needed": need, "unit_dt": px, "line_dt": need * px,
            "orig_unit_cost_dt": line.prx_dt,
            "unit_cost_dt": new_cost if (priced or not swap) else float("nan"),
            "saving_dt": saving,
        })

    lines = _name_both_sides(pd.DataFrame(rows))
    stock = lines[lines["source"] == "stock"]
    buy = lines[lines["source"] == "buy"]

    structural = stock[stock["grp"].isin(STRUCTURAL_GROUPS)]
    worst = int(structural["tier"].max()) if not structural.empty else T_ORIGINAL

    lead = float(buy["lead_days"].max()) if not buy.empty else 0.0
    swapped = stock[stock["swap"]]

    return Proposal(
        model=model, model_name=str(meta["model_name"]), wheel=wheel, tier=tier,
        batch=batch, lines=lines,
        dead_value_dt=float(stock["line_dt"].sum()),
        buy_value_dt=float(buy["line_dt"].sum()),
        accounting_cost_dt=float(lines["line_dt"].sum()),
        worst_structural_tier=worst,
        max_lead_days=0.0 if pd.isna(lead) else lead,
        swaps=int(swapped.shape[0]),
        saving_dt=float(swapped["saving_dt"].sum()),
        cheaper_swaps=int((swapped["saving_dt"] > 0).sum()),
        prefer=prefer,
    )


def rank_retrofits(corpus: pd.DataFrame, pool: pd.DataFrame, eq: pd.DataFrame,
                   rules: SlotRules, tiers: pd.DataFrame, *, batch: int = 100,
                   shortlist: int = 60, tier: "str | None" = None,
                   wheel: "str | None" = None,
                   prefer: str = PREFER_VALUE) -> list:
    """Score every model cheaply, then run the full engine on the best of them."""
    scored = score_models(corpus, pool, batch=batch)
    if scored.empty:
        return []

    cand = scored.merge(tiers[["model", "tier", "wheel"]], on="model", how="left")
    if tier:
        cand = cand[cand["tier"] == tier]
    if wheel:
        cand = cand[cand["wheel"] == str(wheel)]
    if cand.empty:
        return []

    out = []
    for model in cand.head(shortlist)["model"]:
        p = propose_retrofit(model, corpus, pool, eq, rules, tiers, batch=batch,
                             prefer=prefer)
        if p is not None and p.dead_value_dt > 0:
            out.append(p)
    return sorted(out, key=lambda p: p.dead_value_dt, reverse=True)


# ---------------------------------------------------------- contention ---
@dataclass
class Portfolio:
    """Several proposals taken together, with the double-counting removed.

    Two proposals will want the same frame. Each is individually correct and
    summing them is not, so both the standalone figure and the after-allocation
    figure are carried. The second is the one a headline has to use — it is the
    only one that could actually be built."""
    proposals: list = field(default_factory=list)
    allocated: pd.DataFrame = field(default_factory=pd.DataFrame)
    standalone_dt: float = 0.0
    realisable_dt: float = 0.0
    buy_alloc_dt: float = 0.0
    saving_alloc_dt: float = 0.0
    contended: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def overlap_dt(self) -> float:
        return self.standalone_dt - self.realisable_dt

    @property
    def worth_doing(self) -> int:
        """Proposals that clear more stock than they cost to finish.

        The ratio falls away sharply down the ranking — the best few are
        excellent and the tail is not worth the floor space — so the count
        matters more than the total."""
        if self.allocated.empty:
            return 0
        a = self.allocated
        return int((a["realisable_dt"] > a["buy_alloc_dt"]).sum())


def allocate(proposals: list, pool: pd.DataFrame) -> Portfolio:
    """Hand the pool out in rank order and report what survives.

    Greedy on the ranking it is given: the best proposal takes what it needs and
    the next takes what is left. Greedy is not optimal and is not claimed to
    be — it is reported, so the figure is a floor on what could be cleared
    rather than a promise."""
    if not proposals:
        return Portfolio()

    left = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}
    rows, contended = [], []

    for rank, p in enumerate(proposals, 1):
        got = 0.0
        for line in p.lines[p.lines["source"] == "stock"].itertuples(index=False):
            key = (line.part, line.pord)
            have = left.get(key, 0.0)
            take = min(have, line.qty_needed)
            if take < line.qty_needed:
                contended.append({"rank": rank, "model": p.model, "slot": line.slot,
                                  "part": line.part, "pord": line.pord,
                                  "wanted": line.qty_needed, "got": take})
            if take > 0:
                left[key] = have - take
                got += take * line.unit_dt
        # A proposal that only wins part of the stock it wanted can only build
        # that part of the batch, so it only buys that part of its tail. Without
        # this the portfolio's cash figure is the cost of building every
        # proposal in full — six thousand bikes for a sixty-deep list — which is
        # not a programme anybody would run.
        share = (got / p.dead_value_dt) if p.dead_value_dt else 0.0
        rows.append({"rank": rank, "model": p.model, "model_name": p.model_name,
                     "tier": p.tier, "wheel": p.wheel, "batch": p.batch,
                     "standalone_dt": p.dead_value_dt, "realisable_dt": got,
                     "buy_dt": p.buy_value_dt, "buy_alloc_dt": p.buy_value_dt * share,
                     "share": share, "confidence": p.confidence,
                     "swaps": p.swaps, "cheaper_swaps": p.cheaper_swaps,
                     # Scaled on the same share as the bought tail, and for the
                     # same reason: a proposal that wins a tenth of the parts it
                     # wanted builds a tenth of the batch, so it books a tenth
                     # of the saving too.
                     "saving_dt": p.saving_dt, "saving_alloc_dt": p.saving_dt * share,
                     "max_lead_days": p.max_lead_days})

    alloc = pd.DataFrame(rows)
    alloc["ratio"] = (alloc["realisable_dt"] /
                      alloc["buy_alloc_dt"].where(alloc["buy_alloc_dt"] > 0))
    return Portfolio(
        proposals=proposals, allocated=alloc,
        standalone_dt=float(alloc["standalone_dt"].sum()),
        realisable_dt=float(alloc["realisable_dt"].sum()),
        buy_alloc_dt=float(alloc["buy_alloc_dt"].sum()),
        saving_alloc_dt=float(alloc["saving_alloc_dt"].sum()),
        contended=pd.DataFrame(contended),
    )


@st.cache_data(ttl=1800, show_spinner=False)
def dead_stock_portfolio(asof, since, *, mode: int = gpao_stock.MODE_NEVER,
                         batch: int = 100, shortlist: int = 60,
                         tier: "str | None" = None,
                         wheel: "str | None" = None,
                         prefer: str = PREFER_VALUE) -> tuple:
    """`rank_retrofits` + `allocate`, behind a cache. Returns `(proposals, portfolio)`.

    Everything the engine *reads* is already cached — the corpus, the pool, the
    equivalences, the slot rules, the tiers, the cheap scoring pass. What was
    not cached is the expensive part: `rank_retrofits` runs `propose_retrofit`
    over the shortlist, and each of those makes several full passes over a
    corpus covering ~12,000 models. That ran on **every rerun**, so changing the
    display currency or nudging the year slider paid for it again.

    It matters more now than it did as one tab among thirteen: the Overview
    page's attention list carries the dead-stock finding, so this is on the
    landing page — about 48s of the landing page's cold cost, and previously
    that much again on every rerun.

    Keying on the scalars rather than the frames also lets the Actions page and
    the Overview's attention list share one entry, since both ask for the same
    window. The Build-from-stock tab does **not** share it: its shortlist,
    batch, movement rule, tier and wheel all come from its own controls, so it
    keys separately and pays once per combination. That is the right behaviour —
    they are different questions — but don't read this as one shared result for
    the whole view.
    """
    pool = stock_pool(asof, since, mode=mode)
    if pool.empty:
        return [], Portfolio()

    corpus = load_corpus()
    if corpus.empty:
        return [], Portfolio()

    proposals = rank_retrofits(corpus, pool, load_equivalences(), slot_rules(corpus),
                               learn_tiers(corpus), batch=batch, shortlist=shortlist,
                               tier=tier, wheel=wheel, prefer=prefer)
    if not proposals:
        return [], Portfolio()
    return proposals, allocate(proposals, pool)


# --------------------------------------------------- free composition ---
@dataclass
class Build:
    """A bike specified from the shelf rather than from an existing model.

    This is the one that makes new money. A substitution into a model the
    factory already builds swaps one part for another in a bike that was going
    to be built anyway; a `Build` is a bike that does not exist yet, assembled
    out of stock that is currently earning nothing.

    It carries no precedent, which is the trade. Every part in it has been
    built at this wheel size, and every slot it fills is one the corpus says a
    bike of this kind has — but the combination is new, and the tab says so.
    """
    wheel: str
    tier: str
    batch: int
    lines: pd.DataFrame
    stock_value_dt: float        # unmoved stock consumed over the batch
    buy_value_dt: float          # bought in over the batch, at BOM cost
    strategy: str                # which of the two objectives produced it
    buildable_units: int         # what the shelf supports, not what was asked for
    max_lead_days: float
    benchmark_price_dt: float    # what comparable models list at, per bike

    @property
    def cash_per_bike(self) -> float:
        """New money per bike. The stock is already paid for."""
        return self.buy_value_dt / self.batch if self.batch else 0.0

    @property
    def cost_per_bike(self) -> float:
        """What the books would carry: stock at its held value, plus the tail."""
        return ((self.stock_value_dt + self.buy_value_dt) / self.batch
                if self.batch else 0.0)

    @property
    def margin_pct(self) -> float:
        """Against the accounting cost, not the cash cost.

        Costing the bike at only its bought tail would show a margin above 95 %
        on every build, which is true of the cash and useless as a price signal —
        the stock still cost what it cost. `cash_per_bike` is carried separately
        for the cash question."""
        p = self.benchmark_price_dt
        return (p - self.cost_per_bike) / p * 100 if p else float("nan")


@st.cache_data(ttl=3600, show_spinner=False)
def benchmark_prices() -> pd.DataFrame:
    """Median list price per wheel size and tier, for pricing a new build.

    From `costing_nc` via `gpao_requote.load_price_list`, which
    `docs/gpao-parity.md` §7 measured at 7.1 % median error against realised
    selling price — against 35.3 % for `nomachat.prxnach`. Its weakness is
    reach, not accuracy: only about 45 % of live models carry a costing sheet,
    so this is a median over the ones that do.
    """
    import gpao_requote as R
    try:
        pl = R.load_price_list()
    except Exception:
        return pd.DataFrame(columns=["wheel", "tier", "price_dt", "models"])
    if pl.empty:
        return pd.DataFrame(columns=["wheel", "tier", "price_dt", "models"])

    tiers = learn_tiers(load_corpus())
    m = pl.merge(tiers[["model", "wheel", "tier"]], left_on="article",
                 right_on="model", how="inner")
    if m.empty:
        return pd.DataFrame(columns=["wheel", "tier", "price_dt", "models"])
    return (m.groupby(["wheel", "tier"])["price_dt"]
            .agg(price_dt="median", models="size").reset_index())


def benchmark_price(wheel: str, tier: str) -> float:
    b = benchmark_prices()
    if b.empty:
        return 0.0
    hit = b[(b["wheel"] == str(wheel)) & (b["tier"] == tier)]
    if hit.empty:
        hit = b[b["wheel"] == str(wheel)]
    return float(hit["price_dt"].median()) if not hit.empty else 0.0


def propose_free(wheel: str, corpus: pd.DataFrame, pool: pd.DataFrame,
                 rules: SlotRules, tiers: pd.DataFrame, *, tier: str = TIER_MID,
                 batch: int = 100, include_common: bool = True,
                 strategy: str = STRATEGY_MARGIN) -> Build:
    """Specify a new bike out of the shelf, slot by slot.

    **How many slots a bike needs is not a choice made here** — it is read off
    the corpus. A 700C bike in this factory has a median of 70 BOM lines across
    55 distinct slots, and the quartiles are 52 and 57, so a build that fills
    around fifty is a normal bike rather than an inflated one. `include_common`
    is the one lever: required slots are on 95 % or more of live bikes and a
    build without them is not a bike, while common slots (40-95 %) are real
    fittings that many models do without — mudguards, a rack, a bell.

    For each slot the pool's highest-value compatible part wins, because
    clearing the shelf is the objective. Quantity per bike is the corpus median
    for that slot at that wheel size, which is what stops a spoke line being
    filled as though one spoke were enough.
    """
    slots = list(rules.required(wheel))
    if include_common:
        slots += [s for s in rules.common(wheel) if s not in slots]

    at_wheel = corpus[corpus["wheel"] == str(wheel)]
    if at_wheel.empty:
        at_wheel = corpus

    in_tier = set(tiers[tiers["tier"] == tier]["model"])
    scoped = at_wheel[at_wheel["model"].isin(in_tier)]
    if scoped.empty:
        scoped = at_wheel

    qty_by_slot = scoped.groupby("slot")["qty"].median()
    ok = rules.wheel_parts[rules.wheel_parts["wheel"] == str(wheel)]
    ok_keys = set(zip(ok["part"], ok["pord"]))
    required = set(rules.required(wheel))

    # The cost envelope, and the only thing the two strategies disagree about.
    # Under `STRATEGY_MARGIN` each slot gets the most valuable part that still
    # leaves the rest of the bike affordable, with the slots after it reserved at
    # what the corpus typically pays for them. Under `STRATEGY_CLEAR` there is no
    # envelope: every slot takes the dearest part it can, which clears the most
    # stock and may well cost more than the bike lists at.
    price = benchmark_price(wheel, tier)
    budget = (float("inf") if strategy == STRATEGY_CLEAR or not price
              else price * (1 - TARGET_MARGIN_PCT / 100))
    # In dinar, because it is about to be compared with a shelf valuation and
    # spent against a dinar budget. `prxndach` is not dinar — see `in_dinar`.
    scoped = scoped.assign(prx_dt=in_dinar(scoped))
    typical = scoped.groupby("slot").apply(
        lambda g: float((g["qty"] * g["prx_dt"]).median()), include_groups=False
    ).to_dict()

    avail = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}
    spent = 0.0
    rows = []
    for i, slot in enumerate(slots):
        qty = float(qty_by_slot.get(slot, 1.0)) or 1.0
        need = qty * batch
        # What the slots after this one will cost at corpus-typical prices. The
        # current slot may spend the budget less that reservation, no more.
        reserved = sum(typical.get(s2, 0.0) for s2 in slots[i + 1:])
        headroom = budget - spent - reserved

        cands = pool[pool["slot"] == slot]
        if not cands.empty:
            keep = [k in ok_keys and avail.get(k, 0.0) >= need
                    for k in zip(cands["part"], cands["pord"])]
            cands = cands[keep]
        if not cands.empty and headroom != float("inf"):
            afford = cands[cands["unit_dt"] * qty <= max(headroom, 0.0)]
            if afford.empty:
                # Nothing on the shelf fits what is left of the budget. Taking
                # the cheapest shelf part anyway would clear stock at a loss —
                # the point is to free money, not to spend more of it than the
                # part is worth — so take it only where it undercuts buying the
                # slot new, and otherwise buy. `STRATEGY_CLEAR` never reaches
                # here, which is exactly the difference between the two.
                buy_here = typical.get(slot, 0.0)
                cheapest = cands.nsmallest(1, "unit_dt")
                fits_better = float(cheapest.iloc[0]["unit_dt"]) * qty <= buy_here
                cands = cheapest if (buy_here > 0 and fits_better) else cands.iloc[0:0]
            else:
                cands = afford
        if cands.empty:
            px = scoped[scoped["slot"] == slot]["prx_dt"]
            unit = float(px.median()) if not px.empty else 0.0
            rows.append({"slot": slot, "required": slot in required,
                         "qty_per_bike": qty, "source": "buy", "part": pd.NA,
                         "pord": pd.NA, "part_num": "", "part_lib": "",
                         "on_hand": 0.0, "unit_dt": unit, "line_dt": need * unit,
                         "units_supported": pd.NA})
            spent += qty * unit
            continue
        best = cands.sort_values(["unit_dt", "part"], ascending=[False, True]).iloc[0]
        key = (best["part"], best["pord"])
        avail[key] = avail.get(key, 0.0) - need
        spent += qty * float(best["unit_dt"])
        rows.append({"slot": slot, "required": slot in required,
                     "qty_per_bike": qty, "source": "stock",
                     "part": best["part"], "pord": best["pord"],
                     "part_num": best["part_num"], "part_lib": best["part_lib"],
                     "on_hand": float(best["on_hand"]), "unit_dt": float(best["unit_dt"]),
                     "line_dt": need * float(best["unit_dt"]),
                     "units_supported": int(best["on_hand"] // qty) if qty else 0})

    lines = label_slots(pd.DataFrame(rows))
    if not lines.empty:
        lines = lines.sort_values(["build_order", "slot"], na_position="last")

    stock = lines[lines["source"] == "stock"]
    buy = lines[lines["source"] == "buy"]

    # What the shelf actually supports, which is usually well above the batch
    # that was asked for — the batch is a floor on eligibility, not a ceiling.
    supported = pd.to_numeric(stock["units_supported"], errors="coerce").dropna()
    buildable = int(supported.min()) if not supported.empty else 0

    lead = 0.0
    if not buy.empty:
        master = load_part_master()[["slot", "lead_days"]]
        # Median, not max: a buy line is priced at what the corpus typically
        # pays for the slot rather than at one named part, so the worst lead
        # time in the whole slot would be alarmist about a part nobody picked.
        lead_by_slot = master.groupby("slot")["lead_days"].median()
        lead = float(buy["slot"].map(lead_by_slot).max() or 0.0)

    return Build(
        wheel=str(wheel), tier=tier, batch=batch, strategy=strategy, lines=lines,
        stock_value_dt=float(stock["line_dt"].sum()),
        buy_value_dt=float(buy["line_dt"].sum()),
        buildable_units=buildable,
        max_lead_days=0.0 if pd.isna(lead) else lead,
        benchmark_price_dt=benchmark_price(wheel, tier),
    )
