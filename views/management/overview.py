"""Overview tab — the company scoreboard: headline KPIs with YoY, revenue &
margin by year, revenue by distributor, and the margin bridge that explains why
the margin line moved. Produced-vs-invoiced moved to the Production tab."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import erp
import finance_pack
from theme import FONT, style_fig, hbar_categories
from i18n import N_
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
    st.subheader(ctx.t("Company scoreboard") + f" — {ctx.dist_label}")
    ya = _year_agg(scope)                                   # full period: tile values + chart
    lfl = _year_agg(like_for_like(scope, ctx)).set_index("yr")   # trimmed: deltas only
    cur = ya.iloc[-1]
    y_cur = int(cur["yr"])
    y_prev = int(ya.iloc[-2]["yr"]) if len(ya) > 1 else None

    def d(col, *, pct=False):
        """YoY on the like-for-like frame, so a part year isn't read against a full one."""
        if y_prev is None or y_cur not in lfl.index or y_prev not in lfl.index:
            return None
        return yoy(lfl.at[y_cur, col], lfl.at[y_prev, col], pct=pct, ctx=ctx)

    is_partial = ctx.partial_year == y_cur

    c1, c2, c3, c4, c5 = st.columns(5)
    ytd = " (YTD)" if is_partial else ""
    c1.metric(ctx.t("Revenue") + f" {y_cur}{ytd}", tile(cur["revenue"], ctx),
              delta=d("revenue"), border=True, height=TILE_H,
              help=ctx.tf("{amount} — invoice line totals (qte·prx), converted to the "
                          "selected currency.", amount=money(cur["revenue"], ctx))
                   + (" " + ctx.t("Year to date.") if is_partial else ""))
    c2.metric(ctx.t("Margin over build cost"), tile(cur["margin"], ctx),
              delta=d("margin"), border=True, height=TILE_H,
              help=ctx.tf("{amount} — revenue − material cost (`facture_det.mat`). This is "
                          "not the accounting gross margin: it carries no packaging, paint "
                          "or inbound freight. See Profitability below.",
                          amount=money(cur["margin"], ctx)))
    c3.metric(ctx.t("Margin %"), f"{cur['margin_pct']:.1f}%",
              delta=d("margin_pct", pct=True),
              border=True, height=TILE_H, help=ctx.t("Company target is 25% (margeProduitFini)."))
    c4.metric(ctx.t("Units invoiced"), f"{cur['units']:,.0f}",
              delta=d("units"), border=True, height=TILE_H)
    c5.metric(ctx.t("Avg selling price"), money(cur["asp"], ctx, dp=1),
              delta=d("asp"), border=True, height=TILE_H, help=ctx.t("Revenue ÷ units."))
    note = partial_year_note(ctx)
    if note:
        st.caption(note)

    _profitability(ctx)

    st.divider()
    left, right = st.columns([3, 2])

    with left:
        st.subheader(ctx.t("Revenue & margin by year"))
        rev_disp = to_disp(ya["revenue"], ctx)
        mrg_disp = to_disp(ya["margin"], ctx)
        fig = go.Figure()
        fig.add_bar(x=ya["yr"], y=rev_disp, name=ctx.t("Revenue"),
                    marker=dict(color=P["series"][0], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>revenue " + sym(ctx.ccy) +
                                  "%{y:,.0f}<extra></extra>")
        fig.add_bar(x=ya["yr"], y=mrg_disp, name=ctx.t("Gross margin"),
                    marker=dict(color=P["series"][2], line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>margin " + sym(ctx.ccy) +
                                  "%{y:,.0f}<extra></extra>")
        fig.add_trace(go.Scatter(
            x=ya["yr"], y=ya["margin_pct"], name=ctx.t("Margin %"), yaxis="y2",
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

    _margin_bridge_section(scope, ctx)


# --------------------------------------------------------- profitability ---
# Revenue and the two income sections aside, every section is a cost. The order
# is the order the P&L walks them, which is also the order the waterfall reads.
_WALK = ("Cost of sales", "Non-stocked purchases", "External services",
         "Other external charges", "Taxes & duties", "Salaries & charges",
         "Depreciation", "Financial charges")


def _profitability(ctx: Ctx) -> None:
    """Revenue down to net profit, from the accounting pack.

    This sits directly under the KPI strip on purpose. The strip's margin is
    revenue less *material* cost, which is the right measure for comparing
    models against each other and the wrong one for "what did the company
    make" — it ignores every cost below the material line. Without this block
    the reader's only headline is a 30-odd-percent margin on a business whose
    net is single digits."""
    years = finance_pack.available_years()
    if not years:
        return
    year = max(years)
    s = finance_pack.summary(year)
    if not s:
        return

    st.divider()
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
    """Why the KPI strip's margin and the pack's gross margin differ.

    Both are on the page, both are called a margin, and they disagree by about
    five points. Left unexplained that reads as one of them being wrong."""
    totals = erp.load_company_totals(s["year"])
    if totals.empty:
        return
    cut = totals[totals["month"] <= s["period_end"]]
    rev, mat = float(cut["revenue_dt"].sum()), float(cut["material_cogs_dt"].sum())
    if rev <= 0:
        return
    erp_pct = (rev - mat) / rev * 100

    with st.expander(ctx.t("Why this margin differs from the one in the KPI strip")):
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


# ------------------------------------------------------- the margin bridge ---
# The four effects, in the order the waterfall walks them. Volume and mix come
# first because they explain "we sold a different amount of different things",
# then price and cost explain "the same thing earned differently".
BRIDGE_STEPS = ("volume", "mix", "price", "cost", "new", "dropped")


def _per_unit(df: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Units, revenue and COGS per unit of comparison, plus the per-unit figures
    the bridge works in. Rows with no units are dropped — they have no price.

    `grain` is `design_key` or `article`; see `_GRAIN_HELP` for why the default
    is the design and not the code."""
    g = df.groupby(grain).agg(
        model=("libnach", "last"), brand=("brandnach", "last"),
        units=("qte", "sum"), revenue=("line_rev_dt", "sum"), cogs=("line_cogs_dt", "sum"))
    g = g[g["units"] > 0]
    g["price"] = g["revenue"] / g["units"]
    g["cost"] = g["cogs"] / g["units"]
    g["unit_margin"] = g["price"] - g["cost"]
    return g


def _trim_to_common_months(df: pd.DataFrame, ctx: Ctx, y_prev: int, y_cur: int) -> pd.DataFrame:
    """Cut both compared years to the same elapsed months when the later one is
    still running.

    `_common.like_for_like` does this only for the newest year and its
    predecessor; the bridge lets the reader pick any pair, so an 8-month 2026
    could land against a full 2023 and the whole gap would land in `volume`."""
    if ctx.partial_year not in (y_prev, y_cur):
        return df
    affected = df["yr"].isin([y_prev, y_cur])
    return df[~affected | (df["mo"] <= ctx.ytd_month)]


def margin_bridge(prev: pd.DataFrame, cur: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Decompose the change in gross margin between two `_per_unit` frames.

    Returns `(effects, per_model)`. The decomposition is an **exact identity** —
    the six effects sum to the change in margin to the cent, which is what makes
    it auditable rather than a story:

        volume   (Q1 − Q0) · m̄0          sold more or fewer bikes overall
        mix      Σ q1·m0 − Q1·m̄0         sold a different blend of them
        price    Σ q1·(p1 − p0)          same bikes, different selling price
        cost     −Σ q1·(c1 − c0)         same bikes, different build cost
        new      Σ q1·m1  over arrivals  models that weren't sold last year
        dropped  −Σ q0·m0 over leavers   models that stopped selling

    Volume, mix, price and cost are computed over the models sold in **both**
    years; arrivals and leavers get their own buckets rather than being smeared
    across the others, because "we launched a model" is a different fact from
    "we raised a price".

    Nothing is filtered beyond `units > 0`. A model given away free shows a
    large negative price effect, which is true and belongs in the table — drop
    it and the total stops reconciling.

    `per_model` attributes what can honestly be attributed: price and cost per
    model, and volume+mix together (their split needs a company-wide average,
    so it has no per-model meaning). Its three columns sum to the same total."""
    common = prev.index.intersection(cur.index)
    p0, p1 = prev.loc[common], cur.loc[common]

    q0, q1 = p0["units"], p1["units"]
    Q0, Q1 = float(q0.sum()), float(q1.sum())
    M0 = float((q0 * p0["unit_margin"]).sum())
    mbar0 = M0 / Q0 if Q0 else 0.0

    price = float((q1 * (p1["price"] - p0["price"])).sum())
    cost = float(-(q1 * (p1["cost"] - p0["cost"])).sum())
    volume = (Q1 - Q0) * mbar0
    mix = float((q1 * p0["unit_margin"]).sum()) - Q1 * mbar0

    arrivals = cur.index.difference(prev.index)
    leavers = prev.index.difference(cur.index)
    new = float((cur.loc[arrivals, "units"] * cur.loc[arrivals, "unit_margin"]).sum())
    dropped = float(-(prev.loc[leavers, "units"] * prev.loc[leavers, "unit_margin"]).sum())

    effects = {"volume": volume, "mix": mix, "price": price, "cost": cost,
               "new": new, "dropped": dropped}

    per_model = pd.DataFrame({
        "model": p1["model"], "brand": p1["brand"],
        "units_prev": q0, "units": q1,
        "price_effect": q1 * (p1["price"] - p0["price"]),
        "cost_effect": -(q1 * (p1["cost"] - p0["cost"])),
        "volmix_effect": (q1 - q0) * p0["unit_margin"],
    })
    per_model["total_effect"] = (per_model["price_effect"] + per_model["cost_effect"]
                                 + per_model["volmix_effect"])
    return effects, per_model.sort_values("total_effect")


# `N_` again: module-level, so it is translated where it renders.
_GRAIN_HELP = N_(
    "**Design** strips the season digits out of the article code, so the 2025 and "
    "2026 issues of the same bike are one row. **Article** takes the code as it is. "
    "Compare by article and the annual re-coding of the range shows up as models "
    "dropped and replaced: 59% of a year's margin sits on articles that also sold "
    "the year before, against 72-80% by design."
)

# Bucket → the reading. Volume and mix describe *what* was sold, price and cost
# what the same thing earned, arrivals and leavers the range itself.
# `N_` marks these for translation without translating them here — this runs at
# import time, before any Ctx exists, so `ctx.t()` is applied where they render.
_BRIDGE_LABELS = {
    "volume": N_("Volume"),
    "mix": N_("Mix"),
    "price": N_("Price"),
    "cost": N_("Cost"),
    "new": N_("New models"),
    "dropped": N_("Models dropped"),
}


def _margin_bridge_section(scope: pd.DataFrame, ctx: Ctx) -> None:
    st.divider()
    st.subheader(ctx.t("Why margin moved"))

    yrs = sorted(int(y) for y in scope["yr"].unique())
    if len(yrs) < 2:
        st.caption(ctx.t("Needs at least two years in the sidebar's range."))
        return

    c1, c2, c3 = st.columns([1, 1, 2])
    y_cur = int(c2.selectbox(ctx.t("To"), yrs, index=len(yrs) - 1))
    earlier = [y for y in yrs if y < y_cur] or [yrs[0]]
    y_prev = int(c1.selectbox(ctx.t("From"), earlier, index=len(earlier) - 1))
    grain = ("design_key" if c3.radio(ctx.t("Compare by"), ["Design", "Article"],
                                      horizontal=True, format_func=ctx.t,
                                      help=ctx.t(_GRAIN_HELP)) == "Design" else "article")

    df = _trim_to_common_months(scope, ctx, y_prev, y_cur)
    prev = _per_unit(df[df["yr"] == y_prev], grain)
    cur = _per_unit(df[df["yr"] == y_cur], grain)
    if prev.empty or cur.empty:
        st.caption(ctx.t("One of the two years has no rows after filtering."))
        return

    effects, per_model = margin_bridge(prev, cur)
    m0 = float((prev["units"] * prev["unit_margin"]).sum())
    m1 = float((cur["units"] * cur["unit_margin"]).sum())
    r0, r1 = float(prev["revenue"].sum()), float(cur["revenue"].sum())

    trimmed = ctx.partial_year in (y_prev, y_cur)
    period = f"January-{ctx.data_end:%B}" if trimmed else "full year"
    st.caption(
        f"Gross margin went from {money(m0, ctx)} to {money(m1, ctx)} "
        f"({_pct_str(m0, r0)} → {_pct_str(m1, r1)} of revenue), a change of "
        f"**{money(m1 - m0, ctx)}**. The six bars below account for that change exactly — "
        f"they sum to it to the cent. Both years are {period}."
        + (" Trimmed to the same months, so a part year isn't read against a full one."
           if trimmed else "")
    )

    _bridge_waterfall(effects, m0, m1, ctx, y_prev, y_cur)
    _bridge_drill(per_model, effects, ctx)


def _pct_str(margin: float, revenue: float) -> str:
    return f"{margin / revenue * 100:.1f}%" if revenue else "n/a"


def _bridge_waterfall(effects: dict, m0: float, m1: float, ctx: Ctx,
                      y_prev: int, y_cur: int) -> None:
    P = ctx.P
    labels = [str(y_prev)] + [ctx.t(_BRIDGE_LABELS[k]) for k in BRIDGE_STEPS] + [str(y_cur)]
    values = [m0] + [effects[k] for k in BRIDGE_STEPS] + [m1]
    measures = ["absolute"] + ["relative"] * len(BRIDGE_STEPS) + ["total"]
    disp = [to_disp(v, ctx) for v in values]

    fig = go.Figure(go.Waterfall(
        orientation="v", measure=measures, x=labels, y=disp,
        # 2px surface ring so neighbouring blocks read as separate marks.
        increasing=dict(marker=dict(color=P["pos"], line=dict(color=P["surface"], width=2))),
        decreasing=dict(marker=dict(color=P["neg"], line=dict(color=P["surface"], width=2))),
        totals=dict(marker=dict(color=P["series"][0], line=dict(color=P["surface"], width=2))),
        connector=dict(line=dict(color=P["axis"], width=1, dash="dot")),
        text=[compact(v, ctx.ccy) for v in disp], textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        hovertemplate="<b>%{x}</b><br>" + sym(ctx.ccy) + "%{y:,.0f}<extra></extra>"))
    style_fig(fig, height=400, xgrid=False, ygrid=True)
    fig.update_yaxes(title_text=ctx.tf("Gross margin ({ccy})", ccy=ctx.ccy), tickformat="~s")
    fig.update_layout(margin=dict(l=4, r=4, t=32, b=4))
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption(ctx.t("**Volume** is selling more or fewer bikes overall; **mix** is selling a different "
        "blend of them at last year's margins; **price** and **cost** are the same bikes "
        "earning differently; **new** and **dropped** are the range itself changing. Price "
        "and cost are the two anyone can act on directly."))


def _bridge_drill(per_model: pd.DataFrame, effects: dict, ctx: Ctx) -> None:
    """Which models drove it. Volume and mix share one column: splitting them
    needs a company-wide average, so the split has no per-model meaning."""
    core = effects["volume"] + effects["mix"] + effects["price"] + effects["cost"]
    with st.expander(ctx.tf("Which models moved it — {amount} across {n} sold in both years",
                            amount=money(core, ctx), n=f"{len(per_model):,}")):
        st.caption(ctx.t("Every model sold in both years, and what it contributed. The three effect "
            "columns sum to the four middle bars of the waterfall; new and dropped models "
            "aren't here because they have no other year to compare against."))
        which = st.radio(ctx.t("Show"), ["Biggest drags", "Biggest gains", "All"],
                         horizontal=True, format_func=ctx.t, label_visibility="collapsed")
        t = per_model.reset_index(drop=True)
        if which == "Biggest drags":
            t = t.nsmallest(25, "total_effect")
        elif which == "Biggest gains":
            t = t.nlargest(25, "total_effect")
        else:
            t = t.sort_values("total_effect")
        for c in ("price_effect", "cost_effect", "volmix_effect", "total_effect"):
            t[c] = to_disp(t[c], ctx)
        st.dataframe(
            t[["model", "brand", "units_prev", "units", "price_effect", "cost_effect",
               "volmix_effect", "total_effect"]],
            hide_index=True, width="stretch",
            column_config={
                "model": "Model", "brand": "Brand",
                "units_prev": st.column_config.NumberColumn("Units before", format="%d"),
                "units": st.column_config.NumberColumn("Units after", format="%d"),
                "price_effect": st.column_config.NumberColumn(f"Price ({ctx.ccy})",
                                                              format="%.0f"),
                "cost_effect": st.column_config.NumberColumn(f"Cost ({ctx.ccy})", format="%.0f"),
                "volmix_effect": st.column_config.NumberColumn(f"Volume + mix ({ctx.ccy})",
                                                               format="%.0f"),
                "total_effect": st.column_config.NumberColumn(f"Total ({ctx.ccy})",
                                                              format="%.0f"),
            })
