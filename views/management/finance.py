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
from theme import style_fig
from ._common import Ctx, sym, to_disp


def _safe_ratio(num, den):
    """num/den, NaN where den <= 0."""
    return np.where(den > 0, num / den, np.nan)


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.info("**Partial view.** This ERP copy has no customer-level receivables and only a "
            "token treasury forecast, so Finance covers cash-in and sales-commission cost "
            "only. Full AR/AP and cash forecasting would need additional tables (see "
            "`docs/eurocycles-erp-findings.md` §9).\n\n"
            "The two sections below answer to the sidebar differently, and it is worth "
            "knowing which is which: **Billings vs collections is company-wide** (receipts "
            "carry no customer link, so it cannot be filtered), while **commission follows "
            "the Distributor and Bikes-only filters** like the rest of the sales tabs.",
            icon=":material/info:")

    _billings_vs_collections(ctx)
    st.divider()
    _commission(scope, ctx)


def _billings_vs_collections(ctx: Ctx) -> None:
    st.subheader("Billings vs collections")
    bm = erp.load_monthly_billings(start_year=min(ctx.yr_lo, 2019))
    bm = bm[(bm["month"].dt.year >= ctx.yr_lo) & (bm["month"].dt.year <= ctx.yr_hi)]
    bill = bm.set_index("month")["billed_dt"].rename("billed")

    rec = erp.load_receipts(start_year=min(ctx.yr_lo, 2019))
    rec = rec[(rec["paid_date"].dt.year >= ctx.yr_lo) & (rec["paid_date"].dt.year <= ctx.yr_hi)]
    coll = (rec.assign(month=rec["paid_date"].dt.to_period("M").dt.to_timestamp())
            .groupby("month")["amount_dt"].sum().rename("collected"))

    m = pd.concat([bill, coll], axis=1).fillna(0).reset_index()
    if m.empty:
        st.caption("No billing / collection rows in range.")
        return
    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=m["month"], y=to_disp(m["billed"], ctx), name="Billed (invoiced)",
                marker=dict(color=P["series"][0], line=dict(width=0)),
                hovertemplate="<b>%{x|%b %Y}</b><br>billed " + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=m["month"], y=to_disp(m["collected"], ctx), name="Collected (cash in)",
                             mode="lines+markers", line=dict(color=P["series"][3], width=2),
                             marker=dict(size=5),
                             hovertemplate="<b>%{x|%b %Y}</b><br>collected " + sym(ctx.ccy) +
                                           "%{y:,.0f}<extra></extra>"))
    style_fig(fig, height=320)
    fig.update_layout(showlegend=True, margin=dict(l=4, r=4, t=44, b=4))
    fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
    st.plotly_chart(fig, width="stretch", theme=None)
    tb, tc = m["billed"].sum(), m["collected"].sum()
    st.caption(f"Billed {sym(ctx.ccy)}{tb / ctx.rate:,.0f} · collected {sym(ctx.ccy)}{tc / ctx.rate:,.0f} "
               f"({tc / tb * 100:.0f}% of billings) over {ctx.yr_lo}–{ctx.yr_hi}. Collections are "
               "`reglement_det` cash-in (no customer link on `reglement`), so this is company-wide "
               "and not an AR-ageing measure.")


def _commission(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader("Sales-commission cost")
    rates, agent_invoices = erp.load_commissions()
    if rates.empty:
        st.caption("No `agent3` commission schedule on file.")
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
        st.caption("No commission rate on file for any year in range.")
        return

    P = ctx.P
    fig = go.Figure()
    fig.add_bar(x=d["yr"], y=to_disp(d["commission_dt"], ctx), name="Commission cost",
                marker=dict(color=P["series"][1], line=dict(width=0)),
                hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>")
    fig.add_trace(go.Scatter(x=d["yr"], y=d["margin_after_comm_pct"], name="Margin after commission %",
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
                                                   help="Share of the revenue on screen that sits "
                                                        "on an invoice carrying a sales agent."),
            # `compact` not `localized`: localized keeps the fractional part, so
            # DT 831,732.88 renders as "831 732 881" and reads as 831 million.
            "agent_rev": st.column_config.NumberColumn(f"Commissionable ({ctx.ccy})",
                                                       format="compact"),
            "comm": st.column_config.NumberColumn(f"Commission ({ctx.ccy})", format="compact"),
            "margin_after_comm_pct": st.column_config.NumberColumn("Margin after comm.", format="%.1f%%"),
        },
    )
    st.caption("**Estimate.** Which invoices are commissionable is exact — `agent1` names the agent "
               "per invoice, and only those lines are counted. The *rate* is the approximation: "
               "`agent3` sets it per agent × category (0.5–1.2%) and no model→category map exists "
               "locally, so the year's mean rate is applied. Years with no schedule are left blank "
               "rather than borrowing a neighbouring year's rate.")
