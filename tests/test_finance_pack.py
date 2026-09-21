"""The P&L pack is a committed snapshot parsed by layout, so the two things
that can quietly break it are a layout change in a new export and a mistake in
our own walk down to net profit. Both show up here.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import finance_pack as fp  # noqa: E402

YEARS = fp.available_years()
pytestmark = pytest.mark.skipif(not YEARS, reason="no pack in data/finance/")


@pytest.mark.parametrize("year", YEARS)
def test_walk_ties_to_the_packs_own_totals(year):
    """Our revenue → net walk must land on the pack's printed P.B.I.T, tax and
    Total Net. If it doesn't, the sheet's layout moved and the figures on the
    Overview are no longer the pack's figures."""
    assert fp.check(year) == []


@pytest.mark.parametrize("year", YEARS)
def test_cost_of_sales_is_not_the_sum_of_its_lines(year):
    """Guards the one trap in this sheet.

    Four cost-of-sales lines are month-end *balances* (raw and finished-goods
    stock), so totalling the section's members overstates it by an order of
    magnitude — 825M against a real 23.8M when this was first written. The
    parser drops them and takes the pack's own `999` subtotal instead. This
    asserts the dropped lines really are absent, so a future edit can't quietly
    reintroduce them."""
    df = fp.load_pl(year)
    cos = df[df["section_code"] == "020"]
    members = cos[~cos["is_total"]]
    assert not members.empty, "cost of sales lost its member lines"
    assert set(members["line_code"]).isdisjoint({"60", "70", "80", "90"})

    section_total = float(cos[cos["is_total"]]["amount_dt"].sum())
    naive = float(members["amount_dt"].sum())
    assert section_total == pytest.approx(fp.summary(year)["cost_of_sales"])
    # The member lines are purchases only, so they under-run the real figure —
    # the point being that they are not interchangeable with it.
    assert naive != pytest.approx(section_total, rel=0.01)


@pytest.mark.parametrize("year", YEARS)
def test_trailing_summary_rows_are_not_in_a_section(year):
    """P.B.I.T / tax / Total Net carry no line code and sit after the last
    section header. Swept into a section they inflate Other income by ~15x."""
    df = fp.load_pl(year)
    other = df[(df["section_code"] == "100") & (~df["is_total"])]
    assert float(other["amount_dt"].sum()) == pytest.approx(
        fp.summary(year)["other_income"], rel=0.001)


@pytest.mark.parametrize("year", YEARS)
def test_summary_is_internally_consistent(year):
    s = fp.summary(year)
    assert s["gross_margin"] == pytest.approx(s["revenue"] - s["cost_of_sales"])
    assert s["pbit"] == pytest.approx(
        s["gross_margin"] - s["operating_costs"] + s["other_income"])
    assert s["net"] == pytest.approx(s["pbit"] - s["tax"])
    assert 0 < s["net"] < s["gross_margin"] < s["revenue"]
