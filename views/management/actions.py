"""Actions tab — the to-do list, not a report.

Every other tab answers "what happened". This one answers "what should someone
do about it on Monday". It runs a fixed set of rules over the same data the
other tabs chart, keeps only the rows that break a threshold, sizes each one in
money, and ranks them.

Two deliberate constraints:

* **Every item names the money at stake**, on a stated basis, so the list can be
  worked top-down. A rule that can't be sized in money doesn't belong here.
* **Every item says which tab explains it.** This tab is the index; the evidence
  stays where it already lives.

Adding a rule means writing a `_rule_*` function that returns a `Finding`, and
adding it to `RULES`. Nothing else changes — the page ranks and renders whatever
the list produces, and a rule that finds nothing is silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
import streamlit as st

import crosswalk
import erp
from ._common import Ctx, TILE_H, money, tile, to_disp
from . import valuechain

# Company gross-margin target — the same 25% (`margeProduitFini`) the Overview
# and Models tabs draw their reference lines at.
TARGET_MARGIN_PCT = 25.0

# Volume floors. A 12-unit sample line breaking a margin rule is noise, and a
# list that surfaces it gets ignored wholesale.
MIN_UNITS_MARGIN = 200
MIN_UNITS_LOSS = 50
MIN_PLANNED_UNITS = 500

# A model has to lose this many points of margin year on year before it's worth
# a conversation; below it, mix and timing explain most of the movement.
EROSION_PP = 3.0
# An OF this far under its plan is a miss worth listing.
ATTAINMENT_PCT = 80.0
# How far past its planned week an order has to be before a shortfall counts as
# missed rather than simply not started yet.
STALE_OF_WEEKS = 4


@dataclass
class Finding:
    """One rule's output. `value_dt` is what ranks it, so it must be a real DT
    amount on the basis the rule's `basis` line states — never a made-up score."""
    key: str
    title: str
    why: str                      # what the reader should do about it
    basis: str                    # how `value_dt` was arrived at, in one line
    tab: str                      # which tab holds the evidence
    items: pd.DataFrame
    value_dt: float
    columns: dict = field(default_factory=dict)


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader(f"What needs attention — {ctx.dist_label}, {ctx.yr_lo}–{ctx.yr_hi}")

    findings: list[Finding] = []
    for rule in RULES:
        try:
            f = rule(scope, ctx)
        except Exception as e:                      # one broken rule must not
            st.warning(f"Rule `{rule.__name__}` failed: {e}")   # take the page down
            continue
        if f is not None and not f.items.empty:
            findings.append(f)

    if not findings:
        st.success("**Nothing is breaking a threshold in this period.** "
                   "Widen the year range or lower the filters if that reads too quiet.")
        return

    findings.sort(key=lambda f: f.value_dt, reverse=True)
    total = sum(f.value_dt for f in findings)
    items = sum(len(f.items) for f in findings)

    c1, c2, c3 = st.columns(3)
    c1.metric("Open items", f"{items:,}", border=True, height=TILE_H,
              help="Rows breaking a threshold across every rule below.")
    c2.metric("At stake", tile(total, ctx), border=True, height=TILE_H,
              help=f"{money(total, ctx)} — the sum of every item's sizing. Each rule "
                   "states its own basis; they are not all the same kind of money, so "
                   "read this as an order of magnitude, not a forecast.")
    c3.metric("Rules triggered", f"{len(findings)} of {len(RULES)}", border=True, height=TILE_H)

    st.caption("Ranked by money at stake. Each block says how that number was arrived at "
               "and which tab holds the evidence — this page is the index, not the analysis.")

    for f in findings:
        st.divider()
        left, right = st.columns([3, 1])
        left.markdown(f"### {f.title}")
        right.metric("At stake", tile(f.value_dt, ctx), border=True,
                     label_visibility="collapsed")
        st.markdown(f"{f.why}")
        st.caption(f"**{len(f.items)} items** · {f.basis} · evidence: **{f.tab}** tab")
        st.dataframe(f.items, hide_index=True, width="stretch", column_config=f.columns)


# --------------------------------------------------------------- helpers ---
def _per_model_year(scope: pd.DataFrame, yr: int) -> pd.DataFrame:
    """Revenue / COGS / units per model for one year.

    The label is `libnach` ("CHARM 20 GIRLS D.SUS BLACK/AQUA"), not the
    `model_label` the other tabs use — that resolves to `modnach`, which is a
    spec string ("24x13 H.TAIL DDISC BOYS 14SP REVO TY300"). A spec is fine
    beside a chart; on a to-do list the reader has to recognise the bike, so the
    article code travels with it."""
    d = scope[scope["yr"] == yr]
    g = d.groupby("article").agg(
        model=("libnach", "first"), brand=("brandnach", "first"),
        distributor=("distributor", "first"),
        revenue=("line_rev_dt", "sum"), cogs=("line_cogs_dt", "sum"), units=("qte", "sum"))
    g["margin"] = g["revenue"] - g["cogs"]
    g["margin_pct"] = np.where(g["revenue"] > 0, g["margin"] / g["revenue"] * 100, np.nan)
    return g.reset_index()


def _latest_years(scope: pd.DataFrame) -> tuple[int | None, int | None]:
    ys = sorted(scope["yr"].unique())
    return (int(ys[-1]) if ys else None, int(ys[-2]) if len(ys) > 1 else None)


def _money_col(label: str) -> "st.column_config.NumberColumn":
    return st.column_config.NumberColumn(label, format="%.0f")


# ----------------------------------------------------------------- rules ---
def _rule_below_target(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Models earning less than the company target on real volume."""
    yr, _ = _latest_years(scope)
    if yr is None:
        return None
    pm = _per_model_year(scope, yr)
    bad = pm[(pm["units"] >= MIN_UNITS_MARGIN) & (pm["margin_pct"] < TARGET_MARGIN_PCT)
             & (pm["margin_pct"] >= 0)].copy()
    if bad.empty:
        return None
    # What the model would have earned at target, less what it did earn.
    bad["shortfall"] = (TARGET_MARGIN_PCT - bad["margin_pct"]) / 100 * bad["revenue"]
    bad = bad.sort_values("shortfall", ascending=False)
    out = bad[["article", "model", "brand", "distributor", "units", "margin_pct",
               "shortfall"]].copy()
    out["shortfall"] = to_disp(out["shortfall"], ctx)
    return Finding(
        key="below_target", title=f"Models under the {TARGET_MARGIN_PCT:.0f}% margin target",
        why="Each of these sold real volume at a margin below the company target. Either the "
            "price is wrong for the cost, or the cost has moved and the price hasn't followed. "
            "The largest few are worth a costing review before the next season's price list.",
        basis=f"revenue × the gap to {TARGET_MARGIN_PCT:.0f}%, in {yr}",
        tab="Models", items=out.head(25),
        value_dt=float(bad["shortfall"].sum()),
        columns={"article": "Code", "model": "Model", "brand": "Brand",
                 "distributor": "Customer",
                 "units": st.column_config.NumberColumn("Units", format="%d"),
                 "margin_pct": st.column_config.NumberColumn("Margin", format="%.1f%%"),
                 "shortfall": _money_col(f"Shortfall ({ctx.ccy})")})


def _rule_loss_making(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Models invoiced below what they cost to build."""
    yr, _ = _latest_years(scope)
    if yr is None:
        return None
    pm = _per_model_year(scope, yr)
    # `revenue > 0` on purpose: a line with COGS and no revenue is a sample or a
    # free-of-charge shipment, which is a real cost leak but not a pricing
    # decision. Listing it under "sold below cost" would mislabel it, and its
    # margin % is undefined anyway.
    bad = pm[(pm["units"] >= MIN_UNITS_LOSS) & (pm["margin"] < 0)
             & (pm["revenue"] > 0)].copy()
    if bad.empty:
        return None
    bad["loss"] = -bad["margin"]
    bad = bad.sort_values("loss", ascending=False)
    out = bad[["article", "model", "brand", "distributor", "units", "margin_pct",
               "loss"]].copy()
    out["loss"] = to_disp(out["loss"], ctx)
    return Finding(
        key="loss", title="Models sold below build cost",
        why="These were invoiced for less than the ERP costed them at. A handful are usually "
            "run-out or contract lines that were always going to lose money; any that aren't "
            "should stop or be repriced now, not at the next review.",
        basis=f"the realised loss in {yr}",
        tab="Models", items=out.head(25), value_dt=float(bad["loss"].sum()),
        columns={"article": "Code", "model": "Model", "brand": "Brand",
                 "distributor": "Customer",
                 "units": st.column_config.NumberColumn("Units", format="%d"),
                 "margin_pct": st.column_config.NumberColumn("Margin", format="%.1f%%"),
                 "loss": _money_col(f"Loss ({ctx.ccy})")})


def _rule_erosion(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Models whose margin fell year on year on steady volume."""
    yr, prev = _latest_years(scope)
    if prev is None:
        return None
    cur, old = _per_model_year(scope, yr), _per_model_year(scope, prev)
    m = cur.merge(old[["article", "margin_pct", "units"]], on="article",
                  suffixes=("", "_prev"))
    m = m[(m["units"] >= MIN_UNITS_MARGIN) & (m["units_prev"] >= MIN_UNITS_MARGIN)]
    m["drop_pp"] = m["margin_pct_prev"] - m["margin_pct"]
    bad = m[m["drop_pp"] >= EROSION_PP].copy()
    if bad.empty:
        return None
    # What holding last year's margin rate would have been worth on this year's
    # revenue — the cost of the drift, not of the whole gap to target.
    bad["cost_of_drift"] = bad["drop_pp"] / 100 * bad["revenue"]
    bad = bad.sort_values("cost_of_drift", ascending=False)
    out = bad[["article", "model", "brand", "units", "margin_pct_prev", "margin_pct",
               "drop_pp", "cost_of_drift"]].copy()
    out["cost_of_drift"] = to_disp(out["cost_of_drift"], ctx)
    return Finding(
        key="erosion", title=f"Margin eroding — {prev} to {yr}",
        why=f"Models that held their volume but lost at least {EROSION_PP:.0f} points of margin "
            "year on year. Steady volume rules out mix as the explanation, which leaves price "
            "or cost — the Supply & cost tab shows which components moved.",
        basis=f"{yr} revenue × the points of margin lost since {prev}",
        tab="Models", items=out.head(25), value_dt=float(bad["cost_of_drift"].sum()),
        columns={"article": "Code", "model": "Model", "brand": "Brand",
                 "units": st.column_config.NumberColumn("Units", format="%d"),
                 "margin_pct_prev": st.column_config.NumberColumn(f"{prev}", format="%.1f%%"),
                 "margin_pct": st.column_config.NumberColumn(f"{yr}", format="%.1f%%"),
                 "drop_pp": st.column_config.NumberColumn("Change", format="%.1f pp"),
                 "cost_of_drift": _money_col(f"Cost of drift ({ctx.ccy})")})


def _rule_price_rise(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Halfords models where the shelf keeps more than its usual share and we
    keep less than ours — the price-review shortlist from the Value chain tab."""
    hal = scope[scope["distributor"].str.upper().str.contains(valuechain.SHELF_LABEL, na=False)]
    if hal.empty:
        return None
    shelf, _ = crosswalk.load_shelf(valuechain.SHELF)
    if shelf.empty:
        return None
    models = crosswalk.prepare_models(
        erp.load_customer_models(crosswalk.DISTRIBUTOR_CUSTOMER[valuechain.SHELF]))
    groups, links = crosswalk.match(shelf, models)
    rate = round(ctx.fx.get("USD", 3.0) * valuechain.USD_PER_GBP, 3)
    e = valuechain._economics(groups, links, hal, rate, valuechain.UK_VAT_PCT, "shelf_price")
    if e.empty:
        return None
    e = e[e["units"] >= MIN_UNITS_MARGIN]
    if e.empty:
        return None

    our_blend = float(np.average(e["our_margin_pct"], weights=e["units"] * e["fob"]))
    their_blend = float(np.average(e["their_margin_pct"], weights=e["units"] * e["retail"]))
    bad = e[(e["our_margin_pct"] < our_blend) & (e["their_margin_pct"] > their_blend)].copy()
    if bad.empty:
        return None
    # Sized at bringing the model up to our *own* blended margin — not at taking
    # the retailer's share, which isn't ours to take and would inflate this
    # wildly. Back into DT so it ranks against the other rules.
    bad["upside"] = ((our_blend - bad["our_margin_pct"]) / 100
                     * bad["fob"] * bad["units"] * rate)
    bad = bad.sort_values("upside", ascending=False)
    out = bad[["label", "units", "our_margin_pct", "their_margin_pct", "fob", "retail",
               "upside"]].copy()
    out["upside"] = to_disp(out["upside"], ctx)
    return Finding(
        key="price_rise", title=f"{valuechain.SHELF_LABEL.title()} price-review shortlist",
        why=f"On these the shelf keeps more than its usual {their_blend:.0f}% while we keep "
            f"less than our usual {our_blend:.0f}%. That asymmetry is the argument to take "
            "into the next price negotiation — it is evidence about the split, not proof the "
            "price can move.",
        basis=f"our revenue × the gap to our own {our_blend:.0f}% blended margin",
        tab="Value chain", items=out.head(25), value_dt=float(bad["upside"].sum()),
        columns={"label": "Model",
                 "units": st.column_config.NumberColumn("Units", format="%d"),
                 "our_margin_pct": st.column_config.NumberColumn("We keep", format="%.1f%%"),
                 "their_margin_pct": st.column_config.NumberColumn("They keep", format="%.1f%%"),
                 "fob": st.column_config.NumberColumn("We invoice", format="£%.0f"),
                 "retail": st.column_config.NumberColumn("Retail ex-VAT", format="£%.0f"),
                 "upside": _money_col(f"Upside ({ctx.ccy})")})


def _rule_delisted(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Apollo models still selling that no live Halfords listing claims."""
    hal = scope[scope["distributor"].str.upper().str.contains(valuechain.SHELF_LABEL, na=False)]
    if hal.empty:
        return None
    shelf, snapshot = crosswalk.load_shelf(valuechain.SHELF)
    if shelf.empty:
        return None
    models = crosswalk.prepare_models(
        erp.load_customer_models(crosswalk.DISTRIBUTOR_CUSTOMER[valuechain.SHELF]))
    _, links = crosswalk.match(shelf, models)

    yr, _prev = _latest_years(hal)
    recent = hal[(hal["yr"] == yr) & (~hal["article"].isin(set(links["article"])))]
    recent = recent.merge(models[["article", "brand_norm"]], on="article", how="left")
    recent = recent[recent["brand_norm"] == "APOLLO"]
    if recent.empty:
        return None
    agg = (recent.groupby("article")
           .agg(model=("libnach", "first"), units=("qte", "sum"),
                revenue=("line_rev_dt", "sum"))
           .sort_values("revenue", ascending=False).reset_index())
    agg = agg[agg["units"] >= MIN_UNITS_MARGIN]
    if agg.empty:
        return None
    out = agg.copy()
    out["revenue"] = to_disp(out["revenue"], ctx)
    return Finding(
        key="delisted", title="Selling into a shelf that no longer lists them",
        why=f"Apollo models we invoiced in {yr} that nothing on the {snapshot} shelf matches. "
            "Some are ordinary run-out; a large one is a delisting to confirm with the buyer "
            "before the volume is planned into next season.",
        basis=f"{yr} revenue on models with no live listing",
        tab="Value chain", items=out.head(25), value_dt=float(agg["revenue"].sum()),
        columns={"article": "Code", "model": "Our model",
                 "units": st.column_config.NumberColumn("Units", format="%d"),
                 "revenue": _money_col(f"Revenue ({ctx.ccy})")})


def _rule_attainment(scope: pd.DataFrame, ctx: Ctx) -> Finding | None:
    """Production orders whose week is well past and that never caught up.

    **Not** keyed on `declarationprd.fermee`: only 25 of 16,248 orders in this
    copy carry it, all of them for 1-6 units, so a rule gated on "closed" could
    never fire no matter how badly the line slipped. Age is the usable proxy —
    an order whose planned week is weeks behind isn't going to catch up.

    Aged against the **data's own latest invoice**, not the wall clock, for the
    same reason `Ctx.partial_year` is: a stale ERP restore must not make every
    recent order look overdue. `ordprevision` legitimately holds plans months
    into the future, so a wall-clock cut would sweep them all in."""
    po = erp.load_production_orders(start_year=min(ctx.yr_lo, 2022))
    cutoff = ctx.data_end - pd.Timedelta(weeks=STALE_OF_WEEKS)
    po = po[(po["yr"] >= ctx.yr_lo) & (po["yr"] <= ctx.yr_hi)
            & (po["planned_date"] <= cutoff) & (po["planned_qty"] >= MIN_PLANNED_UNITS)]
    bad = po[po["attainment"] < ATTAINMENT_PCT].copy()
    if bad.empty:
        return None
    bad["short_units"] = bad["planned_qty"] - bad["declared_qty"]

    # Value the shortfall at the average margin per bike actually earned in the
    # period — the money the missed units didn't earn. `scope` may be filtered
    # to one customer while production is company-wide, so this is a rate, not
    # a claim about these specific orders.
    units = scope["qte"].sum()
    margin_per_unit = (scope["line_margin_dt"].sum() / units) if units > 0 else 0.0
    value = float(bad["short_units"].sum() * margin_per_unit)

    bad = bad.sort_values("short_units", ascending=False)
    out = bad[["id_o", "article", "brandnach", "yr", "week", "planned_qty", "declared_qty",
               "attainment"]].copy()
    return Finding(
        key="attainment", title="Production orders left under plan",
        why=f"Orders whose planned week is more than {STALE_OF_WEEKS} weeks behind the latest "
            f"invoice and that still declared under {ATTAINMENT_PCT:.0f}% of plan. They aren't "
            "going to catch up, so each is volume the plan expected and the line never made — "
            "check whether the shortfall was re-planned or lost.",
        basis="missed units × the average margin per bike earned in the period",
        tab="Production", items=out.head(25), value_dt=value,
        columns={"id_o": "OF", "article": "Article", "brandnach": "Brand",
                 "yr": st.column_config.NumberColumn("Year", format="%d"),
                 "week": st.column_config.NumberColumn("Week", format="%d"),
                 "planned_qty": st.column_config.NumberColumn("Planned", format="%d"),
                 "declared_qty": st.column_config.NumberColumn("Declared", format="%d"),
                 "attainment": st.column_config.NumberColumn("Attainment", format="%.0f%%")})


RULES: list[Callable[[pd.DataFrame, Ctx], Finding | None]] = [
    _rule_below_target,
    _rule_loss_making,
    _rule_erosion,
    _rule_price_rise,
    _rule_delisted,
    _rule_attainment,
]
