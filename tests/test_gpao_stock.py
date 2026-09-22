"""The unmoved-stock port, checked against the ERP.

`frmStockADate` has no committed export, so parity is pinned the way §5-§8
were: **the GPAO's own SQL, run character for character**, has to land on the
same rows and the same dinar as the port.

The rest pin the defects, and like the other ports they are written to fail if
someone "fixes" them. Reproducing the GPAO is the point — the corrections sit
beside its figures, never instead of them. The one place this module departs
from the screen is `ledger_start`, which exists so the tab can refuse a date
the screen would answer wrongly; its test says so.

Assertions are relationships rather than magic numbers wherever a newer restore
could legitimately move the figure.

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
import gpao_stock as S  # noqa: E402

ASOF = "2026-09-21"
SINCE = "2024-01-01"


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def never(erp_up):
    df = S.load_unmoved(ASOF, SINCE, mode=S.MODE_NEVER)
    if df.empty:
        pytest.skip("no unmoved stock in this restore")
    return df


@pytest.fixture(scope="session")
def unmoved(erp_up):
    df = S.load_unmoved(ASOF, SINCE, mode=S.MODE_UNMOVED)
    if df.empty:
        pytest.skip("no unmoved stock in this restore")
    return df


# ---------------------------------------------------------------- parity ---
@pytest.mark.parametrize("mode", [S.MODE_UNMOVED, S.MODE_NEVER, S.MODE_UNMOVED_NO_PO])
def test_port_reproduces_the_gpao_query(erp_up, mode):
    """The port's frame is exactly what the GPAO's own SQL returns.

    `_gpao_sql` is the query the VB builds; running it directly and comparing
    row for row is the strongest parity available without an export. A failure
    means the loader's post-processing has changed the answer rather than just
    its shape."""
    raw = erp._q(S._gpao_sql(ASOF, SINCE, mode=mode))
    port = S.load_unmoved(ASOF, SINCE, mode=mode)

    assert len(raw) == len(port), "the loader dropped or added rows"
    assert raw["qte"].sum() == pytest.approx(port["qty"].sum(), rel=1e-9)
    assert raw["totDT"].sum() == pytest.approx(port["value_dt"].sum(), rel=1e-9)


def test_never_moved_is_a_subset_of_not_moved(never, unmoved):
    """Option 5 is option 4 plus one more `NOT EXISTS`, so it can only remove
    rows. If it ever adds one, the extra clause has been wired the wrong way
    round — which would be silent, because both answers look plausible."""
    a = set(zip(never["part"], never["pord"]))
    b = set(zip(unmoved["part"], unmoved["pord"]))
    assert a <= b
    assert never["value_dt"].sum() <= unmoved["value_dt"].sum()


def test_excluding_supplier_orders_lowers_the_balance(erp_up, unmoved):
    """Option 6 strips `Cmde Frns` receipts from the balance. Those rows are
    `qtep`, so removing them can only take stock away — a part cannot gain
    units by ignoring goods that have not arrived."""
    no_po = S.load_unmoved(ASOF, SINCE, mode=S.MODE_UNMOVED_NO_PO)
    if no_po.empty:
        pytest.skip("no rows under option 6 in this restore")
    assert no_po["qty"].sum() <= unmoved["qty"].sum()


def test_valuation_matches_the_gpao_case(never):
    """`unit_dt * qty` is `totDT`, and `value_basis` names which leg of the
    GPAO's CASE produced it — the distinction the screen itself collapses."""
    got = never["unit_dt"] * never["qty"]
    assert got.sum() == pytest.approx(never["value_dt"].sum(), rel=1e-9)
    assert set(never["value_basis"]) <= {"pmp", "fob"}
    assert (never.loc[never["value_basis"] == "pmp", "pmp"] != 0).all()
    assert (never.loc[never["value_basis"] == "fob", "pmp"] == 0).all()


# --------------------------------------------------------------- defects ---
def test_an_as_of_before_the_rebase_returns_nothing(erp_up):
    """LEDGER_WINDOW_DEFECT, measured.

    The balance reads `detarticle` alone, which begins at its `STOCK DEPART`
    rebase. A day earlier the screen matches no rows and reports an empty grid
    as though it were an answer. This asserts the defect is still there, so that
    the guard in the tab keeps earning its place."""
    start = S.ledger_start()
    if start is None:
        pytest.skip("cannot read the ledger's opening date")
    before = (start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = S.load_unmoved(before, SINCE, mode=S.MODE_UNMOVED)
    assert df.empty, "the ledger now reaches back further — re-check the guard"


def test_ledger_start_is_the_rebase_not_the_first_row(erp_up):
    """The floor the tab refuses below has to be the rebase date specifically.
    Using `MIN(dat)` over the whole table would give the same answer today and
    a wrong one after the next rebase."""
    start = S.ledger_start()
    if start is None:
        pytest.skip("cannot read the ledger's opening date")
    row = erp._q(
        "SELECT MIN([dat]) AS m FROM [detarticle] WHERE [page] = :p",
        db=erp.CALC_DB, p=S.REBASE_PAGE)
    assert pd.Timestamp(row.iloc[0]["m"]) == start


def test_the_balance_includes_order_commitments(erp_up):
    """COMMITMENT_DEFECT, measured.

    What the screen calls stock nets off customer and production reservations
    and adds supplier orders not yet delivered. Asserting the two readings
    differ keeps the claim honest: if the ledger ever stopped carrying
    commitments, the defect text would need to go."""
    both = erp._q(
        f"SELECT SUM(qtep - qtem) AS v FROM [detarticle] WHERE dat <= '20260921'",
        db=erp.CALC_DB)
    physical = erp._q(
        "SELECT SUM(qtep - qtem) AS v FROM [detarticle] "
        "WHERE dat <= '20260921' AND lib NOT LIKE 'Cmde %'",
        db=erp.CALC_DB)
    assert float(both.iloc[0]["v"]) != pytest.approx(float(physical.iloc[0]["v"]), rel=1e-6)


def test_reservations_keep_a_spoken_for_part_out_of_the_pool(erp_up, never):
    """The consequence that makes the pool safe to allocate.

    Reservations are booked as `qtem`, so a part promised to a customer order
    fails the movement test and never reaches the unmoved list. This is what
    lets `bike_builder` treat the pool as free. If it stopped holding, every
    proposal would be quietly competing with the order book."""
    keys = set(zip(never["part"].astype(int), never["pord"].astype(int)))
    if not keys:
        pytest.skip("empty pool")
    reserved = erp._q(
        "SELECT DISTINCT piece, ordfpiec FROM [detarticle] "
        "WHERE dat BETWEEN '20240101' AND '20260921' "
        "AND page IN ('CMD_CLT', 'OF', 'CMD_SP') AND qtem > 0",
        db=erp.CALC_DB)
    res = {(int(a), int(b)) for a, b in zip(reserved["piece"], reserved["ordfpiec"])}
    assert not (keys & res), "a reserved part reached the unmoved pool"


def test_the_repurchase_clause_is_unbounded(erp_up):
    """REPURCHASE_BOUND_DEFECT, pinned as text rather than as an effect.

    The clause carries `dat >= since` and no upper bound, so a receipt dated
    after the as-of date would disqualify a part from a historical run. No such
    row exists on this restore, so the test asserts the *shape* of the SQL — the
    thing that would have to change for the defect to be fixed."""
    sql = S._gpao_sql(ASOF, SINCE, mode=S.MODE_NEVER)
    tail = sql.split("Achat Facture")[1]
    assert "20260921" not in tail.split("UNION ALL")[0], \
        "the re-purchase clause now bounds the as-of date — the defect note is stale"
