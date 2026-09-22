"""Customers tab — the distributor scorecard (revenue, margin, units, YoY per
customer), a drill-down for one customer, and plan-vs-actual shipment volume
from the customer demand plan (`planningprev`)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, sym, to_disp, compact, like_for_like, partial_year_note

HALFORDS = "HALFORDS"


# ------------------------------------------------------- by distributor ---
def _revenue_by_distributor(scope: pd.DataFrame, ctx: Ctx) -> None:
    """Revenue per distributor, cumulative over the sidebar's year range.

    Came over from the old Overview tab with the page split: it is a cut of
    the same customer dimension the scorecard below works in, and a reader
    asking "who buys from us" wants both at once."""
    P = ctx.P
    st.subheader(ctx.t("Revenue by distributor"))
    st.caption(ctx.tf("{lo}–{hi} cumulative. Top {n}.", lo=ctx.yr_lo, hi=ctx.yr_hi, n=12))
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


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    _revenue_by_distributor(scope, ctx)
    st.divider()
    st.subheader(ctx.t("Customer scorecard") + f" — {ctx.yr_lo}–{ctx.yr_hi}")

    live = scope[scope["distributor"] != "(unmapped)"]
    yrs = sorted(live["yr"].unique())
    board = _scorecard(live, yrs, ctx)
    if board.empty:
        st.info(ctx.t("No customer rows in range."))
        return
    yoy_label = "YoY"
    if len(yrs) >= 2:
        y_cur, y_prev = int(yrs[-1]), int(yrs[-2])
        yoy_label = (f"{y_cur} vs {y_prev}" if ctx.partial_year != y_cur
                     else f"{y_cur} vs {y_prev} YTD")

    board_disp = board.copy()
    for c in ("revenue", "margin"):
        board_disp[c] = to_disp(board_disp[c], ctx)
    st.dataframe(
        board_disp, hide_index=True, width="stretch",
        column_config={
            "distributor": ctx.t("Customer"),
            "revenue": st.column_config.NumberColumn(f"Revenue ({ctx.ccy})", format="compact"),
            "margin": st.column_config.NumberColumn(f"Margin ({ctx.ccy})", format="compact"),
            "margin_pct": st.column_config.NumberColumn("Margin %", format="%.1f%%"),
            "units": st.column_config.NumberColumn("Units", format="localized"),
            "models": st.column_config.NumberColumn("Models", format="%d"),
            "rev_yoy": st.column_config.NumberColumn(f"Revenue {yoy_label}", format="%+.0f%%"),
            "mgn_yoy": st.column_config.NumberColumn(f"Margin {yoy_label}", format="%+.0f%%"),
        },
    )
    caption = ("One row per customer (`nomachat.cusnach`). Revenue, margin and units are totals "
               f"for {ctx.yr_lo}–{ctx.yr_hi}; the last two columns compare the two most recent "
               "years. Sorted by revenue.")
    note = partial_year_note(ctx)
    st.caption(caption + (f" {note}" if note else ""))

    _by_country(scope, ctx)

    st.divider()
    _drilldown(live, ctx, board["distributor"].tolist())


# ------------------------------------------------------------- by country ---
def _by_country(scope: pd.DataFrame, ctx: Ctx) -> None:
    """Revenue per destination country — the ERP report's §06 cut.

    Country is the *invoiced client's* country (`client.pays` via
    `facture.clif`), not the model's owning customer, so it answers "where did
    the bikes ship" rather than "who designed them". The two differ whenever a
    distributor invoices through an entity in another country."""
    st.divider()
    st.subheader(ctx.t("Revenue by country"))
    known = scope[scope["country"] != "(unknown)"]
    if known.empty:
        st.caption(ctx.t("No country on the invoiced clients in range."))
        return
    st.caption(ctx.tf("{lo}–{hi} cumulative, by the invoiced client's country "
                      "(`client.pays`). Label is the share of period revenue.",
                      lo=ctx.yr_lo, hi=ctx.yr_hi))
    g = (known.groupby("country")
         .agg(revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"),
              units=("qte", "sum"))
         .sort_values("revenue").tail(14))
    g["rev_disp"] = to_disp(g["revenue"], ctx)
    g["share"] = g["revenue"] / known["line_rev_dt"].sum() * 100
    g["mpct"] = np.where(g["revenue"] > 0, g["margin"] / g["revenue"] * 100, np.nan)

    P = ctx.P
    fig = go.Figure()
    fig.add_bar(
        x=g["rev_disp"], y=hbar_categories(fig, g.index), orientation="h",
        marker=dict(color=P["series"][0], line=dict(width=0)),
        text=[f"{compact(v, ctx.ccy)}   {s:.0f}%" for v, s in zip(g["rev_disp"], g["share"])],
        textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        customdata=g[["units", "mpct"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>" + sym(ctx.ccy) + "%{x:,.0f}<br>"
                      "%{customdata[0]:,.0f} units · %{customdata[1]:.1f}% margin<extra></extra>",
        cliponaxis=False)
    style_fig(fig, height=max(260, 27 * len(g) + 50), xgrid=True, ygrid=False)
    fig.update_xaxes(tickformat="~s")
    fig.update_layout(margin=dict(l=4, r=96, t=4, b=4), bargap=0.35)
    st.plotly_chart(fig, width="stretch", theme=None)


def _scorecard(live: pd.DataFrame, yrs: list, ctx: Ctx) -> pd.DataFrame:
    """Totals over the whole selected period, plus a like-for-like YoY.

    The totals use every row in range. The YoY columns run off
    `like_for_like()`, so when the newest year is partial both sides of the
    comparison are cut to the same elapsed months — otherwise eight months of
    2026 lands against a full 2025 and every customer looks like a collapse."""
    g = live.groupby("distributor").agg(
        revenue=("line_rev_dt", "sum"),
        margin=("line_margin_dt", "sum"),
        units=("qte", "sum"),
        models=("article", "nunique"),
    )
    g["margin_pct"] = np.where(g["revenue"] > 0, g["margin"] / g["revenue"] * 100, np.nan)
    # `qte` is a float; round before display or a stray .0000001 makes the
    # "localized" column format spill fractional digits into the thousands groups.
    g["units"] = g["units"].round(0)
    g["rev_yoy"] = np.nan
    g["mgn_yoy"] = np.nan
    if len(yrs) >= 2:
        y_cur, y_prev = yrs[-1], yrs[-2]
        cmp_rows = like_for_like(live[live["yr"].isin([y_prev, y_cur])], ctx)
        by_yr = cmp_rows.groupby(["distributor", "yr"]).agg(
            revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"))
        rev = by_yr["revenue"].unstack("yr")
        mgn = by_yr["margin"].unstack("yr")
        if y_cur in rev.columns and y_prev in rev.columns:
            def pct_change(wide):
                out = (wide[y_cur] - wide[y_prev]) / wide[y_prev].abs() * 100
                return out.replace([np.inf, -np.inf], np.nan)
            g["rev_yoy"] = pct_change(rev)
            g["mgn_yoy"] = pct_change(mgn)
    return g.reset_index().sort_values("revenue", ascending=False)[
        ["distributor", "revenue", "margin", "margin_pct", "units", "models", "rev_yoy", "mgn_yoy"]]


def _drilldown(live: pd.DataFrame, ctx: Ctx, names: list) -> None:
    P = ctx.P
    default = HALFORDS if HALFORDS in names else names[0]
    who = st.selectbox(ctx.t("Customer detail"), names, index=names.index(default))
    d = live[live["distributor"] == who]
    if d.empty:
        st.caption(ctx.t("No rows."))
        return

    ya = d.groupby("yr").agg(revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"),
                             units=("qte", "sum"))
    ya["margin_pct"] = np.where(ya["revenue"] > 0, ya["margin"] / ya["revenue"] * 100, np.nan)

    left, right = st.columns([3, 2])
    with left:
        st.markdown("**" + ctx.tf("{who} — revenue & margin by year", who=who) + "**")
        fig = go.Figure()
        fig.add_bar(x=ya.index, y=to_disp(ya["revenue"], ctx), name=ctx.t("Revenue"),
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
        fig.add_bar(x=ya.index, y=to_disp(ya["margin"], ctx), name=ctx.t("Gross margin"),
                    marker=dict(color=P["series"][2], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
        fig.add_trace(go.Scatter(x=ya.index, y=ya["margin_pct"], name=ctx.t("Margin %"), yaxis="y2",
                                 mode="lines+markers", line=dict(color=P["series"][3], width=2),
                                 marker=dict(size=7),
                                 hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
        style_fig(fig, height=340)
        fig.update_layout(barmode="group", showlegend=True, margin=dict(l=4, r=44, t=44, b=4),
                          yaxis2=dict(overlaying="y", side="right", showgrid=False, ticksuffix="%",
                                      range=[0, max(45, np.nanmax(ya["margin_pct"]) + 8)],
                                      tickfont=dict(color=P["muted"], size=12)))
        fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
        fig.add_hline(y=25, line_width=1, line_dash="dot", line_color=P["axis"], yref="y2")
        st.plotly_chart(fig, width="stretch", theme=None)

    with right:
        st.markdown(ctx.t("**Top models**"))
        tm = (d.groupby("article").agg(model=("model_label", "first"),
                                       revenue=("line_rev_dt", "sum"),
                                       margin=("line_margin_dt", "sum"))
              .sort_values("revenue").tail(10))
        tm["rev_disp"] = to_disp(tm["revenue"], ctx)
        tm["mpct"] = np.where(tm["revenue"] > 0, tm["margin"] / tm["revenue"] * 100, 0)
        fig2 = go.Figure()
        fig2.add_bar(x=tm["rev_disp"], y=hbar_categories(fig2, tm["model"].str.slice(0, 34)),
                     orientation="h", marker=dict(color=P["series"][0], line=dict(width=0)),
                     text=[compact(v, ctx.ccy) for v in tm["rev_disp"]], textposition="outside",
                     textfont=dict(color=P["text_secondary"], size=11, family=FONT),
                     customdata=tm[["mpct"]].to_numpy(),
                     hovertemplate="<b>%{y}</b><br>" + sym(ctx.ccy) +
                                   "%{x:,.0f} · %{customdata[0]:.1f}%<extra></extra>",
                     cliponaxis=False)
        style_fig(fig2, height=340, xgrid=True, ygrid=False)
        fig2.update_xaxes(tickformat="~s")
        fig2.update_layout(margin=dict(l=4, r=54, t=4, b=4), bargap=0.3)
        st.plotly_chart(fig2, width="stretch", theme=None)

    _plan_vs_actual(who, ctx)


def _plan_vs_actual(who: str, ctx: Ctx) -> None:
    st.markdown(ctx.t("**Shipment plan vs invoiced (units)**"))
    pva = erp.load_plan_vs_actual(start_year=min(ctx.yr_lo, 2022))
    d = pva[(pva["distributor"] == who)
            & (pva["month"].dt.year >= ctx.yr_lo) & (pva["month"].dt.year <= ctx.yr_hi)]
    if d.empty or d["planned_qty"].sum() == 0:
        st.caption(ctx.tf("No `planningprev` demand plan on file for {who} in range.", who=who))
        return
    m = d.groupby("month").agg(planned=("planned_qty", "sum"), actual=("actual_qty", "sum")).reset_index()
    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=m["month"], y=m["planned"], name=ctx.t("Planned"),
                marker=dict(color=P["series"][3], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>plan %{y:,.0f}<extra></extra>")
    fig.add_bar(x=m["month"], y=m["actual"], name=ctx.t("Invoiced"),
                marker=dict(color=P["series"][0], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>actual %{y:,.0f}<extra></extra>")
    style_fig(fig, height=300)
    fig.update_layout(barmode="group", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    st.plotly_chart(fig, width="stretch", theme=None)
    tot_plan, tot_act = m["planned"].sum(), m["actual"].sum()
    att = tot_act / tot_plan * 100 if tot_plan else np.nan
    st.caption(ctx.tf("Plan {plan} · invoiced {act} · attainment **{att}%** "
                      "({lo}–{hi}). Plan = `planningprev_det` by planned week; "
                      "invoiced = `facture_det` by invoice month. Monthly buckets, "
                      "not lead-time aligned.",
                      plan=f"{tot_plan:,.0f}", act=f"{tot_act:,.0f}", att=f"{att:.0f}",
                      lo=ctx.yr_lo, hi=ctx.yr_hi))
