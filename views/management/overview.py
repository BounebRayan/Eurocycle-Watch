"""Overview tab — the company scoreboard: headline KPIs with YoY, revenue &
margin by year, and revenue by distributor. Produced-vs-invoiced moved to the
Production tab."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import FONT, style_fig, hbar_categories
from ._common import (Ctx, TILE_H, sym, money, tile, compact, to_disp, yoy,
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


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    P = ctx.P
    st.subheader(f"Company scoreboard — {ctx.dist_label}")
    ya = _year_agg(scope)                                   # full period: tile values + chart
    lfl = _year_agg(like_for_like(scope, ctx)).set_index("yr")   # trimmed: deltas only
    cur = ya.iloc[-1]
    y_cur = int(cur["yr"])
    y_prev = int(ya.iloc[-2]["yr"]) if len(ya) > 1 else None

    def d(col, *, pct=False):
        """YoY on the like-for-like frame, so a part year isn't read against a full one."""
        if y_prev is None or y_cur not in lfl.index or y_prev not in lfl.index:
            return None
        return yoy(lfl.at[y_cur, col], lfl.at[y_prev, col], pct=pct)

    is_partial = ctx.partial_year == y_cur

    c1, c2, c3, c4, c5 = st.columns(5)
    ytd = " (YTD)" if is_partial else ""
    c1.metric(f"Revenue {y_cur}{ytd}", tile(cur["revenue"], ctx),
              delta=d("revenue"), border=True, height=TILE_H,
              help=f"{money(cur['revenue'], ctx)} — invoice line totals (qte·prx), "
                   "converted to the selected currency." + (" Year to date." if is_partial else ""))
    c2.metric("Gross margin", tile(cur["margin"], ctx),
              delta=d("margin"), border=True, height=TILE_H,
              help=f"{money(cur['margin'], ctx)} — revenue − COGS (facture_det.mat).")
    c3.metric("Margin %", f"{cur['margin_pct']:.1f}%",
              delta=d("margin_pct", pct=True),
              border=True, height=TILE_H, help="Company target is 25% (margeProduitFini).")
    c4.metric("Units invoiced", f"{cur['units']:,.0f}",
              delta=d("units"), border=True, height=TILE_H)
    c5.metric("Avg selling price", money(cur["asp"], ctx, dp=1),
              delta=d("asp"), border=True, height=TILE_H, help="Revenue ÷ units.")
    note = partial_year_note(ctx)
    if note:
        st.caption(note)

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Revenue & margin by year")
        rev_disp = to_disp(ya["revenue"], ctx)
        mrg_disp = to_disp(ya["margin"], ctx)
        fig = go.Figure()
        fig.add_bar(x=ya["yr"], y=rev_disp, name="Revenue",
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>revenue " + sym(ctx.ccy) +
                                  "%{y:,.0f}<extra></extra>")
        fig.add_bar(x=ya["yr"], y=mrg_disp, name="Gross margin",
                    marker=dict(color=P["series"][2], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>margin " + sym(ctx.ccy) +
                                  "%{y:,.0f}<extra></extra>")
        fig.add_trace(go.Scatter(
            x=ya["yr"], y=ya["margin_pct"], name="Margin %", yaxis="y2",
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

    with right:
        st.subheader("Revenue by distributor")
        st.caption(f"{ctx.yr_lo}–{ctx.yr_hi} cumulative. Top 12.")
        bd = (scope.groupby("distributor")
              .agg(revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"))
              .sort_values("revenue").tail(12))
        bd["rev_disp"] = to_disp(bd["revenue"], ctx)
        bd["mpct"] = np.where(bd["revenue"] > 0, bd["margin"] / bd["revenue"] * 100, 0)
        fig2 = go.Figure()
        fig2.add_bar(
            x=bd["rev_disp"], y=hbar_categories(fig2, bd.index), orientation="h",
            marker=dict(color=P["series"][0], line=dict(width=0)),
            text=[compact(v, ctx.ccy) for v in bd["rev_disp"]], textposition="outside",
            textfont=dict(color=P["text_secondary"], size=11, family=FONT),
            customdata=bd[["mpct"]].to_numpy(),
            hovertemplate="<b>%{y}</b><br>" + sym(ctx.ccy) +
                          "%{x:,.0f}<br>margin %{customdata[0]:.1f}%<extra></extra>",
            cliponaxis=False)
        style_fig(fig2, height=360, xgrid=True, ygrid=False)
        fig2.update_xaxes(tickformat="~s")
        fig2.update_layout(margin=dict(l=4, r=54, t=4, b=4), bargap=0.35)
        st.plotly_chart(fig2, width="stretch", theme=None)
