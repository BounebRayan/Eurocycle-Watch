"""Finance — the P&L walk down to net profit, billings vs collections,
payroll and sales-commission cost.

`profitability()` came over from the old Overview tab when Management split
into pages: it is the only block on the whole view that answers "what did the
company actually make", and it belongs beside the other money sections rather
than under a scoreboard of per-model margins.

Otherwise deliberately lean: the local ERP copy has no customer-linked
receivables and only a token treasury forecast, so the rest is a
cash-and-commission view, not full financial reporting (see doc §9)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
import finance_pack
from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, TILE_H, sym, to_disp, money, tile, compact


def _safe_ratio(num, den):
    """num/den, NaN where den <= 0."""
    return np.where(den > 0, num / den, np.nan)


# ------------------------------------------------------------------ payroll ---
def payroll(scope: pd.DataFrame, ctx: Ctx) -> None:
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


def billings_vs_collections(ctx: Ctx) -> None:
    st.subheader(ctx.t("Billings vs collections"))
    st.info(ctx.t("**Company-wide, and a partial view.** Receipts carry no customer link, "
                  "so the sidebar's **Distributor** and **Bikes only** filters cannot apply "
                  "here — the year range does. This ERP copy also has no customer-level "
                  "receivables and only a token treasury forecast, so this is cash-in, not "
                  "AR/AP or a cash forecast (see `docs/eurocycles-erp-findings.md` §9)."),
            icon=":material/info:")
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


def commission(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.subheader(ctx.t("Sales-commission cost"))
    st.caption(ctx.t("Follows the sidebar's **Distributor** and **Bikes only** filters, "
                     "like the rest of the sales tabs."))
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


# --------------------------------------------------------- profitability ---
# Revenue and the two income sections aside, every section is a cost. The order
# is the order the P&L walks them, which is also the order the waterfall reads.
_WALK = ("Cost of sales", "Non-stocked purchases", "External services",
         "Other external charges", "Taxes & duties", "Salaries & charges",
         "Depreciation", "Financial charges")


def profitability(ctx: Ctx) -> None:
    """Revenue down to net profit, from the accounting pack.

    The Overview page's KPI strip reports margin over *material* cost, which is
    the right measure for comparing models against each other and the wrong one
    for "what did the company make" — it ignores every cost below the material
    line. This block is the answer to the second question, and `_reconcile`
    below explains why the two numbers differ. Without it the reader's only
    headline is a 30-odd-percent margin on a business whose net is single
    digits."""
    years = finance_pack.available_years()
    if not years:
        return
    year = max(years)
    s = finance_pack.summary(year)
    if not s:
        return

    st.subheader(ctx.t("Profitability — from the finance pack"))
    st.caption(ctx.tf(
        "{months} months to {end}, company-wide, from the accounting pack in "
        "`data/finance/` — not the ERP, which holds none of these costs except "
        "salaries. **This block does not follow the sidebar**: it is a fixed "
        "period and the whole company. Drop a newer export in to move it on.",
        months=s["months"], end=f"{s['period_end']:%d %b %Y}"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(ctx.t("Revenue"), tile(s["revenue"], ctx), border=True, height=TILE_H,
              help=ctx.t("The pack's own revenue line. Our ERP total for the same "
                         "months agrees with it to within 0.02%."))
    # The share of revenue goes in the label, not in `delta`: Streamlit draws an
    # arrow on a delta whatever `delta_color` says, and an up arrow beside
    # operating costs reads as an improvement.
    def pct_label(label: str, pct: float) -> str:
        return label + f" · {pct:.1f}% " + ctx.t("of revenue")

    c2.metric(pct_label(ctx.t("Gross margin"), s["gross_margin_pct"]),
              tile(s["gross_margin"], ctx), border=True, height=TILE_H,
              help=ctx.t("Revenue − cost of sales, on the accounting basis: purchases "
                         "of parts, frames, packaging and paint plus inbound freight, "
                         "adjusted for stock movement."))
    c3.metric(pct_label(ctx.t("Operating costs"), s["operating_costs_pct"]),
              tile(s["operating_costs"], ctx), border=True, height=TILE_H,
              help=ctx.t("Everything below the gross-margin line: salaries, external "
                         "services, financial charges, depreciation and the rest."))
    c4.metric(pct_label(ctx.t("Net profit"), s["net_pct"]),
              tile(s["net"], ctx), border=True, height=TILE_H,
              help=ctx.t("After the pack's own profit-tax line. This is the bottom line."))

    _profit_waterfall(s, ctx)
    _reconcile(s, ctx)


def profit_summary(ctx: Ctx) -> None:
    """Net profit and the walk that gets there — the Overview page's cut.

    One tile instead of four, and no reconciliation expander. The question this
    answers on a landing page is "did the company make money", not "how does
    this reconcile with the strip above it" — the Finance page's P&L tab keeps
    the full version, and the link below points at it.

    It is drawn beside the ERP scoreboard but never folded into it: this is a
    fixed period and the whole company, and a tile that quietly ignores the
    sidebar has no business sitting in a strip of tiles that obey it.

    **The period goes in the heading, not just the caption.** The waterfall's
    first bar is labelled "Revenue" and sits one column away from a scoreboard
    tile also labelled "Revenue" — and the two disagree, because the pack ends
    when the accountants closed it and the ERP runs to its last invoice. On the
    full-width block this replaced, four tiles and a long caption made that
    obvious; compressed to one column it is not, so the heading carries the
    period and `_tie_note` shows the two agreeing over the months they share."""
    from . import _nav

    years = finance_pack.available_years()
    if not years:
        return
    s = finance_pack.summary(max(years))
    if not s:
        return

    st.subheader(ctx.tf("Down to net profit — {months} months to {end}",
                        months=s["months"], end=f"{s['period_end']:%d %b %Y}"))
    _tie_note(s, ctx)
    st.metric(ctx.t("Net profit") + f" · {s['net_pct']:.1f}% " + ctx.t("of revenue"),
              tile(s["net"], ctx), border=True, height=TILE_H,
              help=ctx.t("After the pack's own profit-tax line. This is the bottom line."))
    _profit_waterfall(s, ctx)
    _nav.link("finance", ctx, tab="P&L",
              label=ctx.t("Full P&L, and why it differs from the margin above"))


def _tie_note(s: dict, ctx: Ctx) -> None:
    """One line reconciling the pack's revenue with the ERP's, over the months
    they both cover.

    Two numbers called "Revenue" in one row, differing by millions, is the kind
    of thing that makes a reader stop trusting the whole page. They are both
    right — the gap is entirely the period — and the cheapest way to prove it is
    to put the ERP's own figure for the pack's months next to it. Falls back to
    naming the period difference if the company totals can't be read."""
    st.caption(ctx.t("Company-wide, from the accounting pack — "
                     "**this block does not follow the sidebar.**"))
    try:
        totals = erp.load_company_totals(s["year"])
        cut = totals[totals["month"] <= s["period_end"]]
        erp_rev = float(cut["revenue_dt"].sum())
    except Exception:
        return
    if erp_rev <= 0 or not s["revenue"]:
        return
    gap = (erp_rev - s["revenue"]) / s["revenue"] * 100
    st.caption(ctx.tf(
        "It stops earlier than the ERP figures on the left, so the two revenue "
        "numbers on this row are not the same period. Over the {months} months "
        "the pack does cover, the ERP's own revenue is {erp} against the pack's "
        "{pack} — a {gap} difference.",
        months=s["months"], erp=money(erp_rev, ctx), pack=money(s["revenue"], ctx),
        gap=f"{abs(gap):.2f}%"))


def _profit_waterfall(s: dict, ctx: Ctx) -> None:
    P = ctx.P
    labels = [ctx.t("Revenue")]
    values = [s["revenue"]]
    measures = ["absolute"]
    for section in _WALK:
        amount = s["by_section"].get(section)
        if not amount:
            continue
        labels.append(ctx.t(section))
        values.append(-amount)
        measures.append("relative")
    if s["other_income"]:
        labels.append(ctx.t("Other income"))
        values.append(s["other_income"])
        measures.append("relative")
    labels += [ctx.t("Pre-tax profit"), ctx.t("Profit tax"), ctx.t("Net profit")]
    values += [0, -s["tax"], 0]
    measures += ["total", "relative", "total"]

    disp = [to_disp(v, ctx) for v in values]
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=disp,
        text=[compact(v, ctx.ccy) if m != "total" else "" for v, m in zip(disp, measures)],
        textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        connector=dict(line=dict(color=P["axis"], width=1)),
        increasing=dict(marker=dict(color=P["pos"])),
        decreasing=dict(marker=dict(color=P["neg"])),
        totals=dict(marker=dict(color=P["series"][0])),
        hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>"))
    style_fig(fig, height=400, xgrid=False, ygrid=True)
    fig.update_yaxes(tickformat="~s", title_text=ctx.ccy)
    fig.update_xaxes(tickangle=-30)
    fig.update_layout(margin=dict(l=4, r=4, t=28, b=4))
    st.plotly_chart(fig, width="stretch", theme=None)


def _reconcile(s: dict, ctx: Ctx) -> None:
    """Why the Overview page's margin and the pack's gross margin differ.

    Both are called a margin and they disagree by about five points. The split
    across two pages makes this *more* important to spell out, not less: a
    reader who carries the Overview's headline here has no way to see which of
    the two is the odd one out."""
    totals = erp.load_company_totals(s["year"])
    if totals.empty:
        return
    cut = totals[totals["month"] <= s["period_end"]]
    rev, mat = float(cut["revenue_dt"].sum()), float(cut["material_cogs_dt"].sum())
    if rev <= 0:
        return
    erp_pct = (rev - mat) / rev * 100

    with st.expander(ctx.t("Why this margin differs from the one on the Overview page")):
        st.markdown(ctx.tf(
            "Over the same {months} months, company-wide:\n\n"
            "| | {ccy} | % of revenue |\n|---|---:|---:|\n"
            "| Revenue — ERP | {erp_rev} | |\n"
            "| Revenue — pack | {pack_rev} | |\n"
            "| Margin over material cost (ERP `facture_det.mat`) | {erp_margin} | {erp_pct} |\n"
            "| Gross margin (pack) | {pack_margin} | {pack_pct} |\n",
            months=s["months"], ccy=ctx.ccy,
            erp_rev=f"{rev / ctx.rate:,.0f}", pack_rev=f"{s['revenue'] / ctx.rate:,.0f}",
            erp_margin=f"{(rev - mat) / ctx.rate:,.0f}", erp_pct=f"{erp_pct:.1f}%",
            pack_margin=f"{s['gross_margin'] / ctx.rate:,.0f}",
            pack_pct=f"{s['gross_margin_pct']:.1f}%"))
        st.markdown(ctx.t(
            "**Revenue agrees.** The margins don't, because they are different "
            "measures rather than two attempts at the same one:\n\n"
            "- The ERP figure is **standard material cost × units sold** — what "
            "`facture_det.mat` holds for each invoice line. It carries no "
            "packaging, no paint and no inbound freight.\n"
            "- The pack's is **what was actually bought, adjusted for stock "
            "movement**, and it does carry those. Over a short window the two "
            "also drift on timing alone: what was purchased in a month is not "
            "what was sold in it.\n\n"
            "Use the ERP margin to compare models with each other — it is "
            "per-unit and consistent. Use the pack for what the company earned."))
