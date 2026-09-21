"""The re-quotation and price-list port, checked against the ERP.

Like the landed-cost port there is no committed spreadsheet for these screens,
so parity is pinned the strongest way available: **the GPAO's own SQL, run
character for character**, has to land on the same totals as the port. Where the
GPAO computes in VB rather than SQL (the price build-up), the test reconstructs
the VB and compares.

The rest pin the defects. They are written to fail if someone "fixes" the port,
because reproducing the GPAO exactly is the point — the corrections live beside
the GPAO figures, never instead of them.

Needs the ERP; skips when it isn't reachable.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import erp  # noqa: E402
import gpao_requote as R  # noqa: E402

YEAR = 2026


@pytest.fixture(scope="session")
def erp_up():
    ok, msg = erp.status()
    if not ok:
        pytest.skip(f"ERP not reachable: {msg}")
    return True


@pytest.fixture(scope="session")
def requote(erp_up):
    df = R.load_requote(YEAR)
    if df.empty:
        pytest.skip(f"no models invoiced in {YEAR} in this restore")
    return df


@pytest.fixture(scope="session")
def gap(erp_up):
    return R.requote_gap(YEAR)


@pytest.fixture(scope="session")
def prices(erp_up):
    df = R.load_price_list()
    if df.empty:
        pytest.skip("no costing sheets in this restore")
    return df


# --------------------------------------------------------------- parity ---
def test_requote_reproduces_the_gpao_query(requote):
    """The parity check for the re-quotation.

    `frmAnalyseCoutMatNC.getListe` builds one SQL string; this runs that string
    verbatim — including its `ISNULL(..., 0)` on the new price and its use of
    the *part's* currency rate for both legs — and the port has to match it to
    the cent, model by model.
    """
    gpao = erp._q(f"""
        SELECT [codnach], SUM(cost*cours) AS cost, SUM(NVcost*cours) AS NVcost
        FROM (
          SELECT N.[codnach], ISNULL(DN.[prxndach]*DN.[qtendach], 0) as cost,
                 ISNULL(FP.[prxcmdf]*DN.[qtendach], 0) as NVcost,
                 (SELECT TOP 1 CASE
                     WHEN FP.Devfpiec = 'EUR' THEN D.[ceur]
                     WHEN FP.Devfpiec = 'USD' THEN D.[cusd]
                     WHEN FP.Devfpiec = 'YEN' THEN D.[yen]
                     ELSE 1 END
                  FROM devisesc D WHERE dat <= GETDATE() ORDER BY dat DESC) as cours
          FROM [nomachat] N, [nomachat_det] DN, [fpiece] FP
          WHERE N.[isArchived] = 0 AND N.[codnach] = DN.[codndach]
            AND DN.[codendach] = FP.[Code] AND DN.[ordndach] = FP.[ordfpiec]
        ) TAB
        WHERE EXISTS (SELECT * FROM [facture] F, [facture_det] DF
                      WHERE F.[numf] = DF.[fact] AND DF.[article] = TAB.[codnach]
                        AND F.[datf] BETWEEN '{YEAR}0101' AND '{YEAR}1231')
        GROUP BY [codnach]""")
    assert not gpao.empty, "the GPAO's own query returned nothing"

    m = requote.merge(gpao, left_on="article", right_on="codnach", how="inner")
    assert len(m) == len(requote), "the port and the GPAO disagree about which models are in scope"
    assert np.allclose(m["cost_dt"], m["cost"].astype(float), atol=0.01), (
        f"stored cost differs; worst {(m['cost_dt'] - m['cost'].astype(float)).abs().max():,.4f}")
    assert np.allclose(m["requote_dt"], m["NVcost"].astype(float), atol=0.01), (
        f"re-quotation differs; worst "
        f"{(m['requote_dt'] - m['NVcost'].astype(float)).abs().max():,.4f}")


def test_ecart_and_pct_are_the_gpao_expression(requote):
    """`dr(6)` and `dr(7)` in `getListe`: `NVcost - cost`, over `cost`, × 100."""
    d = requote[requote["cost_dt"] != 0]
    assert np.allclose(d["ecart"], d["requote_dt"] - d["cost_dt"], atol=1e-6)
    assert np.allclose(d["ecart_pct"], d["ecart"] / d["cost_dt"] * 100, atol=1e-6)


# -------------------------------------------------- defect: unpriced = 0 ---
def test_unpriced_components_are_scored_as_zero(requote, gap):
    """The defect that matters most, pinned.

    `ISNULL(FP.prxcmdf*DN.qtendach, 0)` means a component with no current order
    price contributes nothing to the new quotation while contributing its full
    cost to the old one. If this ever stops being true the port has drifted."""
    assert gap.unquoted_lines > 0, "no unpriced components — has the part master been filled in?"
    # The whole gap between the two readings is exactly the dropped cost.
    dropped = requote["cost_dt"].sum() - requote["cost_lfl"].sum()
    assert dropped == pytest.approx(gap.unquoted_cost, abs=0.01)
    assert dropped > 0

    # And it is a one-sided drop: the new quotation is untouched by it, because
    # those lines were already contributing zero to it.
    assert requote["requote_dt"].sum() == pytest.approx(requote["requote_lfl"].sum(), abs=0.01)


def test_the_defect_changes_the_sign_of_the_answer(gap):
    """Not a rounding matter: it reverses the conclusion on real models.

    This is why `_rule_requote` in the Actions tab ranks on the like-for-like
    figure rather than the GPAO's."""
    assert gap.flipped > 0, (
        "no model flips between the two readings — if the data has genuinely "
        "been cleaned up, relax this test rather than the port")
    assert gap.gpao_cheaper > gap.lfl_cheaper, (
        "the GPAO should paint more models as cheaper than really are")
    assert gap.lfl_pct > gap.gpao_pct, (
        "like-for-like must read dearer than the GPAO, which drops cost from the "
        "old side only")


# ------------------------------------------------- defect: quote currency ---
def test_the_quote_currency_is_ignored(requote):
    """`frmAnalyseCoutMatNC` never selects `devcmdf`; both legs convert at the
    part's purchase currency. Reproduced deliberately — see `CURRENCY_DEFECT`."""
    conflicts = R.load_currency_conflicts()
    if conflicts.empty:
        pytest.skip("no conflicting quote currencies in this restore")
    assert requote["conflict_lines"].sum() > 0, (
        "conflicting parts exist but no model reports one")
    # Honouring `devcmdf` would move real money — which is the point of *not*
    # doing it silently.
    assert conflicts["implied_dt_swing"].abs().max() > 100


def test_conflicting_currencies_look_like_entry_errors(erp_up):
    """A currency change with an unchanged number is a mis-set dropdown, not a
    re-denomination. If most conflicts look like that, honouring `devcmdf`
    would be worse than the GPAO's behaviour of ignoring it — which is the
    documented reason the dashboard reproduces rather than corrects."""
    c = R.load_currency_conflicts()
    if c.empty:
        pytest.skip("no conflicting quote currencies in this restore")
    assert c["unchanged_number"].sum() > 0, (
        "no conflict has an unchanged number; re-read CURRENCY_DEFECT, the "
        "argument for reproducing the GPAO here may no longer hold")


# ------------------------------------------------------- defect: the tie ---
def test_the_last_invoice_tie_is_broken_deterministically(erp_up):
    """The GPAO's `TOP 1 … ORDER BY F.datf DESC` has no tiebreaker. Ours adds
    the invoice number, so two reads give one answer."""
    a = R._last_sale.__wrapped__(YEAR)
    b = R._last_sale.__wrapped__(YEAR)
    assert a.equals(b)
    # And the tie is real in this data, or the test is guarding nothing.
    ties = erp._q("""
        SELECT COUNT(*) AS n FROM (
          SELECT DF.[article], COUNT(DISTINCT F.[numf]) AS c
          FROM [facture] F, [facture_det] DF
          WHERE F.[numf] = DF.[fact]
            AND F.[datf] = (SELECT MAX(F2.[datf]) FROM [facture] F2, [facture_det] DF2
                            WHERE F2.[numf] = DF2.[fact] AND DF2.[article] = DF.[article])
          GROUP BY DF.[article]) T
        WHERE c > 1""")
    assert int(ties.iloc[0]["n"]) > 0, "no ties in this restore; the tiebreaker is untested"


# ------------------------------------------------------ the price list ----
def test_sale_freight_is_counted_twice(prices):
    """Both price screens add `trsv · ctrsv` inside the sub-total and again at
    the end. Deliberate — `frmCostingTMP3` brackets the second pass with
    `'BEGIN ADD 2 fois FOB IN Tot2'` — and pinned so it can't be quietly
    dropped."""
    d = prices[prices["freight_dt"] > 0]
    assert not d.empty
    # The second leg goes in before the rebate, so it picks the rebate up too:
    # the gap against a one-leg price is `fob · (1 + rebait/100)`, which reduces
    # to exactly one leg on the 97 % of sheets carrying no rebate.
    expected = d["freight_dt"] * (1 + d["rebait"] / 100)
    assert np.allclose(d["second_leg_dt"], expected, atol=0.01), (
        "the gap between the two-leg and one-leg prices must be one freight leg, "
        "rebated")
    plain = d[d["rebait"] == 0]
    assert np.allclose(plain["second_leg_dt"], plain["freight_dt"], atol=0.01)


def test_the_two_price_screens_differ_only_on_the_rebate(prices):
    """`frmCostingTMP3` rebates a base that includes the first freight leg;
    `frmConsultPrixNC` rebates one that excludes it. So they agree wherever
    `rebait` is zero, and differ by `trsv · ctrsv · rebait/100` where it isn't."""
    same = prices[prices["rebait"] == 0]
    assert np.allclose(same["price_dt"], same["price_dt_consult"], atol=0.01), (
        "with no rebate the two derivations must be identical")

    diff = prices[prices["rebait"] != 0]
    if diff.empty:
        pytest.skip("no current costing revision carries a rebate")
    expected = diff["freight_dt"] * diff["rebait"] / 100
    assert np.allclose(diff["price_dt"] - diff["price_dt_consult"], expected, atol=0.01)


def test_the_price_list_beats_prxnach(erp_up):
    """The reason this module exists.

    `docs/eurocycles-erp-findings.md` §4 says the dashboard has no trustworthy
    sell price because `nomachat.prxnach` is stale. The price derived from
    `costing_nc` is the replacement, and this asserts it earns the title against
    realised average selling price."""
    acc = R.price_accuracy(YEAR)
    if acc.empty:
        pytest.skip(f"no invoices in {YEAR}")
    both = acc.dropna(subset=["err_list_pct", "err_prxnach_pct"])
    assert len(both) > 100, "too few models with both figures to be meaningful"

    list_err = both["err_list_pct"].abs().median()
    prx_err = both["err_prxnach_pct"].abs().median()
    assert list_err < prx_err / 2, (
        f"the list price ({list_err:.1f} %) should be far closer to realised ASP "
        f"than prxnach ({prx_err:.1f} %)")
    assert both["err_list_pct"].abs().le(10).mean() > 0.4, (
        "the list price should land within ±10 % on well over a third of models")
    assert both["err_prxnach_pct"].abs().le(10).mean() < 0.15, (
        "prxnach should land within ±10 % on almost nothing — if it has improved, "
        "findings §4 needs revisiting")


def test_coverage_is_the_price_lists_weakness_not_accuracy(erp_up):
    """Stated plainly on the tab, so pinned: fewer than half the models invoiced
    this year have a costing sheet at all. Accuracy is good; reach is not."""
    acc = R.price_accuracy(YEAR)
    if acc.empty:
        pytest.skip(f"no invoices in {YEAR}")
    coverage = acc["err_list_pct"].notna().mean()
    assert coverage < 0.75, (
        f"coverage is now {coverage:.0%}; the tab's caveat about reach needs updating")
    assert coverage > 0.2, f"coverage collapsed to {coverage:.0%} — is the join still right?"


# --------------------------------------------------------- the line view ---
def test_component_detail_sums_to_the_model(requote, erp_up):
    """`load_requote_lines` is the drill-down behind one row of the grid, so its
    columns have to add up to that row — including the unpriced lines the GPAO
    filters out of its own modal."""
    row = requote.nlargest(1, "cost_dt").iloc[0]
    lines = R.load_requote_lines(row["article"])
    assert not lines.empty
    assert lines["cost_dt"].sum() == pytest.approx(row["cost_dt"], rel=1e-6)
    assert lines["new_dt"].sum() == pytest.approx(row["requote_dt"], rel=1e-6)
    assert int((~lines["quoted"]).sum()) == int(row["unquoted_lines"])
