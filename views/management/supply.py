"""Supply & cost tab — component price inflation, biggest part price moves,
supplier spend and freight mix, supplier quality claims, and the inbound
purchase-order pipeline."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, db_notice, UNFILTERED_NOTE


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    # `scope` is deliberately unused — see the note below.
    st.info(UNFILTERED_NOTE.format(what="Supply & cost"), icon=":material/filter_alt_off:")
    if erp.db_present(erp.CALC_DB):
        _component_inflation(ctx)
        st.divider()
        _biggest_moves(ctx)
        st.divider()
    else:
        db_notice(erp.CALC_DB, "Component price inflation")
        st.divider()
    _supplier_spend(ctx)
    st.divider()
    _supplier_claims(ctx)
    st.divider()
    _pipeline(ctx)


# ---------------------------------------------------- component inflation ---
def _component_inflation(ctx: Ctx) -> None:
    st.subheader("Component price inflation")
    ch = erp.load_component_price_changes(start_year=min(ctx.yr_lo, 2019))
    ch = ch[(ch["changed"].dt.year >= ctx.yr_lo) & (ch["changed"].dt.year <= ctx.yr_hi)]
    ch = ch[ch["pct_change"].between(-95, 500)]  # drop data-entry outliers
    if ch.empty:
        st.caption("No component price changes in range.")
        return
    P = ctx.P

    last12 = ch[ch["changed"] >= ch["changed"].max() - pd.Timedelta(days=365)]
    ups = last12[last12["direction"] == "up"]
    downs = last12[last12["direction"] == "down"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Parts repriced up (last 12m)", f"{len(ups):,}", border=True,
              help=f"Average increase +{ups['pct_change'].mean():.0f}%." if len(ups) else None)
    c2.metric("Parts repriced down (last 12m)", f"{len(downs):,}", border=True,
              help=f"Average decrease {downs['pct_change'].mean():.0f}%." if len(downs) else None)
    net = last12["pct_change"].mean()
    c3.metric("Net avg change (last 12m)", f"{net:+.1f}%", border=True,
              help="Mean % change across all recorded part reprices in the last 12 months. "
                   "Unweighted by usage — a direction signal, not a COGS delta.")

    m = (ch.assign(month=ch["changed"].dt.to_period("M").dt.to_timestamp())
         .groupby(["month", "direction"], observed=True).size().unstack("direction", fill_value=0)
         .reset_index())
    for col in ("up", "down", "flat"):
        if col not in m:
            m[col] = 0
    fig = go.Figure()
    fig.add_bar(x=m["month"], y=m["up"], name="Increases",
                marker=dict(color=P["neg"], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>%{y} increases<extra></extra>")
    fig.add_bar(x=m["month"], y=-m["down"], name="Decreases",
                marker=dict(color=P["pos"], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>%{customdata} decreases<extra></extra>",
                customdata=m["down"])
    fig.add_hline(y=0, line_width=1, line_color=P["axis"])
    style_fig(fig, height=300)
    fig.update_layout(barmode="relative", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    fig.update_yaxes(title_text="Parts repriced")
    st.plotly_chart(fig, width="stretch", theme=None)

    st.markdown("**By buying currency**")
    bycc = (ch.groupby("ccy").agg(changes=("pct_change", "size"),
                                  avg_pct=("pct_change", "mean")).reset_index()
            .sort_values("changes", ascending=False))
    bycc = bycc[bycc["changes"] >= 20]
    fig2 = go.Figure(go.Bar(
        x=bycc["ccy"], y=bycc["avg_pct"],
        marker=dict(color=[P["neg"] if v > 0 else P["pos"] for v in bycc["avg_pct"]], line=dict(width=0)),
        text=[f"{v:+.1f}%" for v in bycc["avg_pct"]], textposition="outside",
        textfont=dict(color=P["text_secondary"], size=12, family=FONT),
        customdata=bycc[["changes"]].to_numpy(),
        hovertemplate="<b>%{x}</b><br>avg %{y:+.1f}% · %{customdata[0]} changes<extra></extra>",
        cliponaxis=False))
    style_fig(fig2, height=240)
    fig2.update_yaxes(title_text="Mean % change")
    st.plotly_chart(fig2, width="stretch", theme=None)
    st.caption(f"{len(ch):,} recorded part reprices {ctx.yr_lo}–{ctx.yr_hi} "
               "(`eurocycles_db_calc.hisprix`). Currencies with ≥ 20 changes.")


def _biggest_moves(ctx: Ctx) -> None:
    st.subheader("Biggest recent part price moves")
    ch = erp.load_component_price_changes(start_year=min(ctx.yr_lo, 2019))
    ch = ch[ch["pct_change"].between(-95, 500)]
    ch = ch[ch["changed"] >= ch["changed"].max() - pd.Timedelta(days=365)]
    if ch.empty:
        st.caption("No changes in the last 12 months.")
        return
    big = pd.concat([ch.nlargest(12, "pct_change"), ch.nsmallest(6, "pct_change")]).drop_duplicates("code")
    big = big.sort_values("pct_change")
    P = ctx.P
    fig = go.Figure()
    labels = big["part_label"].fillna(big["part_code"]).str.slice(0, 46)
    fig.add_bar(x=big["pct_change"], y=hbar_categories(fig, labels), orientation="h",
                marker=dict(color=[P["neg"] if v > 0 else P["pos"] for v in big["pct_change"]],
                            line=dict(width=0)),
                text=[f"{v:+.0f}%" for v in big["pct_change"]], textposition="outside",
                textfont=dict(color=P["text_secondary"], size=11, family=FONT),
                customdata=big[["old_price", "new_price", "ccy", "changed"]].to_numpy(),
                hovertemplate="<b>%{y}</b><br>%{customdata[2]} %{customdata[0]:.3f} → "
                              "%{customdata[1]:.3f}<br>%{customdata[3]|%d %b %Y}<extra></extra>",
                cliponaxis=False)
    fig.add_vline(x=0, line_width=1, line_color=P["axis"])
    style_fig(fig, height=max(280, 24 * len(big) + 50), xgrid=True, ygrid=False)
    fig.update_xaxes(title_text="Price change (%)")
    fig.update_layout(margin=dict(l=4, r=54, t=4, b=4), bargap=0.3)
    st.plotly_chart(fig, width="stretch", theme=None)


# ------------------------------------------------------- supplier spend ---
def _supplier_spend(ctx: Ctx) -> None:
    st.subheader("Supplier spend (landed component cost)")
    si = erp.load_supplier_invoices(start_year=min(ctx.yr_lo, 2022))
    si = si[(si["yr"] >= ctx.yr_lo) & (si["yr"] <= ctx.yr_hi)]
    if si.empty:
        st.caption("No supplier invoices in range.")
        return
    P = ctx.P
    by_yr = si.groupby("yr").agg(goods=("goods_dt", "sum"), freight=("freight_dt", "sum")).reset_index()
    left, right = st.columns(2)
    with left:
        st.markdown("**Spend by year**")
        fig = go.Figure()
        fig.add_bar(x=by_yr["yr"], y=by_yr["goods"] / ctx.rate, name="Goods",
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>goods %{y:,.0f}<extra></extra>")
        fig.add_bar(x=by_yr["yr"], y=by_yr["freight"] / ctx.rate, name="Freight / customs / insurance",
                    marker=dict(color=P["series"][1], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>freight %{y:,.0f}<extra></extra>")
        style_fig(fig, height=300)
        fig.update_layout(barmode="stack", showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
        fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
        st.plotly_chart(fig, width="stretch", theme=None)
    with right:
        st.markdown("**Top suppliers**")
        ts = (si.groupby("supplier")["goods_dt"].sum().sort_values().tail(15) / ctx.rate)
        fig2 = go.Figure()
        fig2.add_bar(
            x=ts.values, y=hbar_categories(fig2, ts.index.str.slice(0, 24)), orientation="h",
            marker=dict(color=P["series"][0], line=dict(width=0)),
            text=[f"{ctx.ccy} {v/1e6:.1f}M" if v >= 1e6 else f"{ctx.ccy} {v/1e3:.0f}k" for v in ts.values],
            textposition="outside", textfont=dict(color=P["text_secondary"], size=11, family=FONT),
            hovertemplate="<b>%{y}</b><br>%{x:,.0f}<extra></extra>", cliponaxis=False)
        style_fig(fig2, height=300, xgrid=True, ygrid=False)
        fig2.update_xaxes(tickformat="~s")
        fig2.update_layout(margin=dict(l=4, r=64, t=4, b=4), bargap=0.3)
        st.plotly_chart(fig2, width="stretch", theme=None)
    st.caption("From `facturef` supplier invoices, converted to DT at each invoice's own rate. "
               "Freight bucket = `tim + transit + femb + fdae + fsa + ass + tax`.")


# ------------------------------------------------------ supplier claims ---
def _supplier_claims(ctx: Ctx) -> None:
    st.subheader("Supplier quality / quantity claims")
    sd = erp.load_supplier_disputes()
    if sd.empty:
        st.caption("No `SDisputes` rows.")
        return
    sd = sd.dropna(subset=["dat"])
    sd["dat"] = pd.to_datetime(sd["dat"], errors="coerce")
    sd = sd[(sd["dat"].dt.year >= ctx.yr_lo) & (sd["dat"].dt.year <= ctx.yr_hi)]
    if sd.empty:
        st.caption("No supplier claims in range (data starts 2024).")
        return
    P = ctx.P
    top = (sd.groupby("supplier").agg(claims=("Id", "nunique"), lines=("part_code", "size"),
                                      qty=("qty", "sum"))
           .sort_values("claims").tail(15))
    fig = go.Figure()
    fig.add_bar(
        x=top["claims"], y=hbar_categories(fig, top.index.str.slice(0, 24)),
        orientation="h", marker=dict(color=P["series"][1], line=dict(width=0)),
        text=[f"{v:,.0f}" for v in top["claims"]], textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        customdata=top[["lines", "qty"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>%{x} claims · %{customdata[0]} lines · "
                      "%{customdata[1]:,.0f} units<extra></extra>",
        cliponaxis=False)
    style_fig(fig, height=max(240, 24 * len(top) + 50), xgrid=True, ygrid=False)
    fig.update_xaxes(title_text="Distinct claims")
    fig.update_layout(margin=dict(l=4, r=54, t=4, b=4), bargap=0.3)
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption(f"{sd['Id'].nunique():,} claims covering {sd['qty'].sum():,.0f} units flagged "
               f"{ctx.yr_lo}–{ctx.yr_hi} (`SDisputes` ⋈ `SDisputesLine`, data starts 2024). "
               "Ranked by claim count; claimed value is mixed-currency at line level so not totalled.")


# ------------------------------------------------------------ pipeline ---
def _pipeline(ctx: Ctx) -> None:
    st.subheader("Inbound PO pipeline")
    po = erp.load_open_pos()
    if po.empty:
        st.caption("No purchase-order lines with a future expected-arrival date.")
        return
    P = ctx.P
    po = po.assign(month=po["eta"].dt.to_period("M").dt.to_timestamp())
    m = po.groupby("month").agg(value=("line_value_dt", "sum"), lines=("part_code", "size")).reset_index()
    fig = go.Figure(go.Bar(
        x=m["month"], y=m["value"] / ctx.rate,
        marker=dict(color=P["series"][0], line=dict(width=0)),
        customdata=m[["lines"]].to_numpy(),
        hovertemplate="<b>%{x|%b %Y}</b><br>" + ctx.ccy + " %{y:,.0f} · %{customdata[0]} lines<extra></extra>"))
    style_fig(fig, height=280)
    fig.update_yaxes(tickformat="~s", title_text=f"Order value ({ctx.ccy})")
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption(f"{len(po):,} PO lines · {ctx.ccy} {po['line_value_dt'].sum() / ctx.rate / 1e6:.1f}M "
               "expected ahead, by `commandf_det.eta`. `fermeecmdf` (closed flag) is unpopulated "
               "in this copy, so a forward ETA is the 'still awaited' proxy.")
