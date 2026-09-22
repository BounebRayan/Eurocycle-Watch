"""The margin bridge — why the gross-margin line moved between two years.

Split out of `overview.py` because it now has two readers at two altitudes:
the **Overview** page shows the waterfall (what moved), and **Commercial ›
Models** shows the per-model drill (which models moved it). Both go through
`selectors()` + `compute()`, so the two pages can never disagree about the
arithmetic — only about how much of it they draw.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import FONT, style_fig
from i18n import N_
from ._common import Ctx, sym, money, compact, to_disp, trim_to_date

# The six effects, in the order the waterfall walks them. Volume and mix come
# first because they explain "we sold a different amount of different things",
# then price and cost explain "the same thing earned differently".
BRIDGE_STEPS = ("volume", "mix", "price", "cost", "new", "dropped")

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


def _trim_to_common_period(df: pd.DataFrame, ctx: Ctx, y_prev: int, y_cur: int) -> pd.DataFrame:
    """Cut both compared years to the same elapsed period when the later one is
    still running.

    `_common.like_for_like` does this only for the newest year and its
    predecessor; the bridge lets the reader pick any pair, so an 8-month 2026
    could land against a full 2023 and the whole gap would land in `volume`.

    Cuts on the data's own day, not on the month — see `like_for_like` for why a
    whole-month cut still measures a part month against a full one."""
    if ctx.partial_year not in (y_prev, y_cur):
        return df
    affected = df["yr"].isin([y_prev, y_cur])
    return df[~affected | trim_to_date(df, ctx)]


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


# ------------------------------------------------------------- the sections ---
@dataclass
class Bridge:
    """One computed comparison — everything either section needs to draw."""
    y_prev: int
    y_cur: int
    effects: dict
    per_model: pd.DataFrame
    m0: float               # gross margin in the earlier year
    m1: float               # ... and in the later one
    r0: float               # revenue, for the "% of revenue" reading
    r1: float
    trimmed: bool           # both years cut to the same elapsed period


def selectors(scope: pd.DataFrame, ctx: Ctx, *, key_prefix: str) -> tuple[int, int, str] | None:
    """From / To / Compare-by. None when the range holds fewer than two years.

    `key_prefix` keeps the Overview's and Models' selectors apart. Element ids
    already differ by the page's script hash, so this is documentation more
    than necessity — but it says plainly that these are two sets of controls,
    and leaves room to persist one of them later."""
    yrs = sorted(int(y) for y in scope["yr"].unique())
    if len(yrs) < 2:
        st.caption(ctx.t("Needs at least two years in the sidebar's range."))
        return None

    c1, c2, c3 = st.columns([1, 1, 2])
    y_cur = int(c2.selectbox(ctx.t("To"), yrs, index=len(yrs) - 1, key=f"{key_prefix}_to"))
    earlier = [y for y in yrs if y < y_cur] or [yrs[0]]
    y_prev = int(c1.selectbox(ctx.t("From"), earlier, index=len(earlier) - 1,
                              key=f"{key_prefix}_from"))
    grain = ("design_key" if c3.radio(ctx.t("Compare by"), ["Design", "Article"],
                                      horizontal=True, format_func=ctx.t,
                                      key=f"{key_prefix}_grain",
                                      help=ctx.t(_GRAIN_HELP)) == "Design" else "article")
    return y_prev, y_cur, grain


def compute(scope: pd.DataFrame, ctx: Ctx, y_prev: int, y_cur: int, grain: str) -> Bridge | None:
    """The arithmetic, with no rendering. None when either year is empty."""
    df = _trim_to_common_period(scope, ctx, y_prev, y_cur)
    prev = _per_unit(df[df["yr"] == y_prev], grain)
    cur = _per_unit(df[df["yr"] == y_cur], grain)
    if prev.empty or cur.empty:
        return None

    effects, per_model = margin_bridge(prev, cur)
    return Bridge(
        y_prev=y_prev, y_cur=y_cur, effects=effects, per_model=per_model,
        m0=float((prev["units"] * prev["unit_margin"]).sum()),
        m1=float((cur["units"] * cur["unit_margin"]).sum()),
        r0=float(prev["revenue"].sum()), r1=float(cur["revenue"].sum()),
        trimmed=ctx.partial_year in (y_prev, y_cur))


def _prepare(scope: pd.DataFrame, ctx: Ctx, *, key_prefix: str) -> Bridge | None:
    picked = selectors(scope, ctx, key_prefix=key_prefix)
    if picked is None:
        return None
    b = compute(scope, ctx, *picked)
    if b is None:
        st.caption(ctx.t("One of the two years has no rows after filtering."))
    return b


def waterfall_section(scope: pd.DataFrame, ctx: Ctx) -> None:
    """What moved — the Overview page's copy of the bridge."""
    st.subheader(ctx.t("Why margin moved"))
    b = _prepare(scope, ctx, key_prefix="mgmt_bridge_ov")
    if b is None:
        return

    period = (f"1 January–{ctx.data_end:%d %B}" if b.trimmed else "full year")
    st.caption(
        f"Gross margin went from {money(b.m0, ctx)} to {money(b.m1, ctx)} "
        f"({_pct_str(b.m0, b.r0)} → {_pct_str(b.m1, b.r1)} of revenue), a change of "
        f"**{money(b.m1 - b.m0, ctx)}**. The six bars below account for that change exactly — "
        f"they sum to it to the cent. Both years are {period}."
        + (" Cut to the same day in both years, so neither a part year nor a part "
           "month is read against a full one." if b.trimmed else "")
    )
    _bridge_waterfall(b, ctx)


def drill_section(scope: pd.DataFrame, ctx: Ctx) -> None:
    """Which models moved it — the Models tab's copy, table only.

    The waterfall itself stays on the Overview page. Someone reading Models has
    already been told *what* moved and is here for the names."""
    st.subheader(ctx.t("Which models moved the margin"))
    b = _prepare(scope, ctx, key_prefix="mgmt_bridge_models")
    if b is None:
        return
    st.caption(ctx.tf("Gross margin moved {amount} between {prev} and {cur}. The waterfall that "
                      "splits it into volume, mix, price and cost is on the Overview page.",
                      amount=money(b.m1 - b.m0, ctx), prev=b.y_prev, cur=b.y_cur))
    _bridge_drill(b, ctx)


def _pct_str(margin: float, revenue: float) -> str:
    return f"{margin / revenue * 100:.1f}%" if revenue else "n/a"


def _bridge_waterfall(b: Bridge, ctx: Ctx) -> None:
    P = ctx.P
    labels = [str(b.y_prev)] + [ctx.t(_BRIDGE_LABELS[k]) for k in BRIDGE_STEPS] + [str(b.y_cur)]
    values = [b.m0] + [b.effects[k] for k in BRIDGE_STEPS] + [b.m1]
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


def _bridge_drill(b: Bridge, ctx: Ctx) -> None:
    """Which models drove it. Volume and mix share one column: splitting them
    needs a company-wide average, so the split has no per-model meaning."""
    e = b.effects
    core = e["volume"] + e["mix"] + e["price"] + e["cost"]
    per_model = b.per_model
    with st.expander(ctx.tf("Which models moved it — {amount} across {n} sold in both years",
                            amount=money(core, ctx), n=f"{len(per_model):,}"), expanded=True):
        st.caption(ctx.t("Every model sold in both years, and what it contributed. The three effect "
            "columns sum to the four middle bars of the waterfall; new and dropped models "
            "aren't here because they have no other year to compare against."))
        which = st.radio(ctx.t("Show"), ["Biggest drags", "Biggest gains", "All"],
                         horizontal=True, format_func=ctx.t, label_visibility="collapsed",
                         key="mgmt_bridge_drill_show")
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
