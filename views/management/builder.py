"""Build from stock — turning parts that never moved into bikes.

**Not a port.** Every other tab here reproduces a GPAO screen; there is no GPAO
screen for this one. What it stands on is a port — the unmoved-stock reading
comes from `gpao_stock`, which ports `frmStockADate` — but the proposals are new
analysis and nothing in the ERP can confirm them. The tab says so on its face.

The argument in one line: roughly DT 6.3M of parts have not been issued or
reserved since the window opened, 90 % of that value is a part some live bike
BOM already calls for, and a batch of a hundred bikes can consume DT 50k of it
against DT 2.5k of parts that have to be bought in.

Three views, as different kinds of answer. **Free composition** fills each
required slot from the pool with no model in mind, and says how much of the pile
could be cleared if specification were free. **Cheaper on the shelf** is the
narrowest and the most certain: parts a live bike still calls for where the ERP
itself declares an interchangeable part, that part has not moved, and it is the
cheaper of the two — 258 of them, worth DT 58k off the cost of building and
reaching more than half the range. **Retrofit** starts from a BOM the factory has
already built and swaps unmoved parts into it where a rule allows.

Every substitution names both parts — what comes off and what goes on, with both
prices — because a row that names only the slot cannot be acted on. See
`bike_builder` for the rules and `docs/gpao-parity.md` §10 for the stock port
underneath and for what the GPAO does with `fpieceq`, which is less than it
looks.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import bike_builder as B
import erp
import gpao_stock as S
from theme import style_fig
from . import _export
from ._common import Ctx, TILE_H, money, tile, to_disp

TOP_N = 25
DEFAULT_BATCH = 100


def render(ctx: Ctx) -> None:
    st.subheader(ctx.t("Build from stock") + " — " + ctx.t("bikes out of parts that never moved"))
    st.caption(ctx.t(
        "Stock that has not been issued or reserved since the window opened, "
        "matched against the bills of materials the factory has already built, "
        "to work out what could be assembled from it and what would have to be "
        "bought in."))
    st.info(ctx.t(
        "**This tab sets its own dates and reads the whole part master.** Stock "
        "and bills of materials carry no customer dimension, so the sidebar's "
        "**Distributor**, **Bikes only** and **Years** controls do not apply "
        "here. The currency selector does."))

    asof, since, mode, batch = _controls(ctx)
    if asof is None:
        return

    try:
        # All five reads sit in one try so a missing database gives the tab a
        # single friendly warning instead of five scattered tracebacks. The
        # equivalences aren't used here directly — `cheaper_shelf_swaps` and
        # `dead_stock_portfolio` read them themselves — but loading them now
        # keeps them inside that guard, and it is a cache hit when they ask.
        pool = B.stock_pool(asof, since, mode=mode)
        corpus = B.load_corpus()
        B.load_equivalences()
        rules = B.slot_rules(corpus)
        tiers = B.learn_tiers(corpus)
    except Exception as exc:
        st.warning(ctx.tf("Could not read the stock and BOM data: {err}", err=str(exc)[:250]))
        return

    if pool.empty:
        st.info(ctx.t("No unmoved stock matches these dates."))
        return

    _headline(pool, corpus, ctx)
    st.divider()
    _what_is_there(pool, ctx)
    st.divider()
    _new_bikes(corpus, pool, rules, tiers, batch, ctx)
    st.divider()
    _rules(rules, tiers, corpus, ctx)
    st.divider()
    _cheaper(corpus, asof, since, mode, ctx)
    st.divider()
    _retrofit(tiers, asof, since, mode, batch, ctx)
    st.divider()
    _defects(ctx)


# ------------------------------------------------------------- controls ---
def _controls(ctx: Ctx):
    """As-of, window start, movement rule and batch size.

    The as-of floor is the ledger's own rebase date, not a preference. Below it
    the GPAO screen returns an empty grid and presents it as an answer — see
    `LEDGER_WINDOW_DEFECT` — so the control refuses instead."""
    floor = S.ledger_start()
    end = S.ledger_end()

    c1, c2, c3, c4 = st.columns([1, 1, 1.4, 1])
    with c1:
        asof = st.date_input(
            ctx.t("Stock as at"), value=end.date() if end is not None else None,
            min_value=floor.date() if floor is not None else None,
            key="bld_asof",
            help=ctx.t("The date the stock balance is taken at. It cannot go "
                       "before the ledger's opening balance."))
    with c2:
        since = st.date_input(
            ctx.t("Unmoved since"), value=pd.Timestamp("2024-01-01").date(),
            key="bld_since",
            help=ctx.t("A part counts as unmoved if nothing was issued against "
                       "it between this date and the as-of date."))
    with c3:
        mode = st.selectbox(
            ctx.t("Movement rule"), list(S.MODE_LABELS),
            format_func=lambda m: ctx.t(S.MODE_LABELS[m]),
            index=list(S.MODE_LABELS).index(S.MODE_NEVER), key="bld_mode",
            help=ctx.t("The GPAO's own three readings. \"Never moved\" is the "
                       "ERP's wording and its definition: nothing issued, and "
                       "nothing re-purchased either."))
    with c4:
        batch = int(st.number_input(
            ctx.t("Batch size"), min_value=1, max_value=5000, value=DEFAULT_BATCH,
            step=10, key="bld_batch",
            help=ctx.t("How many bikes per proposal. A part is only used if "
                       "there is enough on hand to cover the whole batch, so a "
                       "bigger batch means fewer parts qualify.")))

    if floor is not None and asof is not None and pd.Timestamp(asof) < floor:
        st.error(ctx.tf(
            "The stock ledger opens on {floor}. An earlier date returns an empty "
            "grid rather than an answer — see the defects below.",
            floor=f"{floor:%d %b %Y}"))
        return None, None, None, None

    if end is not None and asof is not None and pd.Timestamp(asof) > end:
        st.caption(ctx.tf(
            "The ledger's last real movement is {end}; anything after that date "
            "is order commitments only.", end=f"{end:%d %b %Y}"))

    return asof, since, int(mode), batch


# ------------------------------------------------------------- headline ---
def _headline(pool: pd.DataFrame, corpus: pd.DataFrame, ctx: Ctx) -> None:
    total = float(pool["value_dt"].sum())
    keys = set(zip(corpus["part"], corpus["pord"]))
    usable_mask = [k in keys for k in zip(pool["part"], pool["pord"])]
    usable = float(pool.loc[usable_mask, "value_dt"].sum())
    n_usable = int(sum(usable_mask))

    c = st.columns(4)
    c[0].metric(ctx.t("Unmoved stock"), tile(total, ctx), border=True, height=TILE_H,
                help=ctx.tf("{n} parts with a positive balance that nothing has been "
                            "issued against in the window.", n=f"{len(pool):,}"))
    c[1].metric(ctx.t("Used by a live BOM"), tile(usable, ctx),
                delta=f"{usable / total * 100:.0f}%" if total else None,
                delta_color="off", border=True, height=TILE_H,
                help=ctx.tf("{n} of those parts are called for by a bike the factory "
                            "still builds. The rest have no current home.",
                            n=f"{n_usable:,}"))
    c[2].metric(ctx.t("Parts"), f"{len(pool):,}", border=True, height=TILE_H,
                help=ctx.t("Distinct part revisions, keyed as the ERP keys them."))
    c[3].metric(ctx.t("Units"), f"{pool['on_hand'].sum():,.0f}", border=True, height=TILE_H,
                help=ctx.t("Physical pieces. Spokes and decals dominate the count; "
                           "frames and forks dominate the value."))


def _what_is_there(pool: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("What is sitting there"))
    st.caption(ctx.t(
        "The pile is not random — it is the expensive structural parts. Those "
        "are what gets over-ordered and stranded, while the cheap consumables "
        "never stop moving and so never show up here. That shape is why a "
        "proposal always needs a bought tail, and why the tail is cheap."))

    by_slot = (pool.groupby("slot")
               .agg(parts=("part", "size"), units=("on_hand", "sum"),
                    value_dt=("value_dt", "sum"))
               .reset_index().sort_values("value_dt", ascending=False).head(15))
    by_slot = B.label_slots(by_slot)
    by_slot["axis"] = by_slot["slot"] + " — " + by_slot["label"].fillna("")

    fig = go.Figure(go.Bar(
        x=to_disp(by_slot["value_dt"], ctx)[::-1], y=by_slot["axis"][::-1],
        orientation="h", marker_color=ctx.P["series"][0],
        hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
    fig.update_layout(xaxis_title=ctx.tf("Value ({c})", c=ctx.ccy))
    st.plotly_chart(style_fig(fig, height=380), width="stretch")

    with st.expander(ctx.t("Every slot, with what it is worth")):
        show = pool.groupby("slot").agg(
            parts=("part", "size"), units=("on_hand", "sum"),
            value_dt=("value_dt", "sum")).reset_index().sort_values("value_dt", ascending=False)
        show = B.label_slots(show)
        show["value_disp"] = to_disp(show["value_dt"], ctx)
        st.dataframe(
            show[["slot", "label", "grp_name", "parts", "units", "value_disp"]],
            hide_index=True, width="stretch",
            column_config={
                "slot": st.column_config.TextColumn(ctx.t("Slot")),
                "label": st.column_config.TextColumn(ctx.t("Description"), width="medium"),
                "grp_name": st.column_config.TextColumn(ctx.t("Assembly")),
                "parts": st.column_config.NumberColumn(ctx.t("Parts"), format="%d"),
                "units": st.column_config.NumberColumn(ctx.t("Units"), format="%.0f"),
                "value_disp": st.column_config.NumberColumn(
                    ctx.tf("Value ({c})", c=ctx.ccy), format="%.0f")})


# ---------------------------------------------------------------- rules ---
def _rules(rules: B.SlotRules, tiers: pd.DataFrame, corpus: pd.DataFrame, ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("The rules, read off the corpus"))
    st.caption(ctx.tf(
        "Nothing here is hand-written. Every rule is a count over the {n} live "
        "bike bills of materials the factory has already built and shipped, so "
        "it moves when the corpus does.", n=f"{corpus['model'].nunique():,}"))

    req = rules.required()
    com = rules.common()
    c = st.columns(3)
    c[0].metric(ctx.t("Required slots"), f"{len(req)}", border=True, height=TILE_H,
                help=ctx.t("Present on at least 95% of live bikes. A proposal "
                           "missing one of these is not a bike."))
    c[1].metric(ctx.t("Common slots"), f"{len(com)}", border=True, height=TILE_H,
                help=ctx.t("Present on 40-95%. Fitted often enough to be normal, "
                           "rarely enough to be a choice."))
    c[2].metric(ctx.t("Declared equivalences"), f"{len(B.load_equivalences()):,}",
                border=True, height=TILE_H,
                help=ctx.t("Pairs the ERP itself marks interchangeable, in "
                           "`fpieceq`. The production planner already substitutes "
                           "on these, so they are the strongest evidence there is."))

    lab = dict(zip(B.slot_labels()["slot"], B.slot_labels()["label"]))
    with st.expander(ctx.tf("The {n} slots a bike of any kind has to have", n=len(req))):
        st.dataframe(
            B.label_slots(pd.DataFrame({"slot": req}))[["slot", "label", "grp_name"]],
            hide_index=True, width="stretch",
            column_config={
                "slot": st.column_config.TextColumn(ctx.t("Slot")),
                "label": st.column_config.TextColumn(ctx.t("Description"), width="medium"),
                "grp_name": st.column_config.TextColumn(ctx.t("Assembly"))})

    prof = B.tier_profile(tiers)
    if prof.empty:
        return
    st.markdown("##### " + ctx.t("What each tier actually means here"))
    st.caption(ctx.t(
        "Terciles of build cost taken within each wheel size — a 20\" child's "
        "bike and a 29\" mountain bike are not points on one scale. The marker "
        "columns are what separates them in practice, and they are worth "
        "reading: **brakes and electrification move a build up a tier; "
        "suspension and gearing barely do.**"))
    prof = prof.copy()
    prof["median_disp"] = to_disp(prof["median_cost_dt"], ctx)
    st.dataframe(
        prof[["tier", "models", "median_disp", "suspension_fork", "disc_brake",
              "derailleur", "ebike_drive"]],
        hide_index=True, width="stretch",
        column_config={
            "tier": st.column_config.TextColumn(ctx.t("Tier")),
            "models": st.column_config.NumberColumn(ctx.t("Models"), format="%d"),
            "median_disp": st.column_config.NumberColumn(
                ctx.tf("Median build ({c})", c=ctx.ccy), format="%.0f"),
            "suspension_fork": st.column_config.NumberColumn(
                ctx.t("Suspension fork"), format="percent"),
            "disc_brake": st.column_config.NumberColumn(ctx.t("Disc brake"), format="percent"),
            "derailleur": st.column_config.NumberColumn(ctx.t("Derailleur"), format="percent"),
            "ebike_drive": st.column_config.NumberColumn(ctx.t("E-bike drive"), format="percent")})


# -------------------------------------------------------------- cheaper ---
def _cheaper(corpus: pd.DataFrame, asof, since, mode: int, ctx: Ctx) -> None:
    """Swaps that cost less than what the bill of materials specifies.

    The question this answers is the one the ERP has all the data for and never
    asks: of two parts it already calls interchangeable, is the one gathering
    dust the cheaper one? `frmAvailableItem` will show a part's equivalents with
    their prices and their stock, but ordered by part number, each in its own
    purchase currency, with no difference taken."""
    st.markdown("#### " + ctx.t("Cheaper on the shelf"))
    st.caption(ctx.t(
        "Parts a live bike still calls for, where the ERP itself declares an "
        "interchangeable part, that part has not moved, **and it is the cheaper "
        "of the two**. Both prices are the catalogue price converted to dinar at "
        "one rate — the comparison the costing screen would make if it were "
        "asked to re-cost the model with the swap in it. These pay twice: the "
        "bike costs less to build, and the pile gets smaller."))

    rows = B.cheaper_shelf_swaps(asof, since, mode=mode)
    if rows.empty:
        st.info(ctx.t("No declared equivalent in unmoved stock undercuts the part "
                      "the bill of materials specifies."))
        _gpao_note(ctx)
        return

    c = st.columns(4)
    c[0].metric(ctx.t("Swaps worth money"), f"{len(rows):,}", border=True, height=TILE_H,
                help=ctx.t("Pairs of parts the ERP marks interchangeable where the one "
                           "sitting unmoved is the cheaper one and there is enough of "
                           "it to fit at least one bike."))
    c[1].metric(ctx.t("Cost that comes out"), tile(float(rows["saving_dt"].sum()), ctx),
                border=True, height=TILE_H,
                help=ctx.t("Bounded by the shelf, not by demand: each pair is counted "
                           "only as far as the stock of the cheaper part goes. It "
                           "assumes the bikes get built, so it is a saving available "
                           "rather than a saving booked."))
    c[2].metric(ctx.t("Stock it consumes"), tile(float(rows["stock_freed_dt"].sum()), ctx),
                border=True, height=TILE_H,
                help=ctx.t("What the unmoved parts are carried at. This is the same "
                           "pile the proposals below compete for — the two readings "
                           "overlap and must not be added together."))
    # Distinct models, not the sum of the per-row counts: the same bike often
    # carries two of the specified parts, and adding the rows up would count it
    # twice.
    touched = corpus.merge(rows[["part", "pord"]].drop_duplicates(),
                           on=["part", "pord"], how="inner")["model"].nunique()
    c[3].metric(ctx.t("Models affected"), f"{touched:,}",
                border=True, height=TILE_H,
                help=ctx.t("Live bills of materials carrying one of the specified "
                           "parts. One swap can reach many models, which is what "
                           "makes a cheap-looking row worth doing."))

    show = rows.head(TOP_N).copy()
    show["slot_label"] = show["slot"] + " — " + show["label"].fillna("")
    show["orig_disp"] = to_disp(show["orig_unit_cost_dt"], ctx)
    show["alt_disp"] = to_disp(show["alt_unit_cost_dt"], ctx)
    show["per_bike_disp"] = to_disp(show["saving_per_bike_dt"], ctx)
    show["saving_disp"] = to_disp(show["saving_dt"], ctx)
    show["freed_disp"] = to_disp(show["stock_freed_dt"], ctx)

    st.dataframe(
        show[["slot_label", "orig_num", "orig_lib", "part_num", "part_lib",
              "orig_disp", "alt_disp", "per_bike_disp", "on_hand", "bikes_covered",
              "saving_disp", "freed_disp", "models"]],
        hide_index=True, width="stretch",
        column_config={
            "slot_label": st.column_config.TextColumn(ctx.t("Slot"), width="medium"),
            "orig_num": st.column_config.TextColumn(ctx.t("Specified — part")),
            "orig_lib": st.column_config.TextColumn(ctx.t("Specified — description"),
                                                    width="medium"),
            "part_num": st.column_config.TextColumn(ctx.t("On the shelf — part")),
            "part_lib": st.column_config.TextColumn(ctx.t("On the shelf — description"),
                                                    width="medium"),
            "orig_disp": st.column_config.NumberColumn(
                ctx.tf("Costs now ({c})", c=ctx.ccy), format="%.3f"),
            "alt_disp": st.column_config.NumberColumn(
                ctx.tf("Would cost ({c})", c=ctx.ccy), format="%.3f"),
            "per_bike_disp": st.column_config.NumberColumn(
                ctx.tf("Saves a bike ({c})", c=ctx.ccy), format="%.3f"),
            "on_hand": st.column_config.NumberColumn(ctx.t("On hand"), format="%.0f"),
            "bikes_covered": st.column_config.NumberColumn(ctx.t("Bikes it covers"),
                                                           format="%.0f"),
            "saving_disp": st.column_config.NumberColumn(
                ctx.tf("Saves in all ({c})", c=ctx.ccy), format="%.0f"),
            "freed_disp": st.column_config.NumberColumn(
                ctx.tf("Stock used ({c})", c=ctx.ccy), format="%.0f"),
            "models": st.column_config.NumberColumn(ctx.t("Models"), format="%d")})

    st.caption(ctx.t(
        "**Saves a bike** is the unit difference times what one bike uses, so a "
        "part fitted thirty-six times to a wheel counts thirty-six times. "
        "**Saves in all** stops where the shelf does. Every pair here is an "
        "`fpieceq` row — the ERP's own declaration that the two are "
        "interchangeable — so none of them needs an engineering judgement, only "
        "a decision."))

    _cheaper_drill(rows, corpus, ctx)
    _gpao_note(ctx)

    _export.download(
        ctx.t("Download these swaps"), {ctx.t("Cheaper on the shelf"): rows},
        "cheaper-on-the-shelf.xlsx", ctx=ctx,
        title=ctx.t("Cheaper on the shelf"), key="bld_cheap_xlsx",
        meta=[(ctx.t("Swaps worth money"), f"{len(rows):,}"),
              (ctx.t("Cost that comes out"), money(float(rows["saving_dt"].sum()), ctx))],
        notes=[ctx.t("Every pair is an `fpieceq` row — the ERP's own declaration that "
                     "the two parts are interchangeable. Nothing here is inferred from "
                     "the corpus."),
               ctx.t("Both prices are `prxfobfpiec` converted to dinar at one rate. A "
                     "part bought in a currency that has moved since it was last "
                     "quoted will read differently against a part bought in dinar.")])


def _cheaper_drill(rows: pd.DataFrame, corpus: pd.DataFrame, ctx: Ctx) -> None:
    """Which bikes a swap would touch — the question straight after "do it"."""
    labels = {f"{r.orig_num} → {r.part_num}  ({r.orig_lib[:40]})": i
              for i, r in enumerate(rows.head(TOP_N).itertuples(index=False))}
    if not labels:
        return
    with st.expander(ctx.t("Which bikes a swap would touch")):
        pick = st.selectbox(ctx.t("Swap"), list(labels), key="bld_cheap_pick")
        r = rows.iloc[labels[pick]]
        used = B.models_using(corpus, r["part"], r["pord"])
        if used.empty:
            st.info(ctx.t("No live model carries this part."))
            return
        used = used.copy()
        used["wheel_label"] = used["wheel"].map(_wheel_label)
        st.dataframe(
            used[["model", "model_name", "wheel_label", "qty_per_bike"]],
            hide_index=True, width="stretch",
            column_config={
                "model": st.column_config.TextColumn(ctx.t("Model")),
                "model_name": st.column_config.TextColumn(ctx.t("Name"), width="medium"),
                "wheel_label": st.column_config.TextColumn(ctx.t("Wheel")),
                "qty_per_bike": st.column_config.NumberColumn(ctx.t("Per bike"),
                                                              format="%.2f")})
        st.caption(ctx.tf(
            "{n} live models call for this part. The shelf holds enough of the "
            "cheaper one for {b} bikes, so the swap does not have to be made "
            "everywhere — it runs until the pile is gone.",
            n=f"{len(used):,}", b=f"{r['bikes_covered']:,.0f}"))


def _gpao_note(ctx: Ctx) -> None:
    st.info(ctx.t(
        "**Why the GPAO never says this.** The ERP has every ingredient and "
        "never combines them. `fpieceq` holds the interchangeable pairs, and "
        "`frmAvailableItem` will list a part's equivalents beside their price, "
        "their currency and their stock — but ordered by part number, each in "
        "its own currency, with no conversion and no difference taken, so the "
        "screen never says which one is cheaper. The only place the ERP acts on "
        "`fpieceq` is the production planner, which mentions equivalents solely "
        "on a line already in shortage, as a semicolon-joined string in a report "
        "cell. Substituting to save money, or to consume stock that is not "
        "moving, is not a question the GPAO asks."))


# ------------------------------------------------------------- retrofit ---
def _retrofit(tiers, asof, since, mode: int, batch: int, ctx: Ctx):
    st.markdown("#### " + ctx.t("Substituting into models you already build"))
    st.caption(ctx.t(
        "The other way round: start from a bill of materials the factory has "
        "already built and swap unmoved parts into it. **This is the smaller "
        "prize** — the GPAO's production planner already substitutes on "
        "`fpieceq`, and a swap inside a model that was going to be built anyway "
        "moves cost rather than making a sale. It is here because it clears "
        "stock against orders that already exist, and because every line traces "
        "to a bike that has actually been built."))

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        want_tier = st.selectbox(
            ctx.t("Tier"), [None] + B.TIER_ORDER,
            format_func=lambda v: ctx.t("All tiers") if v is None else ctx.t(v),
            key="bld_tier")
    with c2:
        wheels = [None] + sorted(w for w in tiers["wheel"].unique() if w)
        want_wheel = st.selectbox(
            ctx.t("Wheel size"), wheels,
            format_func=lambda v: ctx.t("All wheel sizes") if v is None else _wheel_label(v),
            key="bld_wheel")
    with c3:
        prefer = st.radio(
            ctx.t("When several shelf parts fit"), list(B.PREFERENCES),
            format_func=lambda k: ctx.t(B.PREFERENCES[k]), key="bld_prefer",
            horizontal=False,
            help=ctx.t("The same disagreement the new-bike builder has, one level "
                       "down. Taking the dearest part that fits frees the most money "
                       "off the shelf; taking the cheapest leaves the bike costing "
                       "less than the bill of materials it came from. Both clear the "
                       "same lines — they differ only in which part fills them."))

    # Cached on the scalars, which is what took this off the per-rerun path: the
    # engine used to re-run on every currency change. The controls below are part
    # of the key, so this tab keys separately from the Actions rule — a hit only
    # once the same combination has been asked for before.
    with st.spinner(ctx.t("Building proposals…")):
        proposals, port = B.dead_stock_portfolio(asof, since, mode=mode, batch=batch,
                                                 tier=want_tier, wheel=want_wheel,
                                                 prefer=prefer)
    if not proposals:
        st.info(ctx.t("Nothing can be built at this batch size. Try a smaller batch."))
        return []
    _portfolio_tiles(port, batch, ctx)
    _portfolio_table(port, ctx)
    _proposal_detail(proposals, ctx)
    return proposals


def _portfolio_tiles(port: B.Portfolio, batch: int, ctx: Ctx) -> None:
    # The allocated buy cost, not the raw sum. A proposal that wins a tenth of
    # the stock it wanted builds a tenth of the batch and buys a tenth of the
    # tail; totalling the unscaled figure would price every proposal at full
    # volume and answer a question nobody asked.
    buy = port.buy_alloc_dt
    ratio = port.realisable_dt / buy if buy else 0.0

    c = st.columns(5)
    c[0].metric(ctx.t("Stock cleared"), tile(port.realisable_dt, ctx), border=True,
                height=TILE_H,
                help=ctx.t("After allocation — the same frame cannot be used twice, "
                           "so this is what these proposals could actually consume "
                           "between them, not the sum of what each wants."))
    c[1].metric(ctx.t("Double-counted"), tile(port.overlap_dt, ctx), border=True,
                height=TILE_H, delta_color="off",
                # Not `{s}`: `Ctx.tf`'s own first parameter is named `s`, so a
                # placeholder of that name collides with it at call time.
                help=ctx.tf("Proposals standing alone want {want}. They compete for the "
                            "same parts, and this is the difference. Any headline has "
                            "to use the cleared figure.",
                            want=money(port.standalone_dt, ctx)))
    c[2].metric(ctx.t("Cash to build"), tile(buy, ctx), border=True, height=TILE_H,
                help=ctx.t("What has to be bought in, at bill-of-materials cost, scaled "
                           "to the share of stock each proposal actually won. The "
                           "unmoved parts are already paid for, so they are not cash — "
                           "the accounting cost of the build is the two added together."))
    c[3].metric(ctx.t("Cleared per dinar spent"), f"{ratio:,.1f}×", border=True,
                height=TILE_H,
                delta=ctx.tf("{n} worth doing", n=port.worth_doing),
                delta_color="off",
                help=ctx.t("Stock cleared divided by cash spent. Counting bought lines "
                           "would not answer this, because most of them cost pennies. "
                           "Read it with the count beside it: the ratio falls away "
                           "sharply down the ranking, so the portfolio average is much "
                           "worse than the proposals at the top of it."))
    c[4].metric(ctx.t("Cost change"), tile(-port.saving_alloc_dt, ctx), border=True,
                height=TILE_H, delta_color="off",
                help=ctx.t("What the substitutions do to what these batches cost to "
                           "make — the bill of materials' own price for each swapped "
                           "line against the catalogue price of the part going in, "
                           "both in dinar at one rate, scaled to the share of stock "
                           "each proposal won. Negative is cheaper. Under \"clear the "
                           "most stock\" this is usually positive: the dearest part "
                           "that fits frees the most money and also makes the bike "
                           "cost more."))


def _portfolio_table(port: B.Portfolio, ctx: Ctx) -> None:
    df = port.allocated.copy()
    if df.empty:
        return
    df["cleared_disp"] = to_disp(df["realisable_dt"], ctx)
    df["standalone_disp"] = to_disp(df["standalone_dt"], ctx)
    df["buy_disp"] = to_disp(df["buy_dt"], ctx)
    df["conf"] = df["confidence"].map(lambda t: ctx.t(B.TIERS.get(int(t), "")))
    df["wheel_label"] = df["wheel"].map(_wheel_label)
    df["buy_disp"] = to_disp(df["buy_alloc_dt"], ctx)
    df["cost_disp"] = to_disp(-df["saving_alloc_dt"], ctx)

    st.dataframe(
        df.head(TOP_N)[["model", "model_name", "tier", "wheel_label", "cleared_disp",
                        "standalone_disp", "buy_disp", "ratio", "swaps",
                        "cheaper_swaps", "cost_disp", "conf", "max_lead_days"]],
        hide_index=True, width="stretch",
        column_config={
            "model": st.column_config.TextColumn(ctx.t("Model")),
            "model_name": st.column_config.TextColumn(ctx.t("Name")),
            "tier": st.column_config.TextColumn(ctx.t("Tier")),
            "wheel_label": st.column_config.TextColumn(ctx.t("Wheel")),
            "cleared_disp": st.column_config.NumberColumn(
                ctx.tf("Cleared ({c})", c=ctx.ccy), format="%.0f"),
            "standalone_disp": st.column_config.NumberColumn(
                ctx.tf("Wants ({c})", c=ctx.ccy), format="%.0f"),
            "buy_disp": st.column_config.NumberColumn(
                ctx.tf("Buy ({c})", c=ctx.ccy), format="%.0f"),
            "ratio": st.column_config.NumberColumn(ctx.t("Cleared per DT"), format="%.1f×"),
            "swaps": st.column_config.NumberColumn(ctx.t("Swaps"), format="%d"),
            "cheaper_swaps": st.column_config.NumberColumn(ctx.t("Of those, cheaper"),
                                                           format="%d"),
            "cost_disp": st.column_config.NumberColumn(
                ctx.tf("Cost change ({c})", c=ctx.ccy), format="%.0f"),
            "conf": st.column_config.TextColumn(ctx.t("Weakest swap")),
            "max_lead_days": st.column_config.NumberColumn(
                ctx.t("Lead (days)"), format="%.0f")})

    st.caption(ctx.t(
        "**Cleared** is after allocation, **Wants** is the proposal on its own. "
        "Where the two differ, an earlier proposal already took the parts. "
        "**Buy** is scaled to the share actually won, so **Cleared per DT** is "
        "comparable down the list — and it falls away quickly, which is the "
        "argument for running the top few rather than the whole table. "
        "**Weakest swap** is the least-evidenced substitution among the "
        "structural groups, not an average — one unchecked fork should not hide "
        "behind thirty certain decals. **Cost change** is what the swaps do to "
        "the cost of building the batch; it is separate from the stock cleared, "
        "and the two do not move together."))

    if not port.contended.empty:
        with st.expander(ctx.tf("The {n} lines that lost a part to a higher-ranked proposal",
                                n=f"{len(port.contended):,}")):
            st.dataframe(port.contended.head(200), hide_index=True, width="stretch")


def _proposal_detail(proposals: list, ctx: Ctx) -> None:
    labels = {f"{p.model} — {p.model_name}": i for i, p in enumerate(proposals)}
    pick = st.selectbox(ctx.t("Inspect a proposal"), list(labels), key="bld_pick")
    p = proposals[labels[pick]]

    lines = B.label_slots(p.lines.copy())
    lines["slot_label"] = lines["slot"] + " — " + lines["label"].fillna("")
    lines["tier_label"] = lines["tier"].map(lambda t: ctx.t(B.TIERS.get(int(t), "")))
    lines["line_disp"] = to_disp(lines["line_dt"], ctx)
    lines["saving_disp"] = to_disp(lines["saving_dt"], ctx)
    lines["orig_cost_disp"] = to_disp(lines["orig_unit_cost_dt"], ctx)
    lines["new_cost_disp"] = to_disp(lines["unit_cost_dt"], ctx)
    if "build_order" in lines.columns:
        lines = lines.sort_values(["build_order", "slot"], na_position="last")

    stock = lines[lines["source"] == "stock"]
    buy = lines[lines["source"] == "buy"].sort_values("line_dt", ascending=False)
    swaps = lines[lines["swap"]]

    c = st.columns(5)
    c[0].metric(ctx.t("From stock"), tile(p.dead_value_dt, ctx), border=True, height=TILE_H,
                help=ctx.tf("{n} of {tot} lines, over a batch of {b}.",
                            n=len(stock), tot=len(lines), b=p.batch))
    c[1].metric(ctx.t("To buy"), tile(p.buy_value_dt, ctx), border=True, height=TILE_H,
                help=ctx.tf("{n} lines. Line count is the wrong measure here — what "
                            "matters is that they are cheap, and how long they take.",
                            n=len(buy)))
    c[2].metric(ctx.t("Parts to change"), f"{p.swaps:,}", border=True, height=TILE_H,
                delta=ctx.tf("{n} cheaper than the BOM", n=p.cheaper_swaps),
                delta_color="off",
                help=ctx.t("Lines where the part going in is not the part the bill of "
                           "materials names. Everything else is the model's own part, "
                           "which happens to be sitting in stock — nothing to change "
                           "and nothing to check."))
    c[3].metric(ctx.t("Cost change"), tile(-p.saving_dt, ctx), border=True, height=TILE_H,
                delta_color="off",
                help=ctx.t("What the substitutions do to what the batch costs to make, "
                           "priced the way the costing screen would: the bill of "
                           "materials' own price for each line against the catalogue "
                           "price of the part going in, both in dinar at one rate. "
                           "Negative is cheaper. It is not measured against what the "
                           "shelf is carried at, because most of the shelf is valued "
                           "at average cost paid and that difference is not a saving."))
    c[4].metric(ctx.t("Longest lead"), f"{p.max_lead_days:,.0f} d", border=True,
                height=TILE_H,
                help=ctx.t("The slowest bought part sets when the batch can actually "
                           "start. This, not cost, is usually what makes a bought tail "
                           "expensive."))

    t1, t2, t3 = st.tabs([ctx.tf("Parts to change ({n})", n=len(swaps)),
                          ctx.tf("Everything from stock ({n})", n=len(stock)),
                          ctx.tf("To buy ({n})", n=len(buy))])

    swap_cfg = {
        "slot_label": st.column_config.TextColumn(ctx.t("Slot"), width="medium"),
        "orig_num": st.column_config.TextColumn(ctx.t("Take out — part")),
        "orig_lib": st.column_config.TextColumn(ctx.t("Take out — description"),
                                                width="medium"),
        "part_num": st.column_config.TextColumn(ctx.t("Put in — part")),
        "part_lib": st.column_config.TextColumn(ctx.t("Put in — description"),
                                                width="medium"),
        "qty_per_bike": st.column_config.NumberColumn(ctx.t("Per bike"), format="%.2f"),
        "qty_needed": st.column_config.NumberColumn(ctx.t("Needed"), format="%.0f"),
        "orig_cost_disp": st.column_config.NumberColumn(
            ctx.tf("Costs now ({c})", c=ctx.ccy), format="%.3f"),
        "new_cost_disp": st.column_config.NumberColumn(
            ctx.tf("Would cost ({c})", c=ctx.ccy), format="%.3f"),
        "saving_disp": st.column_config.NumberColumn(
            ctx.tf("Saves ({c})", c=ctx.ccy), format="%.2f"),
        "tier_label": st.column_config.TextColumn(ctx.t("Evidence"), width="medium"),
        "line_disp": st.column_config.NumberColumn(
            ctx.tf("Cleared ({c})", c=ctx.ccy), format="%.2f"),
    }

    with t1:
        if swaps.empty:
            st.info(ctx.t("Nothing changes — every line this proposal takes from stock "
                          "is the part the bill of materials already names."))
        else:
            st.dataframe(
                swaps[["slot_label", "orig_num", "orig_lib", "part_num", "part_lib",
                       "qty_per_bike", "qty_needed", "orig_cost_disp", "new_cost_disp",
                       "saving_disp", "tier_label", "line_disp"]],
                hide_index=True, width="stretch", column_config=swap_cfg)
            st.caption(ctx.t(
                "**Take out** is what the bill of materials names; **put in** is what "
                "is on the shelf. Read the **Evidence** column before acting on a row: "
                "an ERP-declared equivalent is a swap the production planner already "
                "makes, while *same slot and wheel size* means only that both parts "
                "have been built at this size — nobody has yet paired these two. "
                "**Saves** is per line over the whole batch; a negative number means "
                "the shelf part is the dearer one, which is a trade of cost for space "
                "rather than a saving."))

    with t2:
        st.dataframe(
            stock[["slot_label", "change", "part_lib", "qty_per_bike", "qty_needed",
                   "swap", "tier_label", "line_disp"]],
            hide_index=True, width="stretch",
            column_config={
                "slot_label": st.column_config.TextColumn(ctx.t("Slot"), width="medium"),
                "change": st.column_config.TextColumn(ctx.t("Part"), width="medium"),
                "part_lib": st.column_config.TextColumn(ctx.t("Description"),
                                                        width="medium"),
                "qty_per_bike": st.column_config.NumberColumn(ctx.t("Per bike"),
                                                              format="%.2f"),
                "qty_needed": st.column_config.NumberColumn(ctx.t("Needed"), format="%.0f"),
                "swap": st.column_config.CheckboxColumn(ctx.t("Substituted")),
                "tier_label": st.column_config.TextColumn(ctx.t("Evidence"),
                                                          width="medium"),
                "line_disp": st.column_config.NumberColumn(
                    ctx.tf("Cleared ({c})", c=ctx.ccy), format="%.2f")})
        st.caption(ctx.t(
            "In the factory's own build order. A **Part** column reading `A → B` is a "
            "substitution; a single number is the model's own part, already in stock."))

    with t3:
        st.dataframe(
            buy[["slot_label", "part_num", "part_lib", "qty_per_bike", "qty_needed",
                 "new_cost_disp", "line_disp", "lead_days", "supplier"]],
            hide_index=True, width="stretch",
            column_config={
                "slot_label": st.column_config.TextColumn(ctx.t("Slot"), width="medium"),
                "part_num": st.column_config.TextColumn(ctx.t("Part")),
                "part_lib": st.column_config.TextColumn(ctx.t("Description"),
                                                        width="medium"),
                "qty_per_bike": st.column_config.NumberColumn(ctx.t("Per bike"),
                                                              format="%.2f"),
                "qty_needed": st.column_config.NumberColumn(ctx.t("Needed"), format="%.0f"),
                "new_cost_disp": st.column_config.NumberColumn(
                    ctx.tf("Unit ({c})", c=ctx.ccy), format="%.3f"),
                "line_disp": st.column_config.NumberColumn(
                    ctx.tf("Cost ({c})", c=ctx.ccy), format="%.2f"),
                "lead_days": st.column_config.NumberColumn(ctx.t("Lead (days)"),
                                                           format="%.0f"),
                "supplier": st.column_config.TextColumn(ctx.t("Supplier"))})
        st.caption(ctx.tf(
            "The shopping list for this batch, at the bill of materials' own price "
            "converted to dinar. The longest lead on it is {d} days, and that — not "
            "the {v} it costs — is what decides when the batch can start.",
            d=f"{p.max_lead_days:,.0f}", v=money(p.buy_value_dt, ctx)))

    # The substitution list is the one thing here somebody hands to production,
    # so it goes out with the shopping list beside it rather than on its own.
    _export.download(
        ctx.t("Download this proposal"),
        {ctx.t("Parts to change"): swaps.drop(columns=["line_dt", "saving_dt"],
                                              errors="ignore"),
         ctx.t("From stock"): stock.drop(columns=["line_dt", "saving_dt"],
                                         errors="ignore"),
         ctx.t("To buy"): buy.drop(columns=["line_dt", "saving_dt"], errors="ignore")},
        f"proposal_{p.model}_{p.batch}.xlsx".replace(" ", "-"),
        ctx=ctx, title=ctx.t("Substituting into models you already build"),
        key="bld_prop_xlsx",
        meta=[(ctx.t("Model"), f"{p.model} — {p.model_name}"),
              (ctx.t("Batch size"), f"{p.batch:,}"),
              (ctx.t("Parts to change"), f"{p.swaps:,}")],
        notes=[ctx.t("This is not a GPAO report. The substitutions are proposed from "
                     "the bill-of-materials corpus and from `fpieceq`; nothing in the "
                     "ERP has approved them."),
               ctx.t("Check the Evidence column line by line. Only *ERP-declared "
                     "equivalent* is a swap the ERP itself vouches for.")])


# ------------------------------------------------------------- new bikes ---
def _new_bikes(corpus, pool, rules, tiers, batch: int, ctx: Ctx) -> None:
    """The primary view: a bike specified from the shelf, not from a model."""
    st.markdown("#### " + ctx.t("New bikes — specified from the shelf"))
    st.caption(ctx.t(
        "Each slot filled with the most valuable compatible part the shelf can "
        "cover, subject to the build still costing less than its tier sells "
        "for. This is where new money is: the parts are already paid for, so "
        "what a bike costs to finish is only its bought tail."))

    c1, c2, c3, c4 = st.columns([1.1, 1, 1.9, 1.3])
    by_value = (pool.merge(rules.wheel_parts[["part", "pord", "wheel"]].drop_duplicates(),
                           on=["part", "pord"])
                .groupby("wheel")["value_dt"].sum().sort_values(ascending=False))
    wheels = [w for w in by_value.index if w] or sorted(w for w in tiers["wheel"].unique() if w)
    with c1:
        wheel = st.selectbox(ctx.t("Wheel size"), wheels, format_func=_wheel_label,
                             key="bld_free_wheel",
                             help=ctx.t("Ordered by how much unmoved stock that size can "
                                        "absorb, not by the size itself."))
    with c2:
        tier = st.selectbox(ctx.t("Tier"), B.TIER_ORDER, index=1,
                            format_func=ctx.t, key="bld_free_tier")
    with c3:
        strategy = st.radio(
            ctx.t("Objective"), list(B.STRATEGIES),
            format_func=lambda k: ctx.t(B.STRATEGIES[k]), key="bld_free_strategy",
            help=ctx.t("The two pull against each other. Clearing the shelf takes the "
                       "dearest part in every slot, which frees the most money but can "
                       "build a bike that costs more than it sells for. Protecting the "
                       "margin takes the dearest part that still leaves the build inside "
                       "its tier's price."))
    with c4:
        include_common = st.toggle(
            ctx.t("Include optional fittings"), value=True, key="bld_free_common",
            help=ctx.t("Off, the build is the required slots only — the parts without "
                       "which it is not a bike. On, it also fits what most bikes of "
                       "this kind carry: mudguards, a rack, a bell, reflectors."))

    build = B.propose_free(wheel, corpus, pool, rules, tiers, tier=tier,
                           batch=batch, include_common=include_common,
                           strategy=strategy)
    other = B.propose_free(wheel, corpus, pool, rules, tiers, tier=tier,
                           batch=batch, include_common=include_common,
                           strategy=(B.STRATEGY_CLEAR if strategy == B.STRATEGY_MARGIN
                                     else B.STRATEGY_MARGIN))
    if build.lines.empty:
        st.info(ctx.t("No slots could be filled for this wheel size."))
        return

    _slot_explainer(build, corpus, wheel, rules, ctx)
    _build_tiles(build, ctx)
    _tradeoff(build, other, ctx)
    _build_table(build, ctx)


def _slot_explainer(build: B.Build, corpus, wheel, rules: B.SlotRules, ctx: Ctx) -> None:
    """Why the build has this many slots.

    A reader seeing "32 filled, 23 bought" reasonably asks whether 55 slots is a
    real bike or an artefact. It is real, and the corpus says so — this puts the
    comparison on the page instead of leaving it to be trusted."""
    at = corpus[corpus["wheel"] == str(wheel)]
    if at.empty:
        at = corpus
    per = at.groupby("model")["slot"].nunique()
    n_req = len(rules.required(wheel))

    st.caption(ctx.tf(
        "**A complete bike is about this many parts.** A real {w} bike in this "
        "factory fills a median of **{med:.0f} slots** (half of them fall between "
        "{lo:.0f} and {hi:.0f}), of which **{req} are required** — present on 95 % "
        "or more of them. This build fills **{n}**: {stock} from the shelf and "
        "**{buy} bought in for {buyv}**, which is {per_bike} a bike. The bought "
        "ones are the consumable tail — chain, cables, ties, labels, screws — "
        "and they are cheap because they are the parts that never stop moving, "
        "which is exactly why they are never sitting in dead stock.",
        w=_wheel_label(wheel), med=per.median(), lo=per.quantile(0.25),
        hi=per.quantile(0.75), req=n_req, n=len(build.lines),
        stock=int((build.lines["source"] == "stock").sum()),
        buy=int((build.lines["source"] == "buy").sum()),
        buyv=money(build.buy_value_dt, ctx),
        per_bike=money(build.cash_per_bike, ctx, dp=2)))


def _build_tiles(build: B.Build, ctx: Ctx) -> None:
    margin = build.margin_pct
    c = st.columns(5)
    c[0].metric(ctx.t("Buildable now"), f"{build.buildable_units:,}", border=True,
                height=TILE_H,
                help=ctx.t("Bikes the shelf supports, set by whichever chosen part runs "
                           "out first. The batch size above is a floor on which parts "
                           "qualify, not a ceiling on what can be built."))
    c[1].metric(ctx.t("Stock cleared"), tile(build.stock_value_dt, ctx), border=True,
                height=TILE_H,
                help=ctx.tf("Over a batch of {b}. This is money already spent that the "
                            "build turns back into something sellable.", b=build.batch))
    c[2].metric(ctx.t("Cash per bike"), money(build.cash_per_bike, ctx, dp=2),
                border=True, height=TILE_H,
                help=ctx.t("New money per bike — the bought tail only. The shelf parts "
                           "are already paid for, so they are not cash out."))
    c[3].metric(ctx.t("Cost per bike"), money(build.cost_per_bike, ctx, dp=2),
                border=True, height=TILE_H,
                help=ctx.t("What the books would carry: the shelf parts at their held "
                           "value plus the tail. This is the figure the margin is taken "
                           "against, because the stock did cost what it cost."))
    c[4].metric(ctx.t("Margin vs list"), "—" if pd.isna(margin) else f"{margin:.1f}%",
                border=True, height=TILE_H,
                delta=ctx.tf("list {p}", p=money(build.benchmark_price_dt, ctx)),
                delta_color="off",
                help=ctx.t("Against the median list price of live models at this wheel "
                           "size and tier, derived from `costing_nc`. A negative margin "
                           "means the shelf cannot build this tier cheaply — usually an "
                           "e-bike, where the battery and motor have to be bought."))

    if not pd.isna(margin) and margin < 0:
        st.warning(ctx.t(
            "**This build costs more than its tier lists at.** It is the honest "
            "answer rather than a failure: the slots the shelf cannot fill here "
            "are the expensive ones — battery, motor, controller — so the tail "
            "stops being a rounding error. Try a lower tier or another wheel size."),
            icon=":material/warning:")


def _tradeoff(build: B.Build, other: B.Build, ctx: Ctx) -> None:
    """What the other objective would have done.

    The choice between clearing the shelf and protecting the margin is the
    reader's, not the tool's, and it cannot be made without both numbers. Put
    the one they did not pick next to the one they did."""
    if other.lines.empty:
        return
    d_stock = other.stock_value_dt - build.stock_value_dt
    d_margin = other.margin_pct - build.margin_pct
    if abs(d_stock) < 1 and (pd.isna(d_margin) or abs(d_margin) < 0.1):
        st.caption(ctx.t(
            "**Both objectives give the same build here** — the shelf never "
            "offers a part dear enough to push this tier past its price, so "
            "there is nothing to trade off."))
        return

    st.caption(ctx.tf(
        "**The other objective — {alt} — would clear {os} at a {om} margin**, "
        "against {bs} at {bm} here. {verdict}",
        alt=ctx.t(B.STRATEGIES[other.strategy]).split("—")[0].strip().lower(),
        os=money(other.stock_value_dt, ctx), bs=money(build.stock_value_dt, ctx),
        om="—" if pd.isna(other.margin_pct) else f"{other.margin_pct:.1f}%",
        bm="—" if pd.isna(build.margin_pct) else f"{build.margin_pct:.1f}%",
        verdict=ctx.t(
            "Clearing more stock costs margin because the dearest part in a slot "
            "is what frees the most money and also what makes the bike expensive. "
            "Which is right depends on whether the shelf space or the sale is the "
            "binding problem.")))


def _build_table(build: B.Build, ctx: Ctx) -> None:
    df = build.lines.copy()
    df["line_disp"] = to_disp(df["line_dt"], ctx)
    df["slot_label"] = df["slot"] + " — " + df["label"].fillna("")
    df["units_supported"] = pd.to_numeric(df["units_supported"], errors="coerce")

    t1, t2 = st.tabs([ctx.tf("From stock ({n})", n=int((df["source"] == "stock").sum())),
                      ctx.tf("Bought in ({n})", n=int((df["source"] == "buy").sum()))])
    cfg = {
        "slot_label": st.column_config.TextColumn(ctx.t("Slot"), width="medium"),
        "grp_name": st.column_config.TextColumn(ctx.t("Assembly")),
        "required": st.column_config.CheckboxColumn(ctx.t("Required")),
        "qty_per_bike": st.column_config.NumberColumn(ctx.t("Per bike"), format="%.2f"),
        "part_num": st.column_config.TextColumn(ctx.t("Part")),
        "part_lib": st.column_config.TextColumn(ctx.t("Description")),
        "units_supported": st.column_config.NumberColumn(ctx.t("Bikes it covers"), format="%.0f"),
        "line_disp": st.column_config.NumberColumn(ctx.tf("Value ({c})", c=ctx.ccy), format="%.2f"),
        "unit_dt": st.column_config.NumberColumn(ctx.t("Unit (DT)"), format="%.3f"),
    }
    with t1:
        st.dataframe(
            df[df["source"] == "stock"][["slot_label", "grp_name", "required",
                                         "qty_per_bike", "part_num", "part_lib",
                                         "units_supported", "line_disp"]],
            hide_index=True, width="stretch", column_config=cfg)
        st.caption(ctx.t(
            "In the factory's own build order, from `typepieces` — frame first, "
            "then wheels, drive, gears, steering, seating, brakes, and the "
            "finishing groups last. **Bikes it covers** is how far that part's "
            "stock goes on its own; the smallest of them is the batch ceiling."))
    with t2:
        st.dataframe(
            df[df["source"] == "buy"][["slot_label", "grp_name", "required",
                                       "qty_per_bike", "unit_dt", "line_disp"]],
            hide_index=True, width="stretch", column_config=cfg)
        st.caption(ctx.t(
            "Priced at what the corpus typically pays for the slot, since no "
            "specific part is chosen. Sorted the same way — read the **Required** "
            "column first: an optional fitting with nothing in stock can simply "
            "be left off the bike."))

    # The bought-in list is the closest thing on this tab to something someone
    # acts on directly — it is a shopping list for a batch — so both halves of
    # the build go out together, stock and buy on separate sheets.
    _export.download(
        ctx.t("Download this build"),
        {ctx.t("From stock"): df[df["source"] == "stock"].drop(columns=["line_dt"],
                                                               errors="ignore"),
         ctx.t("To buy"): df[df["source"] == "buy"].drop(columns=["line_dt"],
                                                         errors="ignore")},
        f"build_{build.wheel}_{build.tier}_{build.batch}.xlsx".replace(" ", "-"),
        ctx=ctx, title=ctx.t("New bikes — specified from the shelf"), key="bld_xlsx",
        meta=[(ctx.t("Wheel size"), str(build.wheel)), (ctx.t("Tier"), str(build.tier)),
              (ctx.t("Batch size"), f"{build.batch:,}"),
              (ctx.t("Buildable now"), f"{build.buildable_units:,}")],
        notes=[ctx.t("This is not a GPAO report. It is a proposal built from the "
                     "bill-of-materials corpus, and the combination of parts in it "
                     "has no precedent — every part has been used at this wheel "
                     "size, but not necessarily together."),
               ctx.t("The bought-in lines are priced at what the corpus typically "
                     "pays for that slot, not at a quoted price, because no specific "
                     "part has been chosen. Treat them as an estimate to quote "
                     "against, never as an order."),
               ctx.t("Check the lead times before committing: the bought-in tail is "
                     "cheap but some of it is 90 days out.")])


# -------------------------------------------------------------- defects ---
def _defects(ctx: Ctx) -> None:
    st.markdown("#### " + ctx.t("Where the stock reading does not hold up"))
    st.caption(ctx.t(
        "These are defects in `frmStockADate`, the GPAO screen the stock figures "
        "are ported from. They are reproduced rather than corrected, so the "
        "numbers still tie to the ERP — but they shape what can be trusted here."))
    for d in (S.LEDGER_WINDOW_DEFECT, S.COMMITMENT_DEFECT, S.PMP_FALLBACK_DEFECT,
              S.ASOF_RATE_DEFECT, S.REPURCHASE_BOUND_DEFECT):
        st.markdown("- " + ctx.t(d))

    st.caption(ctx.t(
        "One of these works in our favour and is worth naming. Because customer "
        "and production reservations are booked as issues, a part that is spoken "
        "for fails the movement test — so \"unmoved\" here means neither "
        "consumed nor reserved, and the pool is genuinely free to allocate."))


def _wheel_label(code) -> str:
    """`wheelnach` is a code, not a label ('9' is 700C), and the ERP's own
    labels carry trailing spaces. `erp._wheel_sizes` returns the stripped map."""
    try:
        return erp._wheel_sizes().get(str(code).strip(), str(code))
    except Exception:
        return str(code)
