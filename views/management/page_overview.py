"""Overview — the global view, and the only page meant to be read in full.

Four questions, in the order someone actually asks them:

1. **How are we doing?**   the ERP scoreboard, with like-for-like year-on-year
2. **Do we make money?**   the accounting pack's walk down to net profit
3. **Why did it move?**    the margin bridge
4. **What do I do?**       the top few findings, each linking to its evidence

Everything else is one click away and stays there. The sections here are the
compact cuts — `finance.profit_summary` rather than the full P&L,
`bridge.waterfall_section` without the per-model drill, `actions.compact_list`
rather than every finding with its table — so that this page fits on a screen
and the detail pages stay worth opening.

The attention list is last on purpose. It runs every rule, and Streamlit streams
the page top-down, so the tiles and charts paint while it works.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import style_fig
from i18n import N_
from . import actions, bridge, finance, _nav
from ._common import (Ctx, TILE_H, sym, money, tile, to_disp, yoy,
                      like_for_like, partial_year_note)


def _year_agg(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("yr").agg(
        revenue=("line_rev_dt", "sum"),
        cogs=("line_cogs_dt", "sum"),
        units=("qte", "sum"),
        models=("article", "nunique"),
        invoices=("numf", "nunique"),
    )
    g["margin"] = g["revenue"] - g["cogs"]
    g["margin_pct"] = np.where(g["revenue"] > 0, g["margin"] / g["revenue"] * 100, np.nan)
    g["asp"] = np.where(g["units"] > 0, g["revenue"] / g["units"], np.nan)
    return g.reset_index()


def scoreboard(scope: pd.DataFrame, ctx: Ctx) -> pd.DataFrame:
    """The five headline tiles. Returns the per-year aggregate for the chart.

    Tile values are the full period; the deltas come off the like-for-like
    frame, so an eight-month year isn't read against a full one."""
    st.subheader(ctx.t("Company scoreboard") + f" — {ctx.dist_label}")
    ya = _year_agg(scope)                                        # full period
    lfl = _year_agg(like_for_like(scope, ctx)).set_index("yr")    # trimmed: deltas only
    cur = ya.iloc[-1]
    y_cur = int(cur["yr"])
    y_prev = int(ya.iloc[-2]["yr"]) if len(ya) > 1 else None

    def d(col, *, pct=False):
        if y_prev is None or y_cur not in lfl.index or y_prev not in lfl.index:
            return None
        return yoy(lfl.at[y_cur, col], lfl.at[y_prev, col], pct=pct, ctx=ctx)

    is_partial = ctx.partial_year == y_cur

    c1, c2, c3, c4, c5 = st.columns(5)
    ytd = " (YTD)" if is_partial else ""
    c1.metric(ctx.t("Revenue") + f" {y_cur}{ytd}", tile(cur["revenue"], ctx),
              delta=d("revenue"), border=True, height=TILE_H,
              help=ctx.tf("{amount} — invoice line totals (qte·prx), converted to the "
                          "selected currency.", amount=money(cur["revenue"], ctx))
                   + (" " + ctx.t("Year to date.") if is_partial else ""))
    c2.metric(ctx.t("Margin over build cost"), tile(cur["margin"], ctx),
              delta=d("margin"), border=True, height=TILE_H,
              help=ctx.tf("{amount} — revenue − material cost (`facture_det.mat`). This is "
                          "not the accounting gross margin: it carries no packaging, paint "
                          "or inbound freight. See Down to net profit, right.",
                          amount=money(cur["margin"], ctx)))
    c3.metric(ctx.t("Margin %"), f"{cur['margin_pct']:.1f}%",
              delta=d("margin_pct", pct=True),
              border=True, height=TILE_H,
              help=ctx.t("Company target is 25% (margeProduitFini)."))
    c4.metric(ctx.t("Units invoiced"), f"{cur['units']:,.0f}",
              delta=d("units"), border=True, height=TILE_H)
    c5.metric(ctx.t("Avg selling price"), money(cur["asp"], ctx, dp=1),
              delta=d("asp"), border=True, height=TILE_H, help=ctx.t("Revenue ÷ units."))
    note = partial_year_note(ctx)
    if note:
        st.caption(note)
    return ya


def revenue_margin_by_year(ya: pd.DataFrame, ctx: Ctx) -> None:
    """Revenue and margin as bars, margin % as a line against the 25% target."""
    P = ctx.P
    st.subheader(ctx.t("Revenue & margin by year"))
    rev_disp = to_disp(ya["revenue"], ctx)
    mrg_disp = to_disp(ya["margin"], ctx)
    fig = go.Figure()
    fig.add_bar(x=ya["yr"], y=rev_disp, name=ctx.t("Revenue"),
                marker=dict(color=P["series"][0], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>revenue " + sym(ctx.ccy) +
                              "%{y:,.0f}<extra></extra>")
    fig.add_bar(x=ya["yr"], y=mrg_disp, name=ctx.t("Gross margin"),
                marker=dict(color=P["series"][2], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>margin " + sym(ctx.ccy) +
                              "%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(
        x=ya["yr"], y=ya["margin_pct"], name=ctx.t("Margin %"), yaxis="y2",
        mode="lines+markers", line=dict(color=P["series"][3], width=2),
        marker=dict(size=7), hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
    style_fig(fig, height=360)
    fig.update_layout(
        barmode="group", showlegend=True, margin=dict(l=4, r=44, t=48, b=4),
        yaxis2=dict(overlaying="y", side="right", title=None,
                    range=[0, max(45, ya["margin_pct"].max() + 8)],
                    showgrid=False, ticksuffix="%",
                    tickfont=dict(color=P["muted"], size=12)))
    fig.update_yaxes(title_text=ctx.ccy, tickformat="~s")
    fig.add_hline(y=25, line_width=1, line_dash="dot", line_color=P["axis"], yref="y2")
    st.plotly_chart(fig, width="stretch", theme=None)


def attention(scope: pd.DataFrame, ctx: Ctx) -> None:
    """The top findings as one-line rows, with the full list a click away."""
    st.subheader(ctx.t("What needs attention"))
    with st.spinner(ctx.t("Checking what needs attention…")):
        found = actions.findings(scope, ctx)
    if not found:
        actions.nothing_found(ctx)
        return

    total = sum(f.value_dt for f in found)
    st.caption(ctx.tf("{n} rules triggered, {amount} at stake in total. The top {shown} are "
                      "below, ranked by money — each rule states its own basis, so read the "
                      "total as an order of magnitude.",
                      n=len(found), amount=money(total, ctx), shown=min(5, len(found))))
    actions.compact_list(found, ctx, n=5)
    _nav.link("actions", ctx, label=ctx.t("Every finding, with the evidence behind it"))


# One line per page saying what it is for — not a list of its tabs. `N_` marks
# them for translation without translating them here, since this runs at import
# time before any Ctx exists.
_WHERE_NEXT = (
    ("commercial", N_("Which models and customers earn, and what the shelf keeps.")),
    ("operations", N_("Plan attainment, unmoved stock, and what components cost to buy.")),
    ("costing", N_("What a bike costs landed, re-quoted at today's prices, and in FX.")),
    ("finance", N_("The P&L down to net profit, cash collected, payroll and commission.")),
)


def where_next(ctx: Ctx) -> None:
    """Four links out, each saying what question the page answers."""
    st.subheader(ctx.t("Where to look next"))
    for col, (key, blurb) in zip(st.columns(4), _WHERE_NEXT):
        with col.container(border=True):
            _nav.link(key, ctx)
            st.caption(ctx.t(blurb))


def render() -> None:
    from ._shell import begin

    got = begin()
    if got is None:
        return
    scope, ctx = got

    ya = scoreboard(scope, ctx)

    st.divider()
    left, right = st.columns([3, 2])
    with left:
        revenue_margin_by_year(ya, ctx)
    with right:
        finance.profit_summary(ctx)

    st.divider()
    bridge.waterfall_section(scope, ctx)

    st.divider()
    attention(scope, ctx)

    st.divider()
    where_next(ctx)
