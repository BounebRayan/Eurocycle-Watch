"""The exchange-rate port, checked against the ERP.

`frmExchangeRate` has no export either, so parity is pinned the same way §5-§7
were: **the GPAO's own SQL, run character for character**, has to land on the
same totals as the port, and the VB's per-cell arithmetic is reconstructed and
compared.

The rest pin the defects. They are written to fail if someone "fixes" the port,
because reproducing the GPAO exactly is the point — the corrections live beside
the GPAO figures, never instead of them. `fx_split` is the one piece that is
deliberately *not* the GPAO's arithmetic, and its tests say so.

Needs the ERP; skips when it isn't reachable.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erp  # noqa: E402
import gpao_exchange as X  # noqa: E402

YEAR = 2026


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def by_dev(erp_up):
    df = X.load_fx_variation(YEAR, by_customer=False)
    if df.empty:
        pytest.skip(f"no EUR/USD invoices in {YEAR} in this restore")
    return df


@pytest.fixture(scope="session")
def by_cust(erp_up):
    df = X.load_fx_variation(YEAR, by_customer=True)
    if df.empty:
        pytest.skip(f"no EUR/USD invoices in {YEAR} in this restore")
    return df


@pytest.fixture(scope="session")
def totals(erp_up):
    return X.year_total(YEAR)


@pytest.fixture(scope="session")
def sales(erp_up):
    s = erp.load_sales(start_year=YEAR - 2, bikes_only=True)
    if s.empty:
        pytest.skip("no sales in this restore")
    return s


# --------------------------------------------------------------- parity ---
def test_turnover_ties_to_the_invoice_book(by_dev):
    """The foreign-currency turnover the screen groups has to be the invoice
    book's own, filter for filter: `flag <> 1`, EUR/USD only, and the
    `WITHOUT COMMERCIAL VALUE` exclusion the screen applies unconditionally."""
    book = erp._q(f"""
        SELECT F.[dev] AS dev, SUM(DF.[qte]*DF.[prx]) AS total_ccy
        FROM [facture] F, [facture_det] DF
        WHERE F.[numf] = DF.[fact] AND F.[flag] <> 1
          AND (F.[dev] = 'EUR' OR F.[dev] = 'USD')
          AND F.[datf] BETWEEN '{YEAR}0101' AND '{YEAR}1231'
          AND ISNULL(F.[cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE'
        GROUP BY F.[dev]""")
    assert not book.empty, "the GPAO's own filters returned nothing"
    got = by_dev.groupby("dev")["total_ccy"].sum()
    for _, r in book.iterrows():
        assert got[r["dev"]] == pytest.approx(float(r["total_ccy"]), rel=1e-9)


def test_ecart_is_the_vb_arithmetic(by_dev):
    """`Ecart = TotalDev*Cours(2) - TotalDev*Cours(1)`, and the two legs differ
    only by the rate — volume and price are identical on both sides, which is
    what makes the whole difference exchange rate."""
    assert by_dev["ecart"].to_numpy() == pytest.approx(
        (by_dev["total_dt_cur"] - by_dev["total_dt_prev"]).to_numpy(), rel=1e-9)
    assert by_dev["total_dt_cur"].to_numpy() == pytest.approx(
        (by_dev["total_ccy"] * by_dev["rate_cur"]).to_numpy(), rel=1e-9)
    assert by_dev["total_dt_prev"].to_numpy() == pytest.approx(
        (by_dev["total_ccy"] * by_dev["rate_prev"]).to_numpy(), rel=1e-9)


def test_january_prices_against_the_closing_rate_of_the_year_before(by_dev):
    """Month 1 is the one month that does not compare against `CoursM1`. The VB
    branches on `MonthIndexGlb = 1` and uses the `Cours3112` scalar instead —
    the last rate quoted on or before 31/12 of the prior year."""
    jan = by_dev[by_dev["mo"] == 1]
    if jan.empty:
        pytest.skip(f"no January invoices in {YEAR}")
    for _, r in jan.iterrows():
        col = X._RATE_COL[r["dev"]]
        want = erp._q(f"SELECT TOP 1 [{col}] AS r FROM [devisesc] "
                      f"WHERE [dat] <= '{YEAR - 1}1231' ORDER BY [dat] DESC")
        assert float(r["rate_prev"]) == pytest.approx(float(want.iloc[0]["r"]), rel=1e-9)


def test_rate_is_the_plain_monthly_mean_of_devisesc(by_dev):
    """`Cours` is `SUM(x)/COUNT(x)` over the month's rows — a plain mean of the
    daily quotes, not weighted by turnover and not restricted to business days."""
    for _, r in by_dev.iterrows():
        col = X._RATE_COL[r["dev"]]
        want = erp._q(f"""
            SELECT SUM([{col}])/COUNT([{col}]) AS r FROM [devisesc]
            WHERE YEAR([dat]) = {YEAR} AND MONTH([dat]) = {int(r['mo'])}""")
        assert float(r["rate_cur"]) == pytest.approx(float(want.iloc[0]["r"]), rel=1e-9)


def test_customer_grid_sums_to_the_currency_grid(by_dev, by_cust):
    """The screen builds its two grids from the same subquery with and without
    the customer columns, so they have to agree once the customer is summed out."""
    a = by_cust.groupby(["dev", "mo"])["total_ccy"].sum().sort_index()
    b = by_dev.groupby(["dev", "mo"])["total_ccy"].sum().sort_index()
    assert list(a.index) == list(b.index)
    assert a.to_numpy() == pytest.approx(b.to_numpy(), rel=1e-9)


# -------------------------------------------------------------- defects ---
def test_monthly_columns_do_not_sum_to_the_total_band(by_dev, totals):
    """Defect 14. The grid's twelve monthly `Ecart` columns are month-on-month
    movements; its TOTAL band revalues the year's whole volume closing against
    opening. They are different questions, so they do not reconcile — and this
    test exists to keep the port from quietly making them.

    Kept as an inequality rather than a pinned number so it survives a newer
    restore, with the sign disagreement on EUR asserted separately because that
    is the part a reader would be most misled by.
    """
    monthly = by_dev.groupby("dev")["ecart"].sum()
    band = totals.set_index("dev")["ecart"]
    for dev in monthly.index:
        if dev not in band.index:
            continue
        assert monthly[dev] != pytest.approx(band[dev], rel=0.01), (
            f"{dev}: the two readings agreed, which the GPAO's arithmetic does not do")


def test_the_two_readings_disagree_in_sign_on_eur(by_dev, totals):
    """Defect 14, the sharp end: on this restore the monthly columns say the EUR
    book gained on the rate and the TOTAL band beside them says it lost."""
    band = totals.set_index("dev")["ecart"]
    monthly = by_dev.groupby("dev")["ecart"].sum()
    if "EUR" not in band.index or "EUR" not in monthly.index:
        pytest.skip("no EUR invoices in this restore")
    assert np.sign(monthly["EUR"]) != np.sign(band["EUR"])


def test_booked_rate_differs_from_the_month_average(erp_up):
    """Defect 16. The screen prices from monthly averages while every revenue
    figure — in the GPAO and here — is built from `facture.cours`, the rate on
    the invoice. They track closely but do not agree, so this screen's dinar
    totals cannot be reconciled against any other screen's."""
    d = X.booked_vs_average(YEAR)
    if d.empty:
        pytest.skip(f"no EUR/USD invoices in {YEAR}")
    assert d["gap_pct"].abs().max() > 1.0, (
        "no invoice sat more than 1 % from its month average — the defect would be moot")
    assert abs(d["gap_pct"].median()) < 1.0, (
        "the median gap should be small; a large one means the join is wrong, not the GPAO")


def test_band_captions_track_the_window_start_not_the_calendar(erp_up):
    """Defect 15. The data is filed by `DATEPART(Month, datf)` while the grid
    captions its bands by walking forward from the start date. For a window that
    does not start in January the two disagree, and the port keeps the calendar
    month so the mislabelling is visible rather than silently repaired."""
    sql = X._gpao_sql(f"{YEAR}0301", f"{YEAR}1231", by_customer=False)
    assert "DATEPART(Month, F.[datf]) AS MonthIndexGlb" in sql
    df = X._ecart(erp._q(sql))
    if df.empty:
        pytest.skip(f"no invoices from March {YEAR}")
    # Filed by calendar month, so a March-start window begins at month 3 — not
    # at index 1, which is where the grid's first caption sits.
    assert int(df["mo"].min()) >= 3


# ----------------------------------------------------------- correction ---
def test_fx_split_holds_the_rate_constant(sales):
    """`fx_split` reprices the current year at the prior year's booked rate, so
    the constant-rate margin has to sit between the two reported margins
    whenever the rate moved one way — and `fx_pp + drop_pp_ex_fx` has to
    reconstruct the reported drop exactly."""
    yrs = sorted(sales["yr"].unique())
    if len(yrs) < 2:
        pytest.skip("need two years")
    yr, prev = yrs[-1], yrs[-2]
    s = X.fx_split(sales, yr, prev, group="article")
    if s.empty:
        pytest.skip("no model sold in both years")
    ok = s[["fx_pp", "drop_pp_ex_fx", "drop_pp"]].notna().all(axis=1)
    assert ok.any(), "every row was undefined, which means the split is broken, not sparse"
    f = s[ok]
    assert (f["fx_pp"] + f["drop_pp_ex_fx"]).to_numpy() == pytest.approx(
        f["drop_pp"].to_numpy(), rel=1e-9)
    # Where it is undefined it has to be for the one legitimate reason: a year
    # whose constant-rate revenue is zero, i.e. credit notes and no units.
    for _, r in s[~ok].iterrows():
        assert min(r["rev_const"], r["rev_const_prev"]) <= 0, (
            f"{r['article']}: undefined for a reason other than zero-unit revenue")


def test_fx_split_uses_the_booked_rate_not_the_screens_average(sales):
    """The correction has to reconcile with `line_rev_dt`, which is
    `qte * prx * facture.cours`. So the rate it holds constant is the
    turnover-weighted booked rate, and re-deriving revenue from it must return
    the year's actual dinar revenue."""
    r = X.booked_rates(sales)
    for _, row in r.iterrows():
        assert row["rev_ccy"] * row["rate"] == pytest.approx(row["rev_dt"], rel=1e-9)


def test_fx_split_is_zero_when_the_rate_did_not_move(sales):
    """Priced against its own year, nothing moves — the guard that says the
    split is measuring the rate and not some artefact of the grouping."""
    yrs = sorted(sales["yr"].unique())
    yr = yrs[-1]
    s = X.fx_split(sales, yr, yr, group="article")
    if s.empty:
        pytest.skip("no sales in the latest year")
    assert s["fx_pp"].abs().max() == pytest.approx(0.0, abs=1e-9)


def test_fx_split_handles_a_model_that_changed_currency(sales):
    """The split works line by line in each line's own currency, so a model that
    moved between EUR and USD is handled rather than excluded. This asserts such
    models are present and scored, because restricting to single-currency models
    was the shortcut that had to be avoided."""
    yrs = sorted(sales["yr"].unique())
    if len(yrs) < 2:
        pytest.skip("need two years")
    yr, prev = yrs[-1], yrs[-2]
    d = sales[sales["yr"].isin([yr, prev])]
    mixed = (d.groupby("article")["dev"].nunique() > 1)
    mixed = set(mixed[mixed].index)
    if not mixed:
        pytest.skip("no model was invoiced in more than one currency")
    s = X.fx_split(sales, yr, prev, group="article")
    hit = s[s["article"].isin(mixed)]
    if hit.empty:
        pytest.skip("no multi-currency model sold in both years")
    assert hit["fx_pp"].notna().all()
