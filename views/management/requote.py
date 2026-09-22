"""Re-quotation tab — what a model would cost to build now, and what to sell it for.

Two GPAO screens, side by side because they answer halves of one question:

- **`frmAnalyseCoutMatNC`** re-prices every model's bill of materials at today's
  component prices and shows the gap against what it was costed at.
- **`frmConsultPrixNC`** reads the stored costing sheet (`costing_nc`) and
  derives the list price from it.

Together they give the dashboard its first defensible sell price — the field it
has been falling back on, `nomachat.prxnach`, misses realised ASP by a median
35 % (findings §4). This one misses by 7 %.

The re-quotation has one defect serious enough to lead with: a component with no
current price is re-quoted at **zero**, which turns a data gap into an apparent
saving and flips the sign of the answer on roughly one model in six. Every
figure on this tab carries the GPAO's reading and the like-for-like one.
See `docs/gpao-parity.md` §6-7.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import gpao_requote as R
from theme import style_fig
from . import _export
from ._common import Ctx, TILE_H, sym, to_disp, compact

TOP_N = 25


def render(ctx: Ctx) -> None:
    st.subheader(ctx.t("Re-quotation") + " — " + ctx.t("cost at today's prices, and the price list"))
    st.caption(ctx.t(
        "The GPAO's \"Analyse coût matière nomenclature\" and \"Consultation prix "
        "nomenclature\". The first re-prices each model's bill of materials at the "
        "current order price of every component; the second reads the stored "
        "costing sheet and derives the list price."))
    st.info(ctx.t(
        "**This tab sets its own year and reads the whole book.** It works from "
        "bills of materials and costing sheets, which carry no bike/part split, "
        "so the sidebar's **Bikes only** and **Years** controls do not apply. "
        "The **Distributor** and currency selectors do."))

    year = _year_picker(ctx)

    try:
        req = R.load_requote(year)
        gap = R.requote_gap(year)
    except Exception as exc:
        st.warning(ctx.tf("Could not re-quote these models: {err}", err=str(exc)[:250]))
        return

    if req.empty:
        st.info(ctx.tf("No models were invoiced in {yr}.", yr=year))
        return

    _headline(req, gap, year, ctx)
    _unquoted_warning(gap, ctx)
    _distribution(req, ctx)
    _model_table(req, ctx)

    st.divider()
    _price_list(year, ctx)

    st.divider()
    _defects(req, gap, ctx)


# ------------------------------------------------------------- controls ---
def _year_picker(ctx: Ctx) -> int:
    end = int(ctx.data_end.year) if ctx.data_end is not None else 2026
    opts = list(range(end, end - 8, -1))
    return int(st.selectbox(
        ctx.t("Invoiced in"), opts, index=0, key="requote_year",
        help=ctx.t("The GPAO scopes this report to models invoiced during the "
                   "selected year. Note its sale price comes from each model's "
                   "most recent invoice ever, not one inside this year — so a "
                   "past year is priced against a later invoice.")))


# ---------------------------------------------------------------- tiles ---
def _headline(req: pd.DataFrame, gap: R.RequoteGap, year: int, ctx: Ctx) -> None:
    cost, new = req["cost_dt"].sum(), req["requote_dt"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(ctx.t("Models re-quoted"), f"{gap.models:,}", border=True, height=TILE_H,
              help=ctx.tf("Live models invoiced during {yr}, over {n:,} BOM lines.",
                          yr=year, n=gap.lines))
    c2.metric(ctx.t("Cost as booked"), f"{sym(ctx.ccy)}{to_disp(cost, ctx):,.0f}",
              border=True, height=TILE_H,
              help=ctx.t("Σ over every model of its BOM at the price stored on "
                         "each line (`nomachat_det.prxndach`), converted at "
                         "today's rate — as the GPAO does."))
    c3.metric(ctx.t("At today's prices"), f"{sym(ctx.ccy)}{to_disp(new, ctx):,.0f}",
              f"{gap.gpao_pct:+.2f}%", delta_color="inverse",
              border=True, height=TILE_H,
              help=ctx.t("The same BOM at each component's current order price "
                         "(`fpiece.prxcmdf`). This is the GPAO's own figure, "
                         "including its treatment of unpriced components as zero."))
    c4.metric(ctx.t("Like for like"), f"{gap.lfl_pct:+.2f}%",
              f"{gap.lfl_pct - gap.gpao_pct:+.2f} pp", delta_color="off",
              border=True, height=TILE_H,
              help=ctx.t("The same comparison with unpriced components dropped "
                         "from both sides. This is the figure to steer on."))


def _unquoted_warning(gap: R.RequoteGap, ctx: Ctx) -> None:
    """Lead with the defect, because it changes the sign of the answer."""
    if not gap.unquoted_lines:
        return
    st.warning(ctx.tf(
        "**{n:,} of {tot:,} components ({pct:.1f} %) have no current order price**, "
        "and the GPAO scores those at zero rather than excluding them — so "
        "{cost} of stored cost simply vanishes from the new quotation. "
        "It reports material cost moving **{g:+.2f} %**; like for like it moved "
        "**{l:+.2f} %**. The GPAO calls {gc:,} models cheaper to build than "
        "before; only {lc:,} of them actually are, so **{f:,} are painted as a "
        "saving when they are a rise**.",
        n=gap.unquoted_lines, tot=gap.lines, pct=gap.unquoted_pct,
        cost=compact(to_disp(gap.unquoted_cost, ctx), ctx.ccy),
        g=gap.gpao_pct, l=gap.lfl_pct, gc=gap.gpao_cheaper,
        lc=gap.lfl_cheaper, f=gap.flipped))


# --------------------------------------------------------- distribution ---
def _distribution(req: pd.DataFrame, ctx: Ctx) -> None:
    """Both readings of the cost movement, as a pair of histograms.

    The point of the chart is the shift between them: the GPAO's distribution
    sits to the left of the like-for-like one by exactly the cost it dropped."""
    d = req.dropna(subset=["ecart_pct", "ecart_lfl_pct"])
    d = d[(d["ecart_pct"].abs() < 60) & (d["ecart_lfl_pct"].abs() < 60)]
    if d.empty:
        return

    st.markdown("#### " + ctx.t("How far each model moved"))
    P = ctx.P
    fig = go.Figure()
    fig.add_histogram(x=d["ecart_pct"], name=ctx.t("GPAO"), nbinsx=60,
                      marker_color=P["series"][1], opacity=0.65)
    fig.add_histogram(x=d["ecart_lfl_pct"], name=ctx.t("Like for like"), nbinsx=60,
                      marker_color=P["series"][0], opacity=0.65)
    fig.add_vline(x=0, line_dash="dash", line_color=P["grid"])
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title=ctx.t("Change in material cost (%)"))
    fig.update_yaxes(title=ctx.t("Models"))
    fig = style_fig(fig, height=320)
    fig.update_layout(showlegend=True)
    st.plotly_chart(fig, width="stretch")
    st.caption(ctx.t(
        "Models within ±60 % on both readings. Everything left of zero is a model "
        "the re-quotation says got cheaper. The gap between the two distributions "
        "is the cost the GPAO dropped by scoring unpriced components at zero."))


# ----------------------------------------------------------- the models ---
def _model_table(req: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("Models by cost movement"))

    mode = st.radio(
        ctx.t("Show"),
        [ctx.t("Biggest rises"), ctx.t("Biggest falls"), ctx.t("Where the two readings disagree")],
        horizontal=True, key="requote_mode", label_visibility="collapsed")

    d = req.copy()
    if mode == ctx.t("Biggest rises"):
        d = d.sort_values("ecart_lfl_pct", ascending=False)
    elif mode == ctx.t("Biggest falls"):
        d = d.sort_values("ecart_lfl_pct")
    else:
        d["disagreement"] = (d["ecart_lfl_pct"] - d["ecart_pct"]).abs()
        d = d.sort_values("disagreement", ascending=False)

    d = d.head(TOP_N).copy()
    for c in ("cost_dt", "requote_dt", "ecart", "sell_dt"):
        d[c] = to_disp(d[c], ctx)

    # The whole re-quote, not the top slice on screen — the sort is a reading
    # aid, and someone working this list wants every model in it.
    full = req.copy()
    for c in ("cost_dt", "requote_dt", "ecart", "sell_dt"):
        full[c] = to_disp(full[c], ctx)
    _export.download(
        ctx.tf("Download all {n} re-quoted models", n=f"{len(full):,}"),
        {ctx.t("Re-quoted models"): full},
        "re-quotation.xlsx", ctx=ctx, title=ctx.t("Re-quotation"), key="rq_xlsx",
        meta=_export.scope_meta(ctx),
        notes=[ctx.t("Company-wide: this report sets its own year and does not follow "
                     "the sidebar's Distributor or Bikes only filters."),
               ctx.t("Use the like-for-like column, not the raw one. The raw gap also "
                     "moves when a bill of materials gains or loses a line, so it is "
                     "not a pure price movement; the like-for-like column compares "
                     "only the components present in both readings.")])

    st.dataframe(
        d[["article", "libnach", "cost_dt", "requote_dt", "ecart_pct",
           "ecart_lfl_pct", "unquoted_lines", "sell_dt", "ecart_on_sale_pct"]],
        hide_index=True, width="stretch",
        column_config={
            "article": ctx.t("Code"),
            "libnach": ctx.t("Model"),
            "cost_dt": st.column_config.NumberColumn(
                f"{ctx.t('Cost as booked')} ({ctx.ccy})", format="%.2f"),
            "requote_dt": st.column_config.NumberColumn(
                ctx.t("At today's prices") + f" ({ctx.ccy})", format="%.2f"),
            "ecart_pct": st.column_config.NumberColumn(
                ctx.t("Change (GPAO)"), format="%.1f%%"),
            "ecart_lfl_pct": st.column_config.NumberColumn(
                ctx.t("Change (like for like)"), format="%.1f%%"),
            "unquoted_lines": st.column_config.NumberColumn(
                ctx.t("Unpriced parts"), format="%d"),
            "sell_dt": st.column_config.NumberColumn(
                f"{ctx.t('Last sale price')} ({ctx.ccy})", format="%.2f"),
            "ecart_on_sale_pct": st.column_config.NumberColumn(
                ctx.t("Cost move on price"), format="%.2f%%"),
        })
    st.caption(ctx.t(
        "**Change (GPAO)** is the screen's own figure; **like for like** drops "
        "components with no current price from both sides. **Cost move on price** "
        "is the GPAO's `Ecart (Ecart/PV) %` — how many points of the sale price "
        "the cost movement eats — and note it divides the GPAO's variance, not "
        "the like-for-like one."))

    _model_detail(req, ctx)


def _model_detail(req: pd.DataFrame, ctx: Ctx) -> None:
    """The GPAO's `frmAnalyseCoutMatNCModal2`, reached there by double-click."""
    with st.expander(ctx.t("Component detail for one model")):
        pick = st.selectbox(
            ctx.t("Model"), req["article"].tolist(), key="requote_detail",
            format_func=lambda a: f"{a} — {req.loc[req['article'] == a, 'libnach'].iloc[0]}")
        try:
            lines = R.load_requote_lines(pick)
        except Exception as exc:
            st.warning(ctx.tf("Could not load components: {err}", err=str(exc)[:200]))
            return
        if lines.empty:
            st.info(ctx.t("No bill of materials for this model."))
            return

        n_unq = int((~lines["quoted"]).sum())
        n_con = int(lines["conflict"].sum())
        bits = [ctx.tf("{n} components", n=len(lines))]
        if n_unq:
            bits.append(ctx.tf("{n} with no current price", n=n_unq))
        if n_con:
            bits.append(ctx.tf("{n} with a conflicting quote currency", n=n_con))
        st.caption(" · ".join(bits))

        d = lines.copy()
        for c in ("cost_dt", "new_dt", "ecart_dt"):
            d[c] = to_disp(d[c], ctx)
        st.dataframe(
            d[["part", "lib", "grp", "qty", "prx", "dev", "prx_new", "dev_new",
               "cost_dt", "new_dt", "ecart_dt"]],
            hide_index=True, width="stretch",
            column_config={
                "part": ctx.t("Part"), "lib": ctx.t("Description"),
                "grp": ctx.t("Group"),
                "qty": st.column_config.NumberColumn(ctx.t("Qty"), format="%.3f"),
                "prx": st.column_config.NumberColumn(ctx.t("Booked price"), format="%.4f"),
                "dev": ctx.t("Ccy"),
                "prx_new": st.column_config.NumberColumn(ctx.t("Current price"), format="%.4f"),
                "dev_new": ctx.t("Quote ccy"),
                "cost_dt": st.column_config.NumberColumn(
                    f"{ctx.t('Booked')} ({ctx.ccy})", format="%.3f"),
                "new_dt": st.column_config.NumberColumn(
                    f"{ctx.t('Current')} ({ctx.ccy})", format="%.3f"),
                "ecart_dt": st.column_config.NumberColumn(
                    f"{ctx.t('Change')} ({ctx.ccy})", format="%.3f"),
            })
        st.caption(ctx.t(
            "The GPAO shows only lines whose variance is non-zero, which hides "
            "every component with no current price — the ones that caused the "
            "variance. All lines are here. Both money columns convert at the "
            "**booked** currency's rate, because that is what the GPAO does even "
            "when the quote currency differs."))


# ------------------------------------------------------------ price list ---
def _price_list(year: int, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("The price list"))
    st.caption(ctx.t(
        "`frmConsultPrixNC` — the costing sheet stored per model in `costing_nc`, "
        "and the FOB price derived from it. There is no stored price in this ERP, "
        "only the inputs and a formula applied on read."))

    try:
        pl = R.load_price_list()
        acc = R.price_accuracy(year)
    except Exception as exc:
        st.warning(ctx.tf("Could not read the price list: {err}", err=str(exc)[:250]))
        return
    if pl.empty:
        st.info(ctx.t("No costing sheets found."))
        return

    _accuracy_tiles(pl, acc, year, ctx)
    _build_up(pl, ctx)


def _accuracy_tiles(pl: pd.DataFrame, acc: pd.DataFrame, year: int, ctx: Ctx) -> None:
    both = acc.dropna(subset=["err_list_pct", "err_prxnach_pct"]) if not acc.empty else acc
    covered = int(acc["err_list_pct"].notna().sum()) if not acc.empty else 0
    total = int(len(acc)) if not acc.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(ctx.t("Models priced"), f"{len(pl):,}", border=True, height=TILE_H,
              help=ctx.t("Live models with a costing sheet at its latest revision."))
    c2.metric(ctx.t("Coverage this year"),
              f"{covered / total * 100:.0f}%" if total else "—",
              f"{covered:,} of {total:,}", delta_color="off",
              border=True, height=TILE_H,
              help=ctx.tf("Models invoiced in {yr} that have a costing sheet. "
                          "This, not accuracy, is the list price's weakness.", yr=year))
    if not both.empty:
        c3.metric(ctx.t("List price error"),
                  f"{both['err_list_pct'].abs().median():.1f}%",
                  ctx.tf("within ±10 % on {p:.0f} %",
                         p=both["err_list_pct"].abs().le(10).mean() * 100),
                  delta_color="off", border=True, height=TILE_H,
                  help=ctx.tf("Median absolute error against realised average "
                              "selling price in {yr}.", yr=year))
        c4.metric(ctx.t("`prxnach` error"),
                  f"{both['err_prxnach_pct'].abs().median():.1f}%",
                  ctx.tf("within ±10 % on {p:.0f} %",
                         p=both["err_prxnach_pct"].abs().le(10).mean() * 100),
                  delta_color="off", border=True, height=TILE_H,
                  help=ctx.t("The field the rest of the dashboard falls back on, "
                             "measured the same way. Findings §4 flagged it as "
                             "stale; this is how stale."))
        st.success(ctx.tf(
            "**This is a usable sell price.** Over the {n:,} models carrying both "
            "figures, the costing sheet's price misses realised ASP by a median "
            "**{a:.1f} %** and lands within ±10 % on **{pa:.0f} %** of them. "
            "`nomachat.prxnach` misses by **{b:.1f} %** and lands within ±10 % on "
            "**{pb:.0f} %**. Anywhere the dashboard needs a list price, this is "
            "the one to use — where it exists.",
            n=len(both), a=both["err_list_pct"].abs().median(),
            pa=both["err_list_pct"].abs().le(10).mean() * 100,
            b=both["err_prxnach_pct"].abs().median(),
            pb=both["err_prxnach_pct"].abs().le(10).mean() * 100))


def _build_up(pl: pd.DataFrame, ctx: Ctx) -> None:
    """Cost → price, averaged over the book, as the costing sheet builds it."""
    P = ctx.P
    n = len(pl)
    steps = [(ctx.t(lbl), float(pl[col].sum()) / n) for col, lbl in R.COSTING_STEPS]
    price = float(pl["price_dt"].sum()) / n

    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["relative"] * len(steps) + ["total"],
        x=[s[0] for s in steps] + [ctx.t("List price")],
        y=[to_disp(s[1], ctx) for s in steps] + [to_disp(price, ctx)],
        text=[compact(to_disp(abs(s[1]), ctx), ctx.ccy) for s in steps]
             + [compact(to_disp(price, ctx), ctx.ccy)],
        textposition="outside", connector=dict(line=dict(color=P["grid"])),
        increasing=dict(marker=dict(color=P["series"][0])),
        decreasing=dict(marker=dict(color=P["neg"])),
        totals=dict(marker=dict(color=P["pos"]))))
    fig.update_yaxes(title=ctx.ccy)
    st.plotly_chart(style_fig(fig, height=340), width="stretch")
    st.caption(ctx.t(
        "Mean per model over every live costing sheet. **Sale freight appears "
        "once here but is charged twice** in the price — the costing screen adds "
        "it inside the sub-total and again at the end, deliberately. Margin is "
        "taken on material only, not on the labour and charges beside it, so the "
        "realised margin over full cost is lower than the sheet's `marge` says."))

    top = pl.nlargest(TOP_N, "price_dt").copy()
    for c in ("mat_dt", "cost_dt", "price_dt", "second_leg_dt"):
        top[c] = to_disp(top[c], ctx)
    with st.expander(ctx.t("The costing sheets themselves")):
        st.dataframe(
            top[["article", "libnach", "yr", "rev", "cost_dt", "marge",
                 "price_dt", "price_usd", "price_eur", "second_leg_dt",
                 "margin_on_price_pct"]],
            hide_index=True, width="stretch",
            column_config={
                "article": ctx.t("Code"), "libnach": ctx.t("Model"),
                "yr": st.column_config.NumberColumn(ctx.t("Sheet year"), format="%d"),
                "rev": st.column_config.NumberColumn(ctx.t("Revision"), format="%d"),
                "cost_dt": st.column_config.NumberColumn(
                    f"{ctx.t('Full cost')} ({ctx.ccy})", format="%.2f"),
                "marge": st.column_config.NumberColumn(
                    ctx.t("Margin rate"), format="%.1f%%"),
                "price_dt": st.column_config.NumberColumn(
                    f"{ctx.t('List price')} ({ctx.ccy})", format="%.2f"),
                "price_usd": st.column_config.NumberColumn("USD", format="%.2f"),
                "price_eur": st.column_config.NumberColumn("EUR", format="%.2f"),
                "second_leg_dt": st.column_config.NumberColumn(
                    ctx.t("2nd freight leg"), format="%.2f"),
                "margin_on_price_pct": st.column_config.NumberColumn(
                    ctx.t("Margin on price"), format="%.1f%%"),
            })
        st.caption(ctx.t(
            "The USD and EUR columns divide by the rate **stored on the sheet**, "
            "not today's — a sheet written in 2016 still quotes at its 2016 rate. "
            "`Sheet year` is how old that rate is."))


# --------------------------------------------------------------- defects ---
def _defects(req: pd.DataFrame, gap: R.RequoteGap, ctx: Ctx) -> None:
    with st.expander(ctx.t("What this report gets wrong")):
        st.markdown(ctx.t(R.UNQUOTED_DEFECT))
        st.caption(ctx.tf(
            "{n:,} of {tot:,} components ({pct:.1f} %) in this year's models, "
            "carrying {cost} of stored cost. {f:,} models are reported cheaper "
            "when they are dearer.",
            n=gap.unquoted_lines, tot=gap.lines, pct=gap.unquoted_pct,
            cost=compact(to_disp(gap.unquoted_cost, ctx), ctx.ccy), f=gap.flipped))

        st.markdown(ctx.t(R.CURRENCY_DEFECT))
        _conflict_table(ctx)

        st.markdown(ctx.t(R.SALE_FX_DEFECT))
        st.markdown(ctx.t(R.TIE_DEFECT))
        st.markdown(ctx.t(R.DOUBLE_FREIGHT))
        st.markdown(ctx.t(R.REBATE_SPLIT))


def _conflict_table(ctx: Ctx) -> None:
    try:
        cc = R.load_currency_conflicts()
    except Exception:
        return
    if cc.empty:
        return
    st.caption(ctx.tf(
        "{n} live parts carry a quote currency that contradicts their purchase "
        "currency. On {u} of them the number itself never changed — only the "
        "label — which is the signature of a mis-set dropdown. **Fix these at "
        "source**; until then neither reading of their cost is right.",
        n=len(cc), u=int(cc["unchanged_number"].sum())))
    d = cc.head(15).copy()
    d["implied_dt_swing"] = to_disp(d["implied_dt_swing"], ctx)
    st.dataframe(
        d[["part", "lib", "dev", "prx", "dev_new", "prx_new", "ratio",
           "implied_dt_swing", "unchanged_number"]],
        hide_index=True, width="stretch",
        column_config={
            "part": ctx.t("Part"), "lib": ctx.t("Description"),
            "dev": ctx.t("Purchase ccy"),
            "prx": st.column_config.NumberColumn(ctx.t("Purchase price"), format="%.4f"),
            "dev_new": ctx.t("Quote ccy"),
            "prx_new": st.column_config.NumberColumn(ctx.t("Quoted price"), format="%.4f"),
            "ratio": st.column_config.NumberColumn(ctx.t("New ÷ old"), format="%.2f"),
            "implied_dt_swing": st.column_config.NumberColumn(
                f"{ctx.t('Swing if honoured')} ({ctx.ccy})", format="%.2f"),
            "unchanged_number": st.column_config.CheckboxColumn(
                ctx.t("Number unchanged")),
        })
    st.caption(ctx.t(
        "**Swing if honoured** is what taking `devcmdf` at face value would do to "
        "that part's contribution — shown to make the case for fixing the data "
        "rather than the formula, not as a correction to apply."))
