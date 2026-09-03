"""Production tab — plan attainment (declared ÷ planned), unit throughput from
serial-label prints, build lead time, and the ERP's own produced-vs-invoiced
daily scoreboard."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, db_notice, UNFILTERED_NOTE

# Brands below this much planned volume make the attainment chart noisy — a
# 40-unit brand at 0% says nothing about the line.
MIN_BRAND_UNITS = 2000


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    # `scope` is deliberately unused: every figure here comes from production
    # tables that carry no customer or bike/part dimension, so the sidebar's
    # distributor and bikes-only filters cannot be honoured. Said plainly below
    # rather than left for the reader to infer.
    st.info(UNFILTERED_NOTE.format(what="Production"), icon=":material/filter_alt_off:")
    _attainment(ctx)
    st.divider()
    if erp.db_present(erp.LABEL_DB):
        _throughput(ctx)
        st.divider()
        _print_span(ctx)
        st.divider()
    else:
        db_notice(erp.LABEL_DB, "Unit throughput and line flow")
        st.divider()
    _produced_vs_invoiced(ctx)


# ------------------------------------------------------ plan attainment ---
def _attainment(ctx: Ctx) -> None:
    st.subheader("Plan attainment")
    po = erp.load_production_orders(start_year=min(ctx.yr_lo, 2022))
    po = po[(po["yr"] >= ctx.yr_lo) & (po["yr"] <= ctx.yr_hi)]
    if po.empty:
        st.caption("No production orders in range.")
        return
    P = ctx.P
    tot_plan = po["planned_qty"].sum()
    tot_decl = po["declared_qty"].sum()
    closed = po[po["closed"] == 1]
    c1, c2, c3 = st.columns(3)
    c1.metric("Planned units", f"{tot_plan:,.0f}", border=True)
    c2.metric("Declared units", f"{tot_decl:,.0f}",
              delta=f"{(tot_decl - tot_plan) / tot_plan * 100:+.0f}% vs plan" if tot_plan else None,
              border=True)
    c3.metric("Attainment (closed OFs)",
              f"{closed['declared_qty'].sum() / closed['planned_qty'].sum() * 100:.0f}%"
              if not closed.empty and closed["planned_qty"].sum() else "n/a",
              border=True, help=f"{len(closed):,} of {len(po):,} orders in range are closed "
                                "(`declarationprd.fermee = 'Oui'`).")

    wk = (po.assign(period=lambda x: x["yr"].astype(str) + "-W" + x["week"].astype(int).astype(str).str.zfill(2))
          .groupby("period").agg(planned=("planned_qty", "sum"), declared=("declared_qty", "sum"))
          .reset_index().sort_values("period"))
    wk["attain"] = np.where(wk["planned"] > 0, wk["declared"] / wk["planned"] * 100, np.nan)
    fig = go.Figure()
    fig.add_bar(x=wk["period"], y=wk["planned"], name="Planned",
                marker=dict(color=P["series"][3], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>plan %{y:,.0f}<extra></extra>")
    fig.add_bar(x=wk["period"], y=wk["declared"], name="Declared",
                marker=dict(color=P["series"][1], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>declared %{y:,.0f}<extra></extra>")
    style_fig(fig, height=300)
    fig.update_layout(barmode="group", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    fig.update_xaxes(showticklabels=len(wk) <= 60)
    st.plotly_chart(fig, width="stretch", theme=None)

    st.markdown("**Attainment by brand**")
    bd = (po.groupby("brandnach").agg(planned=("planned_qty", "sum"), declared=("declared_qty", "sum"))
          .query(f"planned >= {MIN_BRAND_UNITS}").reset_index())
    bd["attain"] = bd["declared"] / bd["planned"] * 100
    bd = bd.nlargest(18, "planned").sort_values("attain")
    fig2 = go.Figure()
    fig2.add_bar(x=bd["attain"], y=hbar_categories(fig2, bd["brandnach"].str.slice(0, 22)),
                 orientation="h",
                 marker=dict(color=[P["neg"] if v < 85 else P["pos"] for v in bd["attain"]],
                             line=dict(width=0)),
                 text=[f"{v:.0f}%" for v in bd["attain"]], textposition="outside",
                 textfont=dict(color=P["text_secondary"], size=11, family=FONT),
                 customdata=bd[["planned", "declared"]].to_numpy(),
                 hovertemplate="<b>%{y}</b><br>plan %{customdata[0]:,.0f} → "
                               "declared %{customdata[1]:,.0f}<extra></extra>",
                 cliponaxis=False)
    fig2.add_vline(x=100, line_width=1, line_dash="dot", line_color=P["axis"])
    style_fig(fig2, height=max(240, 24 * len(bd) + 50), xgrid=True, ygrid=False)
    fig2.update_xaxes(title_text="Declared ÷ planned (%)")
    fig2.update_layout(margin=dict(l=4, r=48, t=4, b=4), bargap=0.35)
    st.plotly_chart(fig2, width="stretch", theme=None)
    st.caption(f"`ordprevision` planned qty vs summed `declarationprd` declarations per OF. "
               f"The {len(bd)} brands with the most planned volume, of those above "
               f"{MIN_BRAND_UNITS:,} units. Company-wide — the distributor filter does not apply.")


# ---------------------------------------------------------- throughput ---
def _throughput(ctx: Ctx) -> None:
    st.subheader("Unit throughput — frame labels printed")
    lt = erp.load_label_throughput(start_year=min(ctx.yr_lo, 2022))
    lt = lt[(lt["first_print"].dt.year >= ctx.yr_lo) & (lt["first_print"].dt.year <= ctx.yr_hi)]
    if lt.empty:
        st.caption("No label prints in range.")
        return
    P = ctx.P
    # explode to monthly by spreading printed_units across the OF's print window is
    # overkill; attribute each OF's units to its first-print month.
    lt = lt.assign(month=lt["first_print"].dt.to_period("M").dt.to_timestamp())
    top_brands = lt.groupby("brand")["printed_units"].sum().nlargest(6).index.tolist()
    lt["brand_g"] = np.where(lt["brand"].isin(top_brands), lt["brand"], "Other")
    m = lt.groupby(["month", "brand_g"])["printed_units"].sum().reset_index()
    fig = go.Figure()
    order = top_brands + (["Other"] if (lt["brand_g"] == "Other").any() else [])
    for i, b in enumerate(order):
        mb = m[m["brand_g"] == b]
        fig.add_bar(x=mb["month"], y=mb["printed_units"], name=b or "(blank)",
                    marker=dict(color=P["series"][i % len(P["series"])], line=dict(width=0)),
                    hovertemplate="<b>%{x|%b %Y}</b><br>" + f"{b}: " + "%{y:,.0f}<extra></extra>")
    style_fig(fig, height=340)
    fig.update_layout(barmode="stack", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption(f"{lt['printed_units'].sum():,.0f} frame labels printed {ctx.yr_lo}–{ctx.yr_hi} "
               f"across {len(lt):,} production orders (`eurocycles_label.OFTraceLine`). "
               "Each OF attributed to its first-print month.")


# ------------------------------------------------------ line-flow time ---
def _print_span(ctx: Ctx) -> None:
    st.subheader("Line-flow time — first to last frame label per OF")
    lt = erp.load_label_throughput(start_year=min(ctx.yr_lo, 2022))
    lt = lt[(lt["last_print"].dt.year >= ctx.yr_lo) & (lt["last_print"].dt.year <= ctx.yr_hi)]
    lt = lt.assign(span_days=(lt["last_print"] - lt["first_print"]).dt.total_seconds() / 86400)
    lt = lt[(lt["span_days"] >= 0) & (lt["span_days"] <= 60) & (lt["printed_units"] >= 20)]
    if lt.empty:
        st.caption("Not enough OFs with a clean first→last-print window in range.")
        return
    P = ctx.P
    same_day = (lt["span_days"] < 1).mean() * 100
    c1, c2 = st.columns([2, 3])
    with c1:
        st.metric("Framed same day", f"{same_day:.0f}%", border=True,
                  help="Share of OFs (≥ 20 units) whose frame labels are all printed within one "
                       "day of the first — i.e. the batch clears framing in a single run.")
        st.metric("90th percentile", f"{lt['span_days'].quantile(0.9):.0f} days", border=True,
                  help="1 in 10 OFs take longer than this from first to last frame label.")
    with c2:
        fig = go.Figure(go.Histogram(x=lt["span_days"], nbinsx=40,
                                     marker=dict(color=P["series"][0], line=dict(width=0)),
                                     hovertemplate="%{x:.0f} days<br>%{y} OFs<extra></extra>"))
        style_fig(fig, height=240)
        fig.update_xaxes(title_text="Days (first → last print)")
        fig.update_yaxes(title_text="OFs")
        st.plotly_chart(fig, width="stretch", theme=None)

    mth = (lt.assign(month=lt["last_print"].dt.to_period("M").dt.to_timestamp())
           .groupby("month")["span_days"].median().reset_index())
    fig2 = go.Figure(go.Scatter(x=mth["month"], y=mth["span_days"], mode="lines+markers",
                                line=dict(color=P["series"][0], width=2), marker=dict(size=6),
                                hovertemplate="<b>%{x|%b %Y}</b><br>median %{y:.1f} days<extra></extra>"))
    style_fig(fig2, height=240)
    fig2.update_yaxes(title_text="Median flow days")
    st.plotly_chart(fig2, width="stretch", theme=None)
    st.caption("Frame-label prints only (`OFTraceLine.PrintDate`). Excludes OFs over 60 days or "
               "under 20 units. Not the full order lead time — the trace starts at first print, "
               "not order creation.")


# ------------------------------------------------ produced vs invoiced ---
def _produced_vs_invoiced(ctx: Ctx) -> None:
    st.subheader("Produced vs invoiced (ERP daily scoreboard)")
    try:
        ds = erp.load_daily_summary()
    except Exception as e:
        st.caption(f"DailySummary unavailable: {str(e).splitlines()[0]}")
        return
    ds = ds[(ds["DocumentDate"].dt.year >= ctx.yr_lo) & (ds["DocumentDate"].dt.year <= ctx.yr_hi)]
    m = ds.set_index("DocumentDate")[["CatId8", "CatId9"]].resample("MS").last().dropna(how="all")
    if m.empty:
        st.caption("No DailySummary rows in range.")
        return
    m = m.diff().clip(lower=0)
    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=m.index, y=m["CatId8"], name="Bikes produced",
                marker=dict(color=P["series"][1], line=dict(width=0)))
    fig.add_bar(x=m.index, y=m["CatId9"], name="Bikes invoiced",
                marker=dict(color=P["series"][0], line=dict(width=0)))
    style_fig(fig, height=280)
    fig.update_layout(barmode="group", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption("Derived from `DailySummary` cumulative counters (CatId8 produced, CatId9 "
               "invoiced) differenced to monthly. Company-wide — not filtered by distributor.")
