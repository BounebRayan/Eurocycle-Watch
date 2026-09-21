"""Exchange rate tab — what the dinar did to the sales book.

Ports the GPAO's `frmExchangeRate` ("Variation du taux de change"). The screen
revalues each month's foreign-currency sales at the previous month's rate and
calls the difference `Ecart`; a TOTAL band does the same for the year against
the opening rate.

The tab leads with the correction rather than the screen, because the screen's
own two answers disagree — its monthly columns and its yearly total measure
different things, and on 2026 they disagree in sign on EUR. What the business
needs from this data is the piece the Actions tab was missing: every sale is
invoiced in EUR or USD while build cost is stored in dinar, so the rate alone
moves reported margin. `gpao_exchange.fx_split` sizes that, and the erosion rule
now nets it out. See `docs/gpao-parity.md` §8.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
import gpao_exchange as X
from theme import style_fig
from ._common import Ctx, TILE_H, money, tile, to_disp, compact

TOP_N = 20


def render(ctx: Ctx) -> None:
    st.subheader(ctx.t("Exchange rate") + " — " + ctx.t("what the dinar did to the book"))
    st.caption(ctx.t(
        "The GPAO's \"Variation du taux de change\". It revalues each month's "
        "EUR and USD sales at the previous month's rate, and the year's sales at "
        "the opening rate, and reports the difference."))
    st.info(ctx.t(
        "**This tab sets its own year and reads the whole sales book.** The "
        "GPAO screen filters to EUR and USD invoices only and carries no "
        "bike/part split, so the sidebar's **Bikes only** and **Years** controls "
        "do not apply. The currency selector does."))

    year = _year_picker(ctx)

    try:
        by_dev = X.load_fx_variation(year, by_customer=False)
        by_cust = X.load_fx_variation(year, by_customer=True)
        totals = X.year_total(year)
    except Exception as exc:
        st.warning(ctx.tf("Could not read exchange-rate variation: {err}", err=str(exc)[:250]))
        return

    if by_dev.empty:
        st.info(ctx.tf("No EUR or USD invoices in {yr}.", yr=year))
        return

    _headline(totals, by_dev, ctx)
    st.divider()
    _margin_effect(year, ctx)
    st.divider()
    _monthly(by_dev, ctx)
    st.divider()
    _customers(by_cust, ctx)
    st.divider()
    _defects(by_dev, totals, year, ctx)


# ------------------------------------------------------------- controls ---
def _year_picker(ctx: Ctx) -> int:
    end = int(ctx.data_end.year) if ctx.data_end is not None else 2026
    opts = list(range(end, end - 8, -1))
    return int(st.selectbox(ctx.t("Year"), opts, index=0, key="fx_year",
                            help=ctx.t("The GPAO screen opens on 1 January – 31 December "
                                       "of the current year; this matches that window.")))


# ------------------------------------------------------------- headline ---
def _headline(totals: pd.DataFrame, by_dev: pd.DataFrame, ctx: Ctx) -> None:
    if totals.empty:
        return
    cols = st.columns(len(totals) + 1)
    for col, (_, r) in zip(cols, totals.iterrows()):
        move = r["rate_close"] - r["rate_open"]
        col.metric(
            ctx.tf("{dev} sales revalued", dev=r["dev"]), tile(r["ecart"], ctx),
            delta=f"{move:+.4f} DT/{r['dev']} ({r['ecart_pct']:+.2f}%)",
            border=True, height=TILE_H,
            help=ctx.tf("{vol} {dev} of turnover, priced at the closing rate "
                        "({close:.4f}) instead of the opening one ({open:.4f}). This is "
                        "the GPAO's TOTAL band.",
                        vol=f"{r['total_ccy']:,.0f}", dev=r["dev"],
                        close=r["rate_close"], open=r["rate_open"]))
    net = float(totals["ecart"].sum())
    cols[-1].metric(ctx.t("Net"), tile(net, ctx), border=True, height=TILE_H,
                    help=ctx.t("EUR and USD together. A positive figure means the year's "
                               "turnover was worth more dinar at the closing rate than at "
                               "the opening one."))


# -------------------------------------------------------- the correction ---
def _margin_effect(year: int, ctx: Ctx) -> None:
    """The reason this tab exists: FX moves reported margin on its own."""
    st.markdown("#### " + ctx.t("Effect on reported margin"))
    st.caption(ctx.t(
        "Sales are invoiced in EUR and USD and translated to dinar at each "
        "invoice's own rate; build cost (`facture_det.mat`) is already in dinar. "
        "The two sides of the margin therefore do not move together when the "
        "rate does. This holds the rate constant across the two years and reports "
        "what changes — computed from the booked rate, not this screen's monthly "
        "averages, so it reconciles with every other tab."))

    prev = year - 1
    sales = erp.load_sales(start_year=min(prev, year) - 1, bikes_only=True)
    sales = sales[sales["yr"].isin([prev, year])]
    if sales.empty:
        st.info(ctx.tf("No sales in {a} and {b} to compare.", a=prev, b=year))
        return
    split = X.fx_split(sales, year, prev, group="article")
    if split.empty:
        st.info(ctx.tf("No model sold in both {a} and {b}.", a=prev, b=year))
        return

    # A model invoiced in a year with credit notes and no units has no
    # constant-rate margin — see `fx_split`. Those rows are dropped here rather
    # than allowed to split the weighted average's numerator from its
    # denominator.
    split = split[split[["fx_pp", "drop_pp_ex_fx", "rev"]].notna().all(axis=1)]
    if split.empty:
        st.info(ctx.tf("No model sold in both {a} and {b} with units in each.",
                       a=prev, b=year))
        return
    tot_rev = float(split["rev"].sum())
    fx_dt = float(split["fx_dt"].sum())
    fx_pp = float((split["fx_pp"] * split["rev"]).sum() / tot_rev) if tot_rev else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric(ctx.t("Margin moved by FX"), f"{fx_pp:+.2f} pp", border=True, height=TILE_H,
              help=ctx.tf("Revenue-weighted, across every model sold in both {a} and {b}.",
                          a=prev, b=year))
    c2.metric(ctx.t("Worth"), tile(fx_dt, ctx), border=True, height=TILE_H,
              help=ctx.t("Those points applied to this year's revenue. Positive means the "
                         "rate move depressed reported margin by this much."))
    c3.metric(ctx.t("Models affected"), f"{len(split):,}", border=True, height=TILE_H,
              help=ctx.tf("Sold in both {a} and {b}.", a=prev, b=year))

    worst = split.nlargest(TOP_N, "fx_dt")[
        ["article", "units", "margin_pct_prev", "margin_pct", "margin_pct_const",
         "drop_pp", "fx_pp", "drop_pp_ex_fx", "fx_dt"]].copy()
    worst["fx_dt"] = to_disp(worst["fx_dt"], ctx)
    st.dataframe(worst, hide_index=True, width="stretch", column_config={
        "article": ctx.t("Code"),
        "units": st.column_config.NumberColumn(ctx.t("Units"), format="%d"),
        "margin_pct_prev": st.column_config.NumberColumn(f"{prev}", format="%.1f%%"),
        "margin_pct": st.column_config.NumberColumn(f"{year}", format="%.1f%%"),
        "margin_pct_const": st.column_config.NumberColumn(
            ctx.t("at last year's rate"), format="%.1f%%"),
        "drop_pp": st.column_config.NumberColumn(ctx.t("Change"), format="%.1f pp"),
        "fx_pp": st.column_config.NumberColumn(ctx.t("of which FX"), format="%.1f pp"),
        "drop_pp_ex_fx": st.column_config.NumberColumn(ctx.t("Ex-FX"), format="%.1f pp"),
        "fx_dt": st.column_config.NumberColumn(ctx.tf("FX ({c})", c=ctx.ccy), format="%.0f")})
    st.caption(ctx.t("Ranked by the dinar value of the FX effect. **Ex-FX** is what the "
                     "Actions tab's erosion rule now triggers on."))


# -------------------------------------------------------------- monthly ---
def _monthly(by_dev: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("Month on month, as the GPAO reports it"))
    st.caption(ctx.t("Each month's turnover revalued at the previous month's average rate. "
                     "January uses the 31/12 rate."))

    fig = go.Figure()
    for i, dev in enumerate(X.CURRENCIES):
        d = by_dev[by_dev["dev"] == dev]
        if d.empty:
            continue
        fig.add_bar(x=d["mo"], y=to_disp(d["ecart"], ctx), name=dev,
                    marker_color=ctx.P["series"][i],
                    hovertemplate="%{x}: %{y:,.0f}<extra>" + dev + "</extra>")
    style_fig(fig, height=320)
    fig.update_layout(barmode="relative", showlegend=True,
                      margin=dict(l=4, r=4, t=44, b=4),
                      xaxis_title=ctx.t("Month"),
                      yaxis_title=ctx.tf("Ecart ({c})", c=ctx.ccy))
    st.plotly_chart(fig, width="stretch")

    show = by_dev.copy()
    for c in ("total_dt_prev", "total_dt_cur", "ecart"):
        show[c] = to_disp(show[c], ctx)
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "dev": ctx.t("Currency"),
        "mo": st.column_config.NumberColumn(ctx.t("Month"), format="%d"),
        "total_ccy": st.column_config.NumberColumn(ctx.t("Turnover (invoice ccy)"), format="%.0f"),
        "rate_prev": st.column_config.NumberColumn(ctx.t("Rate (1)"), format="%.4f"),
        "rate_cur": st.column_config.NumberColumn(ctx.t("Rate (2)"), format="%.4f"),
        "total_dt_prev": st.column_config.NumberColumn(ctx.tf("At rate (1) ({c})", c=ctx.ccy),
                                                       format="%.0f"),
        "total_dt_cur": st.column_config.NumberColumn(ctx.tf("At rate (2) ({c})", c=ctx.ccy),
                                                      format="%.0f"),
        "ecart": st.column_config.NumberColumn(ctx.tf("Ecart ({c})", c=ctx.ccy), format="%.0f"),
        "ecart_pct": st.column_config.NumberColumn(ctx.t("Ecart %"), format="%.2f%%")})


# ------------------------------------------------------------ customers ---
def _customers(by_cust: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("By customer"))
    st.caption(ctx.t("The same month-on-month reading, summed over the year per customer. "
                     "Read it as exposure — who the rate moved most money on — not as a "
                     "gain or loss."))
    g = (by_cust.groupby(["code", "customer", "dev"])
                .agg(total_ccy=("total_ccy", "sum"), ecart=("ecart", "sum"))
                .reset_index())
    g["abs"] = g["ecart"].abs()
    g = g.nlargest(TOP_N, "abs").drop(columns="abs")
    g["ecart"] = to_disp(g["ecart"], ctx)
    st.dataframe(g, hide_index=True, width="stretch", column_config={
        "code": ctx.t("Code"), "customer": ctx.t("Customer"), "dev": ctx.t("Currency"),
        "total_ccy": st.column_config.NumberColumn(ctx.t("Turnover (invoice ccy)"),
                                                   format="%.0f"),
        "ecart": st.column_config.NumberColumn(ctx.tf("Ecart ({c})", c=ctx.ccy), format="%.0f")})


# -------------------------------------------------------------- defects ---
def _defects(by_dev: pd.DataFrame, totals: pd.DataFrame, year: int, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("Where this screen does not hold up"))

    monthly = by_dev.groupby("dev")["ecart"].sum()
    rows = []
    for _, r in totals.iterrows():
        rows.append({"dev": r["dev"],
                     "monthly": float(monthly.get(r["dev"], 0.0)),
                     "band": float(r["ecart"])})
    cmp = pd.DataFrame(rows)
    st.markdown(ctx.t(X.MONTHLY_TOTAL_DEFECT))
    if not cmp.empty:
        for c in ("monthly", "band"):
            cmp[c] = to_disp(cmp[c], ctx)
        st.dataframe(cmp, hide_index=True, width="stretch", column_config={
            "dev": ctx.t("Currency"),
            "monthly": st.column_config.NumberColumn(
                ctx.tf("Sum of the 12 columns ({c})", c=ctx.ccy), format="%.0f"),
            "band": st.column_config.NumberColumn(
                ctx.tf("TOTAL band ({c})", c=ctx.ccy), format="%.0f")})

    st.markdown(ctx.t(X.AVERAGE_RATE_DEFECT))
    try:
        bva = X.booked_vs_average(year)
    except Exception:
        bva = pd.DataFrame()
    if not bva.empty:
        off = bva["gap_pct"].abs()
        st.caption(ctx.tf(
            "In {yr}: median gap {med:+.2f} %, {n1} of {n} invoices more than 1 % from the "
            "month average, worst {worst:.2f} %.",
            yr=year, med=float(bva["gap_pct"].median()), n1=int((off > 1).sum()),
            n=len(bva), worst=float(off.max())))

    st.markdown(ctx.t(X.BAND_OFFSET_DEFECT))
    st.markdown(ctx.t(X.ZERO_RATE_DEFECT))
    st.caption(ctx.t("Reproduced, not corrected — the figures above are the GPAO's own. "
                     "The **Effect on reported margin** block at the top of this tab is the "
                     "corrected reading, and it is the one the Actions tab consumes."))
