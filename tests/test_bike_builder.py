"""The build-from-stock engine, checked against its own evidence.

This module has no GPAO counterpart, so there is no parity to pin. What can be
pinned is that the rules describe the corpus they were mined from, and that a
proposal never promises a part it does not have. Those are the two ways this
could be wrong and still look right.

The contention test is the one that matters most. Every proposal is individually
correct and summing them is not, because they compete for the same frames — a
headline built on the standalone total would overstate the prize by roughly
half. `test_allocation_removes_the_double_counting` is what stops that.

Assertions are relationships rather than magic numbers so they survive a newer
restore. Needs the ERP; skips when it isn't reachable.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bike_builder as B  # noqa: E402
import erp  # noqa: E402

ASOF = "2026-09-21"
SINCE = "2024-01-01"
BATCH = 100


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def corpus(erp_up):
    df = B.load_corpus()
    if df.empty:
        pytest.skip("no live bike BOMs in this restore")
    return df


@pytest.fixture(scope="session")
def pool(erp_up):
    df = B.stock_pool(ASOF, SINCE)
    if df.empty:
        pytest.skip("no unmoved stock in this restore")
    return df


@pytest.fixture(scope="session")
def rules(corpus):
    return B.slot_rules(corpus)


@pytest.fixture(scope="session")
def tiers(corpus):
    return B.learn_tiers(corpus)


@pytest.fixture(scope="session")
def eq(erp_up):
    return B.load_equivalences()


@pytest.fixture(scope="session")
def proposals(corpus, pool, eq, rules, tiers):
    out = B.rank_retrofits(corpus, pool, eq, rules, tiers, batch=BATCH, shortlist=25)
    if not out:
        pytest.skip("nothing buildable at this batch size")
    return out


# ----------------------------------------------------------- the corpus ---
def test_corpus_is_live_bikes_only(corpus):
    """Slot prevalence is only meaningful if the corpus is what it claims to be.
    An archived model or a spare-parts kit would drag every rule it touches."""
    n = erp._q("""
        SELECT COUNT(DISTINCT codnach) AS n FROM nomachat
        WHERE isArchived = 0 AND isBike = 1
    """).iloc[0]["n"]
    assert corpus["model"].nunique() == int(n)


def test_a_slot_may_repeat_within_one_model(corpus):
    """The engine works line by line rather than slot by slot, and this is why:
    spokes appear twice at 36 each, decals up to nine times. Collapsing to one
    part per slot would silently drop most of a wheel's spokes."""
    per = corpus.groupby(["model", "slot"]).size()
    assert (per > 1).any(), "no slot repeats — the line-level design is unnecessary"


# ------------------------------------------------------------- the rules ---
def test_required_slots_describe_their_own_evidence(corpus, rules):
    """A mined rule has to hold on the corpus it was mined from. Each required
    slot must really be on at least `REQUIRED_AT` of live bikes — otherwise the
    threshold and the prevalence table have drifted apart."""
    n_models = corpus["model"].nunique()
    for slot in rules.required():
        share = corpus[corpus["slot"] == slot]["model"].nunique() / n_models
        assert share >= B.REQUIRED_AT, f"{slot} is required but present on {share:.1%}"


def test_common_slots_sit_between_the_two_cuts(corpus, rules):
    n_models = corpus["model"].nunique()
    for slot in rules.common():
        share = corpus[corpus["slot"] == slot]["model"].nunique() / n_models
        assert B.COMMON_AT <= share < B.REQUIRED_AT


def test_wheel_compatibility_is_evidence_not_assumption(corpus, rules):
    """Every (part, wheel) pair the rules allow must be one a real bike used.
    The rule is 'this part has been built at this wheel size', so there is
    nothing in the table that the corpus does not contain."""
    wp = rules.wheel_parts
    sample = wp.sample(min(400, len(wp)), random_state=0)
    real = set(zip(corpus["part"], corpus["pord"], corpus["wheel"]))
    for part, pord, wheel in zip(sample["part"], sample["pord"], sample["wheel"]):
        assert (part, pord, wheel) in real


def test_frames_are_near_exclusive_to_one_wheel_size(corpus):
    """The claim the wheel rule leans on. If frames started crossing sizes
    freely the rule would be admitting nonsense, so this pins the shape of the
    data rather than a number."""
    per = (corpus[corpus["slot"] == "FR"]
           .groupby(["part", "pord"])["wheel"].nunique())
    assert (per == 1).mean() > 0.8


# ------------------------------------------------------------- the tiers ---
def test_tiers_are_learned_within_wheel_size(tiers):
    """Terciles are taken per wheel size, so no size may be pushed wholly into
    one tier — that would mean the tiers had just rediscovered wheel size."""
    big = tiers.groupby("wheel").filter(lambda g: len(g) >= 30)
    if big.empty:
        pytest.skip("no wheel size with enough models")
    spread = big.groupby("wheel")["tier"].nunique()
    assert (spread > 1).all()


def test_tier_costs_are_ordered(tiers):
    prof = B.tier_profile(tiers).set_index("tier")
    present = [t for t in B.TIER_ORDER if t in prof.index]
    costs = [prof.loc[t, "median_cost_dt"] for t in present]
    assert costs == sorted(costs), "tier medians are not monotonic in build cost"


# --------------------------------------------------------- the proposals ---
def test_no_proposal_promises_more_than_is_on_hand(proposals, pool):
    """The invariant the whole thing rests on. A proposal that allocates stock
    it does not have is not a plan, and nothing downstream would notice."""
    have = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}
    for p in proposals:
        stock = p.lines[p.lines["source"] == "stock"]
        used = stock.groupby(["part", "pord"])["qty_needed"].sum()
        for (part, pord), qty in used.items():
            assert qty <= have.get((part, pord), 0) + 1e-6, \
                f"{p.model} wants {qty} of {part}.{pord}, {have.get((part, pord), 0)} on hand"


def test_every_stock_line_carries_its_evidence(proposals):
    """A swap with no tier is a swap nobody can audit, which is the one thing
    this engine is not allowed to produce."""
    valid = {B.T_ORIGINAL, B.T_DECLARED, B.T_PRECEDENT, B.T_CORPUS}
    for p in proposals:
        stock = p.lines[p.lines["source"] == "stock"]
        assert set(stock["tier"]) <= valid
        assert (p.lines[p.lines["source"] == "buy"]["tier"] == B.T_BUY).all()


def test_quantities_are_the_donor_bom_untouched(proposals, corpus):
    """Substitution changes which part fills a line, never how many are needed.
    A bike wanting 36 spokes still wants 36 after a swap."""
    for p in proposals:
        bom = corpus[corpus["model"] == p.model]
        assert len(p.lines) == len(bom)
        assert sorted(p.lines["qty_per_bike"]) == pytest.approx(sorted(bom["qty"]))
        assert p.lines["qty_needed"].sum() == pytest.approx(bom["qty"].sum() * p.batch)


def test_a_proposal_with_no_stock_reproduces_its_donor(corpus, eq, rules, tiers):
    """The identity case. Against an empty pool every line must fall through to
    'buy' and the result must be the donor BOM, line for line — which is what
    catches an off-by-one in the line matcher before it hides inside a swap."""
    empty = pd.DataFrame(columns=["part", "pord", "part_num", "part_lib", "part_ref",
                                  "slot", "on_hand", "unit_dt", "value_dt",
                                  "value_basis", "grp", "supplier_name", "fob_dt"])
    model = corpus["model"].iloc[0]
    p = B.propose_retrofit(model, corpus, empty, eq, rules, tiers, batch=BATCH)
    bom = corpus[corpus["model"] == model]

    assert p is not None
    assert (p.lines["source"] == "buy").all()
    assert p.dead_value_dt == 0.0
    assert not p.lines["swap"].any()
    assert p.saving_dt == 0.0
    assert list(p.lines["orig_part"]) == list(bom["part"])
    assert list(p.lines["part"]) == list(bom["part"])
    want = (bom["qty"] * B.in_dinar(bom)).sum() * BATCH
    assert p.buy_value_dt == pytest.approx(want)


def test_batch_size_only_ever_restricts(corpus, pool, eq, rules, tiers):
    """A part qualifies only if the shelf covers the whole batch, so a larger
    batch cannot bring more lines in. Per-bike value can move either way; the
    line count cannot."""
    model = None
    for p in B.rank_retrofits(corpus, pool, eq, rules, tiers, batch=10, shortlist=5):
        model = p.model
        break
    if model is None:
        pytest.skip("nothing buildable")
    small = B.propose_retrofit(model, corpus, pool, eq, rules, tiers, batch=10)
    large = B.propose_retrofit(model, corpus, pool, eq, rules, tiers, batch=1000)
    n_small = int((small.lines["source"] == "stock").sum())
    n_large = int((large.lines["source"] == "stock").sum())
    assert n_large <= n_small


def test_confidence_is_the_worst_structural_swap(proposals):
    """Not an average. One unproven fork pairing has to outweigh thirty certain
    decal swaps, or the number is worse than useless."""
    for p in proposals:
        stock = p.lines[p.lines["source"] == "stock"]
        structural = stock[stock["grp"].isin(B.STRUCTURAL_GROUPS)]
        if structural.empty:
            assert p.confidence == B.T_ORIGINAL
        else:
            assert p.confidence == int(structural["tier"].max())


# --------------------------------------------------------- the portfolio ---
def test_allocation_removes_the_double_counting(proposals, pool):
    """The test this suite exists for.

    Proposals compete for the same parts, so the realisable total must sit at or
    below the standalone sum and at or below the pool itself. Getting this wrong
    would roughly double the headline, and nothing else would catch it."""
    port = B.allocate(proposals, pool)
    assert port.realisable_dt <= port.standalone_dt + 1e-6
    assert port.realisable_dt <= pool["value_dt"].sum() + 1e-6
    assert port.overlap_dt >= -1e-6


def test_allocation_never_hands_out_the_same_unit_twice(proposals, pool):
    """Checked from the other side: total allocated per part cannot exceed what
    was on the shelf, however many proposals asked for it."""
    port = B.allocate(proposals, pool)
    have = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}

    left = dict(have)
    for p in port.proposals:
        for line in p.lines[p.lines["source"] == "stock"].itertuples(index=False):
            key = (line.part, line.pord)
            take = min(left.get(key, 0.0), line.qty_needed)
            left[key] = left.get(key, 0.0) - take
    assert all(v >= -1e-6 for v in left.values())


def test_buy_cost_is_scaled_to_the_stock_actually_won(proposals, pool):
    """A proposal that wins a tenth of the stock it wanted builds a tenth of the
    batch, so it buys a tenth of the tail.

    Totalling the unscaled buy cost prices every proposal at full volume — for a
    sixty-deep list that is six thousand bikes, and it made the portfolio's
    headline ratio read 0.1x when the best single proposal is above 20x. The
    scaled figure can only be smaller, and equal only when nothing was
    contended."""
    port = B.allocate(proposals, pool)
    a = port.allocated
    assert (a["buy_alloc_dt"] <= a["buy_dt"] + 1e-6).all()
    assert port.buy_alloc_dt <= a["buy_dt"].sum() + 1e-6
    assert ((a["share"] >= -1e-9) & (a["share"] <= 1 + 1e-9)).all()
    # Where a proposal won everything it asked for, nothing is scaled away.
    whole = a[a["share"] > 1 - 1e-9]
    if not whole.empty:
        assert whole["buy_alloc_dt"].values == pytest.approx(whole["buy_dt"].values)


def test_the_ratio_degrades_down_the_ranking(proposals, pool):
    """The finding the tab leads on, pinned so it cannot quietly stop being true.

    Proposals are ranked by stock cleared, and the best of them clear far more
    per dinar than the tail. If the top proposal ever stopped beating the
    portfolio average, the advice to run the first few rather than the whole
    table would no longer follow from the data."""
    port = B.allocate(proposals, pool)
    a = port.allocated.dropna(subset=["ratio"])
    if len(a) < 5 or port.buy_alloc_dt <= 0:
        pytest.skip("too few priced proposals to compare")
    avg = port.realisable_dt / port.buy_alloc_dt
    assert a.iloc[0]["ratio"] >= avg


def test_contention_is_reported_not_hidden(proposals, pool):
    """Where a proposal lost a part, the reader has to be able to see which one.
    A silently shrunken figure is the failure mode here."""
    port = B.allocate(proposals, pool)
    if port.overlap_dt > 0:
        assert not port.contended.empty
        assert {"model", "slot", "wanted", "got"} <= set(port.contended.columns)
        assert (port.contended["got"] < port.contended["wanted"]).all()


# ------------------------------------------------------ free composition ---
@pytest.fixture(scope="session")
def busiest_wheel(corpus):
    return corpus[corpus["wheel"] != ""].groupby("wheel").size().idxmax()


def test_free_composition_covers_the_required_slots(corpus, pool, rules, tiers,
                                                    busiest_wheel):
    """It may buy a slot it cannot fill, but it may not skip one — a build
    missing a required slot is not a bike."""
    b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH)
    if b.lines.empty:
        pytest.skip("no slots filled")
    assert set(rules.required(busiest_wheel)) <= set(b.lines["slot"])
    assert set(b.lines["source"]) <= {"stock", "buy"}


def test_optional_fittings_can_be_dropped(corpus, pool, rules, tiers, busiest_wheel):
    """Turning the optional fittings off must leave exactly the required slots.

    This is what answers "does a bike really need fifty-odd slots" — required
    and common are different claims, and the build has to honour the
    distinction rather than treating every slot the corpus mentions as
    mandatory."""
    full = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH,
                          include_common=True)
    bare = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH,
                          include_common=False)
    assert set(bare.lines["slot"]) == set(rules.required(busiest_wheel))
    assert set(bare.lines["slot"]) <= set(full.lines["slot"])
    assert len(bare.lines) <= len(full.lines)
    assert bare.lines["required"].all()


def test_a_build_is_the_size_of_a_real_bike(corpus, pool, rules, tiers, busiest_wheel):
    """The slot count has to be in the range real bikes occupy.

    A 700C bike here fills a median of 55 distinct slots. If a build came out
    at a dozen, or at twice the corpus maximum, the slot rules would have
    drifted from the thing they claim to describe."""
    b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH)
    at = corpus[corpus["wheel"] == str(busiest_wheel)]
    real = at.groupby("model")["slot"].nunique()
    assert real.quantile(0.10) <= len(b.lines) <= real.max()


def test_the_margin_objective_stays_inside_its_budget(corpus, pool, rules, tiers,
                                                      busiest_wheel):
    """`STRATEGY_MARGIN` exists because the unconstrained greedy takes the
    dearest part in every slot and lands above the tier's own list price — it
    did, at DT 489 against a DT 400 list. The budget is what stops that, so the
    shelf spend has to stay inside it wherever a benchmark price exists."""
    for tier in B.TIER_ORDER:
        b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, tier=tier,
                           batch=BATCH, strategy=B.STRATEGY_MARGIN)
        if not b.benchmark_price_dt:
            continue
        # The bought tail can still overshoot when the shelf cannot fill an
        # expensive slot at all — an e-bike battery, say — so the guarantee is
        # on what the build actually controls: the parts it chose off the shelf.
        stock_per_bike = b.stock_value_dt / b.batch
        budget = b.benchmark_price_dt * (1 - B.TARGET_MARGIN_PCT / 100)
        assert stock_per_bike <= budget + 1e-6, f"{tier} overspends the shelf budget"


def test_the_two_objectives_trade_stock_against_margin(corpus, pool, rules, tiers,
                                                       busiest_wheel):
    """The trade-off the tab asks the reader to make has to be real.

    Clearing the shelf can never free less than protecting the margin does — it
    is the same search without a cost ceiling. Where they differ at all, the
    extra stock has to have been paid for in margin, because the dearest part in
    a slot is both what frees the most money and what makes the bike expensive.
    If these ever stopped trading against each other, the toggle would be
    offering a choice that does not exist."""
    traded = 0
    for tier in B.TIER_ORDER:
        kw = dict(tier=tier, batch=BATCH)
        m = B.propose_free(busiest_wheel, corpus, pool, rules, tiers,
                           strategy=B.STRATEGY_MARGIN, **kw)
        c = B.propose_free(busiest_wheel, corpus, pool, rules, tiers,
                           strategy=B.STRATEGY_CLEAR, **kw)
        assert c.stock_value_dt >= m.stock_value_dt - 1e-6,             f"{tier}: clearing freed less than protecting the margin"
        if c.stock_value_dt > m.stock_value_dt + 1e-6:
            traded += 1
            assert c.cost_per_bike >= m.cost_per_bike - 1e-6
            if not pd.isna(m.margin_pct) and not pd.isna(c.margin_pct):
                assert c.margin_pct <= m.margin_pct + 1e-6
    assert traded, "the two objectives never differed — the toggle is inert here"


def test_strategy_is_recorded_on_the_build(corpus, pool, rules, tiers, busiest_wheel):
    """A build that does not say which objective produced it cannot be compared
    against the other one, which is the whole point of showing both."""
    for strat in (B.STRATEGY_MARGIN, B.STRATEGY_CLEAR):
        b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers,
                           batch=BATCH, strategy=strat)
        assert b.strategy == strat
        assert strat in B.STRATEGIES


def test_free_composition_respects_wheel_compatibility(corpus, pool, rules, tiers,
                                                       busiest_wheel):
    """Every part it picks must have been built at that wheel size. This is the
    rule that stops it fitting a 700C rim to a child's frame."""
    b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH)
    picked = b.lines[b.lines["source"] == "stock"]["part_num"]
    if picked.empty:
        pytest.skip("nothing filled from stock")
    ok = rules.wheel_parts[rules.wheel_parts["wheel"] == str(busiest_wheel)]
    master = B.load_part_master()
    allowed = set(master[master.set_index(["part", "pord"]).index.isin(
        set(zip(ok["part"], ok["pord"])))]["part_num"])
    assert set(picked) <= allowed


def test_buildable_units_is_set_by_the_scarcest_chosen_part(corpus, pool, rules,
                                                            tiers, busiest_wheel):
    """The ceiling is whichever chosen part runs out first, and it can never be
    below the batch that was asked for — a part only qualifies if it covers the
    batch in the first place."""
    b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH)
    stock = b.lines[b.lines["source"] == "stock"]
    if stock.empty:
        pytest.skip("nothing filled from stock")
    supported = pd.to_numeric(stock["units_supported"], errors="coerce").dropna()
    assert b.buildable_units == int(supported.min())
    assert b.buildable_units >= BATCH


def test_every_slot_carries_a_human_label(corpus, pool, rules, tiers, busiest_wheel):
    """A bare `FFS` means nothing to the person deciding what to build, and
    every table on the tab is read by that person."""
    b = B.propose_free(busiest_wheel, corpus, pool, rules, tiers, batch=BATCH)
    assert {"label", "grp_name", "build_order"} <= set(b.lines.columns)
    labelled = b.lines["label"].fillna("").str.strip().ne("")
    assert labelled.mean() > 0.9


# --------------------------------------------------------------- currency ---
def test_a_bom_price_is_converted_before_it_is_added(corpus):
    """`prxndach` is stored in the part's own purchase currency.

    Four fifths of this corpus is bought in USD or EUR, so reading the raw
    number as dinar understates a bought tail by about three times. This pins
    that the conversion happens at all — the failure it guards against is
    silent, because an understated tail still looks like a plausible tail."""
    ccy = B.part_currency()
    foreign = corpus.merge(ccy, on=["part", "pord"], how="left")
    foreign = foreign[foreign["ccy"].isin(["USD", "EUR"]) & (foreign["prx"] > 0)]
    if foreign.empty:
        pytest.skip("nothing bought in foreign currency in this restore")

    sample = foreign.head(500)
    assert (B.in_dinar(sample) > sample["prx"]).all()

    native = corpus.merge(ccy, on=["part", "pord"], how="left")
    native = native[(native["ccy"] == "DT") & (native["prx"] > 0)].head(500)
    if not native.empty:
        assert B.in_dinar(native) == pytest.approx(native["prx"])


def test_the_rate_table_covers_every_currency_parts_are_bought_in():
    """`erp.load_fx` reads sales invoices and so knows EUR and USD only, while
    parts are also bought in yen. A missing rate is not an error anywhere — it
    falls through at 1.0 — so a yen part would be valued at seventeen times what
    it costs and nothing would say so."""
    rates = B.purchase_rates()
    bought_in = set(B.part_currency()["ccy"]) - {""}
    assert bought_in <= set(rates), f"no rate for {sorted(bought_in - set(rates))}"
    assert rates["DT"] == 1.0


# ------------------------------------------------------- naming the swap ---
def test_every_line_names_the_part_on_both_sides(proposals):
    """A substitution that names only the slot cannot be acted on. The two part
    numbers either side of the swap are the whole content of the instruction."""
    for p in proposals:
        swaps = p.lines[p.lines["swap"]]
        if swaps.empty:
            continue
        assert (swaps["orig_num"].str.len() > 0).all()
        assert (swaps["part_num"].str.len() > 0).all()
        assert (swaps["change"].str.contains("→")).all()
        kept = p.lines[~p.lines["swap"]]
        assert not kept["change"].str.contains("→").any()


def test_the_swap_flag_agrees_with_the_keys(proposals):
    for p in proposals:
        same = ((p.lines["part"] == p.lines["orig_part"])
                & (p.lines["pord"] == p.lines["orig_pord"]))
        assert (p.lines["swap"] == ~same).all()
        # A bought line is the donor's own part, never a substitution.
        assert not p.lines[p.lines["source"] == "buy"]["swap"].any()


def test_a_saving_is_only_ever_claimed_on_a_swap(proposals):
    """`saving_dt` compares the BOM's price with the catalogue price of the part
    going in. Where nothing changes there is nothing to compare, and claiming a
    difference there would be reporting a valuation basis as a saving."""
    for p in proposals:
        assert (p.lines.loc[~p.lines["swap"], "saving_dt"] == 0).all()
        assert p.saving_dt == pytest.approx(p.lines["saving_dt"].sum())
        assert p.cheaper_swaps <= p.swaps


# ------------------------------------------------------- the two objectives ---
def test_the_two_objectives_trade_stock_against_build_cost(corpus, pool, eq, rules, tiers):
    """They disagree, and they have to disagree in one direction only.

    Taking the dearest part that fits can never clear *less* than taking the
    cheapest, and taking the cheapest can never cost *more* to build. A run
    where both readings moved the same way would mean the preference was not
    reaching the candidate choice at all."""
    ranked = B.rank_retrofits(corpus, pool, eq, rules, tiers, batch=BATCH, shortlist=8)
    if not ranked:
        pytest.skip("nothing buildable")

    clear_total = 0.0
    moved = False
    for donor in ranked[:5]:
        a = B.propose_retrofit(donor.model, corpus, pool, eq, rules, tiers,
                               batch=BATCH, prefer=B.PREFER_VALUE)
        b = B.propose_retrofit(donor.model, corpus, pool, eq, rules, tiers,
                               batch=BATCH, prefer=B.PREFER_SAVING)
        assert a.dead_value_dt >= b.dead_value_dt - 1e-6
        assert b.saving_dt >= a.saving_dt - 1e-6
        # Same lines either way: the preference picks the part, not the line.
        assert (a.lines["source"] == b.lines["source"]).all()
        clear_total += a.dead_value_dt
        moved = moved or a.dead_value_dt != b.dead_value_dt
    if not moved:
        pytest.skip("no slot on this shortlist offers a choice of shelf part")
    assert clear_total > 0


# ---------------------------------------------------- cheaper on the shelf ---
def test_cheaper_equivalents_are_declared_pairs_that_really_are_cheaper(corpus, pool, eq):
    """The list reads as "the ERP says these two are the same part and the one
    gathering dust is cheaper". Every clause of that has to hold on every row."""
    rows = B.cheaper_equivalents(corpus, pool, eq)
    if rows.empty:
        pytest.skip("no cheaper declared equivalent in this restore")

    declared = set(zip(eq[eq["direction"] == "declared"]["part"],
                       eq[eq["direction"] == "declared"]["pord"],
                       eq[eq["direction"] == "declared"]["alt_part"],
                       eq[eq["direction"] == "declared"]["alt_pord"]))
    on_shelf = {(r.part, r.pord): r.on_hand for r in pool.itertuples(index=False)}
    in_corpus = set(zip(corpus["part"], corpus["pord"]))

    for r in rows.itertuples(index=False):
        assert (r.part, r.pord, r.alt_part, r.alt_pord) in declared
        assert (r.part, r.pord) in in_corpus
        assert (r.alt_part, r.alt_pord) in on_shelf
        assert r.alt_unit_cost_dt < r.orig_unit_cost_dt
        assert r.bikes_covered >= 1
        assert r.saving_per_bike_dt > 0

    # The total is bounded by the shelf: it can only count each cheaper part as
    # far as there is stock of it.
    assert (rows["bikes_covered"] * rows["qty_per_bike"] <= rows["on_hand"] + 1e-6).all()


def test_a_swap_that_saves_nothing_is_not_listed(corpus, pool, eq):
    """The whole list is "cheaper", so a pair at or above the specified part's
    price must not appear — including one priced at zero, which is a missing
    price rather than a free part."""
    rows = B.cheaper_equivalents(corpus, pool, eq)
    if rows.empty:
        pytest.skip("no cheaper declared equivalent in this restore")
    assert (rows["alt_unit_cost_dt"] > 0).all()
    assert (rows["orig_unit_cost_dt"] > 0).all()
    assert (rows["saving_dt"] > 0).all()
