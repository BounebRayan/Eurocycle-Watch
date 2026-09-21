"""Finance tab — billings vs collections, sales-commission cost and
margin-after-commission. Deliberately lean: the local ERP copy has no
customer-linked receivables and only a token treasury forecast, so this is a
cash-and-commission view, not full financial reporting (see doc §9)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, TILE_H, sym, to_disp, money, tile, compact


def _safe_ratio(num, den):
    """num/den, NaN where den <= 0."""
    return np.where(den > 0, num / den, np.nan)


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.info(ctx.t("**Partial view.** This ERP copy has no customer-level receivables and only "
                  "a token treasury forecast, so Finance covers cash-in and sales-commission "
                  "cost only. Full AR/AP and cash forecasting would need additional tables "
                  "(see `docs/eurocycles-erp-findings.md` §9).\n\n"
                  "The two sections below answer to the sidebar differently, and it is worth "
                  "knowing which is which: **Billings vs collections is company-wide** "
                  "(receipts carry no customer link, so it cannot be filtered), while "
                  "**commission follows the Distributor and Bikes-only filters** like the "
                  "rest of the sales tabs."),
            icon=":material/info:")

    _billings_vs_collections(ctx)
    st.divider()
    _payroll(scope, ctx)
    st.divider()
    _commission(scope, ctx)


# ------------------------------------------------------------------ payroll ---
def _payroll(scope: pd.DataFrame, ctx: Ctx) -> None:
    """Labour cost from `chargeprs`, against the revenue and units it produced.

    The absolute figure is the least interesting part. Payroll as a share of
    revenue, and payroll per bike invoiced, are the two that say whether the
    cost base is tracking the business — so those lead."""
    st.subheader(ctx.t("Payroll & labour cost"))
    pay = erp.load_payroll(start_year=min(ctx.yr_lo, 2019))
    pay = pay[(pay["month"].dt.year >= ctx.yr_lo) & (pay["month"].dt.year <= ctx.yr_hi)]
    if pay.empty:
        st.caption(ctx.t("No payroll rows in range."))
        return

    st.caption(ctx.t("Company-wide, from `chargeprs` — domestic payroll, already in DT. "
                     "Two lines are thin in this ERP copy (staff transport is empty, social "
                     "charges run slightly light), so read the total as a floor. Every other "
                     "line matches the finance pack exactly."))

    by_year = pay.groupby(pay["month"].dt.year)["amount_dt"].sum()
    rev_by_year = scope.groupby("yr")["line_rev_dt"].sum()
    units_by_year = scope.groupby("yr")["qte"].sum()
    y_cur = int(by_year.index.max())
    cur_pay = by_year.loc[y_cur]
    cur_rev = rev_by_year.get(y_cur, np.nan)
    cur_units = units_by_year.get(y_cur, np.nan)

    c1, c2, c3 = st.columns(3)
    c1.metric(ctx.t("Payroll") + f" {y_cur}", tile(cur_pay, ctx), border=True, height=TILE_H,
              help=ctx.t("Sum of every `chargeprs` line for the year to date."))
    c2.metric(ctx.t("Share of revenue"),
              f"{cur_pay / cur_rev * 100:.1f}%" if cur_rev and not pd.isna(cur_rev) else "n/a",
              border=True, height=TILE_H,
              help=ctx.t("Payroll ÷ invoiced revenue, on the sidebar's current filters. "
                         "Payroll is company-wide, so narrowing the distributor filter "
                         "raises this ratio artificially."))
    c3.metric(ctx.t("Payroll per bike"),
              money(cur_pay / cur_units, ctx, dp=1) if cur_units and not pd.isna(cur_units) else "n/a",
              border=True, height=TILE_H,
              help=ctx.t("Payroll ÷ units invoiced. Same caveat: payroll is company-wide."))

    P = ctx.P
    left, right = st.columns([3, 2])

    with left:
        st.markdown("**" + ctx.t("Payroll vs revenue by year") + "**")
        yrs = sorted(set(by_year.index) & set(rev_by_year.index))
        share = [by_year[y] / rev_by_year[y] * 100 for y in yrs]
        fig = go.Figure()
        fig.add_bar(x=yrs, y=to_disp(by_year.reindex(yrs), ctx), name=ctx.t("Payroll"),
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
        fig.add_trace(go.Scatter(x=yrs, y=share, name=ctx.t("Share of revenue"), yaxis="y2",
                                 mode="lines+markers", line=dict(color=P["series"][3], width=2),
                                 hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
        style_fig(fig, height=320)
        fig.update_layout(showlegend=True, margin=dict(l=4, r=44, t=44, b=4),
                          yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                      ticksuffix="%"))
        fig.update_xaxes(type="category")
        st.plotly_chart(fig, width="stretch", theme=None)

    with right:
        st.markdown("**" + ctx.t("Where the payroll goes") + "**")
        by_line = pay.groupby("line")["amount_dt"].sum().sort_values()
        # `line` is a data value from erp.PAYROLL_LINES, not a UI literal, so it
        # is translated here rather than at the call site.
        by_line.index = [ctx.t(v) for v in by_line.index]
        fig2 = go.Figure()
        fig2.add_bar(x=to_disp(by_line, ctx), y=hbar_categories(fig2, by_line.index),
                     orientation="h", marker=dict(color=P["series"][0], line=dict(width=0)),
                     text=[compact(v, ctx.ccy) for v in to_disp(by_line, ctx)],
                     textposition="outside",
                     textfont=dict(color=P["text_secondary"], size=11, family=FONT),
                     hovertemplate="<b>%{y}</b><br>" + sym(ctx.ccy) + "%{x:,.0f}<extra></extra>",
                     cliponaxis=False)
        style_fig(fig2, height=320, xgrid=True, ygrid=False)
        fig2.update_xaxes(tickformat="~s")
        fig2.update_layout(margin=dict(l=4, r=62, t=4, b=4), bargap=0.35)
        st.plotly_chart(fig2, width="stretch", theme=None)


def _billings_vs_collections(ctx: Ctx) -> None:
    st.subheader(ctx.t("Billings vs collections"))
    bm = erp.load_monthly_billings(start_year=min(ctx.yr_lo, 2019))
    bm = bm[(bm["month"].dt.year >= ctx.yr_lo) & (bm["month"].dt.year <= ctx.yr_hi)]
    bill = bm.set_index("month")["billed_dt"].rename("billed")

    rec = erp.load_receipts(start_year=min(ctx.yr_lo, 2019))
    rec = rec[(rec["paid_date"].dt.year >= ctx.yr_lo) & (rec["paid_date"].dt.year <= ctx.yr_hi)]
    coll = (rec.assign(month=rec["paid_date"].dt.to_period("M").dt.to_timestamp())
            .groupby("month")["amount_dt"].sum().rename("collected"))

    m = pd.concat([bill, coll], axis=1).fillna(0).reset_index()
    if m.empty:
        st.caption(ctx.t("No billing / collection rows in range."))
        return
    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=m["month"], y=to_disp(m["billed"], ctx), name=ctx.t("Billed (invoiced)"),
                marker=dict(color=P["series"][0], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>billed " + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=m["month"], y=to_disp(m["collected"], ctx), name=ctx.t("Collected (cash in)"),
                             mode="lines+markers", line=dict(color=P["series"][3], width=2),
                             marker=dict(size=5),
                             hovertemplate="<b>%{x|%b %Y}</b><br>collected " + sym(ctx.ccy) +
                                           "%{y:,.0f}<extra></extra>"))
    style_fig(fig, height=320)
    fig.update_layout(showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
    st.plotly_chart(fig, width="stretch", theme=None)
    tb, tc = m["billed"].sum(), m["collected"].sum()
    st.caption(ctx.tf("Billed {billed} · collected {collected} ({pct}% of billings) over "
                      "{lo}–{hi}. Collections are `reglement_det` cash-in (no customer link "
                      "on `reglement`), so this is company-wide and not an AR-ageing measure.",
                      billed=f"{sym(ctx.ccy)}{tb / ctx.rate:,.0f}",
                      collected=f"{sym(ctx.ccy)}{tc / ctx.rate:,.0f}",
                      pct=f"{tc / tb * 100:.0f}", lo=ctx.yr_lo, hi=ctx.yr_hi))


def _commission(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader(ctx.t("Sales-commission cost"))
    rates, agent_invoices = erp.load_commissions()
    if rates.empty:
        st.caption(ctx.t("No `agent3` commission schedule on file."))
        return

    # Commissionable revenue is measured on the rows actually on screen: mark each
    # invoice line as agent-carried, then aggregate. (Scaling a filtered revenue
    # figure by a company-wide invoice ratio overstates it whenever the sidebar
    # narrows the scope — the two numbers describe different populations.)
    agent_nums = set(agent_invoices["numf"].dropna())
    has_agent = scope["numf"].isin(agent_nums)
    by_yr = scope.assign(_agent_rev=scope["line_rev_dt"].where(has_agent, 0.0)).groupby("yr")
    d = by_yr.agg(revenue=("line_rev_dt", "sum"),
                  agent_revenue=("_agent_rev", "sum"),
                  margin_dt=("line_margin_dt", "sum")).reset_index()

    d = d.merge(rates[["yr", "rate_pct"]], on="yr", how="left")
    # No schedule for a year means no rate — leave it blank rather than borrowing
    # a neighbouring year's.
    d["commission_dt"] = d["agent_revenue"] * d["rate_pct"] / 100
    d["agent_share"] = _safe_ratio(d["agent_revenue"], d["revenue"]) * 100
    d["margin_after_comm_pct"] = _safe_ratio(
        d["margin_dt"] - d["commission_dt"].fillna(0), d["revenue"]) * 100
    if d["commission_dt"].isna().all():
        st.caption(ctx.t("No commission rate on file for any year in range."))
        return

    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=d["yr"], y=to_disp(d["commission_dt"], ctx), name=ctx.t("Commission cost"),
                marker=dict(color=P["series"][1], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=d["yr"], y=d["margin_after_comm_pct"], name=ctx.t("Margin after commission %"),
                             yaxis="y2", mode="lines+markers",
                             line=dict(color=P["series"][3], width=2), marker=dict(size=6),
                             hovertemplate="<b>%{x}</b><br>%{y:.1f}%<extra></extra>"))
    style_fig(fig, height=320)
    fig.update_layout(showlegend=True, margin=dict(l=4, r=44, t=44, b=4),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False, ticksuffix="%",
                                  range=[0, max(35, np.nanmax(d["margin_after_comm_pct"]) + 6)],
                                  tickfont=dict(color=P["muted"], size=12)))
    fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
    fig.add_hline(y=25, line_width=1, line_dash="dot", line_color=P["axis"], yref="y2")
    st.plotly_chart(fig, width="stretch", theme=None)
    tbl = pd.DataFrame({
        "yr": d["yr"],
        "rate": d["rate_pct"],
        "share": d["agent_share"],
        "agent_rev": to_disp(d["agent_revenue"], ctx),
        "comm": to_disp(d["commission_dt"], ctx),
        "margin_after_comm_pct": d["margin_after_comm_pct"],
    })
    st.dataframe(
        tbl, hide_index=True, width="stretch",
        column_config={
            "yr": st.column_config.NumberColumn("Year", format="%d"),
            "rate": st.column_config.NumberColumn("Blended rate", format="%.2f%%"),
            "share": st.column_config.NumberColumn("Commissionable revenue", format="%.0f%%",
                                                   help=ctx.t("Share of the revenue on screen that sits "
                                                        "on an invoice carrying a sales agent.")),
            # `compact` not `localized`: localized keeps the fractional part, so
            # DT 831,732.88 renders as "831 732 881" and reads as 831 million.
            "agent_rev": st.column_config.NumberColumn(f"Commissionable ({ctx.ccy})",
                                                       format="compact"),
            "comm": st.column_config.NumberColumn(f"Commission ({ctx.ccy})", format="compact"),
            "margin_after_comm_pct": st.column_config.NumberColumn("Margin after comm.", format="%.1f%%"),
        },
    )
    st.caption(ctx.t("**Estimate.** Which invoices are commissionable is exact — `agent1` names the agent "
               "per invoice, and only those lines are counted. The *rate* is the approximation: "
               "`agent3` sets it per agent × category (0.5–1.2%) and no model→category map exists "
               "locally, so the year's mean rate is applied. Years with no schedule are left blank "
               "rather than borrowing a neighbouring year's rate."))
