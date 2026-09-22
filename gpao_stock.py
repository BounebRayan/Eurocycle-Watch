"""Stock at a date, and the stock that has been sitting untouched.

Ports **`09-Managements reports/02-Stock/frmStockADate.vb`**, captioned _"Etat
du stock a une date"_. It is the fifth management screen `docs/gpao-parity.md`
set out to port, and the first from the stock folder.

The screen is a radio group over one query. Four of its seven readings are
plain balance filters (`qte <> 0`, `> 0`, `< 0`, `= 0`); the three this module
exists for are the movement ones:

    option 4  "Stock non mouvemente"    balance <> 0, and nothing was issued
                                        between `since` and `asof`
    option 5  "Stock jamais mouvemente" option 4, and nothing was re-purchased
                                        since `since` either
    option 6  option 4, with supplier-order lines excluded from the balance

**Option 5 is the ERP's own wording for "jamais mouvemente"**, and its own
definition of it, which is why this module leads with it rather than inventing
a rule. Both are *window* rules: a part is "never moved" relative to a date you
choose, not over all time.

One consequence of how the ledger is written is worth stating up front, because
it makes the reading stronger than it looks. Customer-order and production
reservations are booked as issues (`qtem`), so a part that is spoken for fails
the movement test. "Unmoved" therefore means *neither consumed nor reserved* —
which is exactly the pool `bike_builder` needs, and the reason it can treat the
result as genuinely free to allocate.

`bike_builder` is the consumer. Everything about *which* bikes can be made out
of this is that module's job; this one only answers what is sitting there and
what it is worth.

**Five defects are reproduced and flagged**; see the `*_DEFECT` constants. The
first is the one that matters: for an as-of date before the ledger's rebase the
screen returns an empty grid rather than saying it cannot see that far back.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import erp
from i18n import N_

# The radio indices, as `frmStockADate.rbChoix` numbers them. Only the movement
# readings are ported — the plain balance filters (0-3) are a `HAVING` away and
# nothing in the dashboard needs them yet.
MODE_UNMOVED = 4
MODE_NEVER = 5
MODE_UNMOVED_NO_PO = 6

MODE_LABELS = {
    MODE_UNMOVED: N_("Not moved — nothing issued in the window"),
    MODE_NEVER: N_("Never moved — nothing issued and nothing re-purchased"),
    MODE_UNMOVED_NO_PO: N_("Not moved, excluding goods not yet received"),
}

# `detarticle` opens with a `STOCK DEPART` rebase and carries nothing before it.
# The balance subquery reads that table alone, so an as-of date earlier than the
# rebase matches no rows — see LEDGER_WINDOW_DEFECT. Discovered by query rather
# than assumed; `ledger_start()` re-reads it so a newer restore moves the guard.
REBASE_PAGE = "STK_DEP"


# --------------------------------------------------------------- defects ---
LEDGER_WINDOW_DEFECT = N_(
    "**Before the ledger's rebase the screen reports nothing, and presents it as "
    "an answer.** The balance is summed over `detarticle` alone, which in this "
    "restore starts at its `STOCK DEPART` rebase on 2026-07-01. An as-of date a "
    "day earlier matches no rows at all, so the grid comes back empty — not "
    "'I cannot see that far back', but a clean and confident nothing. The older "
    "history is in `detart`, which the balance never reads. This tab refuses an "
    "as-of date before the rebase rather than showing the empty grid."
)

COMMITMENT_DEFECT = N_(
    "**The balance is not physical stock — it nets off orders that have not "
    "happened yet.** `SUM(qtep - qtem)` runs over every ledger page, so "
    "customer-order reservations (`Cmde Client`, 3.97M units) and production "
    "reservations (`Of En Cours`, 2.20M) are subtracted, while supplier orders "
    "not yet delivered (`Cmde Frns`, 1.77M) are added. The screen offers a "
    "switch for the supplier side only. What it calls stock is an availability "
    "figure: 26.69M units, against 28.89M of purely physical movement."
)

REPURCHASE_BOUND_DEFECT = N_(
    "**Option 5's re-purchase test has no upper date bound.** Every other clause "
    "is bracketed by the as-of date, but the `NOT EXISTS ... LIKE 'Achat "
    "Facture%'` check carries only `dat >= since`. A receipt dated after the "
    "as-of date would therefore disqualify a part from a historical run. "
    "`detarticle` does hold 84,721 forward-dated rows out to 2027-12-06, but "
    "none is a receipt, so this is latent on this restore rather than measured."
)

PMP_FALLBACK_DEFECT = N_(
    "**A part with no costed movement is valued at its FOB price instead, with "
    "nothing to say so.** The valuation reads `CASE WHEN PMP <> 0 THEN PMP ELSE "
    "prxfobfpiec * cours END`, so a part that never reached the costing ledger "
    "falls back to a list price. The two are different things — one is what was "
    "paid, the other what was quoted — and the grid shows them in one column. "
    "`value_basis` carries which of the two each row used."
)

ASOF_RATE_DEFECT = N_(
    "**The FOB fallback is converted at the as-of rate, not the rate it was "
    "quoted at.** `prxfobfpiec` was set when the part was sourced, sometimes "
    "years earlier, but the query multiplies it by the latest `devisesc` rate at "
    "the as-of date. For the 15,478 live parts priced in USD, EUR or YEN that "
    "turns a currency move into an apparent change in the value of stock that "
    "has not moved at all."
)


# ------------------------------------------------------------ the screen ---
def _d(v) -> str:
    """`yyyyMMdd`, the way the VB formats every date it splices into SQL."""
    return pd.Timestamp(v).strftime("%Y%m%d")


def _gpao_sql(asof, since, *, mode: int = MODE_NEVER, pmp: bool = True) -> str:
    """`frmStockADate.GetList`'s own query, rebuilt branch for branch.

    Kept as one string so `tests/test_gpao_stock.py` can run the GPAO's SQL
    character for character against the port, the way the landed-cost and
    re-quotation ports are pinned.

    `mode` is `rbChoix.SelectedIndex`; `pmp` is `rbPrice.SelectedIndex = 1`.
    The VB's optional blocks — the manufacturer column, the last customs
    declaration, the `listCodes` aggregate — are left out. Nothing here reads
    them and each is a correlated subquery per row.
    """
    a, s = _d(asof), _d(since)

    # Option 6 strips supplier-order lines from the *balance*. The VB writes it
    # into the balance subquery only, never into the movement test.
    without_po = " AND DA1.[lib] NOT LIKE 'Cmde Frns%' " if mode == MODE_UNMOVED_NO_PO else ""

    # The movement test: nothing issued in the window, across both ledgers.
    filtre2 = (
        " AND ( SELECT SUM(TABDA.[qtem]) FROM ( "
        f" SELECT DA3.[qtem] FROM [{erp.CALC_DB}].[dbo].[detart] DA3 WHERE P.[Code] = DA3.piece "
        f"AND P.[ordfpiec] = DA3.ordfpiec AND DA3.[dat] >= '{s}' AND DA3.[dat] <= '{a}' "
        " UNION ALL "
        f" SELECT DA3.[qtem] FROM [{erp.CALC_DB}].[dbo].[detarticle] DA3 WHERE P.[Code] = DA3.piece "
        f"AND P.[ordfpiec] = DA3.ordfpiec AND DA3.[dat] >= '{s}' AND DA3.[dat] <= '{a}' "
        " )TABDA ) = 0 "
    )
    if mode == MODE_NEVER:
        # ...and never bought again since. Note `dat >= since` with no upper
        # bound — see REPURCHASE_BOUND_DEFECT. Reproduced as the VB has it.
        filtre2 += (
            " AND NOT EXISTS ( "
            f" SELECT DA3.[lib] FROM [{erp.CALC_DB}].[dbo].[detart] DA3 WHERE P.[Code] = DA3.piece "
            f"And P.[ordfpiec] = DA3.ordfpiec AND DA3.[dat] >= '{s}' "
            "AND DA3.[lib] LIKE 'Achat Facture%' "
            " UNION ALL "
            f" SELECT DA3.[lib] FROM [{erp.CALC_DB}].[dbo].[detarticle] DA3 WHERE P.[Code] = DA3.piece "
            f"And P.[ordfpiec] = DA3.ordfpiec AND DA3.[dat] >= '{s}' "
            "AND DA3.[lib] LIKE 'Achat Facture%' "
            " ) "
        )

    sel_pmp = (
        f"ISNULL((SELECT TOP 1 pmp FROM [{erp.CALC_DB}].[dbo].[detart] DA "
        f"WHERE DA.[piece] = P.code AND DA.[ordfpiec] = P.ordfpiec AND DA.[dat] <= '{a}' "
        "ORDER BY dat DESC, [indice] DESC, [lib] DESC), 0)"
    ) if pmp else "0"
    sel_price = ("(CASE WHEN PMP <> 0 THEN PMP ELSE [prxfobfpiec]*cours END)" if pmp
                 else "[prxfobfpiec]*cours")

    return f"""
        SELECT [nnum], [num], [reffpiec], [Libfpiec], [typfpiec], [qte],
               {sel_price} AS unit_dt, [pmp], [prxfobfpiec], [cours], [Devfpiec],
               {sel_price}*qte AS totDT,
               [Code], [ordfpiec], [Codefourn], [Libfourn], [Codegroupe], [Libgroupe]
        FROM (
          SELECT P.[nnum], P.[num], P.[reffpiec], P.[Libfpiec], P.[typfpiec],
                 P.[prxfobfpiec], {sel_pmp} as pmp, P.[Devfpiec],
                 P.[Code], P.[ordfpiec],
                 F.[Codefourn], F.[Libfourn], G.[Codegroupe], G.[Libgroupe]
            ,(SELECT ISNULL(SUM(DA1.[qtep] - DA1.[qtem]), 0)
                FROM [{erp.CALC_DB}].[dbo].[detarticle] DA1
               WHERE DA1.[piece] = P.[Code] AND DA1.[ordfpiec] = P.[ordfpiec]
                 AND DA1.[dat] <= '{a}' {without_po} ) as qte,
             (SELECT TOP 1 CASE WHEN P.[Devfpiec] = 'EUR' THEN D.[ceur]
                                WHEN P.[Devfpiec] = 'USD' THEN D.[cusd]
                                WHEN P.[Devfpiec] = 'YEN' THEN D.[yen]
                                ELSE 1 END
                FROM [devisesc] D WHERE D.[dat] <= '{a}' ORDER BY dat DESC) as cours
          FROM [fpiece] P, [fournisseur] F, [groupes] G
          WHERE P.[isArchived] = 0 AND F.[Codefourn] = P.[frnfpiec]
            AND G.[Codegroupe] = P.[groupe]
            AND EXISTS (SELECT * FROM [{erp.CALC_DB}].[dbo].[detarticle] DA1
                         WHERE DA1.[piece] = P.[Code] AND DA1.[ordfpiec] = P.[ordfpiec]
                           AND DA1.[dat] <= '{a}' )
            {filtre2}
          GROUP BY P.[nnum], P.[num], P.[reffpiec], P.[Libfpiec], P.[typfpiec],
                   P.[prxfobfpiec], P.[Devfpiec], P.[Code], P.[ordfpiec],
                   F.[Codefourn], F.[Libfourn], G.[Codegroupe], G.[Libgroupe]
        )TAB
        GROUP BY [nnum], [num], [reffpiec], [Libfpiec], [typfpiec], [prxfobfpiec],
                 [pmp], [cours], [Devfpiec], [Code], [ordfpiec],
                 [Codefourn], [Libfourn], [Codegroupe], [Libgroupe], [qte]
        HAVING qte <> 0
        ORDER BY [nnum], [num]
    """


# ------------------------------------------------------------- loaders ---
@st.cache_data(ttl=3600, show_spinner=False)
def ledger_start() -> pd.Timestamp | None:
    """First date the balance ledger can answer for, or None if unreadable.

    `detarticle` opens at its `STOCK DEPART` rebase and holds nothing earlier.
    The screen's balance reads no other table, so this is the floor on any
    as-of date — see LEDGER_WINDOW_DEFECT. Read rather than hardcoded so a
    newer restore moves it on its own."""
    try:
        r = erp._q(
            "SELECT MIN([dat]) AS m FROM [detarticle] WHERE [page] = :page",
            db=erp.CALC_DB, page=REBASE_PAGE,
        )
    except Exception:
        return None
    if r.empty or pd.isna(r.iloc[0]["m"]):
        return None
    return pd.Timestamp(r.iloc[0]["m"])


@st.cache_data(ttl=3600, show_spinner=False)
def ledger_end() -> pd.Timestamp | None:
    """Newest real (non-commitment) movement in the balance ledger.

    Commitment pages run forward to 2027, so `MAX(dat)` over the whole table
    would say the ledger is more current than it is. Mirrors
    `gpao_activity.consumption_coverage`, which makes the same distinction for
    `detart`."""
    try:
        r = erp._q(
            "SELECT MAX([dat]) AS m FROM [detarticle] "
            "WHERE [page] IN ('DEC_PROD', 'ACHAT_FACT', 'STK_DEP')",
            db=erp.CALC_DB,
        )
    except Exception:
        return None
    if r.empty or pd.isna(r.iloc[0]["m"]):
        return None
    return pd.Timestamp(r.iloc[0]["m"])


@st.cache_data(ttl=1800, show_spinner="Reading unmoved stock…")
def load_unmoved(asof, since, *, mode: int = MODE_NEVER,
                 pmp: bool = True) -> pd.DataFrame:
    """The screen's grid, as a frame.

    One row per `(part, pord)` — the composite key every part table in this ERP
    uses. Columns beyond the GPAO's own:

      `part`, `pord`   the key, as nullable ints so it merges against the BOM
      `value_dt`       `totDT`, the row's stock value in dinar
      `value_basis`    'pmp' or 'fob' — which leg of the CASE priced it, since
                       the GPAO collapses both into one column (PMP_FALLBACK_DEFECT)

    Rows with a negative balance are kept. The screen's `HAVING qte <> 0` admits
    them and they are real — a part issued against a receipt that was never
    booked. `bike_builder` filters to `qty > 0`; nothing is hidden here.
    """
    df = erp._q(_gpao_sql(asof, since, mode=mode, pmp=pmp))
    if df.empty:
        return df

    df = df.rename(columns={
        "Code": "part", "ordfpiec": "pord", "num": "part_num",
        "reffpiec": "part_ref", "Libfpiec": "part_lib", "typfpiec": "slot",
        "qte": "qty", "totDT": "value_dt", "Devfpiec": "ccy",
        "Codefourn": "supplier", "Libfourn": "supplier_name",
        "Codegroupe": "grp", "Libgroupe": "grp_name",
    })
    for c in ("part", "pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    for c in ("qty", "unit_dt", "value_dt", "pmp", "prxfobfpiec", "cours"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # Which leg of the valuation CASE actually fired. The GPAO shows one column
    # and never says; a reader cannot otherwise tell a paid cost from a quote.
    df["value_basis"] = pd.Series("fob", index=df.index).where(df["pmp"] == 0, "pmp")
    if not pmp:
        df["value_basis"] = "fob"

    return df.dropna(subset=["part", "pord"]).reset_index(drop=True)
