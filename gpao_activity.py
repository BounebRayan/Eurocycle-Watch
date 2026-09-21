"""The GPAO's own "Activités comparatives" report, reproduced query-for-query.

The Eurocycles GPAO (`Eurocycles.sln`, VB.NET) carries management reporting in
`10-Financier/05-Direction generale`. Its flagship screen is
**`frmActiviteComp1.vb` — "Activities comparatives"**: nineteen sections that
compare a date window against the same window one year earlier, across sales,
customer orders, purchasing, component consumption and freight. It is the screen
that produced `data/finance/erp-report-ytd-2026-09-21.xlsx`.

This module is a **faithful port** of that screen's SQL. The point is that the
dashboard ties to the GPAO to the dinar, so the queries keep the GPAO's joins,
filters and quirks even where they look wrong — including the old-style comma
joins, which are what make section 01 land on 41,894,202.57 exactly.

Where the GPAO's own arithmetic is demonstrably inconsistent, the defect is
reproduced **and** flagged: `Section.caveat` carries the note, and `Asp` exposes
the GPAO's average-price figure next to the corrected one. See
`docs/gpao-parity.md` for the confirmed defects and the evidence.

Section numbering follows the GPAO's own labels (01-19, as exported). Section 03
("prix moyen par type") exists in the GPAO but is skipped by its bulk export,
which is why the spreadsheet jumps 02 → 04.

All money is DT: every amount is multiplied by its own document's FX rate
(`facture.cours`, `facturef.coursfctf`, or the `devisesc` rate current at the
production order's date). Callers convert to the display currency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

import erp
from i18n import N_

# `frmActiviteComp1.vb` L465. Drops free-of-charge / sample invoices from the
# revenue figures. The GPAO applies it to some sections and not others — that
# inconsistency is defect #1, see SECTIONS below.
CONDITIONS_PAI = " AND ISNULL(F.[cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE' "

# `facture.flag = 1` marks a cancelled invoice. Every section excludes it.
NOT_CANCELLED = " AND F.[flag] <> 1 "


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _prior(d: date) -> date:
    """The GPAO compares against the same window shifted back exactly one year
    (`datedeb.AddYears(-1)`), not against a calendar year."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:            # 29 Feb -> 28 Feb, as .NET AddYears does
        return d.replace(year=d.year - 1, month=2, day=28)


@dataclass(frozen=True)
class Section:
    code: str
    title: str                    # the GPAO's own French label
    english: str
    unit: str                     # "money" | "units"
    description: str              # the GPAO's own meDescription text, translated
    source: str                   # which ERP tables it reads, for the caption
    remise: bool = False          # does the GPAO subtract the rebate block?
    caveat: str = ""              # our note where the GPAO's own maths is off
    slow: bool = False            # correlated subquery over calc.detart


SECTIONS: list[Section] = [
    Section("01", "Chiffre d'affaire par type de vélos",
            N_("Revenue by wheel size"), "money",
            N_("Revenue by year, grouped by wheel size."),
            "facture ⋈ facture_det ⋈ nomachat ⋈ Wheelsize", remise=True,
            caveat=N_("Excludes free-of-charge invoices; section 02 — the units it is "
                      "divided by for average price — does not.")),
    Section("02", "Chiffre en nombre par type de vélos",
            N_("Units by wheel size"), "units",
            N_("Total units invoiced for each wheel size."),
            "facture ⋈ facture_det ⋈ nomachat ⋈ Wheelsize",
            caveat=N_("The only sales section with no free-of-charge filter.")),
    Section("03", "Prix moyen par type de vélos",
            N_("Average price by wheel size"), "money",
            N_("Average price per year, grouped by wheel size."),
            "facture ⋈ facture_det ⋈ nomachat ⋈ Wheelsize",
            caveat=N_("The GPAO computes this section but leaves it out of its bulk "
                      "export, which is why its spreadsheet jumps from 02 to 04.")),
    Section("04", "Chiffre d'affaire par client",
            N_("Revenue by customer"), "money",
            N_("Revenue by year, grouped by invoiced customer."),
            "facture ⋈ facture_det ⋈ client", remise=True,
            caveat=N_("No wheel-size join and no free-of-charge filter, so its total is "
                      "not the same population as section 01's.")),
    Section("05", "Chiffre en nombre par client",
            N_("Units by customer"), "units",
            N_("Total units invoiced for each customer."),
            "facture ⋈ facture_det ⋈ nomachat ⋈ Wheelsize ⋈ client"),
    Section("06", "Chiffre d'affaire par pays",
            N_("Revenue by country"), "money",
            N_("Revenue by year, grouped by the invoiced customer's country."),
            "facture ⋈ facture_det ⋈ client ⋈ payss", remise=True),
    Section("07", "Chiffre en nombre par pays",
            N_("Units by country"), "units",
            N_("Total units invoiced for each country."),
            "facture ⋈ facture_det ⋈ nomachat ⋈ client ⋈ payss",
            caveat=N_("Joins nomachat but not Wheelsize, unlike its revenue twin.")),
    Section("08", "Chiffre en nombre (e-bike) par type de vélos",
            N_("E-bike units by wheel size"), "units",
            N_("Total e-bike units invoiced for each wheel size."),
            "facture ⋈ facture_det ⋈ nomachat (ebike = 1) ⋈ Wheelsize"),
    Section("09", "Commandes clients par pays",
            N_("Customer orders by country"), "units",
            N_("Volume of customer orders created each year, per country."),
            "planningprev ⋈ planningprev_det ⋈ nomachat ⋈ Wheelsize ⋈ client"),
    Section("10", "Commandes par client",
            N_("Customer orders by customer"), "units",
            N_("Volume of customer orders created each year, per customer."),
            "planningprev ⋈ planningprev_det ⋈ nomachat ⋈ Wheelsize ⋈ client"),
    Section("11", "Commandes par famille de vélo",
            N_("Customer orders by wheel size"), "units",
            N_("Volume of customer orders created each year, per wheel size."),
            "planningprev ⋈ planningprev_det ⋈ nomachat ⋈ Wheelsize"),
    Section("12", "Achats par pays d'origine",
            N_("Purchases by country of origin"), "money",
            N_("Purchasing spend per year, grouped by the part's country of origin."),
            "facturef ⋈ facturef_det ⋈ fpiece"),
    Section("13", "Achats par fournisseur",
            N_("Purchases by supplier"), "money",
            N_("Purchasing spend per year, per supplier."),
            "facturef ⋈ facturef_det ⋈ fournisseur"),
    Section("14", "Achats par groupes",
            N_("Purchases by part group"), "money",
            N_("Purchasing spend per year, grouped by part group."),
            "facturef ⋈ facturef_det ⋈ fpiece ⋈ groupes"),
    Section("15", "Consommation par pays d'origine",
            N_("Consumption by country of origin"), "money",
            N_("Value of components consumed, grouped by country of origin."),
            "calc.detart (DEC_PROD) ⋈ fpiece ⋈ ordprevision ⋈ devisesc", slow=True),
    Section("16", "Consommation par fournisseur",
            N_("Consumption by supplier"), "money",
            N_("Value of components consumed, grouped by supplier."),
            "calc.detart (DEC_PROD) ⋈ fpiece ⋈ ordprevision ⋈ devisesc", slow=True),
    Section("17", "Consommation par groupes",
            N_("Consumption by part group"), "money",
            N_("Value of components consumed, grouped by part group."),
            "calc.detart (DEC_PROD) ⋈ fpiece ⋈ ordprevision ⋈ devisesc", slow=True),
    Section("18", "Transport import",
            N_("Inbound freight"), "money",
            N_("Inbound freight cost per year, by charge heading."),
            "transportf (imp = 1) ⋈ transportdf ⋈ rubriquest"),
    Section("19", "Transport export",
            N_("Outbound freight"), "money",
            N_("Outbound freight cost per year, by charge heading."),
            "transportf (imp ≠ 1) ⋈ transportdf ⋈ rubriquest"),
]

BY_CODE = {s.code: s for s in SECTIONS}


# --------------------------------------------------------------------------
# The rebate block (`frmActiviteComp1.vb` L468-497)
# --------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def load_remise(d1: date, d2: date) -> tuple[float, float]:
    """Rebate given away in each window, as the GPAO computes it.

    `facture_remise` / `facture_remise_det` hold a negotiated per-article price
    (`nprix`) valid over a date range for one customer. The rebate is the gap
    between what was invoiced and that price, and the GPAO subtracts it from the
    three revenue sections' totals.

    Note this is **not** `facture.trem`, which is unpopulated in this restore —
    the dashboard's earlier pass looked at the wrong column and dropped
    "discounts given" as a result.
    """
    p1, p2 = _prior(d1), _prior(d2)
    amt = ("ISNULL(SUM(DF.[qte]*DF.[prx]*F.[cours] - DF.[qte]*DR.[nprix]*F.[cours]), 0)")
    frm = ("FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS, "
           "[facture_remise] R, [facture_remise_det] DR")
    whr = (f"AND DF.[fact] = F.[numf] {NOT_CANCELLED} {CONDITIONS_PAI} "
           "AND R.[ID] = DR.[ID] AND DF.[article] = N.[codnach] "
           "AND N.[wheelnach] = WS.[codwheel] AND F.[datf] BETWEEN R.[datedeb] AND R.[datefin] "
           "AND DR.[cod] = DF.[article] AND F.[clif] = R.[client]")
    sql = f"""
        SELECT ISNULL(SUM(totYear1), 0) AS totYear1, ISNULL(SUM(totYear2), 0) AS totYear2 FROM (
            SELECT {amt} AS totYear1, 0 AS totYear2 {frm}
            WHERE F.[datf] BETWEEN '{_d(d1)}' AND '{_d(d2)}' {whr}
            UNION ALL
            SELECT 0 AS totYear1, {amt} AS totYear2 {frm}
            WHERE F.[datf] BETWEEN '{_d(p1)}' AND '{_d(p2)}' {whr}
        ) TAB"""
    r = erp._q(sql)
    if r.empty:
        return 0.0, 0.0
    return float(r.iloc[0]["totYear1"]), float(r.iloc[0]["totYear2"])


# --------------------------------------------------------------------------
# Section SQL — one builder per section, each taking the two date windows
# --------------------------------------------------------------------------

def _two_window(label: str, measure: str, frm: str, where: str, date_col: str,
                d1: date, d2: date, *, order: str = "totYear1 DESC",
                group: str | None = None) -> str:
    """The GPAO's shape: the same aggregate run over both windows, UNION ALL'd,
    then re-grouped. Kept rather than a CASE-based single pass so the row sets
    match the GPAO's exactly, including labels present in only one year."""
    p1, p2 = _prior(d1), _prior(d2)
    g = group or label
    return f"""
        SELECT lbl, SUM(totYear1) AS totYear1, SUM(totYear2) AS totYear2 FROM (
            SELECT {label} AS lbl, ISNULL({measure}, 0) AS totYear1, 0 AS totYear2
            {frm} WHERE {where} AND {date_col} BETWEEN '{_d(d1)}' AND '{_d(d2)}'
            GROUP BY {g}
            UNION ALL
            SELECT {label} AS lbl, 0 AS totYear1, ISNULL({measure}, 0) AS totYear2
            {frm} WHERE {where} AND {date_col} BETWEEN '{_d(p1)}' AND '{_d(p2)}'
            GROUP BY {g}
        ) TAB GROUP BY lbl ORDER BY {order}"""


# Sales measures, DT.
_REV = "SUM(DF.[qte]*DF.[prx]*F.[cours])"
_QTY = "SUM(DF.[qte])"
# Purchasing measure, DT.
_BUY = "SUM(DF.[qtedfctf]*DF.[prxdfctf]*F.[coursfctf])"


def _sql_01(d1, d2):
    """Revenue by wheel size, plus the GPAO's separate PIECES DIVERS bucket.

    The wheel-joined half carries no `typ` filter while the PIECES DIVERS half
    takes `typ NOT IN ('O','I')`, so a non-bike line whose article still resolves
    to a wheel size lands in both. Measured on the 2026 YTD window that is one
    line and DT 513 — reproduced for parity, noted in docs/gpao-parity.md."""
    p1, p2 = _prior(d1), _prior(d2)

    def half(a, b, col):
        return f"""
            SELECT [libwheel], SUM({col}) AS {col} FROM (
                SELECT WS.[libwheel], ISNULL({_REV}, 0) AS {col}
                FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS
                WHERE F.[numf] = DF.[fact] AND DF.[article] = N.[codnach]
                  AND N.[wheelnach] = WS.[codwheel]
                  AND F.[datf] BETWEEN '{_d(a)}' AND '{_d(b)}' {NOT_CANCELLED} {CONDITIONS_PAI}
                GROUP BY [libwheel]
                UNION ALL
                SELECT 'PIECES DIVERS' AS [libwheel], ISNULL({_REV}, 0) AS {col}
                FROM [facture] F, [facture_det] DF
                WHERE F.[datf] BETWEEN '{_d(a)}' AND '{_d(b)}' AND DF.[fact] = F.[numf]
                  {NOT_CANCELLED} AND (DF.[typ] <> 'O' AND DF.[typ] <> 'I') {CONDITIONS_PAI}
            ) TAB GROUP BY [libwheel]"""
    return f"""
        SELECT [libwheel] AS lbl, SUM(totYear1) AS totYear1, SUM(totYear2) AS totYear2 FROM (
            SELECT [libwheel], totYear1, 0 AS totYear2 FROM ({half(d1, d2, 'totYear1')}) A
            UNION ALL
            SELECT [libwheel], 0 AS totYear1, totYear2 FROM ({half(p1, p2, 'totYear2')}) B
        ) TABG GROUP BY [libwheel] ORDER BY SUM(totYear1) DESC"""


def _sql_02(d1, d2):
    # No CONDITIONS_PAI here — the GPAO omits it, and that omission is defect #1.
    return _two_window(
        "[libwheel]", _QTY,
        "FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS",
        f"F.[numf] = DF.[fact] AND DF.[article] = N.[codnach] "
        f"AND N.[wheelnach] = WS.[codwheel] {NOT_CANCELLED}",
        "F.[datf]", d1, d2, order="SUM(totYear1) DESC")


def _sql_03(d1, d2):
    """Average selling price per wheel size = revenue ÷ units, both computed on
    the same rows (the GPAO comments out its PIECES DIVERS union here, so this
    section — unlike the headline ASP — is internally consistent)."""
    p1, p2 = _prior(d1), _prior(d2)

    def half(a, b, n):
        return f"""
            SELECT WS.[libwheel], ISNULL({_REV}, 0) AS v{n}, ISNULL({_QTY}, 0) AS q{n}
            FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS
            WHERE F.[numf] = DF.[fact] AND DF.[article] = N.[codnach]
              AND N.[wheelnach] = WS.[codwheel]
              AND F.[datf] BETWEEN '{_d(a)}' AND '{_d(b)}' {NOT_CANCELLED}
            GROUP BY [libwheel]"""
    return f"""
        SELECT [libwheel] AS lbl,
               CASE WHEN SUM(q1) = 0 THEN 0 ELSE SUM(v1)/SUM(q1) END AS totYear1,
               CASE WHEN SUM(q2) = 0 THEN 0 ELSE SUM(v2)/SUM(q2) END AS totYear2
        FROM (
            SELECT [libwheel], v1, 0 AS v2, q1, 0 AS q2 FROM ({half(d1, d2, 1)}) A
            UNION ALL
            SELECT [libwheel], 0 AS v1, v2, 0 AS q1, q2 FROM ({half(p1, p2, 2)}) B
        ) TABG GROUP BY [libwheel] ORDER BY totYear1 DESC"""


def _sql_04(d1, d2):
    # No wheel-size join and no CONDITIONS_PAI — defect #1 again.
    return _two_window(
        "[libcli]", _REV, "FROM [facture] F, [facture_det] DF, [client] C",
        f"F.[numf] = DF.[fact] AND F.[clif] = C.[codecli] {NOT_CANCELLED}",
        "F.[datf]", d1, d2, order="SUM(totYear1) DESC", group="[libcli], F.[clif]")


def _sql_05(d1, d2):
    return _two_window(
        "[libcli]", _QTY,
        "FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS, [client] C",
        f"F.[numf] = DF.[fact] AND F.[clif] = C.[codecli] AND DF.[article] = N.[codnach] "
        f"AND N.[wheelnach] = WS.[codwheel] {NOT_CANCELLED} {CONDITIONS_PAI}",
        "F.[datf]", d1, d2, order="SUM(totYear1) DESC")


def _sql_06(d1, d2):
    return _two_window(
        "C.[pays]", _REV, "FROM [facture] F, [facture_det] DF, [client] C, [payss] P",
        f"F.[numf] = DF.[fact] AND F.[clif] = C.[codecli] AND C.[pays] = P.[lib] "
        f"{NOT_CANCELLED} {CONDITIONS_PAI}",
        "F.[datf]", d1, d2, group="C.[pays]")


def _sql_07(d1, d2):
    # Joins nomachat but not Wheelsize, unlike its revenue twin (section 06
    # joins neither). Three different populations across 01/04/06-07.
    return _two_window(
        "C.[pays]", _QTY,
        "FROM [facture] F, [facture_det] DF, [nomachat] N, [client] C, [payss] P",
        f"F.[numf] = DF.[fact] AND DF.[article] = N.[codnach] AND F.[clif] = C.[codecli] "
        f"AND C.[pays] = P.[lib] {NOT_CANCELLED} {CONDITIONS_PAI}",
        "F.[datf]", d1, d2, group="C.[pays]")


def _sql_08(d1, d2):
    return _two_window(
        "[libwheel]", _QTY,
        "FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS",
        f"F.[numf] = DF.[fact] AND N.[ebike] = 1 AND DF.[article] = N.[codnach] "
        f"AND N.[wheelnach] = WS.[codwheel] {NOT_CANCELLED} {CONDITIONS_PAI}",
        "F.[datf]", d1, d2, order="SUM(totYear1) DESC")


# Customer orders come from the forecast/planning book, not the order book:
# `planningprev_det.qte` by its own line date.
_PP = "FROM [planningprev] P, [planningprev_det] DP, [nomachat] N, [Wheelsize] WS"
_PP_C = _PP + ", [client] C"
_PP_W = ("P.[ordre] = DP.[ordre] AND DP.[reference] = N.[codnach] "
         "AND N.[wheelnach] = WS.[codwheel]")


def _sql_09(d1, d2):
    return _two_window("C.[pays]", "SUM(DP.[qte])", _PP_C,
                       f"{_PP_W} AND P.[client] = C.[codecli]", "DP.[dat]", d1, d2,
                       order="SUM(totYear1) DESC", group="C.[pays], P.[client]")


def _sql_10(d1, d2):
    lbl = "(C.[codecli] + ' | ' + C.[libcli])"
    p1, p2 = _prior(d1), _prior(d2)
    return f"""
        SELECT lbl, SUM(totYear1) AS totYear1, SUM(totYear2) AS totYear2 FROM (
            SELECT {lbl} AS lbl, ISNULL(SUM(DP.[qte]), 0) AS totYear1, 0 AS totYear2
            {_PP_C} WHERE {_PP_W} AND P.[client] = C.[codecli]
              AND DP.[dat] BETWEEN '{_d(d1)}' AND '{_d(d2)}' GROUP BY {lbl}
            UNION ALL
            SELECT {lbl} AS lbl, 0 AS totYear1, ISNULL(SUM(DP.[qte]), 0) AS totYear2
            {_PP_C} WHERE {_PP_W} AND P.[client] = C.[codecli]
              AND DP.[dat] BETWEEN '{_d(p1)}' AND '{_d(p2)}' GROUP BY {lbl}
        ) DC GROUP BY lbl ORDER BY SUM(totYear1) DESC"""


def _sql_11(d1, d2):
    return _two_window("[libwheel]", "SUM(DP.[qte])", _PP, _PP_W, "DP.[dat]", d1, d2,
                       order="SUM(totYear1) DESC")


# Purchasing: supplier invoices, dated by delivery (`datlivfctf`), valued at the
# invoice's own FX rate. No cancellation flag exists on this document.
_FF = "FROM [facturef] F, [facturef_det] DF"
_FF_W = "F.[numfctf] = DF.[numdfctf] AND F.[frnfctf] = DF.[frndfctf]"
_FP_W = "DF.[artdfctf] = FP.[Code] AND DF.[orddfctf] = FP.[ordfpiec]"


def _sql_12(d1, d2):
    return _two_window("FP.[orgfpiec]", _BUY, _FF + ", [fpiece] FP",
                       f"{_FF_W} AND {_FP_W}", "F.[datlivfctf]", d1, d2,
                       order="SUM(totYear1) DESC", group="FP.[orgfpiec]")


def _sql_13(d1, d2):
    return _two_window("FR.[Libfourn]", _BUY, _FF + ", [fournisseur] FR",
                       f"{_FF_W} AND F.[frnfctf] = FR.[Codefourn]", "F.[datlivfctf]",
                       d1, d2, order="SUM(totYear1) DESC", group="FR.[Libfourn]")


def _sql_14(d1, d2):
    return _two_window("G.[Libgroupe]", _BUY, _FF + ", [fpiece] FP, [groupes] G",
                       f"{_FF_W} AND {_FP_W} AND FP.[groupe] = G.[Codegroupe]",
                       "F.[datlivfctf]", d1, d2, order="SUM(totYear1) DESC",
                       group="G.[Libgroupe]")


# --- Consumption (15-17) --------------------------------------------------
# The heaviest queries in the report. Components consumed are read from the
# production-declaration movements in `eurocycles_db_calc.detart`
# (`page = 'DEC_PROD'`, or an "Equivalence ---" relabel), then valued by walking
# back to the production order's BOM line and applying the FX rate in force at
# the order's date. The correlated subquery is the GPAO's own shape.

_CONSO_DEC = ("(D.[page] = 'DEC_PROD' OR D.lib LIKE '%Equivalence ---%')")


def _conso_value(a: date, b: date) -> str:
    """The GPAO's per-part valuation subquery (identical in 15, 16 and 17)."""
    return f"""(
        SELECT ISNULL(SUM(TABOF.[totPrix]), 0) FROM (
            SELECT O.[qte]*DO.[qtendach]*DO.[prxndach]*(
                SELECT TOP 1 CASE WHEN P2.[devfpiec] = 'EUR' THEN DV.[ceur]
                                  WHEN P2.[devfpiec] = 'USD' THEN DV.[cusd]
                                  WHEN P2.[devfpiec] = 'YEN' THEN DV.[yen]
                                  ELSE 1 END
                FROM [devisesc] DV WHERE DV.[dat] <= O.[dat] ORDER BY DV.[dat] DESC
            ) AS totPrix
            FROM [ordprevision] O, [ordprevision_det] DO, [fpiece] P2
            WHERE O.[id_o] = DO.[id_o] AND DO.[codendach] = P2.[Code]
              AND DO.[ordndach] = P2.[ordfpiec] AND P.[Code] = P2.[Code]
              AND P.[ordfpiec] = P2.[ordfpiec]
              AND EXISTS (
                SELECT * FROM [{erp.CALC_DB}].[dbo].[detart] D1
                WHERE (D1.[page] = 'DEC_PROD' OR D1.lib LIKE '%Equivalence ---%')
                  AND O.dat BETWEEN '{_d(a)}' AND '{_d(b)}'
                  AND D1.piece = P2.Code AND D1.ordfpiec = P2.ordfpiec
                  AND O.[ordre] = CAST(D1.[num] AS int) AND O.[codnach] = D1.[code]
                  AND O.[ofdnach] = D1.[code2] AND O.[cmde] = D1.[code3]
                  AND O.[indic] = D1.[code4] AND D1.[piece] = DO.[codendach]
                  AND D1.[ordfpiec] = DO.[ordndach])
        ) TABOF)"""


def _sql_conso(label: str, group: str, d1: date, d2: date) -> str:
    p1, p2 = _prior(d1), _prior(d2)

    def half(a, b):
        return f"""
            SELECT {label} AS lbl, {_conso_value(a, b)} AS totQte
            FROM [{erp.CALC_DB}].[dbo].[detart] D, [fpiece] P
            WHERE {_CONSO_DEC} AND D.dat BETWEEN '{_d(a)}' AND '{_d(b)}'
              AND D.piece = P.Code AND D.ordfpiec = P.ordfpiec
            GROUP BY {group}, P.[Code], P.[ordfpiec]"""
    return f"""
        SELECT lbl, SUM(totYear1) AS totYear1, SUM(totYear2) AS totYear2 FROM (
            SELECT lbl, SUM(totQte) AS totYear1, 0 AS totYear2
            FROM ({half(d1, d2)}) TAB GROUP BY lbl
            UNION ALL
            SELECT lbl, 0 AS totYear1, SUM(totQte) AS totYear2
            FROM ({half(p1, p2)}) TAB GROUP BY lbl
        ) DC GROUP BY lbl ORDER BY SUM(totYear1) DESC"""


def _sql_15(d1, d2):
    return _sql_conso("P.[orgfpiec]", "P.[orgfpiec]", d1, d2)


def _sql_16(d1, d2):
    lbl = ("ISNULL((SELECT F.[Libfourn] FROM [fournisseur] F "
           "WHERE P.[frnfpiec] = F.[Codefourn]), 'AUTRE')")
    return _sql_conso(lbl, "P.[frnfpiec]", d1, d2)


def _sql_17(d1, d2):
    lbl = "(SELECT G.[Libgroupe] FROM [groupes] G WHERE G.[Codegroupe] = P.[groupe])"
    return _sql_conso(lbl, "P.[groupe]", d1, d2)


# --- Freight (18-19) ------------------------------------------------------
# `transportf.imp` splits inbound (1) from outbound. `mntdf` is already DT.

def _sql_transport(inbound: bool, d1, d2):
    return _two_window(
        "(F.[artdf] + ' | ' + R.[lib])", "SUM(F.[mntdf])",
        "FROM [transportf] T, [transportdf] F, [rubriquest] R",
        f"T.numf = F.numdf AND T.frnf = F.frndf AND F.[artdf] = R.[code] "
        f"AND T.imp {'=' if inbound else '<>'} 1",
        "T.[datf]", d1, d2, order="SUM(totYear1) DESC",
        group="F.[artdf] + ' | ' + R.[lib]")


def _sql_18(d1, d2):
    return _sql_transport(True, d1, d2)


def _sql_19(d1, d2):
    return _sql_transport(False, d1, d2)


_BUILDERS = {f"{i:02d}": globals()[f"_sql_{i:02d}"] for i in range(1, 20)}


# --------------------------------------------------------------------------
# Loading + the GPAO's presentation arithmetic
# --------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def load_section(code: str, d1: date, d2: date) -> pd.DataFrame:
    """One section, shaped the way the GPAO's grid shows it.

    Columns: `label`, `v1`, `pct1`, `v2`, `pct2`, `delta`, `delta_pct`, `kind`
    (`"row"`, `"remise"` or `"total"`). `v1` is the requested window, `v2` the
    same window a year earlier. Money sections are DT; callers convert.
    """
    sec = BY_CODE[code]
    df = erp._q(_BUILDERS[code](d1, d2))
    if df.empty:
        return pd.DataFrame(columns=["label", "v1", "pct1", "v2", "pct2",
                                     "delta", "delta_pct", "kind"])

    df = df.rename(columns={"lbl": "label", "totYear1": "v1", "totYear2": "v2"})
    df["label"] = df["label"].astype(str).str.strip().replace({"0": "PIECES DIVERS", "": "(blank)"})
    df[["v1", "v2"]] = df[["v1", "v2"]].astype(float).fillna(0.0)
    df = df.groupby("label", as_index=False)[["v1", "v2"]].sum()
    df = df.sort_values("v1", ascending=False, kind="stable").reset_index(drop=True)
    df["kind"] = "row"

    # Section 03 is a ratio per row; the GPAO shows no percentages and no total.
    if code == "03":
        df["pct1"] = pd.NA
        df["pct2"] = pd.NA
        df["delta"] = df["v1"] - df["v2"]
        df["delta_pct"] = pd.NA
        return df[["label", "v1", "pct1", "v2", "pct2", "delta", "delta_pct", "kind"]]

    df["delta"] = df["v1"] - df["v2"]
    df["delta_pct"] = _pct_change(df["v1"], df["v2"])

    t1, t2 = df["v1"].sum(), df["v2"].sum()
    if sec.remise:
        r1, r2 = load_remise(d1, d2)
        t1, t2 = t1 - r1, t2 - r2
        df = pd.concat([df, pd.DataFrame([{
            "label": "Remise", "v1": -r1, "v2": -r2, "delta": pd.NA,
            "delta_pct": pd.NA, "kind": "remise"}])], ignore_index=True)

    # The GPAO divides every row — the rebate line included — by the post-rebate
    # total, then sums those percentages for the TOTAL row (which is why its
    # exports read 100.00000000000001 rather than a clean 100).
    df["pct1"] = 0.0 if t1 == 0 else df["v1"] * 100 / t1
    df["pct2"] = 0.0 if t2 == 0 else df["v2"] * 100 / t2

    df = pd.concat([df, pd.DataFrame([{
        "label": "TOTAL", "v1": t1, "pct1": df["pct1"].sum(), "v2": t2,
        "pct2": df["pct2"].sum(), "delta": t1 - t2,
        "delta_pct": (t1 - t2) * 100 / t2 if t2 else 0.0, "kind": "total"}])],
        ignore_index=True)
    return df[["label", "v1", "pct1", "v2", "pct2", "delta", "delta_pct", "kind"]]


def _pct_change(cur: pd.Series, prev: pd.Series) -> pd.Series:
    return ((cur - prev) * 100 / prev.where(prev != 0)).fillna(0.0)


@st.cache_data(ttl=3600, show_spinner=False)
def consumption_coverage() -> pd.Timestamp | None:
    """Last movement date in `eurocycles_db_calc.detart`, or None if unreadable.

    Sections 15-17 read the costing database, which is restored separately from
    `eurocycles_db`. In the current restore it stops two months short of the
    sales data, so those three sections under-report a window that runs past it —
    the queries are right, the ledger just ends early. Callers warn on this
    rather than quietly showing a short number.
    """
    try:
        r = erp._q("SELECT MAX([dat]) AS m FROM [detart] WHERE [page] = 'DEC_PROD'",
                   db=erp.CALC_DB)
    except Exception:
        return None
    if r.empty or pd.isna(r.iloc[0]["m"]):
        return None
    return pd.Timestamp(r.iloc[0]["m"])


# --------------------------------------------------------------------------
# Average selling price — the GPAO's "MOYENNE PAR VELO", and the honest version
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Asp:
    """Average revenue per bike, both as the GPAO reports it and as it should be.

    The GPAO divides section 01's revenue by section 02's units. Those are two
    different populations: section 01 filters out free-of-charge invoices and
    adds a PIECES DIVERS bucket, section 02 does neither. `corrected` divides
    revenue by units over one population — bike lines that resolve to a wheel
    size, free-of-charge invoices excluded from both halves.
    """
    gpao_cur: float
    gpao_prev: float
    corrected_cur: float
    corrected_prev: float

    @property
    def gpao_delta(self) -> float:
        return self.gpao_cur - self.gpao_prev

    @property
    def gpao_delta_pct(self) -> float:
        """What the GPAO prints: it rounds the numerator *and* the denominator to
        whole dinars before dividing (`frmActiviteComp1.vb` L~700,
        `Math.Round(a-b) / Math.Round(b)`), which on the 2026 YTD window turns
        -3.257 % into -3.155 %."""
        if not round(self.gpao_prev):
            return 0.0
        return round(self.gpao_delta) / round(self.gpao_prev) * 100

    @property
    def true_delta_pct(self) -> float:
        return self.gpao_delta / self.gpao_prev * 100 if self.gpao_prev else 0.0

    @property
    def corrected_delta_pct(self) -> float:
        if not self.corrected_prev:
            return 0.0
        return (self.corrected_cur - self.corrected_prev) / self.corrected_prev * 100


@st.cache_data(ttl=1800, show_spinner=False)
def load_asp(d1: date, d2: date) -> Asp:
    """Both readings of average price per bike. See `Asp`."""
    s1, s2 = load_section("01", d1, d2), load_section("02", d1, d2)
    g1 = s1[s1["kind"] == "total"]
    g2 = s2[s2["kind"] == "total"]
    rev1 = float(g1["v1"].iloc[0]) if not g1.empty else 0.0
    rev2 = float(g1["v2"].iloc[0]) if not g1.empty else 0.0
    u1 = float(g2["v1"].iloc[0]) if not g2.empty else 0.0
    u2 = float(g2["v2"].iloc[0]) if not g2.empty else 0.0

    p1, p2 = _prior(d1), _prior(d2)
    lf = erp._q(f"""
        SELECT SUM(CASE WHEN F.[datf] BETWEEN '{_d(d1)}' AND '{_d(d2)}'
                        THEN DF.[qte]*DF.[prx]*F.[cours] ELSE 0 END) AS rev1,
               SUM(CASE WHEN F.[datf] BETWEEN '{_d(d1)}' AND '{_d(d2)}'
                        THEN DF.[qte] ELSE 0 END) AS u1,
               SUM(CASE WHEN F.[datf] BETWEEN '{_d(p1)}' AND '{_d(p2)}'
                        THEN DF.[qte]*DF.[prx]*F.[cours] ELSE 0 END) AS rev2,
               SUM(CASE WHEN F.[datf] BETWEEN '{_d(p1)}' AND '{_d(p2)}'
                        THEN DF.[qte] ELSE 0 END) AS u2
        FROM [facture] F, [facture_det] DF, [nomachat] N, [Wheelsize] WS
        WHERE F.[numf] = DF.[fact] AND DF.[article] = N.[codnach]
          AND N.[wheelnach] = WS.[codwheel] {NOT_CANCELLED} {CONDITIONS_PAI}
          AND F.[datf] BETWEEN '{_d(p1)}' AND '{_d(d2)}'""")
    r = lf.iloc[0] if not lf.empty else {}
    c1 = float(r.get("rev1") or 0) / float(r.get("u1") or 1)
    c2 = float(r.get("rev2") or 0) / float(r.get("u2") or 1)

    return Asp(gpao_cur=rev1 / u1 if u1 else 0.0, gpao_prev=rev2 / u2 if u2 else 0.0,
               corrected_cur=c1, corrected_prev=c2)
