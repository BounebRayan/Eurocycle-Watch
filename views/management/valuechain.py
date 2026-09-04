"""Value chain tab — the shelf price against our build cost.

The one question the two halves of this dashboard could never answer together:
of the price a customer pays in Halfords for an Apollo bike, how much is our
build cost, how much do we keep, and how much does Halfords keep?

Everything here is **per bike, in £, ex-VAT**. The shelf sets the currency: the
retail price is quoted in pounds including UK VAT, so the comparison strips VAT
and pulls our side over to £ rather than the reverse. That makes this the one
tab the sidebar's currency selector doesn't drive — see `_controls`.

The join lives in `crosswalk.py`; this module only does the money.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import crosswalk
import erp
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, TILE_H

# The scraped shelf we can join to. `crosswalk.DISTRIBUTOR_CUSTOMER` maps it to
# the ERP customer; the label is how that customer reads in `customer.libcust`.
SHELF = "halfords"
SHELF_LABEL = "HALFORDS"

# The ERP invoices Halfords in USD and holds no GBP rate anywhere, so a £ rate
# has to come from outside it. The default is built from the ERP's own live USD
# rate times this one hand-set constant, which is the only number on the page
# not traceable to a source system — hence the visible control to override it.
USD_PER_GBP = 1.27
UK_VAT_PCT = 20.0


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader(f"Value chain — the {SHELF_LABEL.title()} shelf against our build cost")

    shelf, snapshot = crosswalk.load_shelf(SHELF)
    if shelf.empty:
        st.info(
            f"**No {SHELF_LABEL.title()} shelf snapshot to join to.** This tab reads the same "
            "scraped SQLite file as the Distributor view (`data/apollo_dashboard.db`). "
            "Run `python run_daily.py` to take a snapshot."
        )
        return

    hal = scope[scope["distributor"].str.upper().str.contains(SHELF_LABEL, na=False)]
    if hal.empty:
        st.info(
            f"**No {SHELF_LABEL.title()} sales in the current filters.** This tab is "
            f"{SHELF_LABEL.title()}-only — it needs both sides of the same shelf — so pick "
            f"*(all)* or *{SHELF_LABEL}* in the sidebar's **Distributor** filter."
        )
        return

    codcust = crosswalk.DISTRIBUTOR_CUSTOMER[SHELF]
    models = crosswalk.prepare_models(erp.load_customer_models(codcust))
    groups, links = crosswalk.match(shelf, models)

    rate, vat, basis, min_units = _controls(ctx)
    econ = _economics(groups, links, hal, rate, vat, basis)

    _coverage_caption(econ, groups, hal, links, models, snapshot, ctx)
    if econ.empty:
        st.warning("Nothing on the shelf matched a model with sales in this year range.")
        return

    ranked = econ[econ["units"] >= min_units]
    if ranked.empty:
        st.info(f"No matched model reached {min_units:,} units in {ctx.yr_lo}–{ctx.yr_hi}.")
        return

    _headline(ranked, ctx)
    st.divider()
    _value_split(ranked, ctx)
    st.divider()
    _leverage(ranked, ctx)
    st.divider()
    _table(ranked, ctx)
    _leftovers(groups, econ, hal, links, models, ctx)


# --------------------------------------------------------------- controls ---
def _controls(ctx: Ctx) -> tuple[float, float, str, int]:
    c1, c2, c3, c4 = st.columns([1.1, 1, 1.2, 1])
    default_rate = round(ctx.fx.get("USD", 3.0) * USD_PER_GBP, 3)
    rate = c1.number_input(
        "DT per £1", min_value=0.5, max_value=20.0, value=default_rate, step=0.01,
        format="%.3f",
        help=f"The ERP invoices {SHELF_LABEL.title()} in USD and stores no GBP rate, so this "
             f"one is set here. The default is the ERP's own latest USD rate "
             f"({ctx.fx.get('USD', 0):.3f} DT/$) × {USD_PER_GBP} $/£. Every £ figure on this "
             "tab moves with it — our costs and prices, never the retail price.")
    vat = c2.number_input("UK VAT %", min_value=0.0, max_value=30.0, value=UK_VAT_PCT, step=0.5,
                          help="Stripped from the shelf price before anything is compared with "
                               "our ex-works invoice.")
    basis = c3.selectbox("Retail price", ["Shelf price today", "RRP"],
                         help="Shelf price is what the site charges now (after any discount). "
                              "RRP is the price it is discounted from.")
    min_units = c4.number_input("Min units (period)", 0, 100000, 100, step=50,
                                help="Drops sample and run-out lines before ranking.")
    return rate, vat, ("shelf_price" if basis.startswith("Shelf") else "rrp"), int(min_units)


# --------------------------------------------------------------- the money ---
def _economics(groups: pd.DataFrame, links: pd.DataFrame, hal: pd.DataFrame,
               rate: float, vat: float, basis: str) -> pd.DataFrame:
    """One row per matched shelf model, with the whole chain per bike in £.

    Our side is a period average — total revenue and total COGS over the year
    range, divided by units — while the retail side is one day's shelf price.
    That asymmetry is the honest one: it is the only retail price we hold. A
    wide year range therefore reads today's shelf against an old average, which
    is what the year slider is for."""
    if links.empty:
        return pd.DataFrame()

    per_group = (links.merge(hal[["article", "qte", "line_rev_dt", "line_cogs_dt"]],
                             on="article", how="inner")
                 .groupby("group_id")
                 .agg(units=("qte", "sum"),
                      revenue_dt=("line_rev_dt", "sum"),
                      cogs_dt=("line_cogs_dt", "sum")))
    if per_group.empty:
        return pd.DataFrame()

    e = groups.merge(per_group, left_on="group_id", right_index=True, how="inner")
    e = e[e["units"] > 0].copy()

    # Ours, per bike, in £.
    e["cost"] = e["cogs_dt"] / e["units"] / rate
    e["fob"] = e["revenue_dt"] / e["units"] / rate
    e["our_margin"] = e["fob"] - e["cost"]

    # Theirs, per bike, in £ — the shelf price with VAT taken back off.
    e["retail_inc"] = e[basis]
    e["retail"] = e["retail_inc"] / (1 + vat / 100)
    e["their_margin"] = e["retail"] - e["fob"]

    e["our_margin_pct"] = _pct(e["our_margin"], e["fob"])
    e["their_margin_pct"] = _pct(e["their_margin"], e["retail"])
    e["we_keep_pct"] = _pct(e["fob"], e["retail"])
    e["multiple"] = np.where(e["fob"] > 0, e["retail"] / e["fob"], np.nan)

    e["label"] = np.where(e["ebike"] == 1, e["model"] + " ⚡", e["model"])
    has_wheel = e["wheel_in"].notna()
    e.loc[has_wheel, "label"] = (e.loc[has_wheel, "label"] + " "
                                 + e.loc[has_wheel, "wheel_in"].map(lambda w: f'{w:g}"'))
    return e.sort_values("units", ascending=False)


def _pct(num, den):
    return np.where(den > 0, num / den * 100, np.nan)


# ------------------------------------------------------------- the framing ---
def _coverage_caption(econ, groups, hal, links, models, snapshot, ctx) -> None:
    """How much of the picture this tab actually covers, stated before any
    number is read — the shelf is Apollo-only while we build several brands for
    this customer, so an unqualified "matched %" would badly understate it."""
    shelf_brand_units = hal.merge(models[["article", "brand_norm"]], on="article", how="left")
    apollo = shelf_brand_units[shelf_brand_units["brand_norm"] == "APOLLO"]
    matched = shelf_brand_units[shelf_brand_units["article"].isin(set(links["article"]))]

    apollo_units = apollo["qte"].sum()
    all_units = shelf_brand_units["qte"].sum()
    cover = matched["qte"].sum() / apollo_units * 100 if apollo_units > 0 else 0
    apollo_share = apollo_units / all_units * 100 if all_units > 0 else 0

    st.caption(
        f"Shelf read {snapshot} · {int(groups['n_articles'].gt(0).sum())} of {len(groups)} "
        f"listed models matched to {links['article'].nunique():,} of our articles, covering "
        f"**{cover:.0f}%** of the Apollo bikes we invoiced {SHELF_LABEL.title()} in "
        f"{ctx.yr_lo}–{ctx.yr_hi}. Apollo is {apollo_share:.0f}% of our "
        f"{SHELF_LABEL.title()} volume — the rest is Carrera, Indi and Trax, which this "
        "Apollo-only shelf scrape can't see. Every figure below is **per bike, in £, "
        "ex-VAT**, so the sidebar's currency selector doesn't apply here."
    )


def _headline(e: pd.DataFrame, ctx: Ctx) -> None:
    units = e["units"].sum()
    # Blended on units, not a mean of percentages — a 30 000-unit ENVY and a
    # 400-unit TUCK must not weigh the same.
    rev = (e["fob"] * e["units"]).sum()
    cost = (e["cost"] * e["units"]).sum()
    retail = (e["retail"] * e["units"]).sum()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Bikes in view", f"{units:,.0f}", border=True, height=TILE_H,
              help=f"Units invoiced {ctx.yr_lo}–{ctx.yr_hi} on the models matched to the shelf.")
    c2.metric("Retail value", f"£{retail / 1e6:,.1f}M", border=True, height=TILE_H,
              help="What those bikes are worth at today's shelf price, ex-VAT — "
                   "the end-customer value the whole chain shares.")
    c3.metric("Our margin", f"{_pct(rev - cost, rev):.1f}%", border=True, height=TILE_H,
              help=f"£{(rev - cost) / 1e6:,.1f}M on £{rev / 1e6:,.1f}M invoiced. "
                   "Company target is 25%.")
    c4.metric(f"{SHELF_LABEL.title()}' margin", f"{_pct(retail - rev, retail):.1f}%",
              border=True, height=TILE_H,
              help=f"£{(retail - rev) / 1e6:,.1f}M — what the shelf keeps between our invoice "
                   "and the ex-VAT retail price. Their buying, warehousing and store cost "
                   "comes out of this; it is a gross margin, not their profit.")
    c5.metric("We keep", f"{_pct(rev, retail):.0f}%", border=True, height=TILE_H,
              help="Our invoice as a share of the ex-VAT retail price — our slice of what "
                   "the end customer pays.")


# -------------------------------------------------------- where it all goes ---
def _value_split(e: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader("Where the retail price goes")
    st.caption("Each bar is one bike at its shelf price, ex-VAT, split three ways. "
               "Ordered by units invoiced, so the bikes that matter most are at the top.")
    P = ctx.P
    d = e.head(18).iloc[::-1]

    fig = go.Figure()
    for col, name, colour in (("cost", "Our build cost", P["series"][0]),
                              ("our_margin", "Our margin", P["series"][2]),
                              ("their_margin", f"{SHELF_LABEL.title()}' margin", P["series"][1])):
        fig.add_bar(
            x=d[col], y=hbar_categories(fig, d["label"]), orientation="h",
            name=name,
            # 2px surface ring so the stacked segments read as separate blocks.
            marker=dict(color=colour, line=dict(color=P["surface"], width=2)),
            customdata=d[["units", "retail"]].to_numpy(),
            hovertemplate="<b>%{y}</b><br>" + name + " £%{x:,.0f}<br>"
                          "retail £%{customdata[1]:,.0f} ex-VAT · "
                          "%{customdata[0]:,.0f} units<extra></extra>")

    style_fig(fig, height=max(320, 28 * len(d) + 70), xgrid=True, ygrid=False)
    fig.update_layout(barmode="relative", showlegend=True, bargap=0.35,
                      margin=dict(l=4, r=16, t=48, b=4))
    fig.update_xaxes(title_text="£ per bike, ex-VAT", tickprefix="£")
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption("A bar where the third block runs backwards is a bike whose shelf price has "
               "fallen below what we invoice — a clearance line, or a match worth checking.")


# -------------------------------------------------------------- the leverage ---
def _leverage(e: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader("Who is making the money")
    P = ctx.P
    their_blend = float(_pct((e["their_margin"] * e["units"]).sum(),
                             (e["retail"] * e["units"]).sum()))
    our_blend = float(_pct((e["our_margin"] * e["units"]).sum(),
                           (e["fob"] * e["units"]).sum()))
    # Both dividers are the blended averages, not the 25 % company target: every
    # Apollo model sold to this shelf clears 25 %, so a target line would leave
    # the quadrant empty and say nothing. The question worth asking here is
    # relative — on which bikes does the shelf do better than its usual while we
    # do worse than ours.
    st.caption(
        f"Our margin against theirs, one dot per model, sized by units. The lines are the "
        f"blended averages — ours {our_blend:.0f}%, theirs {their_blend:.0f}%. "
        "**Top-left is the negotiating list**: bikes where the shelf keeps more than its "
        "usual share and we keep less than ours."
    )

    d = e.dropna(subset=["our_margin_pct", "their_margin_pct"]).copy()
    sizes = np.sqrt(d["units"].clip(lower=1))
    # Only the quadrant that carries an action gets the accent; everything else
    # is one recessive colour, so the eye lands on the list to act on.
    leverage = (d["our_margin_pct"] < our_blend) & (d["their_margin_pct"] > their_blend)
    colours = [P["series"][1] if lv else P["series"][0] for lv in leverage]

    fig = go.Figure(go.Scatter(
        x=d["our_margin_pct"], y=d["their_margin_pct"], mode="markers",
        marker=dict(color=colours, size=sizes, sizemode="area",
                    sizeref=2.0 * sizes.max() / (34 ** 2), sizemin=5,
                    line=dict(color=P["surface"], width=1)),
        customdata=d[["label", "units", "fob", "retail"]].to_numpy(),
        hovertemplate="<b>%{customdata[0]}</b><br>we keep %{x:.1f}% of £%{customdata[2]:,.0f}"
                      "<br>they keep %{y:.1f}% of £%{customdata[3]:,.0f}"
                      "<br>%{customdata[1]:,.0f} units<extra></extra>"))
    fig.add_vline(x=our_blend, line_width=1, line_dash="dot", line_color=P["axis"])
    fig.add_hline(y=their_blend, line_width=1, line_dash="dot", line_color=P["axis"])
    style_fig(fig, height=420, xgrid=True, ygrid=True)
    fig.update_xaxes(title_text="Our gross margin %", ticksuffix="%")
    fig.update_yaxes(title_text=f"{SHELF_LABEL.title()}' gross margin %", ticksuffix="%")
    st.plotly_chart(fig, width="stretch", theme=None)

    top = (d[leverage].assign(gap=lambda f: (f["their_margin"] - f["our_margin"]) * f["units"])
           .nlargest(5, "gap"))
    if not top.empty:
        names = ", ".join(f"**{r.label}** ({r.our_margin_pct:.0f}% vs {r.their_margin_pct:.0f}%)"
                          for r in top.itertuples())
        st.markdown(f"Biggest gaps by volume: {names}.")


# ------------------------------------------------------------------- table ---
def _table(e: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader("Every matched model")
    st.dataframe(
        e[["label", "units", "cost", "fob", "our_margin_pct", "retail", "retail_inc",
           "their_margin_pct", "we_keep_pct", "multiple", "listings", "n_articles",
           "confidence"]],
        hide_index=True, width="stretch",
        column_config={
            "label": "Model",
            "units": st.column_config.NumberColumn("Units", format="%d"),
            "cost": st.column_config.NumberColumn("Build cost", format="£%.0f"),
            "fob": st.column_config.NumberColumn("We invoice", format="£%.0f"),
            "our_margin_pct": st.column_config.NumberColumn("Our margin", format="%.1f%%"),
            "retail": st.column_config.NumberColumn("Retail ex-VAT", format="£%.0f"),
            "retail_inc": st.column_config.NumberColumn("Shelf price", format="£%.0f"),
            "their_margin_pct": st.column_config.NumberColumn("Their margin", format="%.1f%%"),
            "we_keep_pct": st.column_config.NumberColumn("We keep", format="%.0f%%"),
            "multiple": st.column_config.NumberColumn("Retail ×", format="%.2f×",
                                                      help="Ex-VAT retail ÷ our invoice."),
            "listings": st.column_config.NumberColumn("Listings", format="%d",
                                                      help="Colourways listed for this model."),
            "n_articles": st.column_config.NumberColumn("Articles", format="%d",
                                                        help="Our articles folded into it."),
            "confidence": "Match",
        },
    )


# --------------------------------------------------------- the two leftovers ---
def _leftovers(groups, econ, hal, links, models, ctx: Ctx) -> None:
    """Both sides' unmatched rows. Neither is an error to hide: one is shelf
    space we don't supply, the other is a model we build that has left the
    shelf."""
    st.divider()
    matched_ids = set(econ["group_id"]) if not econ.empty else set()

    unbuilt = groups[groups["n_articles"] == 0]
    with st.expander(f"On the shelf, not built by us — {len(unbuilt)} models"):
        st.caption(
            "Apollo-branded bikes Halfords lists that no model of ours matches. Some are "
            "genuinely sourced elsewhere — that is shelf space to compete for; others are "
            "ours under a name the shelf doesn't use. Either is worth a look."
        )
        if unbuilt.empty:
            st.caption("Nothing — every listed model matched.")
        else:
            u = unbuilt.assign(wheel=unbuilt["wheel_in"].map(
                lambda w: "" if pd.isna(w) else f'{w:g}"'))
            st.dataframe(
                u[["example", "wheel", "listings", "shelf_price", "categories", "url"]],
                hide_index=True, width="stretch",
                column_config={
                    "example": "Listing", "wheel": "Wheel",
                    "listings": st.column_config.NumberColumn("Listings", format="%d"),
                    "shelf_price": st.column_config.NumberColumn("Shelf price", format="£%.0f"),
                    "categories": "Category",
                    "url": st.column_config.LinkColumn("Link", display_text="open"),
                })

    # Ours with sales in the period that no live listing claims. `hal` already
    # carries `libnach` from `load_sales`, so only the derived columns are
    # merged in — pulling `libnach` across again would collide into
    # `libnach_x` / `libnach_y`.
    unlisted = (hal[~hal["article"].isin(set(links["article"]))]
                .merge(models[["article", "brand_norm", "season_yr"]],
                       on="article", how="left"))
    unlisted = unlisted[unlisted["brand_norm"] == "APOLLO"]
    agg = (unlisted.groupby("libnach")
           .agg(units=("qte", "sum"), revenue=("line_rev_dt", "sum"),
                last_yr=("yr", "max"), season=("season_yr", "max"))
           .sort_values("units", ascending=False).reset_index())
    with st.expander(f"Built by us, not on the shelf today — {len(agg)} Apollo articles"):
        st.caption(
            f"Apollo models we invoiced {SHELF_LABEL.title()} in {ctx.yr_lo}–{ctx.yr_hi} that "
            "no live listing matches: models dropped from the range, sizes or e-bike versions "
            "the shelf doesn't carry, and run-out stock. A recent `last sold` on a big volume "
            "is a delisting worth asking about."
        )
        if agg.empty:
            st.caption("Nothing — every model we sold is still listed.")
        else:
            st.dataframe(
                agg.head(60), hide_index=True, width="stretch",
                column_config={
                    "libnach": "Our model",
                    "units": st.column_config.NumberColumn("Units", format="%d"),
                    "revenue": st.column_config.NumberColumn("Revenue (DT)", format="%.0f"),
                    "last_yr": st.column_config.NumberColumn("Last sold", format="%d"),
                    "season": st.column_config.NumberColumn("Season", format="%d"),
                })

    with st.expander("How the join works"):
        st.markdown(f"""
No identifier is shared between the two systems, so a listing is matched to our
articles by **model name**, narrowed by **e-bike flag** and **wheel size**:

| | Halfords listing | Our article |
|---|---|---|
| name | first word after *Apollo* | `libnach`, less the `A - ` brand initial, an e-bike's leading `e`, and a `NEW` season marker |
| e-bike | *Electric* in the title, or the electric-bikes category | `nomachat.ebike` |
| wheel | `24" Wheel` in the title (adult bikes are sized by frame and give none) | decoded from `codnach` — `HA 2427813` is season 2024, 27.5" wheel |

Grain is the model as the shelf presents it, not the listing and not the
article: Halfords lists one bike several times (once per colourway) and we hold
one article per frame size per season, so each side is folded up before they
meet. **Every article is claimed by at most one model**, so units can't be
double-counted.

`Match` in the table says how tight each one is — *{crosswalk.CONF_WHEEL}* is
name and wheel size; *{crosswalk.CONF_MODEL}* is a frame-sized bike where the
name alone decides it.

**An exact-key join isn't available.** `nomachat.gencodnach` holds an EAN on most
Halfords models, but Halfords publishes no GTIN to match it against — checked
across the search API, the product page, and the payload the page itself fetches.
That payload does carry the bike's spec as separate fields (wheel size, gender,
frame material, brake type, gears, derailleur), which are the very tokens
`modnach` is built from. Capturing those would let the models that currently
match on name alone match on spec too.
""")
