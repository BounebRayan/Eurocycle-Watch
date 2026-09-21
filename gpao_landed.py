"""Landed cost — the GPAO's valued production orders, with freight.

Ports three things from `10-Financier/05-Direction generale`:

- **`frmEtatOFValorises.vb`** — *"Etat des OFs valorisés"*. One row per invoice
  line: what it sold for, what its material cost was, the gap, and cost as a
  share of revenue. `load_valued_lines`.
- **`frmEtatOFValorisesTrans.vb`** — *"Etat des ofs valorisés avec transport"*.
  The same idea one level down: for each production order, walk its bill of
  materials, price each component at the FX rate current on the order's date,
  and add the **freight coefficient** carried by the supplier invoice that last
  delivered that component. `load_of_landed`.
- The coefficient itself, built in `08-Transport/frmDroitDouane.vb` and
  `frmFactTransport.vb`, read back in `07-Fournisseurs/frmFactFour.vb` as
  `prxdfctf·coursfctf + coef`. `load_coef_audit`.

**Why this matters.** Everything else in the dashboard measures margin over
`facture_det.mat` — standard material cost, which carries no inbound freight
(see `docs/eurocycles-erp-findings.md` §12d). `coef` is the ERP's own answer to
that gap: total ancillary charges on a supplier invoice, spread per unit. On
2026 H1 it adds roughly 9-10 % on top of material.

**Performance.** The GPAO does the as-of lookups as correlated subqueries per
BOM line; over a six-month window that is 351 s. The same result assembled with
`merge_asof` in pandas takes 1.6 s, so that is what this module does — the same
rule, a different execution. `docs/gpao-parity.md` records the check.

Three defects are reproduced and flagged; see `COEF_DEFECT` and
`docs/gpao-parity.md` §7.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import streamlit as st

import erp
from i18n import N_

# The ancillary-charge columns on a supplier invoice that make up the freight
# pool, exactly as `frmDroitDouane.vb` sums them. Note `tim` (stamp duty) and
# `aut`/`autres` are deliberately **not** in the pool — the GPAO leaves them out.
CHARGE_COLS = ["transit", "femb", "fdae", "fsa", "ass", "mag", "frsta", "tax", "trs"]

CHARGE_LABELS = {
    "transit": N_("Transit / clearing"),
    "femb":    N_("Embarkation fees"),
    "fdae":    N_("DAE fees"),
    "fsa":     N_("Handling"),
    "ass":     N_("Insurance"),
    "mag":     N_("Warehousing"),
    "frsta":   N_("Standing charges"),
    "tax":     N_("Duty & tax"),
    "trs":     N_("Freight"),
}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# --------------------------------------------------------------------------
# 1. Valued invoice lines — `frmEtatOFValorises.vb`
# --------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner="Loading valued production orders…")
def load_valued_lines(d1: date, d2: date, client: str | None = None) -> pd.DataFrame:
    """One row per invoiced OF line: revenue, material cost, gap, cost share.

    A faithful port, including two filters worth knowing about:

    - `DF.qte > 1` — the GPAO drops every **single-unit** line. Its own source
      carries the note _"TODO ADD ECHANTILLON FROM FACTURE AND DELETE
      DF.[qte] > 1"_, i.e. this stands in for a sample flag it never wired up.
      It also drops genuine one-off sales. `single_unit_lines_dropped()` sizes it.
    - `DF.typ = 'O'` only — unlike the Activity report, which needs `'O'` and
      `'I'` both (findings §12c). So this report's revenue is *not* the Activity
      report's revenue.

    Rebates are handled properly here, unlike the Activity report's lump sum:
    the query is a UNION of lines with no negotiated price (use `facture_det.prx`)
    and lines with one (use `facture_remise_det.nprix`).
    """
    where_client = f" AND F.[clif] = '{client}' " if client else ""
    cond_pai = " AND ISNULL(F.[cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE' "
    common = (f"F.[numf] = DF.[fact] AND F.[clif] = C.[codecli] AND DF.[typ] = 'O' "
              f"AND F.[datf] BETWEEN '{_d(d1)}' AND '{_d(d2)}' AND F.[flag] = 0 "
              f"AND DF.[qte] > 1 {where_client} {cond_pai}")
    rebate_link = ("R.[ID] = DR.[ID] AND F.[datf] BETWEEN R.[datedeb] AND R.[datefin] "
                   "AND DR.[cod] = DF.[article] AND F.[clif] = R.[client]")

    def half(price: str, extra_from: str, extra_where: str) -> str:
        return f"""
            SELECT F.[datf] AS datf, DF.[article] AS article, DF.[ofnach] AS of_no,
                   DF.[lib] AS lib, F.[clif] AS clif, C.[libcli] AS customer,
                   DF.[qte] AS qty, {price}*F.[cours] AS unit_price,
                   DF.[mat] / NULLIF(DF.[qte], 0) AS unit_cost,
                   {price}*F.[cours]*DF.[qte] AS revenue, DF.[mat] AS cost,
                   ({price}*F.[cours]*DF.[qte]) - DF.[mat] AS margin,
                   ISNULL(DF.[mat] / NULLIF({price}*F.[cours]*DF.[qte], 0) * 100, 0) AS cost_pct,
                   DF.[fact] AS numf
            FROM [facture] F, [facture_det] DF, [client] C{extra_from}
            WHERE {common} {extra_where}"""

    sql = (half("DF.[prx]", "",
                f"AND NOT EXISTS (SELECT * FROM [facture_remise] R, [facture_remise_det] DR "
                f"WHERE {rebate_link})")
           + " UNION ALL "
           + half("DR.[nprix]", ", [facture_remise] R, [facture_remise_det] DR",
                  f"AND {rebate_link}")
           + " ORDER BY [datf] ")

    df = erp._q(sql)
    if df.empty:
        return df
    df["datf"] = pd.to_datetime(df["datf"])
    # The GPAO labels this column "%" and puts it beside "Ecarts" (the margin),
    # so it reads as a margin rate. It is cost over revenue. Carry both.
    df["margin_pct"] = 100.0 - df["cost_pct"]
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def single_unit_lines_dropped(d1: date, d2: date) -> tuple[int, float]:
    """How much the `qte > 1` filter throws away: (lines, revenue in DT)."""
    r = erp._q(f"""
        SELECT COUNT(*) AS lines, ISNULL(SUM(DF.[qte]*DF.[prx]*F.[cours]), 0) AS revenue
        FROM [facture] F, [facture_det] DF
        WHERE F.[numf] = DF.[fact] AND DF.[typ] = 'O' AND F.[flag] = 0
          AND F.[datf] BETWEEN '{_d(d1)}' AND '{_d(d2)}'
          AND ISNULL(F.[cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE'
          AND DF.[qte] <= 1""")
    if r.empty:
        return 0, 0.0
    return int(r.iloc[0]["lines"]), float(r.iloc[0]["revenue"])


# --------------------------------------------------------------------------
# 2. The freight coefficient
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CoefAmbiguity:
    """How often the GPAO's "latest supplier invoice" rule has no single answer.

    `TOP 1 … ORDER BY F.datlivfctf DESC` has no tiebreaker, so when a part was
    delivered twice on one day SQL Server returns whichever row it likes. The
    landed cost of that part is then not reproducible between two runs of the
    same report."""
    groups: int
    tied: int
    mean_spread: float
    max_spread: float

    @property
    def tied_pct(self) -> float:
        return self.tied / self.groups * 100 if self.groups else 0.0


@st.cache_data(ttl=3600, show_spinner=False)
def _coef_history() -> tuple[pd.DataFrame, CoefAmbiguity]:
    """Every part's freight coefficient over time, ready for an as-of join.

    Samples (`facturef.echantillon = 1`) are excluded, as the GPAO excludes them.
    Same-day duplicates are collapsed to the highest invoice number so the result
    is deterministic — the GPAO leaves this to chance, and `CoefAmbiguity` says
    how often that matters.
    """
    df = erp._q("""
        SELECT DF.[artdfctf] AS part, DF.[orddfctf] AS pord, F.[datlivfctf] AS d,
               DF.[coef] AS coef, F.[numfctf] AS inv
        FROM [facturef] F, [facturef_det] DF
        WHERE F.[numfctf] = DF.[numdfctf] AND F.[frnfctf] = DF.[frndfctf]
          AND ISNULL(F.[echantillon], 0) = 0 AND F.[datlivfctf] IS NOT NULL""")
    if df.empty:
        return df, CoefAmbiguity(0, 0, 0.0, 0.0)

    df = _as_keys(df)
    df["d"] = pd.to_datetime(df["d"])
    df["coef"] = df["coef"].astype(float).fillna(0.0)

    g = df.groupby(["part", "pord", "d"])["coef"]
    spread = (g.max() - g.min())
    tied = g.nunique() > 1
    amb = CoefAmbiguity(groups=int(len(spread)), tied=int(tied.sum()),
                        mean_spread=float(spread[tied].mean()) if tied.any() else 0.0,
                        max_spread=float(spread.max()))

    df = (df.sort_values(["part", "pord", "d", "inv"])
            .drop_duplicates(["part", "pord", "d"], keep="last")
            .sort_values("d")
            .reset_index(drop=True))
    return df[["part", "pord", "d", "coef"]], amb


def _as_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Part keys arrive as float in one table and int/text in another; a merge
    needs one type on both sides."""
    for c in ("part", "pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df.dropna(subset=["part", "pord"])


@st.cache_data(ttl=3600, show_spinner=False)
def load_coef_audit(start_year: int = 2024) -> pd.DataFrame:
    """Every supplier invoice with the inputs to the GPAO's `coef` formula.

    Columns: `tot_frais` (the charge pool, DT), `tot_qte` (units over which it is
    spread), `tot_neg` (value of credit lines), plus three readings —
    `coef_gpao` (what the ERP stores), `coef_net` (the net-off the formula was
    reaching for) and `coef_plain` (ignore credits entirely).

    The GPAO computes, when there are credit lines and a non-zero pool:

        (totFrais − (totPrxNeg * 100 / totFrais)) / totQte

    which subtracts *a percentage* from *an amount*. See `COEF_DEFECT`.
    """
    charges = " + ".join(f"F.[{c}]" for c in CHARGE_COLS)
    df = erp._q(f"""
        SELECT F.[numfctf] AS invoice, F.[frnfctf] AS supplier, F.[datlivfctf] AS d,
               SUM({charges}) AS tot_frais,
               (SELECT SUM(DF.[qtedfctf]) FROM [facturef_det] DF
                 WHERE F.[numfctf] = DF.[numdfctf] AND F.[frnfctf] = DF.[frndfctf]
                   AND DF.[prxdfctf] >= 0) AS tot_qte,
               (SELECT ABS(ISNULL(SUM(DF.[qtedfctf]*DF.[prxdfctf])*F.[coursfctf], 0))
                 FROM [facturef_det] DF
                 WHERE F.[numfctf] = DF.[numdfctf] AND F.[frnfctf] = DF.[frndfctf]
                   AND DF.[prxdfctf] < 0) AS tot_neg,
               (SELECT TOP 1 DF.[coef] FROM [facturef_det] DF
                 WHERE F.[numfctf] = DF.[numdfctf] AND F.[frnfctf] = DF.[frndfctf])
                 AS coef_stored
        FROM [facturef] F
        WHERE YEAR(F.[datlivfctf]) >= {start_year}
        GROUP BY F.[numfctf], F.[frnfctf], F.[coursfctf], F.[datlivfctf]""")
    if df.empty:
        return df

    df = df.dropna(subset=["tot_qte"])
    df = df[df["tot_qte"] > 0].copy()
    for c in ("tot_frais", "tot_neg", "coef_stored"):
        df[c] = df[c].astype(float).fillna(0.0)
    df["d"] = pd.to_datetime(df["d"])

    has_neg = df["tot_neg"] > 0
    pool_ok = df["tot_frais"] > 0
    df["coef_gpao"] = np.where(
        has_neg,
        np.where(pool_ok,
                 (df["tot_frais"] - (df["tot_neg"] * 100 / df["tot_frais"].where(pool_ok, 1)))
                 / df["tot_qte"], 0.0),
        df["tot_frais"] / df["tot_qte"])
    df["coef_net"] = (df["tot_frais"] - df["tot_neg"]) / df["tot_qte"]
    df["coef_plain"] = df["tot_frais"] / df["tot_qte"]
    df["has_credit"] = has_neg
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_charge_mix(start_year: int = 2022) -> pd.DataFrame:
    """The freight pool split into the GPAO's nine charge headings, by year."""
    cols = ", ".join(f"ISNULL(SUM(F.[{c}]), 0) AS [{c}]" for c in CHARGE_COLS)
    df = erp._q(f"""
        SELECT YEAR(F.[datlivfctf]) AS yr, {cols},
               ISNULL(SUM(F.[tim]), 0) AS tim
        FROM [facturef] F
        WHERE YEAR(F.[datlivfctf]) >= {start_year} AND F.[datlivfctf] IS NOT NULL
        GROUP BY YEAR(F.[datlivfctf]) ORDER BY 1""")
    return df


# --------------------------------------------------------------------------
# 3. Landed cost per production order — `frmEtatOFValorisesTrans.vb`
# --------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner="Costing production orders…")
def load_of_landed(d1: date, d2: date) -> pd.DataFrame:
    """Material + freight per production order, per bike.

    One row per OF (`codnach` × `ofdnach` × `indic` × date). Columns:

    | | |
    |---|---|
    | `mat_dt` | Σ `prxndach · qtendach · cours` — the GPAO's *TOTAL Gen DT* |
    | `freight_gpao` | Σ `coef` — the GPAO's *COÛT DU TRANSPORT* |
    | `freight_weighted` | Σ `coef · qtendach` — the same, scaled by how many of each part the bike uses |
    | `landed_gpao` / `landed_weighted` | material + the respective freight |

    Both freight readings are carried because the GPAO applies `qtendach` to a
    component's price but not to its freight. See `FREIGHT_DEFECT`.

    Sub-assemblies (a BOM line that is itself in `nompiec`) take their freight
    from the components underneath, which is the GPAO's second UNION branch.
    """
    lines = _bom_lines(d1, d2)
    if lines.empty:
        return pd.DataFrame()

    coef, _amb = _coef_history()
    fx = erp._q("SELECT [dat] AS d, [cusd], [ceur], [yen] FROM [devisesc]")
    fx["d"] = pd.to_datetime(fx["d"])
    fx = fx.sort_values("d")

    m = pd.merge_asof(lines.sort_values("of_date"), fx, left_on="of_date", right_on="d",
                      direction="backward")
    dev = m["dev"].astype(str).str.strip()
    m["cours"] = np.select([dev == "EUR", dev == "USD", dev == "YEN"],
                           [m["ceur"], m["cusd"], m["yen"]], default=1.0)
    m["line_mat"] = m["prx"].fillna(0) * m["qty"] * m["cours"]

    # Direct parts: the coefficient of the part itself.
    direct = m[~m["is_sub"]].copy()
    direct = _attach_coef(direct, coef, "part", "pord")

    # Sub-assemblies: the coefficients of the components underneath, summed.
    sub = m[m["is_sub"]].copy()
    if not sub.empty:
        sub = _sub_assembly_freight(sub, coef)
    else:
        sub["coef"] = 0.0

    m = pd.concat([direct, sub], ignore_index=True)
    m["coef"] = m["coef"].fillna(0.0)
    m["fr_weighted"] = m["coef"] * m["qty"]

    keys = ["article", "of_no", "idx", "of_date", "of_qty", "po"]
    g = m.groupby(keys, as_index=False).agg(
        mat_dt=("line_mat", "sum"),
        freight_gpao=("coef", "sum"),
        freight_weighted=("fr_weighted", "sum"),
        parts=("part", "size"))
    g["landed_gpao"] = g["mat_dt"] + g["freight_gpao"]
    g["landed_weighted"] = g["mat_dt"] + g["freight_weighted"]
    g["freight_pct"] = np.where(g["mat_dt"] > 0, g["freight_gpao"] / g["mat_dt"] * 100, np.nan)
    return g.sort_values("of_date").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def _bom_lines(d1: date, d2: date) -> pd.DataFrame:
    """Every BOM line of every production order in the window, flagged for
    whether the component is itself a sub-assembly."""
    df = erp._q(f"""
        SELECT O.[codnach] AS article, O.[ofdnach] AS of_no, O.[indic] AS idx,
               O.[dat] AS of_date, O.[qte] AS of_qty, O.[cmde] AS po,
               DO.[codendach] AS part, DO.[ordndach] AS pord,
               DO.[qtendach] AS qty, ISNULL(DO.[prxndach], 0) AS prx,
               FP.[Devfpiec] AS dev,
               CASE WHEN EXISTS (SELECT * FROM [nompiec] NP
                     WHERE NP.[code] = DO.[codendach] AND NP.[ordnach] = DO.[ordndach])
                    THEN 1 ELSE 0 END AS is_sub
        FROM [ordprevision] O, [ordprevision_det] DO, [fpiece] FP
        WHERE O.[id_o] = DO.[id_o] AND DO.[codendach] = FP.[Code]
          AND DO.[ordndach] = FP.[ordfpiec]
          AND O.[dat] BETWEEN '{_d(d1)}' AND '{_d(d2)}'""")
    if df.empty:
        return df
    df = _as_keys(df)
    df["of_date"] = pd.to_datetime(df["of_date"])
    df["is_sub"] = df["is_sub"].astype(bool)
    return df


def _attach_coef(lines: pd.DataFrame, coef: pd.DataFrame,
                 part_col: str, ord_col: str) -> pd.DataFrame:
    """As-of join: the latest coefficient on or before each order's date.

    `merge_asof` needs both frames sorted on the join column and matching key
    dtypes; `_as_keys` has already normalised the keys."""
    if coef.empty:
        lines["coef"] = 0.0
        return lines
    left = lines.rename(columns={part_col: "part", ord_col: "pord"}).sort_values("of_date")
    out = pd.merge_asof(left, coef, left_on="of_date", right_on="d",
                        by=["part", "pord"], direction="backward", suffixes=("", "_c"))
    out["coef"] = out["coef"].fillna(0.0)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _nompiec_map() -> pd.DataFrame:
    """Sub-assembly part → its own components (`nompiec` ⋈ `nompiec_det`)."""
    df = erp._q("""
        SELECT NP.[code] AS part, NP.[ordnach] AS pord,
               DN.[codendach] AS sub_part, DN.[ordndach] AS sub_pord
        FROM [nompiec] NP, [nompiec_det] DN
        WHERE NP.[codnach] = DN.[codndach]""")
    if df.empty:
        return df
    for c in ("part", "pord", "sub_part", "sub_pord"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df.dropna()


def _sub_assembly_freight(sub: pd.DataFrame, coef: pd.DataFrame) -> pd.DataFrame:
    """A sub-assembly's freight is the sum of its components' coefficients.

    The GPAO's second UNION branch: it keeps the parent line's price and quantity
    but looks the coefficient up against `nompiec_det.codendach`, summing over the
    components. A sub-assembly with no component rows falls back to zero freight,
    as the GPAO's inner join would drop it."""
    nm = _nompiec_map()
    if nm.empty:
        sub["coef"] = 0.0
        return sub

    sub = sub.reset_index(drop=True)
    sub["_row"] = sub.index
    exp = sub[["_row", "of_date", "part", "pord"]].merge(nm, on=["part", "pord"], how="inner")
    if exp.empty:
        sub["coef"] = 0.0
        return sub.drop(columns=["_row"])

    exp = _attach_coef(exp.drop(columns=["part", "pord"]), coef, "sub_part", "sub_pord")
    per_row = exp.groupby("_row")["coef"].sum()
    sub["coef"] = sub["_row"].map(per_row).fillna(0.0)
    return sub.drop(columns=["_row"])


def coef_ambiguity() -> CoefAmbiguity:
    """How often the GPAO's coefficient lookup is a coin toss. See `CoefAmbiguity`."""
    return _coef_history()[1]


# --------------------------------------------------------------------------
# 4. What the two halves give together: margin after inbound freight
# --------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner="Costing invoiced orders…")
def load_landed_margin(d1: date, d2: date) -> pd.DataFrame:
    """Invoiced OF lines with their order's freight attached.

    `facture_det.ofnach` is the production order the line shipped from, so a
    valued invoice line joins to its own OF's costed bill of materials. On
    2025-2026 that lands **99.3 % of lines and 99.7 % of revenue**.

    This is the number the rest of the dashboard cannot produce: the Overview's
    "margin over build cost" stops at `facture_det.mat`, which carries no inbound
    freight. Adding it moves company margin by roughly five points.

    Columns are `load_valued_lines`' plus `freight_unit` (DT per bike, quantity
    weighted), `freight_cost` (× line quantity), `landed_cost`, `landed_margin`
    and `landed_margin_pct`. Unmatched lines keep NaN freight so a caller can
    tell "no freight" from "no order found".
    """
    lines = load_valued_lines(d1, d2)
    orders = load_of_landed(d1, d2)
    if lines.empty or orders.empty:
        return lines

    of = (orders.groupby(["article", "of_no"], as_index=False)
                .agg(mat_of=("mat_dt", "mean"),
                     freight_gpao_unit=("freight_gpao", "mean"),
                     freight_unit=("freight_weighted", "mean")))

    out = lines.copy()
    for df in (out, of):
        for c in ("article", "of_no"):
            df[c] = df[c].astype(str).str.strip()

    out = out.merge(of, on=["article", "of_no"], how="left")
    out["freight_cost"] = out["freight_unit"] * out["qty"]
    out["landed_cost"] = out["cost"] + out["freight_cost"].fillna(0.0)
    out["landed_margin"] = out["revenue"] - out["landed_cost"]
    out["landed_margin_pct"] = np.where(
        out["revenue"] > 0, out["landed_margin"] / out["revenue"] * 100, np.nan)
    out["matched"] = out["freight_unit"].notna()
    return out


# --------------------------------------------------------------------------
# The defects, as text the tab renders
# --------------------------------------------------------------------------

COEF_DEFECT = N_(
    "**The freight coefficient subtracts a percentage from an amount.** When a "
    "supplier invoice carries credit lines, `frmDroitDouane.vb` spreads the "
    "charge pool with `(totFrais − (totPrxNeg × 100 ÷ totFrais)) ÷ totQte`. The "
    "middle term is *credits as a percentage of the pool* — a ratio — being "
    "subtracted from the pool itself, which is dinars. The intent was plainly "
    "`(totFrais − totPrxNeg) ÷ totQte`.\n\n"
    "It misbehaves worst when the pool is small: the smaller `totFrais`, the "
    "larger the stray term. On invoice `AB23-EURO-CYCLES005` a 29 DT pool over "
    "2 units became **−81.35 DT per unit** — the ERP stores that figure, so "
    "those parts carry negative freight into every order that uses them."
)

FREIGHT_DEFECT = N_(
    "**Freight is not scaled by how many of a part a bike uses.** For each "
    "component the GPAO takes price × `qtendach` but freight as a bare `coef`, "
    "so a bike using four of a part carries four times its material and once its "
    "freight. The two readings below differ on **every** order in the window."
)

TIE_DEFECT = N_(
    "**The coefficient lookup is not reproducible.** `TOP 1 … ORDER BY "
    "datlivfctf DESC` has no tiebreaker, so when a part was delivered twice on "
    "one day the answer is whichever row SQL Server happens to return. This "
    "dashboard breaks the tie on the highest invoice number so a figure does not "
    "move between refreshes."
)
