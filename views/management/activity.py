"""Activity report tab — the GPAO's "Activités comparatives", section for section.

The GPAO (`frmActiviteComp1.vb`) compares a date window against the same window
one year earlier across nineteen cuts: sales by wheel size, customer and country;
customer orders; purchasing; component consumption; inbound and outbound freight.
That screen is what management reads, and it is what produced
`data/finance/erp-report-ytd-2026-09-21.xlsx`.

This tab reproduces all nineteen. Sixteen of the eighteen the GPAO exports tie to
its spreadsheet **to the cent**; the other two are the consumption sections,
which are slow enough to sit behind a button.

Where the GPAO's own arithmetic is inconsistent the tab shows the GPAO figure —
so the numbers still tie — and says so in the same breath. See
`docs/gpao-parity.md`.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import gpao_activity as G
from i18n import N_
from theme import style_fig, hbar_categories
from ._common import Ctx, TILE_H, sym, to_disp, compact

# The GPAO's own grouping of the nineteen sections into its tree menu.
GROUPS: list[tuple[str, list[str]]] = [
    (N_("Sales"), ["01", "02", "03", "04", "05", "06", "07", "08"]),
    (N_("Customer orders"), ["09", "10", "11"]),
    (N_("Purchasing"), ["12", "13", "14"]),
    (N_("Component consumption"), ["15", "16", "17"]),
    (N_("Freight"), ["18", "19"]),
]

MAX_BARS = 15


def render(ctx: Ctx) -> None:
    st.subheader(ctx.t("Activity report") + " — " + ctx.t("GPAO parity"))
    st.caption(ctx.t(
        "The GPAO's own \"Activités comparatives\" screen, reproduced query for query. "
        "It compares a date window against the same window one year earlier. Every "
        "figure here is the GPAO's — including, where they differ, its quirks."
    ))

    st.info(ctx.t(
        "**This tab is company-wide and sets its own dates.** It reproduces a GPAO "
        "report that takes a date window and nothing else, so the sidebar's "
        "**Distributor**, **Bikes only** and **Years** controls do not apply here — "
        "narrowing them elsewhere will not move these figures. The currency selector "
        "does apply."))

    d1, d2 = _window(ctx)
    p1, p2 = G._prior(d1), G._prior(d2)
    st.caption(ctx.tf(
        "**{a} → {b}** compared against **{c} → {d}**. Money is DT converted at each "
        "document's own FX rate, then shown in {ccy}.",
        a=f"{d1:%d %b %Y}", b=f"{d2:%d %b %Y}", c=f"{p1:%d %b %Y}", d=f"{p2:%d %b %Y}",
        ccy=ctx.ccy))

    _headline(d1, d2, ctx)
    _caveats(ctx)

    st.divider()
    for group, codes in GROUPS:
        st.markdown(f"#### {ctx.t(group)}")
        if group == "Component consumption":
            _consumption(codes, d1, d2, ctx)
            continue
        for code in codes:
            _section(code, d1, d2, ctx)
        st.write("")


# ------------------------------------------------------------ the window ---
def _window(ctx: Ctx) -> tuple[dt.date, dt.date]:
    """Defaults to year-to-date on the data's own last invoice date, which is
    what the GPAO export was run for. Both ends are free so any window works."""
    end = ctx.data_end.date() if ctx.data_end is not None else dt.date.today()
    c1, c2 = st.columns(2)
    d1 = c1.date_input(ctx.t("From"), value=dt.date(end.year, 1, 1),
                       min_value=dt.date(2010, 1, 1), max_value=end, key="act_d1",
                       format="DD/MM/YYYY")
    d2 = c2.date_input(ctx.t("To"), value=end, min_value=dt.date(2010, 1, 1),
                       max_value=end, key="act_d2", format="DD/MM/YYYY")
    if d1 > d2:
        d1, d2 = d2, d1
    return d1, d2


# ------------------------------------------------------- headline figures ---
def _headline(d1: dt.date, d2: dt.date, ctx: Ctx) -> None:
    """Revenue, units and average price per bike, as section 01/02 give them."""
    try:
        s01 = G.load_section("01", d1, d2)
        s02 = G.load_section("02", d1, d2)
        asp = G.load_asp(d1, d2)
    except Exception as exc:                        # a missing satellite table
        st.warning(ctx.tf("The sales sections could not be read: {err}", err=str(exc)[:200]))
        return

    rev, units = _total(s01), _total(s02)
    c1, c2, c3 = st.columns(3)
    c1.metric(ctx.t("Revenue"), compact(to_disp(rev[0], ctx), ctx.ccy),
              delta=_pct_delta(rev), border=True, height=TILE_H,
              help=ctx.t("Section 01 total, after the rebate line."))
    c2.metric(ctx.t("Units"), f"{units[0]:,.0f}",
              delta=_pct_delta(units), border=True, height=TILE_H,
              help=ctx.t("Section 02 total."))
    c3.metric(ctx.t("Average price per bike"),
              f"{sym(ctx.ccy)}{to_disp(asp.gpao_cur, ctx):,.2f}",
              delta=f"{asp.gpao_delta_pct:+.2f}%", border=True, height=TILE_H,
              help=ctx.tf(
                  "The GPAO's MOYENNE PAR VELO: section 01 revenue ÷ section 02 units. "
                  "Those two sections filter differently, and the GPAO rounds both "
                  "sides of the change to whole dinars before dividing — it prints "
                  "{gp:+.2f}% where the same two numbers give {tp:+.2f}%. On one "
                  "population the figure is {cor} ({cp:+.2f}%).",
                  gp=asp.gpao_delta_pct, tp=asp.true_delta_pct,
                  cor=f"{sym(ctx.ccy)}{to_disp(asp.corrected_cur, ctx):,.2f}",
                  cp=asp.corrected_delta_pct))


def _total(df: pd.DataFrame) -> tuple[float, float]:
    row = df[df["kind"] == "total"]
    if row.empty:
        return 0.0, 0.0
    return float(row["v1"].iloc[0]), float(row["v2"].iloc[0])


def _pct_delta(pair: tuple[float, float]) -> str | None:
    cur, prev = pair
    if not prev:
        return None
    return f"{(cur - prev) / abs(prev) * 100:+.1f}%"


# --------------------------------------------------------------- caveats ---
def _caveats(ctx: Ctx) -> None:
    """The three places the GPAO's own maths does not hold up.

    Kept on the page rather than in the docs alone: the tab's whole claim is that
    these numbers are the GPAO's, so a reader has to be told where the GPAO is
    the reason a figure looks odd."""
    with st.expander(ctx.t("Three places these figures don't reconcile — and why")):
        st.markdown(ctx.t(
            "These are defects in the GPAO's report, reproduced here so the dashboard "
            "ties to it. Each one is confirmed against the ERP and against the "
            "spreadsheet the GPAO exported.\n\n"
            "**1. Three different revenue totals for the same period.** Sections 01, 04 "
            "and 06 all claim to be *chiffre d'affaire*, and all three disagree: on the "
            "2026 YTD window they read 41,894,203 / 41,895,159 / 41,893,690 DT. They "
            "filter differently — 01 requires a wheel size and drops free-of-charge "
            "invoices, 04 does neither, 06 drops free-of-charge but needs no wheel "
            "size. Same for units: section 02 reports 136,426 where 05 and 07 report "
            "136,421.\n\n"
            "**2. Average price per bike mixes two populations.** MOYENNE PAR VELO is "
            "section 01's revenue over section 02's units — a filtered numerator over "
            "an unfiltered denominator, with a PIECES DIVERS bucket in the top and not "
            "the bottom. The headline tile above carries the one-population figure in "
            "its tooltip.\n\n"
            "**3. The price-change percentage is rounded before it is divided.** The "
            "GPAO computes `Math.Round(current − prior) / Math.Round(prior)`, so on the "
            "2026 window it prints −3.15 % where those same two averages give −3.26 %. "
            "The tile above shows the GPAO figure and names the correct one.\n\n"
            "A fourth is immaterial but real: section 01 counts a non-bike line in both "
            "its wheel bucket and its PIECES DIVERS bucket when the article still "
            "resolves to a wheel size. On the 2026 window that is one line, DT 513."
        ))


# --------------------------------------------------------------- sections ---
def _section(code: str, d1: dt.date, d2: dt.date, ctx: Ctx) -> None:
    sec = G.BY_CODE[code]
    label = f"{sec.code} — {ctx.t(sec.english)}"
    with st.expander(label):
        # The GPAO's own French heading, so a reader who knows that screen can
        # match this section to the one they already read every month.
        st.caption(f"{ctx.t(sec.description)}  ·  GPAO: *{sec.title}*  ·  `{sec.source}`")
        if sec.caveat:
            st.caption("⚠︎ " + ctx.t(sec.caveat))
        try:
            df = G.load_section(code, d1, d2)
        except Exception as exc:
            st.warning(ctx.tf("Could not read this section: {err}", err=str(exc)[:200]))
            return
        if df.empty:
            st.info(ctx.t("No rows in this window."))
            return
        _table(df, sec, ctx)
        _chart(df, sec, ctx)


def _table(df: pd.DataFrame, sec: G.Section, ctx: Ctx) -> None:
    out = df.copy()
    money = sec.unit == "money"
    if money:
        for c in ("v1", "v2", "delta"):
            out[c] = to_disp(out[c], ctx)

    st.dataframe(
        out.drop(columns=["kind"]), hide_index=True, width="stretch",
        column_config={
            "label": ctx.t("Designation"),
            "v1": st.column_config.NumberColumn(
                f"{ctx.t('Current')} ({ctx.ccy})" if money else ctx.t("Current"),
                format="localized"),
            "pct1": st.column_config.NumberColumn("%", format="%.2f%%"),
            "v2": st.column_config.NumberColumn(
                f"{ctx.t('Prior year')} ({ctx.ccy})" if money else ctx.t("Prior year"),
                format="localized"),
            "pct2": st.column_config.NumberColumn("% ", format="%.2f%%"),
            "delta": st.column_config.NumberColumn(ctx.t("Variance"), format="localized"),
            "delta_pct": st.column_config.NumberColumn(ctx.t("Variance %"), format="%+.1f%%"),
        },
    )
    if sec.remise:
        st.caption(ctx.t(
            "The `Remise` line is the rebate the GPAO subtracts from the total: the gap "
            "between what was invoiced and the negotiated price in `facture_remise_det`. "
            "Percentages are each row over the post-rebate total, which is why they sum "
            "to a hair over 100."))


def _chart(df: pd.DataFrame, sec: G.Section, ctx: Ctx) -> None:
    """Both years side by side for the biggest rows, sorted by the current year."""
    rows = df[df["kind"] == "row"].head(MAX_BARS).iloc[::-1]
    if rows.empty:
        return
    P = ctx.P
    v1 = to_disp(rows["v1"], ctx) if sec.unit == "money" else rows["v1"]
    v2 = to_disp(rows["v2"], ctx) if sec.unit == "money" else rows["v2"]

    fig = go.Figure()
    # Positional y with relabelled ticks — two rows can share a label (a country
    # billed through two client records) and would otherwise stack into one bar.
    y = hbar_categories(fig, rows["label"])
    fig.add_bar(y=y, x=v2, orientation="h", name=ctx.t("Prior year"),
                marker_color=P["muted"], opacity=0.55)
    fig.add_bar(y=y, x=v1, orientation="h", name=ctx.t("Current"),
                marker_color=P["series"][0])
    fig.update_layout(barmode="group", bargap=0.25)
    fig.update_xaxes(title=ctx.ccy if sec.unit == "money" else ctx.t("units"))
    fig = style_fig(fig, height=max(240, 26 * len(rows) + 90), xgrid=True, ygrid=False)
    # style_fig hides the legend by default; this chart has two series, so it needs one.
    fig.update_layout(showlegend=True)
    st.plotly_chart(fig, width="stretch")


# ----------------------------------------------------------- consumption ---
def _consumption(codes: list[str], d1: dt.date, d2: dt.date, ctx: Ctx) -> None:
    """Sections 15-17 walk every production declaration back to its BOM line and
    price it at the FX rate of the order's date. The GPAO does that with a
    correlated subquery over a 4.2M-row movement ledger, and it takes minutes —
    so it runs on request rather than on every page load."""
    st.caption(ctx.t(
        "Components consumed, valued from the production declarations "
        "(`eurocycles_db_calc.detart`) back through each order's bill of materials. "
        "These are the GPAO's heaviest queries, so they run on request and stay "
        "cached for the session."))

    # The costing database is restored separately from the sales one, and in this
    # copy it ends first. A window running past it produces a real but short
    # number, which is worth saying before the reader compares it to anything.
    covered = G.consumption_coverage()
    if covered is not None and covered.date() < d2:
        st.warning(ctx.tf(
            "**These three sections will under-report this window.** The costing "
            "database `{db}` is restored separately and its movements stop at "
            "**{end}**, while the window runs to {to}. The queries are the GPAO's, "
            "unchanged — the ledger simply ends early. Restore `{db}` alongside "
            "`eurocycles_db` to close the gap.",
            db="eurocycles_db_calc", end=f"{covered:%d %b %Y}", to=f"{d2:%d %b %Y}"))

    if not st.button(ctx.t("Run the consumption sections"), key="act_conso"):
        return
    with st.spinner(ctx.t("Walking the production declarations…")):
        for code in codes:
            _section(code, d1, d2, ctx)
