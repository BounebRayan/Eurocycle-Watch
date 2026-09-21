"""The landed-cost port, checked against the ERP's own stored values.

There is no committed spreadsheet for this report the way there is for the
Activity one, so parity is pinned differently: `facturef_det.coef` is a value the
GPAO **wrote into the database**, so recomputing it from the same inputs and
getting the same number proves the formula port, bug and all.

The rest of the tests pin the three defects. They are written to fail if someone
"fixes" the port, because the point of the port is to show what the GPAO does.

Needs the ERP; skips when it isn't reachable.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erp  # noqa: E402
import gpao_landed as L  # noqa: E402

WINDOW = (dt.date(2026, 1, 1), dt.date(2026, 6, 30))


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def audit(erp_up):
    df = L.load_coef_audit(start_year=2024)
    if df.empty:
        pytest.skip("no supplier invoices since 2024 in this restore")
    return df


def test_our_coefficient_reproduces_the_one_the_erp_stored(audit):
    """The parity check for this module.

    `coef` is written by `frmDroitDouane.vb` / `frmFactTransport.vb` and stored on
    every `facturef_det` row. Recomputing it here from `facturef`'s charge columns
    has to land on the stored value — otherwise our charge pool, our quantity base
    or our credit-line handling is not the GPAO's."""
    a = audit.dropna(subset=["coef_stored"])
    a = a[a["coef_stored"] != 0]
    assert len(a) > 100, "too few stored coefficients to be a meaningful check"
    close = (a["coef_gpao"] - a["coef_stored"]).abs() <= 0.01
    rate = close.mean()
    assert rate > 0.95, (
        f"only {rate:.1%} of stored coefficients reproduce; "
        f"worst gap {(a['coef_gpao'] - a['coef_stored']).abs().max():,.2f} DT")


def test_the_credit_line_formula_is_the_broken_one(audit):
    """Defect 1, pinned.

    Where an invoice has credit lines, the GPAO subtracts `totPrxNeg × 100 ÷
    totFrais` — a ratio — from a dinar amount. `coef_gpao` must reproduce that and
    `coef_net` must be the sane net-off, or the tab is claiming a difference that
    does not exist."""
    a = audit[audit["has_credit"] & (audit["tot_frais"] > 0)]
    if a.empty:
        pytest.skip("no credit-line invoices in this restore")
    assert not (a["coef_gpao"] - a["coef_net"]).abs().le(1e-9).all(), \
        "the GPAO and net-off readings are identical — the defect is not reproduced"
    assert (a["coef_gpao"] < 0).any(), \
        "no invoice lands on a negative coefficient; the blast radius claim is stale"


def test_negative_coefficients_are_driven_by_a_small_charge_pool(audit):
    """The signature of the bug: the stray term is `×100 ÷ totFrais`, so the
    smaller the pool the worse it gets. If that correlation ever inverts, the
    explanation in the docs is wrong."""
    a = audit[audit["has_credit"] & (audit["tot_frais"] > 0)]
    if a.empty or not (a["coef_gpao"] < 0).any():
        pytest.skip("no negative coefficients in this restore")
    neg = a[a["coef_gpao"] < 0]["tot_frais"].median()
    pos = a[a["coef_gpao"] >= 0]["tot_frais"].median()
    assert neg < pos, f"negative-coef pool median {neg} not below positive {pos}"


@pytest.fixture(scope="session")
def orders(erp_up):
    df = L.load_of_landed(*WINDOW)
    if df.empty:
        pytest.skip("no production orders in the test window")
    return df


def test_landed_is_material_plus_freight(orders):
    """Arithmetic guard on the two landed columns."""
    a = (orders["landed_gpao"] - (orders["mat_dt"] + orders["freight_gpao"])).abs().max()
    b = (orders["landed_weighted"]
         - (orders["mat_dt"] + orders["freight_weighted"])).abs().max()
    assert a < 1e-6 and b < 1e-6


def test_freight_is_not_quantity_scaled_by_the_gpao(orders):
    """Defect 2, pinned. The GPAO applies `qtendach` to price but not to freight,
    so the two readings must differ on essentially every order."""
    differ = (orders["freight_gpao"].round(4) != orders["freight_weighted"].round(4))
    assert differ.mean() > 0.5, (
        f"only {differ.mean():.1%} of orders differ — either the port now scales "
        "both the same way, or the data changed shape")


def test_freight_is_a_material_share_of_build_cost(orders):
    """Sanity band. Inbound freight runs ~5-20 % of material; far outside that and
    either the coefficient join broke or the charge pool changed."""
    o = orders[orders["mat_dt"] > 0]
    ratio = o["freight_weighted"].sum() / o["mat_dt"].sum() * 100
    assert 2 < ratio < 30, f"freight is {ratio:.1f}% of material, outside the sane band"


def test_sub_assemblies_are_costed_not_dropped(erp_up):
    """The GPAO's second UNION branch. Sub-assembly BOM lines are ~6 % of the
    window; dropping them would quietly understate every order that uses one."""
    lines = L._bom_lines(*WINDOW)
    assert lines["is_sub"].any(), "no sub-assembly lines found — the flag join broke"
    assert lines["is_sub"].mean() < 0.5


def test_coefficient_lookup_is_deterministic(erp_up):
    """Defect 3, pinned. The GPAO's `TOP 1 … ORDER BY datlivfctf DESC` has no
    tiebreaker; ours breaks on invoice number. The history must therefore hold at
    most one row per part-day, and the ambiguity counter must still see the ties."""
    hist, amb = L._coef_history()
    assert not hist.duplicated(["part", "pord", "d"]).any(), \
        "coefficient history is not unique per part-day — lookups will be unstable"
    assert amb.tied > 0, "no ties detected; the reproducibility caveat is stale"


def test_invoiced_lines_mostly_match_a_costed_order(erp_up):
    """`facture_det.ofnach` → `ordprevision.ofdnach` is what makes margin-after-
    freight possible. If the match rate collapses, the walk is measuring a
    shrinking subset and saying nothing about it."""
    m = L.load_landed_margin(dt.date(2025, 1, 1), dt.date(2026, 6, 30))
    if m.empty:
        pytest.skip("no invoiced lines in the window")
    covered = m.loc[m["matched"], "revenue"].sum() / m["revenue"].sum()
    assert covered > 0.90, f"only {covered:.1%} of revenue matched a costed order"


def test_freight_moves_margin_by_a_few_points(erp_up):
    """The tab's headline claim. Freight should cost several points of margin —
    enough to matter, not so much that something is double-counted."""
    m = L.load_landed_margin(*WINDOW)
    m = m[m["matched"]]
    if m.empty:
        pytest.skip("nothing matched in the test window")
    rev = m["revenue"].sum()
    over_material = (rev - m["cost"].sum()) / rev * 100
    after_freight = (rev - m["cost"].sum() - m["freight_cost"].sum()) / rev * 100
    drop = over_material - after_freight
    assert 1 < drop < 15, f"freight moves margin by {drop:.1f} pp, outside the sane band"


def test_the_single_unit_filter_is_reported_not_hidden(erp_up):
    """`frmEtatOFValorises` drops every `qte <= 1` line and its own source calls
    that a stand-in for a sample flag. Whatever it costs, the number has to be
    available to say so."""
    lines, revenue = L.single_unit_lines_dropped(*WINDOW)
    assert lines >= 0 and revenue >= 0
    valued = L.load_valued_lines(*WINDOW)
    if not valued.empty:
        assert (valued["qty"] > 1).all(), "a single-unit line survived the GPAO filter"
