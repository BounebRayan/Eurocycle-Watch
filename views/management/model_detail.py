"""Model detail dialog — one bike's full profile: photo, identity, economics
over the selected range, year-by-year history, its family across seasons, and
how it did on the shop floor. Opened from the Models tab's model picker."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
from theme import style_fig
from ._common import Ctx, TILE_H, money, tile, to_disp


@st.dialog("Model detail", width="large")
def show(article: str, scope: pd.DataFrame, per_model: pd.DataFrame, ctx: Ctx) -> None:
    if article not in per_model.index:
        st.warning(ctx.t("No sales for this model under the current filters."))
        return
    row = per_model.loc[article]
    rows = scope[scope["article"] == article]
    dkey = rows["design_key"].iloc[0] if "design_key" in rows.columns and not rows.empty else None

    img_col, info_col = st.columns([1, 2])
    with img_col:
        img = erp.load_model_image(article)
        if img:
            st.image(img, width="stretch")
        else:
            st.caption(ctx.t("No photo on file for this code."))
    with info_col:
        st.markdown(f"### {row['model']}")
        st.caption(f"{row['brand']} · {row['distributor']} · `{article}`")
        _, season_yr, wheel = erp.decode_article(article)
        badges = []
        if season_yr:
            badges.append(f"{season_yr} season")
        if wheel:
            badges.append(f'{wheel}" wheel')
        if row.get("ebike"):
            badges.append("e-bike")
        archived = bool(rows["isArchived"].fillna(0).max()) if "isArchived" in rows.columns else False
        if archived:
            badges.append("archived")
        st.write(" · ".join(badges) if badges else "Code doesn't parse to the season/wheel convention.")

    st.divider()
    st.caption(ctx.tf("Economics — {who}, {lo}–{hi}",
                      who=ctx.dist_label, lo=ctx.yr_lo, hi=ctx.yr_hi))
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(ctx.t("Revenue"), tile(row["revenue"], ctx), border=True, height=TILE_H)
    c2.metric(ctx.t("Gross margin"), tile(row["margin"], ctx), border=True, height=TILE_H)
    c3.metric(ctx.t("Margin %"), f"{row['margin_pct']:.1f}%" if pd.notna(row["margin_pct"]) else "n/a",
              border=True, height=TILE_H)
    c4.metric(ctx.t("Units"), f"{row['units']:,.0f}", border=True, height=TILE_H)
    c5.metric(ctx.t("Avg selling price"), money(row["asp"], ctx, dp=1), border=True, height=TILE_H)
    c6, c7, _ = st.columns(3)
    c6.metric(ctx.t("Cost / bike"), money(row["cost_per_unit"], ctx, dp=1), border=True, height=TILE_H,
              help=ctx.t("COGS ÷ units (facture_det.mat)."))
    c7.metric(ctx.t("Margin / bike"), money(row["margin_per_unit"], ctx, dp=1), border=True, height=TILE_H,
              help=ctx.t("Sale price − build cost, per unit. Red elsewhere in this tab means this is negative."))

    _yearly_history(rows, ctx)
    if dkey:
        _design_family(scope, dkey, article, ctx)
    _production(article, ctx)


def _yearly_history(rows: pd.DataFrame, ctx: Ctx) -> None:
    by_yr = rows.groupby("yr").agg(revenue=("line_rev_dt", "sum"), cogs=("line_cogs_dt", "sum"),
                                    units=("qte", "sum"))
    if len(by_yr) < 2:
        return
    by_yr["margin"] = by_yr["revenue"] - by_yr["cogs"]
    st.divider()
    st.subheader(ctx.t("Year by year — this code"))
    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=by_yr.index, y=to_disp(by_yr["revenue"], ctx), name=ctx.t("Revenue"),
                marker=dict(color=P["series"][0], line=dict(width=0)))
    fig.add_bar(x=by_yr.index, y=to_disp(by_yr["margin"], ctx), name=ctx.t("Margin"),
                marker=dict(color=P["series"][2], line=dict(width=0)))
    fig.add_trace(go.Scatter(x=by_yr.index, y=by_yr["units"], name=ctx.t("Units"), yaxis="y2",
                              mode="lines+markers", line=dict(color=P["series"][3], width=2)))
    style_fig(fig, height=280)
    fig.update_layout(barmode="group", showlegend=True, margin=dict(l=4, r=44, t=28, b=4),
                       yaxis2=dict(overlaying="y", side="right", title="Units", showgrid=False))
    fig.update_xaxes(type="category")
    st.plotly_chart(fig, width="stretch", theme=None)


def _design_family(scope: pd.DataFrame, dkey: str, article: str, ctx: Ctx) -> None:
    fam = scope[scope["design_key"] == dkey]
    if fam["article"].nunique() <= 1:
        return
    st.divider()
    st.subheader(ctx.t("Same bike, other seasons"))
    st.caption(ctx.tf("Every article code sharing design key `{key}` — this bike's own "
                      "year-over-year recoding (see docs/eurocycles-erp-findings.md §4).",
                      key=dkey))
    g = fam.groupby("article").agg(
        model=("model_label", "first"), first_yr=("yr", "min"), last_yr=("yr", "max"),
        units=("qte", "sum"), revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"),
    )
    g["margin_pct"] = np.where(g["revenue"] > 0, g["margin"] / g["revenue"] * 100, np.nan)
    g["rev_disp"] = to_disp(g["revenue"], ctx)
    g["mrg_disp"] = to_disp(g["margin"], ctx)
    g = g.sort_values("first_yr").reset_index()
    st.dataframe(
        g[["article", "model", "first_yr", "last_yr", "units", "rev_disp", "mrg_disp", "margin_pct"]],
        hide_index=True, width="stretch",
        column_config={
            "article": "Code", "model": "Model",
            "first_yr": st.column_config.NumberColumn("First yr", format="%d"),
            "last_yr": st.column_config.NumberColumn("Last yr", format="%d"),
            "units": st.column_config.NumberColumn("Units", format="%d"),
            "rev_disp": st.column_config.NumberColumn(f"Revenue ({ctx.ccy})", format="%.0f"),
            "mrg_disp": st.column_config.NumberColumn(f"Margin ({ctx.ccy})", format="%.0f"),
            "margin_pct": st.column_config.NumberColumn("Margin %", format="%.1f%%"),
        },
    )
    st.caption("This code is highlighted in the KPI tiles above; the rest of the range's sales "
               f"for `{article}` only — other rows here are its earlier or later seasons.")


def _production(article: str, ctx: Ctx) -> None:
    orders = erp.load_production_orders(start_year=min(ctx.yr_lo, 2022))
    mine = orders[orders["article"] == article]
    if mine.empty:
        return
    st.divider()
    st.subheader(ctx.t("Production"))
    planned = mine["planned_qty"].sum()
    declared = mine["declared_qty"].sum()
    attainment = declared / planned * 100 if planned else np.nan
    c1, c2, c3 = st.columns(3)
    c1.metric(ctx.t("Planned (all OFs)"), f"{planned:,.0f}", border=True, height=TILE_H)
    c2.metric(ctx.t("Declared"), f"{declared:,.0f}", border=True, height=TILE_H)
    c3.metric(ctx.t("Attainment"), f"{attainment:.0f}%" if pd.notna(attainment) else "n/a",
              border=True, height=TILE_H)
    st.caption(ctx.tf("{n} production order line(s) for this exact code, {closed} closed.",
                      n=f"{len(mine):,}", closed=int(mine["closed"].sum())))
