"""The figures that have no GPAO screen to tie to.

The five GPAO ports are pinned against the GPAO's own export and its own SQL.
The company scoreboard, the margin bridge and the year-on-year deltas are ours,
so there is nothing external to compare them with — only their own invariants:
they have to add up, and they have to compare like with like.

Two of these exist because something was actually wrong:

* **`like_for_like` cut on the month, not the day.** On data ending 19 Aug 2026
  it put 19 days of August 2026 against all 31 days of August 2025 — DT 2.0M of
  prior-year revenue with no counterpart — and overstated the revenue decline by
  1.6 points, while the caption promised "a part year isn't measured against a
  full one". The tail test below fails if the month-level cut ever comes back.
* **The margin bridge is only worth reading if it is exact.** Its whole claim is
  that six effects sum to the change in margin to the cent. A "simplification"
  that breaks the identity turns an audit into a story.

Needs the ERP; skips when it isn't reachable.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erp  # noqa: E402
from theme import palette  # noqa: E402
from views.management._common import Ctx, like_for_like  # noqa: E402
from views.management import bridge as BR  # noqa: E402
from views.management.page_overview import _year_agg  # noqa: E402

TOL = 0.01          # one cent


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def scoped(erp_up):
    """The same scope the Overview page opens on: bikes only, last five years."""
    sales = erp.load_sales(start_year=2019, bikes_only=True)
    if sales.empty:
        pytest.skip("no sales rows")
    years = sorted(int(y) for y in sales["yr"].unique())
    lo, hi = max(years[0], years[-1] - 5), years[-1]
    scope = sales[(sales["yr"] >= lo) & (sales["yr"] <= hi)].copy()
    ctx = Ctx(ccy="DT", fx=erp.load_fx(), P=palette(), years=years, yr_lo=lo, yr_hi=hi,
              dist_label="all distributors", data_end=sales["datf"].max())
    return scope, ctx


# ------------------------------------------------------------- the scoreboard ---
def test_scoreboard_is_the_sum_of_its_lines(scoped):
    """Every headline tile recomputed straight off the invoice lines."""
    scope, ctx = scoped
    ya = _year_agg(scope).set_index("yr")
    for y in ya.index:
        raw = scope[scope["yr"] == y]
        rev = float(raw["line_rev_dt"].sum())
        cogs = float(raw["line_cogs_dt"].sum())
        qty = float(raw["qte"].sum())
        assert ya.at[y, "revenue"] == pytest.approx(rev, abs=TOL)
        assert ya.at[y, "margin"] == pytest.approx(rev - cogs, abs=TOL)
        assert ya.at[y, "margin_pct"] == pytest.approx((rev - cogs) / rev * 100, abs=1e-9)
        assert ya.at[y, "units"] == pytest.approx(qty, abs=TOL)
        assert ya.at[y, "asp"] == pytest.approx(rev / qty, abs=1e-9)


def test_line_margin_agrees_with_revenue_less_cogs(scoped):
    """Other tabs sum `line_margin_dt` directly; it must be the same measure."""
    scope, _ = scoped
    assert float(scope["line_margin_dt"].sum()) == pytest.approx(
        float(scope["line_rev_dt"].sum()) - float(scope["line_cogs_dt"].sum()), abs=1.0)


# -------------------------------------------------------------- like-for-like ---
def test_like_for_like_cuts_on_the_day_not_the_month(scoped):
    """The regression that started this file.

    Both compared years must stop on the data's own day. A month-level cut
    leaves the prior year's late-month tail in and silently exaggerates every
    year-on-year delta on the page."""
    scope, ctx = scoped
    p = ctx.partial_year
    if p is None:
        pytest.skip("newest year is complete; nothing to trim")
    lfl = like_for_like(scope, ctx)
    for y in (p, p - 1):
        last = lfl[lfl["yr"] == y]["datf"].max()
        assert (last.month, last.day) <= (ctx.ytd_month, ctx.ytd_day), (
            f"{y} keeps rows past {ctx.ytd_month:02d}-{ctx.ytd_day:02d}: last is {last:%d %b}")


def test_like_for_like_actually_trims_the_prior_year(scoped):
    """The prior year is the one with a tail to lose. If nothing is dropped the
    cut isn't happening at all."""
    scope, ctx = scoped
    p = ctx.partial_year
    if p is None:
        pytest.skip("newest year is complete; nothing to trim")
    prev = scope[scope["yr"] == p - 1]
    if prev.empty or prev["datf"].max() <= pd.Timestamp(
            year=p - 1, month=ctx.ytd_month, day=ctx.ytd_day):
        pytest.skip("prior year has no tail past the cut-off")
    kept = like_for_like(scope, ctx)
    assert len(kept[kept["yr"] == p - 1]) < len(prev)


def test_like_for_like_leaves_other_years_whole(scoped):
    """Only the two compared years are trimmed — the chart behind the tiles
    still shows full years."""
    scope, ctx = scoped
    p = ctx.partial_year
    if p is None:
        pytest.skip("newest year is complete")
    lfl = like_for_like(scope, ctx)
    others = [y for y in scope["yr"].unique() if y not in (p, p - 1)]
    for y in others:
        assert len(lfl[lfl["yr"] == y]) == len(scope[scope["yr"] == y])


# --------------------------------------------------------------- the bridge ---
@pytest.mark.parametrize("grain", ["design_key", "article"])
def test_bridge_effects_sum_to_the_change_exactly(scoped, grain):
    """Six bars, to the cent. This is the bridge's entire claim."""
    scope, ctx = scoped
    years = sorted(int(y) for y in scope["yr"].unique())
    checked = 0
    for y_cur in years[1:]:
        b = BR.compute(scope, ctx, y_cur - 1, y_cur, grain)
        if b is None:
            continue
        checked += 1
        assert sum(b.effects[k] for k in BR.BRIDGE_STEPS) == pytest.approx(
            b.m1 - b.m0, abs=TOL), f"{grain} {y_cur - 1}->{y_cur}"
    assert checked, "no year pair was comparable"


@pytest.mark.parametrize("grain", ["design_key", "article"])
def test_bridge_per_model_table_reconciles_to_the_middle_bars(scoped, grain):
    """The drill-down table has to account for volume+mix+price+cost and
    nothing else — new and dropped models have no other year to compare to."""
    scope, ctx = scoped
    years = sorted(int(y) for y in scope["yr"].unique())
    for y_cur in years[1:]:
        b = BR.compute(scope, ctx, y_cur - 1, y_cur, grain)
        if b is None:
            continue
        core = sum(b.effects[k] for k in ("volume", "mix", "price", "cost"))
        cols = b.per_model[["price_effect", "cost_effect", "volmix_effect"]]
        assert float(cols.to_numpy().sum()) == pytest.approx(core, abs=TOL)
        assert float(b.per_model["total_effect"].sum()) == pytest.approx(core, abs=TOL)


def test_bridge_trims_the_partial_year_on_the_day_too(scoped):
    """The bridge has its own trim because the reader can pick any year pair.
    It must cut the same way `like_for_like` does."""
    scope, ctx = scoped
    p = ctx.partial_year
    if p is None:
        pytest.skip("newest year is complete")
    cut = BR._trim_to_common_period(scope, ctx, p - 1, p)
    for y in (p, p - 1):
        last = cut[cut["yr"] == y]["datf"].max()
        assert (last.month, last.day) <= (ctx.ytd_month, ctx.ytd_day)
