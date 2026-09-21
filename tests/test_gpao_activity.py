"""The Activity tab's whole claim is that it *is* the GPAO's report.

`gpao_activity.py` is a hand port of `frmActiviteComp1.vb`, so the thing that can
quietly break it is someone tidying a join or a filter that looked redundant and
was in fact the reason a total landed where it did. These tests run the port
against the committed spreadsheet the GPAO itself exported and demand the
section totals match to the cent.

They need the ERP, so they skip when it isn't reachable.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erp  # noqa: E402
import gpao_activity as G  # noqa: E402

# The export's own filename carries the window's end date; the GPAO was run
# year-to-date, which is what its header says ("01/01/2026 au 21/09/2026").
EXPORT = ROOT / "data" / "finance" / "erp-report-ytd-2026-09-21.xlsx"

# The three consumption sections take minutes each (a correlated subquery over a
# 4.2M-row ledger), so they are opt-in here exactly as they are in the tab.
SLOW = {"15", "16", "17"}

pytestmark = pytest.mark.skipif(not EXPORT.exists(), reason="no GPAO export committed")


def _window() -> tuple[dt.date, dt.date]:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", EXPORT.name)
    end = dt.date(*(int(g) for g in m.groups()))
    return dt.date(end.year, 1, 1), end


def _expected() -> dict[str, tuple[float, float]]:
    """Each section's TOTAL row, straight off the GPAO's own spreadsheet."""
    import openpyxl
    ws = openpyxl.load_workbook(EXPORT, data_only=True).active
    out, code = {}, None
    for r in range(1, ws.max_row + 1):
        a, b = ws.cell(r, 1).value, ws.cell(r, 2).value
        if a and str(a).strip().startswith(":"):
            code = str(a).strip()[2:].strip()[:2]
        if code and b and "TOTAL" in str(b):
            out[code] = (float(ws.cell(r, 3).value), float(ws.cell(r, 5).value))
    return out


EXPECTED = _expected()
FAST = sorted(set(EXPECTED) - SLOW)


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.mark.parametrize("code", FAST)
def test_section_total_matches_the_gpao_export(code, erp_up):
    """Both years of every exported section, to the cent.

    A cent of drift means a join or a filter moved, and the tab is no longer
    showing what the GPAO shows."""
    d1, d2 = _window()
    df = G.load_section(code, d1, d2)
    total = df[df["kind"] == "total"]
    assert not total.empty, f"section {code} produced no TOTAL row"
    got = (float(total["v1"].iloc[0]), float(total["v2"].iloc[0]))
    want = EXPECTED[code]
    assert got[0] == pytest.approx(want[0], abs=0.01), f"section {code} current year"
    assert got[1] == pytest.approx(want[1], abs=0.01), f"section {code} prior year"


@pytest.mark.slow
@pytest.mark.parametrize("code", sorted(SLOW & set(EXPECTED)))
def test_consumption_section_total_matches(code, erp_up):
    """Run with `-m slow`. ~20s per section.

    Skips when the costing database is restored short of the window, which it is
    in the current copy — `detart` ends 2026-06-30 against sales to 2026-08-19,
    so these sections legitimately come out low and there is nothing to compare.
    The port itself is verified against the GPAO's literal SQL in
    `test_consumption_matches_the_gpao_literal_sql`."""
    d1, d2 = _window()
    covered = G.consumption_coverage()
    if covered is None or covered.date() < d2:
        pytest.skip(f"eurocycles_db_calc.detart ends {covered} — short of {d2}")
    total = G.load_section(code, d1, d2).pipe(lambda d: d[d["kind"] == "total"])
    got = (float(total["v1"].iloc[0]), float(total["v2"].iloc[0]))
    want = EXPECTED[code]
    assert got[0] == pytest.approx(want[0], abs=0.05)
    assert got[1] == pytest.approx(want[1], abs=0.05)


@pytest.mark.slow
def test_the_three_consumption_sections_agree_with_each_other(erp_up):
    """15, 16 and 17 slice one number three ways — origin, supplier, group — so
    their totals must be identical. They are in the GPAO's export too. This holds
    whether or not the costing database covers the window, so it is the parity
    check that still bites on a short restore."""
    d1, d2 = _window()
    totals = {c: float(G.load_section(c, d1, d2).pipe(
        lambda d: d[d["kind"] == "total"])["v1"].iloc[0]) for c in sorted(SLOW)}
    assert len(set(round(v, 2) for v in totals.values())) == 1, totals


def test_the_export_covers_every_section_we_claim_parity_on():
    """The GPAO's bulk export skips section 03, so 18 of our 19 are checkable.
    If that ever changes, this test says so rather than silently shrinking."""
    assert set(EXPECTED) == {s.code for s in G.SECTIONS} - {"03"}


def test_revenue_sections_disagree_with_each_other(erp_up):
    """Defect #1, pinned as a fact rather than a bug to fix.

    Sections 01, 04 and 06 are all "chiffre d'affaire" and all three differ,
    because they filter differently. We reproduce that on purpose. If they ever
    come out equal, someone has 'fixed' the port and it no longer ties to the
    GPAO — which is the one thing this module is for."""
    d1, d2 = _window()
    totals = {c: float(G.load_section(c, d1, d2).pipe(
        lambda d: d[d["kind"] == "total"])["v1"].iloc[0]) for c in ("01", "04", "06")}
    assert len(set(round(v, 2) for v in totals.values())) == 3, totals


def test_asp_reproduces_the_gpao_rounding_defect(erp_up):
    """Defect #3: the GPAO rounds numerator and denominator to whole dinars
    before dividing. `gpao_delta_pct` must reproduce that and `true_delta_pct`
    must not, or the tab's tooltip is claiming a difference that isn't there."""
    asp = G.load_asp(*_window())
    assert asp.gpao_delta_pct != pytest.approx(asp.true_delta_pct, abs=1e-6)
    assert asp.true_delta_pct == pytest.approx(
        asp.gpao_delta / asp.gpao_prev * 100, rel=1e-12)
