"""Models tab — best / worst by revenue, margin or margin %; per-unit economics
(what a bike costs to build vs what it sells for); planned-vs-realised cost from
the costing sheet; margin drift YoY; revenue-vs-margin scatter; full table."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import FONT, style_fig, hbar_categories
from ._common import Ctx, sym, to_disp, compact, like_for_like, partial_year_note
from . import model_detail


def _per_model(scope: pd.DataFrame) -> pd.DataFrame:
    pm = scope.groupby("article").agg(
        model=("model_label", "first"),
        brand=("brandnach", "first"),
        distributor=("distributor", "first"),
        ebike=("ebike", "max"),
        revenue=("line_rev_dt", "sum"),
        cogs=("line_cogs_dt", "sum"),
        units=("qte", "sum"),
    )
    pm["margin"] = pm["revenue"] - pm["cogs"]
    pm["margin_pct"] = np.where(pm["revenue"] > 0, pm["margin"] / pm["revenue"] * 100, np.nan)
    pm["asp"] = np.where(pm["units"] > 0, pm["revenue"] / pm["units"], np.nan)
    pm["cost_per_unit"] = np.where(pm["units"] > 0, pm["cogs"] / pm["units"], np.nan)
    pm["margin_per_unit"] = np.where(pm["units"] > 0, pm["margin"] / pm["units"], np.nan)
    return pm[pm["units"] > 0]


def render(scope: pd.DataFrame, ctx: Ctx) -> None:
    P = ctx.P
    st.subheader(ctx.t("Model performance") + f" — {ctx.dist_label}, {ctx.yr_lo}–{ctx.yr_hi}")

    per_model = _per_model(scope)

    c1, c2, c3 = st.columns(3)
    # Values stay English — they key the `key` lookup below. Only the display
    # of each option is translated, via format_func.
    metric = c1.selectbox(ctx.t("Rank by"), ["Gross margin", "Margin %", "Margin per bike",
                                             "Revenue", "Units"], format_func=ctx.t)
    min_units = c2.number_input(ctx.t("Min units (period)"), 0, 100000, 50, step=10,
                                help=ctx.t("Filters out one-off / sample lines before ranking."))
    topn = c3.slider(ctx.t("Show N"), 5, 30, 12)

    ranked = per_model[per_model["units"] >= min_units].copy()
    key = {"Revenue": "revenue", "Gross margin": "margin", "Margin %": "margin_pct",
           "Margin per bike": "margin_per_unit", "Units": "units"}[metric]
    is_pct = key == "margin_pct"
    per_unit = key == "margin_per_unit"

    if ranked.empty:
        st.info(ctx.t("No models clear the minimum-units filter."))
        return

    best = ranked.sort_values(key, ascending=False).head(topn)
    worst = ranked.sort_values(key, ascending=True).head(topn)

    def bar(df, title, ascending):
        df = df.sort_values(key, ascending=ascending)
        if is_pct:
            xs = df["margin_pct"]
            txt = [f"{v:.1f}%" for v in xs]
            colours = [P["neg"] if v < 0 else P["series"][0] for v in xs]
        elif key == "units":
            xs = df["units"]
            txt = [f"{v:,.0f}" for v in xs]
            colours = P["series"][0]
        elif per_unit:
            xs = to_disp(df["margin_per_unit"], ctx)
            txt = [f"{sym(ctx.ccy)}{v:,.0f}" for v in xs]
            colours = [P["neg"] if v < 0 else P["series"][0] for v in xs]
        else:
            xs = to_disp(df[key], ctx)
            txt = [f"{sym(ctx.ccy)}{v:,.0f}" for v in xs]
            colours = [P["neg"] if v < 0 else P["series"][0] for v in xs]
        fig = go.Figure()
        labels = (df["model"].str.slice(0, 42) + "  ·  " + df["brand"].str.slice(0, 14)).str.strip(" ·")
        fig.add_bar(x=xs, y=hbar_categories(fig, labels), orientation="h",
                    marker=dict(color=colours, line=dict(width=0)),
                    text=txt, textposition="outside",
                    textfont=dict(color=P["text_secondary"], size=11, family=FONT),
                    customdata=df[["units", "margin_pct", "distributor", "margin_per_unit"]].to_numpy(),
                    hovertemplate="<b>%{y}</b><br>%{customdata[2]}<br>"
                                  "%{customdata[0]:,.0f} units · "
                                  "%{customdata[1]:.1f}% margin<extra></extra>",
                    cliponaxis=False)
        style_fig(fig, height=max(240, 26 * len(df) + 50), xgrid=True, ygrid=False)
        fig.update_layout(margin=dict(l=4, r=76, t=4, b=4), bargap=0.35)
        if is_pct:
            fig.add_vline(x=25, line_width=1, line_dash="dot", line_color=P["axis"])
        st.markdown(f"**{title}**")
        st.plotly_chart(fig, width="stretch", theme=None)

    left, right = st.columns(2)
    with left:
        bar(best, ctx.tf("Best {n} by {metric}", n=topn, metric=ctx.t(metric).lower()),
            ascending=True)
    with right:
        bar(worst, ctx.tf("Weakest {n} by {metric}", n=topn, metric=ctx.t(metric).lower()),
            ascending=False)
    if is_pct:
        st.caption(ctx.t("Dotted line = 25% company target. Red bars are loss-making models."))
    elif per_unit:
        st.caption(ctx.t("Absolute margin per bike (sale price − build cost), in the selected currency. "
                   "Red = sold below cost."))

    _by_wheel_size(scope, ctx)
    _per_unit_scatter(ranked, ctx)
    _margin_drift(scope, ctx, min_units)
    _rev_vs_margin(ranked, ctx)
    _model_picker(per_model, scope, ctx)
    _full_table(per_model, ctx)


# ---------------------------------------------------------- by wheel size ---
def _by_wheel_size(scope: pd.DataFrame, ctx: Ctx) -> None:
    """Revenue / units / margin per wheel size, newest year against the prior.

    This is the cut the ERP's own management report leads with (Classeur3 §01
    and §02). `wheel_size` is `nomachat.wheelnach` resolved through the
    `Wheelsize` table, so the buckets are the ERP's own, not parsed out of a
    model name."""
    st.divider()
    st.subheader(ctx.t("Revenue by wheel size"))
    yrs = sorted(scope["yr"].unique())
    if not yrs:
        return
    y_cur = yrs[-1]
    y_prev = yrs[-2] if len(yrs) > 1 else None
    lfl = like_for_like(scope, ctx)
    st.caption(ctx.tf("{cur} vs {prev}. Bars are revenue; the label is the year-on-year "
                      "change in units.", cur=y_cur, prev=y_prev) if y_prev else
               ctx.tf("{cur}. Bars are revenue.", cur=y_cur))

    def agg(df, yr):
        g = df[df["yr"] == yr].groupby("wheel_size").agg(
            revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"),
            units=("qte", "sum"))
        return g

    cur, prev = agg(lfl, y_cur), (agg(lfl, y_prev) if y_prev else None)
    if cur.empty:
        st.caption(ctx.t("No rows in the newest year."))
        return
    cur = cur.sort_values("revenue")
    cur["rev_disp"] = to_disp(cur["revenue"], ctx)
    cur["mpct"] = np.where(cur["revenue"] > 0, cur["margin"] / cur["revenue"] * 100, np.nan)
    if prev is not None:
        pu = prev["units"].reindex(cur.index)
        cur["du"] = np.where(pu > 0, (cur["units"] - pu) / pu * 100, np.nan)
    else:
        cur["du"] = np.nan

    P = ctx.P
    fig = go.Figure()
    fig.add_bar(
        x=cur["rev_disp"], y=hbar_categories(fig, cur.index), orientation="h",
        marker=dict(color=P["series"][0], line=dict(width=0)),
        text=[f"{compact(v, ctx.ccy)}   {d:+.0f}%" if pd.notna(d) else compact(v, ctx.ccy)
              for v, d in zip(cur["rev_disp"], cur["du"])],
        textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        customdata=cur[["units", "mpct"]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>" + sym(ctx.ccy) + "%{x:,.0f}<br>"
                      "%{customdata[0]:,.0f} units · %{customdata[1]:.1f}% margin<extra></extra>",
        cliponaxis=False)
    style_fig(fig, height=max(260, 28 * len(cur) + 50), xgrid=True, ygrid=False)
    fig.update_xaxes(tickformat="~s")
    fig.update_layout(margin=dict(l=4, r=96, t=4, b=4), bargap=0.35)
    st.plotly_chart(fig, width="stretch", theme=None)
    note = partial_year_note(ctx)
    if note:
        st.caption(note)


# ------------------------------------------------------------ model detail ---
def _model_picker(per_model: pd.DataFrame, scope: pd.DataFrame, ctx: Ctx) -> None:
    st.divider()
    st.subheader(ctx.t("Model detail"))
    st.caption(ctx.t("Pick a model for its full profile — photo, identity, economics, "
               "year-by-year history, its family across seasons, and production."))
    opts = per_model.sort_values("revenue", ascending=False)
    labels = [f"{r.model} · {r.brand} — {article}" for article, r in opts.iterrows()]
    label_to_article = dict(zip(labels, opts.index))
    c1, c2 = st.columns([4, 1])
    choice = c1.selectbox(ctx.t("Model"), labels, index=None,
                          placeholder=ctx.t("Search by name, brand or code…"), label_visibility="collapsed")
    if c2.button(ctx.t("View details"), disabled=choice is None, width="stretch"):
        model_detail.show(label_to_article[choice], scope, per_model, ctx)


# --------------------------------------------------- per-unit economics ---
def _per_unit_scatter(ranked: pd.DataFrame, ctx: Ctx) -> None:
    st.divider()
    st.subheader(ctx.t("Cost vs sale price per bike"))
    st.caption(ctx.t("Each dot is a model. Distance above the diagonal is the margin per bike; "
               "bubble size = units. Below the diagonal = sold under build cost."))
    P = ctx.P
    d = ranked.dropna(subset=["cost_per_unit", "asp"]).copy()
    d["cost_disp"] = to_disp(d["cost_per_unit"], ctx)
    d["asp_disp"] = to_disp(d["asp"], ctx)
    sizes = np.sqrt(d["units"].clip(lower=1))
    lim = float(np.nanpercentile(d[["cost_disp", "asp_disp"]].to_numpy(), 99)) * 1.05
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, lim], y=[0, lim], mode="lines",
                             line=dict(color=P["axis"], width=1, dash="dot"),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=d["cost_disp"], y=d["asp_disp"], mode="markers",
        marker=dict(color=[P["neg"] if m < 0 else P["series"][0] for m in d["margin_per_unit"]],
                    size=sizes, sizemode="area", sizeref=2.0 * sizes.max() / (32 ** 2),
                    sizemin=4, line=dict(color=P["surface"], width=1)),
        customdata=d[["model", "brand", "units", "margin_pct"]].to_numpy(),
        hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                      "build " + sym(ctx.ccy) + "%{x:,.0f} → sell " + sym(ctx.ccy) + "%{y:,.0f}"
                      "<br>%{customdata[3]:.1f}% · %{customdata[2]:,.0f} units<extra></extra>"))
    style_fig(fig, height=420, xgrid=True, ygrid=True)
    fig.update_xaxes(title_text=ctx.tf("Build cost per bike ({ccy})", ccy=ctx.ccy), range=[0, lim])
    fig.update_yaxes(title_text=ctx.tf("Sale price per bike ({ccy})", ccy=ctx.ccy), range=[0, lim])
    st.plotly_chart(fig, width="stretch", theme=None)


# --------------------------------------------------- margin drift (YoY) ---
def _margin_drift(scope: pd.DataFrame, ctx: Ctx, min_units: int) -> None:
    st.divider()
    st.subheader(ctx.t("Margin gain / erosion — latest full year vs prior"))
    yrs = sorted(scope["yr"].unique())
    if len(yrs) < 2:
        st.caption(ctx.t("Need at least two years in range."))
        return
    y_cur, y_prev = yrs[-1], yrs[-2]
    by_year = (scope[scope["yr"].isin([y_prev, y_cur])]
               .groupby(["article", "yr"])
               .agg(model=("model_label", "first"), brand=("brandnach", "first"),
                    revenue=("line_rev_dt", "sum"), margin=("line_margin_dt", "sum"),
                    units=("qte", "sum")))
    by_year["mpct"] = np.where(by_year["revenue"] > 0, by_year["margin"] / by_year["revenue"] * 100, np.nan)
    piv = by_year["mpct"].unstack("yr").dropna()
    u_cur = by_year["units"].unstack("yr").reindex(piv.index)
    piv = piv[(u_cur[y_prev] >= min_units) & (u_cur[y_cur] >= min_units)]
    if piv.empty:
        st.caption(ctx.t("No models meet the both-years / min-units threshold."))
        return
    piv["delta"] = piv[y_cur] - piv[y_prev]
    meta = by_year.xs(y_cur, level="yr")[["model", "brand"]].reindex(piv.index)
    piv = piv.join(meta)
    movers = pd.concat([piv.nlargest(8, "delta"), piv.nsmallest(8, "delta")]).drop_duplicates().sort_values("delta")
    P = ctx.P
    fig = go.Figure()
    labels = (movers["model"].str.slice(0, 40) + "  ·  " + movers["brand"].str.slice(0, 12)).str.strip(" ·")
    fig.add_bar(
        x=movers["delta"], y=hbar_categories(fig, labels), orientation="h",
        marker=dict(color=[P["pos"] if v >= 0 else P["neg"] for v in movers["delta"]], line=dict(width=0)),
        text=[f"{v:+.1f} pp" for v in movers["delta"]], textposition="outside",
        textfont=dict(color=P["text_secondary"], size=11, family=FONT),
        customdata=movers[[y_prev, y_cur]].to_numpy(),
        hovertemplate="<b>%{y}</b><br>" + f"{y_prev}: " + "%{customdata[0]:.1f}%  →  "
                      + f"{y_cur}: " + "%{customdata[1]:.1f}%<extra></extra>",
        cliponaxis=False)
    fig.add_vline(x=0, line_width=1, line_color=P["axis"])
    style_fig(fig, height=max(260, 26 * len(movers) + 50), xgrid=True, ygrid=False)
    fig.update_xaxes(title_text=ctx.tf("Margin-% change, {prev} → {cur} (percentage points)",
                                       prev=y_prev, cur=y_cur))
    fig.update_layout(margin=dict(l=4, r=70, t=4, b=4), bargap=0.35)
    st.plotly_chart(fig, width="stretch", theme=None)
    st.caption(ctx.tf("Models sold in both {prev} and {cur} with ≥ {n} units each year. "
                      "Green = margin improved, red = eroded.",
                      prev=y_prev, cur=y_cur, n=min_units))


# ------------------------------------------------ revenue vs margin % ---
def _rev_vs_margin(ranked: pd.DataFrame, ctx: Ctx) -> None:
    st.divider()
    st.subheader(ctx.t("Revenue vs margin %"))
    st.caption(ctx.t("Top-right is the prize — high revenue and healthy margin. "
               "Bubble size = units. Below the line is under the 25% target."))
    P = ctx.P
    sc = ranked.copy()
    sc["rev_disp"] = to_disp(sc["revenue"], ctx)
    sizes = np.sqrt(sc["units"].clip(lower=1))
    fig = go.Figure(go.Scatter(
        x=sc["rev_disp"], y=sc["margin_pct"], mode="markers",
        marker=dict(color=P["series"][0], size=sizes, sizemode="area",
                    sizeref=2.0 * sizes.max() / (34 ** 2), sizemin=4,
                    line=dict(color=P["surface"], width=1)),
        customdata=sc[["model", "brand", "units"]].to_numpy(),
        hovertemplate="<b>%{customdata[0]}</b> · %{customdata[1]}<br>"
                      + sym(ctx.ccy) + "%{x:,.0f} · %{y:.1f}% · %{customdata[2]:,.0f} units<extra></extra>"))
    fig.add_hline(y=25, line_width=1, line_dash="dot", line_color=P["axis"])
    style_fig(fig, height=420, xgrid=True, ygrid=True)
    fig.update_xaxes(title_text=ctx.tf("Revenue ({ccy})", ccy=ctx.ccy), type="log")
    fig.update_yaxes(title_text=ctx.t("Gross margin %"))
    st.plotly_chart(fig, width="stretch", theme=None)


# ----------------------------------------------------------- full table ---
def _full_table(per_model: pd.DataFrame, ctx: Ctx) -> None:
    st.divider()
    with st.expander(ctx.tf("All {n} models sold in range", n=f"{len(per_model):,}")):
        q = st.text_input(ctx.t("Search model / brand"),
                          placeholder=ctx.t("e.g. MALIBU or APOLLO"))
        tbl = per_model.reset_index()
        if q:
            m = (tbl["model"].str.contains(q, case=False, na=False)
                 | tbl["brand"].str.contains(q, case=False, na=False)
                 | tbl["article"].str.contains(q, case=False, na=False))
            tbl = tbl[m]
        tbl = tbl.sort_values("revenue", ascending=False)
        for src, dst in [("revenue", "rev_disp"), ("margin", "mrg_disp"), ("asp", "asp_disp"),
                         ("cost_per_unit", "cpu_disp"), ("margin_per_unit", "mpu_disp")]:
            tbl[dst] = to_disp(tbl[src], ctx)
        st.dataframe(
            tbl[["article", "model", "brand", "distributor", "units", "rev_disp", "mrg_disp",
                 "margin_pct", "asp_disp", "cpu_disp", "mpu_disp"]],
            hide_index=True, width="stretch",
            column_config={
                "article": "Code", "model": "Model", "brand": "Brand", "distributor": "Distributor",
                "units": st.column_config.NumberColumn("Units", format="%d"),
                "rev_disp": st.column_config.NumberColumn(f"Revenue ({ctx.ccy})", format="%.0f"),
                "mrg_disp": st.column_config.NumberColumn(f"Margin ({ctx.ccy})", format="%.0f"),
                "margin_pct": st.column_config.NumberColumn("Margin %", format="%.1f%%"),
                "asp_disp": st.column_config.NumberColumn(f"ASP ({ctx.ccy})", format="%.1f"),
                "cpu_disp": st.column_config.NumberColumn(f"Cost/bike ({ctx.ccy})", format="%.1f"),
                "mpu_disp": st.column_config.NumberColumn(f"Margin/bike ({ctx.ccy})", format="%.1f"),
            },
        )
