"""Landed cost tab — the GPAO's valued production orders, with inbound freight.

Everywhere else the dashboard measures margin over `facture_det.mat`: standard
material cost, carrying no packaging, no paint and **no inbound freight**. The
GPAO's own answer to that gap is the freight coefficient `facturef_det.coef` —
the ancillary charges on a supplier invoice, spread per unit — which
`frmEtatOFValorisesTrans.vb` adds to each production order's bill of materials.

This tab ports that: what an order costs to build once freight is in, what the
bike then sold for, and the margin that survives. It also surfaces three defects
in how the GPAO builds the coefficient. See `docs/gpao-parity.md` §7.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import gpao_landed as L
from i18n import N_
from theme import style_fig
from ._common import Ctx, TILE_H, sym, to_disp, compact

TOP_N = 25


def render(ctx: Ctx) -> None:
    st.subheader(ctx.t("Landed cost") + " — " + ctx.t("build cost with freight"))
    st.caption(ctx.t(
        "The GPAO's \"Etat des OFs valorisés avec transport\". Each production order's "
        "bill of materials priced at the order date's FX rate, plus the freight "
        "coefficient carried by the supplier invoice that last delivered each "
        "component."))
    st.info(ctx.t(
        "**This tab is company-wide and sets its own dates.** It reads production "
        "orders and supplier invoices, which carry no distributor or bike/part "
        "dimension, so the sidebar's **Distributor**, **Bikes only** and **Years** "
        "controls do not apply. The currency selector does."))

    d1, d2 = _window(ctx)

    try:
        margin = L.load_landed_margin(d1, d2)
        orders = L.load_of_landed(d1, d2)
    except Exception as exc:
        st.warning(ctx.tf("Could not cost these orders: {err}", err=str(exc)[:250]))
        return

    if orders.empty:
        st.info(ctx.t("No production orders in this window."))
        return

    _headline(margin, orders, ctx)
    _margin_walk(margin, ctx)
    _defects(orders, ctx)

    st.divider()
    _by_model(margin, ctx)
    _orders_table(orders, ctx)
    _charge_mix(ctx)


# ------------------------------------------------------------ the window ---
def _window(ctx: Ctx) -> tuple[dt.date, dt.date]:
    end = ctx.data_end.date() if ctx.data_end is not None else dt.date.today()
    c1, c2 = st.columns(2)
    d1 = c1.date_input(ctx.t("From"), value=dt.date(end.year, 1, 1),
                       min_value=dt.date(2010, 1, 1), max_value=end,
                       key="landed_d1", format="DD/MM/YYYY")
    d2 = c2.date_input(ctx.t("To"), value=end, min_value=dt.date(2010, 1, 1),
                       max_value=end, key="landed_d2", format="DD/MM/YYYY")
    return (d2, d1) if d1 > d2 else (d1, d2)


# ---------------------------------------------------------------- tiles ---
def _headline(margin: pd.DataFrame, orders: pd.DataFrame, ctx: Ctx) -> None:
    o = orders[orders["mat_dt"] > 0]
    mat = o["mat_dt"].mean()
    fr = o["freight_weighted"].mean()
    ratio = o["freight_weighted"].sum() / o["mat_dt"].sum() * 100 if o["mat_dt"].sum() else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(ctx.t("Material per bike"), f"{sym(ctx.ccy)}{to_disp(mat, ctx):,.2f}",
              border=True, height=TILE_H,
              help=ctx.t("Mean over the orders in this window of Σ price × quantity × "
                         "FX rate at the order date — the GPAO's TOTAL Gen DT."))
    c2.metric(ctx.t("Freight per bike"), f"{sym(ctx.ccy)}{to_disp(fr, ctx):,.2f}",
              border=True, height=TILE_H,
              help=ctx.t("Σ coefficient × quantity per component. The GPAO does not "
                         "scale by quantity; this does. Both are in the table below."))
    c3.metric(ctx.t("Landed per bike"), f"{sym(ctx.ccy)}{to_disp(mat + fr, ctx):,.2f}",
              border=True, height=TILE_H)
    c4.metric(ctx.t("Freight on material"), f"{ratio:.1f}%", border=True, height=TILE_H,
              help=ctx.t("What inbound freight adds on top of material cost."))

    matched = margin[margin["matched"]] if "matched" in margin else margin.iloc[0:0]
    if not matched.empty:
        st.caption(ctx.tf(
            "{n:,} of {tot:,} invoiced order lines matched a costed production order "
            "({pct:.1f} % of revenue). Unmatched lines keep their material cost and "
            "carry no freight.",
            n=int(matched.shape[0]), tot=int(margin.shape[0]),
            pct=matched["revenue"].sum() / margin["revenue"].sum() * 100
            if margin["revenue"].sum() else 0.0))


# ----------------------------------------------------------- the walk ------
def _margin_walk(margin: pd.DataFrame, ctx: Ctx) -> None:
    """Revenue → margin over material → margin after freight.

    The point of the tab in one chart: how much of the headline margin is spent
    getting the components to the factory."""
    if margin.empty:
        return
    m = margin[margin["matched"]] if "matched" in margin else margin
    if m.empty:
        return
    rev = m["revenue"].sum()
    mat = m["cost"].sum()
    fr = m["freight_cost"].sum()
    if rev <= 0:
        return

    st.markdown("#### " + ctx.t("Where the margin goes"))
    c1, c2, c3 = st.columns(3)
    c1.metric(ctx.t("Margin over material"), f"{(rev - mat) / rev * 100:.1f}%",
              compact(to_disp(rev - mat, ctx), ctx.ccy), delta_color="off",
              border=True, height=TILE_H,
              help=ctx.t("What the Overview tab calls margin over build cost."))
    c2.metric(ctx.t("Inbound freight"), f"{fr / rev * 100:.1f}%",
              compact(to_disp(fr, ctx), ctx.ccy), delta_color="off",
              border=True, height=TILE_H)
    c3.metric(ctx.t("Margin after freight"), f"{(rev - mat - fr) / rev * 100:.1f}%",
              compact(to_disp(rev - mat - fr, ctx), ctx.ccy), delta_color="off",
              border=True, height=TILE_H)

    P = ctx.P
    steps = [(ctx.t("Revenue"), rev), (ctx.t("Material"), -mat),
             (ctx.t("Inbound freight"), -fr), (ctx.t("Margin after freight"), rev - mat - fr)]
    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=[s[0] for s in steps], y=[to_disp(s[1], ctx) for s in steps],
        text=[compact(to_disp(abs(s[1]), ctx), ctx.ccy) for s in steps],
        textposition="outside", connector=dict(line=dict(color=P["grid"])),
        increasing=dict(marker=dict(color=P["series"][0])),
        decreasing=dict(marker=dict(color=P["neg"])),
        totals=dict(marker=dict(color=P["pos"]))))
    fig.update_yaxes(title=ctx.ccy)
    st.plotly_chart(style_fig(fig, height=330), width="stretch")
    st.caption(ctx.t(
        "Invoiced order lines that matched a costed production order. Material is "
        "`facture_det.mat`, the ERP's standard cost; freight is the order's own "
        "coefficient total × line quantity. This still carries no packaging, paint "
        "or labour, so it is not the accounting gross margin either — it is that "
        "figure with one more real cost put back."))


# --------------------------------------------------------------- defects ---
def _defects(orders: pd.DataFrame, ctx: Ctx) -> None:
    o = orders[orders["mat_dt"] > 0]
    differ = int((o["freight_gpao"].round(4) != o["freight_weighted"].round(4)).sum())
    negative = int((o["freight_gpao"] < 0).sum())
    amb = L.coef_ambiguity()

    with st.expander(ctx.t("Three problems with the GPAO's freight coefficient")):
        st.markdown(ctx.t(L.COEF_DEFECT))
        audit = L.load_coef_audit()
        if not audit.empty:
            bad = audit[audit["has_credit"] & (audit["coef_gpao"] < 0)]
            st.caption(ctx.tf(
                "On supplier invoices since 2024: {credit:,} of {tot:,} carry credit "
                "lines, and {bad:,} of those end up with a **negative** coefficient. "
                "{negof:,} production orders in this window inherit negative freight.",
                credit=int(audit["has_credit"].sum()), tot=int(audit.shape[0]),
                bad=int(bad.shape[0]), negof=negative))
        st.markdown(ctx.t(L.FREIGHT_DEFECT))
        st.caption(ctx.tf("Differs on {n:,} of {tot:,} orders in this window.",
                          n=differ, tot=int(o.shape[0])))
        st.markdown(ctx.t(L.TIE_DEFECT))
        st.caption(ctx.tf(
            "{tied:,} of {groups:,} part-days ({pct:.1f} %) have more than one "
            "coefficient, mean spread {mean:,.2f} DT, worst {mx:,.0f} DT.",
            tied=amb.tied, groups=amb.groups, pct=amb.tied_pct,
            mean=amb.mean_spread, mx=amb.max_spread))


# -------------------------------------------------------------- by model ---
def _by_model(margin: pd.DataFrame, ctx: Ctx) -> None:
    if margin.empty:
        return
    m = margin[margin["matched"]] if "matched" in margin else margin
    if m.empty:
        return
    st.markdown("#### " + ctx.t("Margin after freight, by model"))

    g = m.groupby("article", as_index=False).agg(
        revenue=("revenue", "sum"), cost=("cost", "sum"),
        freight=("freight_cost", "sum"), units=("qty", "sum"))
    g["margin_mat"] = g["revenue"] - g["cost"]
    g["margin_landed"] = g["margin_mat"] - g["freight"]
    for c in ("margin_mat", "margin_landed"):
        g[c + "_pct"] = np.where(g["revenue"] > 0, g[c] / g["revenue"] * 100, np.nan)
    g["freight_per_bike"] = np.where(g["units"] > 0, g["freight"] / g["units"], np.nan)
    g = g.sort_values("revenue", ascending=False).head(TOP_N)

    disp = g.copy()
    for c in ("revenue", "cost", "freight", "margin_mat", "margin_landed",
              "freight_per_bike"):
        disp[c] = to_disp(disp[c], ctx)
    st.dataframe(
        disp[["article", "units", "revenue", "cost", "freight", "margin_landed",
              "margin_mat_pct", "margin_landed_pct", "freight_per_bike"]],
        hide_index=True, width="stretch",
        column_config={
            "article": ctx.t("Model"),
            "units": st.column_config.NumberColumn(ctx.t("Units"), format="localized"),
            "revenue": st.column_config.NumberColumn(f"{ctx.t('Revenue')} ({ctx.ccy})",
                                                     format="compact"),
            "cost": st.column_config.NumberColumn(f"{ctx.t('Material')} ({ctx.ccy})",
                                                 format="compact"),
            "freight": st.column_config.NumberColumn(f"{ctx.t('Freight')} ({ctx.ccy})",
                                                     format="compact"),
            "margin_landed": st.column_config.NumberColumn(
                f"{ctx.t('Margin after freight')} ({ctx.ccy})", format="compact"),
            "margin_mat_pct": st.column_config.NumberColumn(
                ctx.t("Margin over material") + " %", format="%.1f%%"),
            "margin_landed_pct": st.column_config.NumberColumn(
                ctx.t("Margin after freight") + " %", format="%.1f%%"),
            "freight_per_bike": st.column_config.NumberColumn(
                ctx.t("Freight per bike"), format="%.2f"),
        })
    st.caption(ctx.tf("Top {n} models by revenue in this window.", n=TOP_N))


# -------------------------------------------------------- order detail ----
def _orders_table(orders: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("Production orders"))
    o = orders.sort_values("mat_dt", ascending=False).head(TOP_N * 4).copy()
    for c in ("mat_dt", "freight_gpao", "freight_weighted", "landed_gpao",
              "landed_weighted"):
        o[c] = to_disp(o[c], ctx)
    st.dataframe(
        o[["of_date", "article", "of_no", "po", "of_qty", "parts", "mat_dt",
           "freight_gpao", "freight_weighted", "landed_weighted"]],
        hide_index=True, width="stretch",
        column_config={
            "of_date": st.column_config.DateColumn(ctx.t("Order date"), format="DD/MM/YYYY"),
            "article": ctx.t("Model"),
            "of_no": ctx.t("Order"),
            "po": ctx.t("Customer PO"),
            "of_qty": st.column_config.NumberColumn(ctx.t("Order qty"), format="localized"),
            "parts": st.column_config.NumberColumn(ctx.t("Parts"), format="%d"),
            "mat_dt": st.column_config.NumberColumn(f"{ctx.t('Material')} ({ctx.ccy})",
                                                    format="%.2f"),
            "freight_gpao": st.column_config.NumberColumn(
                ctx.t("Freight (GPAO)"), format="%.2f"),
            "freight_weighted": st.column_config.NumberColumn(
                ctx.t("Freight (qty weighted)"), format="%.2f"),
            "landed_weighted": st.column_config.NumberColumn(
                f"{ctx.t('Landed per bike')} ({ctx.ccy})", format="%.2f"),
        })
    st.caption(ctx.t(
        "Per bike, not per order. The two freight columns are the GPAO's own "
        "unweighted sum and the quantity-weighted one; they differ on every order."))


# ------------------------------------------------------------ charge mix ---
def _charge_mix(ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("What the freight pool is made of"))
    try:
        mix = L.load_charge_mix()
    except Exception as exc:
        st.warning(ctx.tf("Could not read the charge mix: {err}", err=str(exc)[:200]))
        return
    if mix.empty:
        st.info(ctx.t("No supplier invoices in range."))
        return

    P = ctx.P
    fig = go.Figure()
    for i, col in enumerate(L.CHARGE_COLS):
        fig.add_bar(x=mix["yr"], y=to_disp(mix[col], ctx),
                    name=ctx.t(L.CHARGE_LABELS[col]),
                    marker_color=P["series"][i % len(P["series"])])
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title=ctx.t("Year"), type="category")
    fig.update_yaxes(title=ctx.ccy)
    fig = style_fig(fig, height=340)
    fig.update_layout(showlegend=True)
    st.plotly_chart(fig, width="stretch")
    st.caption(ctx.t(
        "The nine `facturef` columns the GPAO sums into the pool it spreads per "
        "unit. Stamp duty (`tim`) and `autres` are deliberately outside it."))
