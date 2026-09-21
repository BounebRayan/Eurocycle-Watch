"""Re-quotation — what a model would cost to build at today's component prices,
and what the GPAO's own price list says to sell it for.

Ports two screens from `10-Financier/05-Direction generale`:

- **`frmAnalyseCoutMatNC.vb`** — *"Analyse coût matière nomenclature"*. For each
  model, walk the bill of materials twice: once at the price stored on the BOM
  line (`nomachat_det.prxndach`, what the model was costed at) and once at the
  part's current order price (`fpiece.prxcmdf`, what it would cost to buy now).
  The gap is the re-quotation. `load_requote`, `load_requote_lines`.
- **`frmConsultPrixNC.vb`** — *"Consultation prix nomenclature"*. The stored
  price list: `costing_nc` holds a per-model costing sheet (material by
  currency, labour, charges, margin, commissions, rebate, eco-contribution,
  sale freight) from which a FOB price in DT, USD and EUR is derived on read.
  `load_price_list`.

**Why these two together.** `docs/eurocycles-erp-findings.md` §4 flagged that the
dashboard has no trustworthy sell price — `nomachat.prxnach` is stale. It is:
against 2026 realised average selling price its median absolute error is
**35 %**, and it lands within ±10 % on 2 % of models. The price derived from
`costing_nc` lands within ±10 % on **60 %**, median absolute error **7.1 %**.
So this module is where a usable list price comes from. `price_accuracy` is the
check, and `tests/test_gpao_requote.py` pins it.

Paired with `gpao_landed`, this closes the loop: that module says what a bike
actually costs to land, this one says what it would cost to build today and
what the price list wants for it.

**Six defects are reproduced and flagged**; see the `*_DEFECT` constants and
`docs/gpao-parity.md` §6-7 (defects 8-13). The most material by far is
`UNQUOTED_DEFECT`: a
component with no current order price is scored as costing **zero** in the
re-quotation, which makes models look cheaper than they are. It flips the sign
of the answer on 126 of 749 models.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import streamlit as st

import erp
from i18n import N_

# `devisesc` carries a rate for EUR, USD and YEN only. Everything else —
# including `'DT'` and blank — is rate 1.0, which is the `ELSE 1` every GPAO
# screen's currency CASE falls through to.


def _rates() -> dict[str, float]:
    """The latest FX rates, which is what `frmAnalyseCoutMatNC` prices with.

    Note it uses **today's** rate for both legs of the comparison, not the rate
    at the date the BOM was costed. That is deliberate on the GPAO's part — the
    question the screen asks is "what would this cost now" — but it means the
    `cost` column is *not* the figure the model was actually costed at, and will
    not tie to `gpao_landed`'s `mat_dt`, which prices at the order date."""
    df = erp._q("SELECT TOP 1 [cusd], [ceur], [yen] FROM [devisesc] "
                "WHERE [dat] <= GETDATE() ORDER BY [dat] DESC")
    if df.empty:
        return {"EUR": 1.0, "USD": 1.0, "YEN": 1.0}
    r = df.iloc[0]
    return {"EUR": float(r["ceur"]), "USD": float(r["cusd"]), "YEN": float(r["yen"])}


def _cours(ccy: pd.Series, rates: dict[str, float]) -> pd.Series:
    """Map a currency column to its rate, defaulting to 1.0 — the GPAO's ELSE."""
    return ccy.astype(str).str.strip().str.upper().map(rates).fillna(1.0)


# --------------------------------------------------------------------------
# 1. Material-cost re-quotation — `frmAnalyseCoutMatNC.vb`
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RequoteGap:
    """How far the GPAO's re-quotation is from a like-for-like one.

    The GPAO scores a component with no current order price as costing zero
    (`ISNULL(FP.prxcmdf*DN.qtendach, 0)`), so those components silently
    disappear from the new quotation while remaining in the old cost. The
    re-quotation then reads as a saving that is really a gap in the data.

    `lfl_*` is the same comparison with the unquoted components dropped from
    *both* sides."""
    models: int
    lines: int
    unquoted_lines: int
    unquoted_cost: float        # DT of stored cost the new quote scores as 0
    gpao_pct: float             # what the GPAO reports, %
    lfl_pct: float              # like-for-like, %
    gpao_cheaper: int           # models the GPAO paints as getting cheaper
    lfl_cheaper: int            # ... and how many really are
    flipped: int                # models the GPAO calls cheaper that are dearer

    @property
    def unquoted_pct(self) -> float:
        return self.unquoted_lines / self.lines * 100 if self.lines else 0.0


@st.cache_data(ttl=1800, show_spinner="Re-quoting models…")
def load_requote(year: int, customer: int | None = None) -> pd.DataFrame:
    """One row per model: stored BOM cost vs the same BOM at today's prices.

    A faithful port of the GPAO's grid, plus the columns needed to say how far
    it can be trusted. The GPAO's own figures are `cost_dt`, `requote_dt`,
    `ecart` and `ecart_pct`; `*_lfl` are the like-for-like readings.

    Scope, exactly as the GPAO sets it: models that are **not archived**
    (`nomachat.isArchived = 0`) and were **invoiced at some point during
    `year`** — note the invoice filter takes any `facture_det` row for the
    article, with no cancelled-invoice (`flag`) or line-type (`typ`) test, so it
    is broader than any revenue figure elsewhere in the dashboard.

    `customer` filters on `nomachat.cusnach` — the customer a model is *designed
    for*, not the one it was invoiced to. The GPAO requires it; here it is
    optional, because the whole-book view is the useful one.

    Columns beyond the GPAO's:

    | | |
    |---|---|
    | `lines` / `unquoted_lines` | BOM components, and how many carry no current price |
    | `unquoted_cost_dt` | the stored cost of those components — scored as 0 in `requote_dt` |
    | `cost_lfl` / `requote_lfl` / `ecart_lfl_pct` | the comparison with them dropped from both sides |
    | `conflict_lines` | components whose quote currency contradicts their purchase currency |
    """
    where_cust = f" AND C.[codcust] = N.[cusnach] AND C.[codecli] = {int(customer)} " if customer else ""
    from_cust = ", [Client] C " if customer else ""

    df = erp._q(f"""
        SELECT N.[codnach] AS article, N.[libnach] AS libnach, N.[modnach] AS modnach,
               N.[refnach] AS refnach, N.[cusnach] AS cusnach,
               DN.[qtendach] AS qty, ISNULL(DN.[prxndach], 0) AS prx,
               ISNULL(FP.[prxcmdf], 0) AS prx_new,
               ISNULL(FP.[Devfpiec], '') AS dev, ISNULL(FP.[devcmdf], '') AS dev_new
        FROM [nomachat] N, [nomachat_det] DN, [fpiece] FP {from_cust}
        WHERE N.[isArchived] = 0 AND N.[codnach] = DN.[codndach]
          AND DN.[codendach] = FP.[Code] AND DN.[ordndach] = FP.[ordfpiec]
          {where_cust}
          AND EXISTS (SELECT * FROM [facture] F, [facture_det] DF
                      WHERE F.[numf] = DF.[fact] AND DF.[article] = N.[codnach]
                        AND F.[datf] BETWEEN '{year}0101' AND '{year}1231')""")
    if df.empty:
        return df

    rates = _rates()
    for c in ("qty", "prx", "prx_new"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["cours"] = _cours(df["dev"], rates)

    # Both legs at the *part's purchase* currency rate. `devcmdf` is deliberately
    # ignored here because the GPAO ignores it — see `CURRENCY_DEFECT` for why
    # that is the safer of two bad options on this data.
    df["line_cost"] = df["prx"] * df["qty"] * df["cours"]
    df["line_new"] = df["prx_new"] * df["qty"] * df["cours"]
    df["quoted"] = df["prx_new"] != 0
    df["conflict"] = (df["dev_new"].str.strip() != "") & (
        df["dev_new"].str.strip().str.upper() != df["dev"].str.strip().str.upper())

    g = df.groupby("article", as_index=False).agg(
        libnach=("libnach", "first"), modnach=("modnach", "first"),
        refnach=("refnach", "first"), cusnach=("cusnach", "first"),
        cost_dt=("line_cost", "sum"), requote_dt=("line_new", "sum"),
        lines=("qty", "size"), conflict_lines=("conflict", "sum"))

    q = df[df["quoted"]].groupby("article", as_index=False).agg(
        cost_lfl=("line_cost", "sum"), requote_lfl=("line_new", "sum"),
        quoted_lines=("qty", "size"))
    g = g.merge(q, on="article", how="left")
    for c in ("cost_lfl", "requote_lfl", "quoted_lines"):
        g[c] = g[c].fillna(0.0)
    g["unquoted_lines"] = (g["lines"] - g["quoted_lines"]).astype(int)
    g["unquoted_cost_dt"] = g["cost_dt"] - g["cost_lfl"]

    g["ecart"] = g["requote_dt"] - g["cost_dt"]
    g["ecart_pct"] = np.where(g["cost_dt"] != 0, g["ecart"] / g["cost_dt"] * 100, np.nan)
    g["ecart_lfl"] = g["requote_lfl"] - g["cost_lfl"]
    g["ecart_lfl_pct"] = np.where(g["cost_lfl"] != 0,
                                  g["ecart_lfl"] / g["cost_lfl"] * 100, np.nan)
    # The GPAO paints a row red when `cost > NVcost` — i.e. when the re-quotation
    # says the bike got *cheaper*. Carry the flag and whether it survives
    # like-for-like, because that is the whole question.
    g["gpao_cheaper"] = g["ecart"] < 0
    g["lfl_cheaper"] = g["ecart_lfl"] < 0
    g["flipped"] = g["gpao_cheaper"] & ~g["lfl_cheaper"]

    sell = _last_sale(year)
    if not sell.empty:
        g = g.merge(sell, on="article", how="left")
        g["ecart_on_sale_pct"] = np.where(
            g["sell_dt"].notna() & (g["sell_dt"] != 0),
            g["ecart"] / g["sell_dt"] * 100, np.nan)
    else:
        for c in ("last_invoice", "sell_prx", "sell_ccy", "sell_dt", "sell_date"):
            g[c] = np.nan
        g["ecart_on_sale_pct"] = np.nan

    return g.sort_values("article").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def _last_sale(year: int) -> pd.DataFrame:
    """Each article's most recent invoiced price, converted at today's rate.

    Three things about the GPAO's version of this, all reproduced:

    - It runs **one query per row** (`getInfoFacture`, called inside the render
      loop). This does the whole book in one, with `ROW_NUMBER()`.
    - Its `TOP 1 … ORDER BY F.datf DESC` has **no tiebreaker**, and the tie
      arises at two levels: several invoices can share the last date, and one
      invoice can carry the same article on several lines at different prices
      (41 articles do). Here the order is `datf DESC, numf DESC, prx DESC`,
      which is fully determined, so the figure does not move between refreshes.
      See `TIE_DEFECT`.
    - It is **not restricted to `year`**, and applies no cancelled-invoice
      filter. A model last sold in 2019 contributes its 2019 price to a 2026
      report. `sell_date` is carried so a caller can see how stale it is.

    The conversion uses today's rate rather than the invoice's own
    `facture.cours`, again as the GPAO does — the screen is asking what the old
    price is worth now. `sell_ccy` is the invoice currency, and note the GPAO's
    CASE covers only EUR and USD, so a YEN invoice is treated as dinars
    (`SALE_FX_DEFECT`)."""
    df = erp._q("""
        SELECT article, numf AS last_invoice, prx AS sell_prx, dev AS sell_ccy,
               datf AS sell_date
        FROM (SELECT DF.[article] AS article, F.[numf] AS numf, DF.[prx] AS prx,
                     F.[dev] AS dev, F.[datf] AS datf,
                     ROW_NUMBER() OVER (PARTITION BY DF.[article]
                                        ORDER BY F.[datf] DESC, F.[numf] DESC,
                                                 DF.[prx] DESC) AS rn
              FROM [facture] F, [facture_det] DF
              WHERE F.[numf] = DF.[fact]) T
        WHERE rn = 1""")
    if df.empty:
        return df
    rates = _rates()
    # The GPAO's sale-price CASE lists EUR and USD only — no YEN branch — so a
    # yen invoice falls through to ELSE 1. Reproduced; `SALE_FX_DEFECT` says so.
    sale_rates = {k: v for k, v in rates.items() if k != "YEN"}
    df["sell_prx"] = pd.to_numeric(df["sell_prx"], errors="coerce").fillna(0.0)
    df["sell_cours"] = _cours(df["sell_ccy"], sale_rates)
    df["sell_dt"] = df["sell_prx"] * df["sell_cours"]
    df["sell_date"] = pd.to_datetime(df["sell_date"])
    # SQL Server does not promise a row order without an ORDER BY, and two reads
    # of this query do come back ordered differently. The *content* per article
    # is already deterministic thanks to the `numf` tiebreaker above; sorting
    # makes the frame itself so, which is what a caller comparing two runs sees.
    return df.sort_values("article").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def load_requote_lines(article: str) -> pd.DataFrame:
    """Per-component detail behind one model's re-quotation — the GPAO's
    `frmAnalyseCoutMatNCModal2`, which it opens on a double-click of *Ecart*.

    One row per BOM line: quantity, the stored price and its currency, the
    current order price and *its* currency, and the gap in DT.

    The GPAO filters this to `Ecart <> 0`; here every line comes back and the
    caller decides, because the lines it drops include every component with no
    current price — the ones that matter most. `quoted` flags them."""
    rates = _rates()
    df = erp._q("""
        SELECT FP.[num] AS part, FP.[Libfpiec] AS lib, DN.[qtendach] AS qty,
               ISNULL(DN.[prxndach], 0) AS prx, ISNULL(FP.[Devfpiec], '') AS dev,
               ISNULL(FP.[prxcmdf], 0) AS prx_new, ISNULL(FP.[devcmdf], '') AS dev_new,
               G.[Libgroupe] AS grp
        FROM [nomachat] N
        JOIN [nomachat_det] DN ON N.[codnach] = DN.[codndach]
        JOIN [fpiece] FP ON DN.[codendach] = FP.[Code] AND DN.[ordndach] = FP.[ordfpiec]
        LEFT JOIN [groupes] G ON G.[Codegroupe] = DN.[grpndach]
        WHERE N.[isArchived] = 0 AND N.[codnach] = :article""", article=article)
    if df.empty:
        return df
    for c in ("qty", "prx", "prx_new"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["cours"] = _cours(df["dev"], rates)
    df["cost_dt"] = df["prx"] * df["qty"] * df["cours"]
    df["new_dt"] = df["prx_new"] * df["qty"] * df["cours"]
    df["ecart_dt"] = df["new_dt"] - df["cost_dt"]
    df["quoted"] = df["prx_new"] != 0
    df["conflict"] = (df["dev_new"].str.strip() != "") & (
        df["dev_new"].str.strip().str.upper() != df["dev"].str.strip().str.upper())
    return df.sort_values("ecart_dt").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_currency_conflicts() -> pd.DataFrame:
    """Parts whose quote currency (`devcmdf`) contradicts their purchase
    currency (`Devfpiec`) — a data-quality queue, not an arithmetic correction.

    See `CURRENCY_DEFECT`. `ratio` is the new price over the old **in their own
    units**: a ratio near 1.0 on a currency change means the number did not move
    while the currency label did, which is the signature of a mis-set dropdown
    rather than a genuine re-denomination. `implied_dt_swing` is what honouring
    `devcmdf` would do to that part's contribution — the column exists to show
    how dangerous a blanket "fix" would be, not to suggest applying it."""
    rates = _rates()
    df = erp._q("""
        SELECT [num] AS part, [Libfpiec] AS lib, [Devfpiec] AS dev,
               [Prxfpiec] AS prx, [devcmdf] AS dev_new, [prxcmdf] AS prx_new
        FROM [fpiece]
        WHERE [isArchived] = 0 AND [prxcmdf] IS NOT NULL AND [prxcmdf] <> 0
          AND ISNULL([devcmdf], '') <> ''
          AND ISNULL([devcmdf], '') <> ISNULL([Devfpiec], '')""")
    if df.empty:
        return df
    for c in ("prx", "prx_new"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["ratio"] = np.where(df["prx"] != 0, df["prx_new"] / df["prx"], np.nan)
    df["cours_gpao"] = _cours(df["dev"], rates)
    df["cours_stated"] = _cours(df["dev_new"], rates)
    df["gpao_dt"] = df["prx_new"] * df["cours_gpao"]
    df["stated_dt"] = df["prx_new"] * df["cours_stated"]
    df["implied_dt_swing"] = df["stated_dt"] - df["gpao_dt"]
    # A ratio within ±5 % of 1.0 across a currency change means the number never
    # moved — the currency alone did. Those are almost certainly entry errors.
    df["unchanged_number"] = df["ratio"].between(0.95, 1.05)
    return df.reindex(df["implied_dt_swing"].abs().sort_values(ascending=False).index
                      ).reset_index(drop=True)


def requote_gap(year: int, customer: int | None = None) -> RequoteGap:
    """Size the unquoted-component problem for one year. See `RequoteGap`."""
    g = load_requote(year, customer)
    if g.empty:
        return RequoteGap(0, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0)
    cost, req = g["cost_dt"].sum(), g["requote_dt"].sum()
    cost_l, req_l = g["cost_lfl"].sum(), g["requote_lfl"].sum()
    return RequoteGap(
        models=int(len(g)), lines=int(g["lines"].sum()),
        unquoted_lines=int(g["unquoted_lines"].sum()),
        unquoted_cost=float(g["unquoted_cost_dt"].sum()),
        gpao_pct=float((req / cost - 1) * 100) if cost else 0.0,
        lfl_pct=float((req_l / cost_l - 1) * 100) if cost_l else 0.0,
        gpao_cheaper=int(g["gpao_cheaper"].sum()),
        lfl_cheaper=int(g["lfl_cheaper"].sum()),
        flipped=int(g["flipped"].sum()))


# --------------------------------------------------------------------------
# 2. The stored price list — `frmConsultPrixNC.vb`
# --------------------------------------------------------------------------

# The costing sheet's build-up, in the order `frmCostingTMP3` presents it. Each
# entry is (column, label) for the waterfall the tab draws.
COSTING_STEPS = [
    ("mat_dt", N_("Material")),
    ("labour_dt", N_("Labour & charges")),
    ("margin_dt", N_("Margin")),
    ("commission_dt", N_("Commissions")),
    ("rebate_dt", N_("Rebate")),
    ("eco_dt", N_("Eco-contribution")),
    ("freight_dt", N_("Sale freight")),
]


@st.cache_data(ttl=3600, show_spinner="Reading the price list…")
def load_price_list(customer: int | None = None) -> pd.DataFrame:
    """The current costing sheet and derived FOB price for every live model.

    `costing_nc` holds one row per model per revision (`inddf`); the GPAO always
    reads the **highest `inddf`**, which is what this does. There is no stored
    price — only inputs — so the price is re-derived on every read, and the two
    screens that derive it do not use quite the same expression:

    ```
    totGDT1 = tusd·cusd + teur·ceur + tyen·cyen + tdt        material, DT
    totGDT2 = mov·cmov + cha·ccha + trs·ctrs                 labour, charges, inbound
    totG1   = totGDT1 + totGDT2
    marge   = totGDT1 · marge/100          ← on material only, not on totG1
    com     = totG1·com1/100 + totG1·com2/100
    fob     = trsv · ctrsv                                   sale freight
    ```

    | screen | price in DT |
    |---|---|
    | `frmCostingTMP3` (authoring) | `(totG1 + marge + fob + com)·(1 + rebait/100) + Eco + fob` |
    | `frmConsultPrixNC` (this list) | `(totG1 + marge + fob + com) + (totG1 + marge + com)·rebait/100 + Eco + fob` |

    Both add `fob` **twice** — deliberately; the authoring screen's source
    brackets the second pass with `'BEGIN ADD 2 fois FOB IN Tot2'`. They differ
    only in whether the rebate applies to the first freight leg, so they agree
    except where `rebait ≠ 0`: **2 models of 10,934**, worst gap 11.15 DT. Both
    are returned (`price_dt`, `price_dt_consult`) and `price_dt_single` gives
    the one-leg reading for reference; see `DOUBLE_FREIGHT`.

    `price_dt` is the one to use — it is what the person who signed the costing
    sheet off saw.
    """
    where_cust = f" AND N.[cusnach] = {int(customer)} " if customer else ""
    df = erp._q(f"""
        SELECT C.[coden] AS article, C.[inddf] AS rev, C.[an] AS yr, C.[saison] AS season,
               C.[tusd], C.[cusd], C.[teur], C.[ceur], C.[tyen], C.[cyen], C.[tdt],
               C.[mov], C.[cmov], C.[cha], C.[ccha], C.[trs], C.[ctrs],
               C.[com1], C.[com2], C.[marge], C.[rebait], C.[EcoValue] AS eco,
               C.[trsv], C.[ctrsv], C.[trsvd], C.[ctrsvd],
               N.[libnach] AS libnach, N.[modnach] AS modnach, N.[cusnach] AS cusnach,
               P.[libpack] AS packing
        FROM [costing_nc] C
        JOIN [nomachat] N ON N.[codnach] = C.[coden] AND N.[isArchived] = 0
        LEFT JOIN [packing] P ON P.[codpack] = N.[pacnach]
        WHERE C.[inddf] IN (SELECT TOP 1 C2.[inddf] FROM [costing_nc] C2
                            WHERE C2.[coden] = C.[coden] ORDER BY C2.[inddf] DESC)
              {where_cust}""")
    if df.empty:
        return df

    num = [c for c in df.columns
           if c not in ("article", "season", "libnach", "modnach", "packing")]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["mat_dt"] = (df["tusd"] * df["cusd"] + df["teur"] * df["ceur"]
                    + df["tyen"] * df["cyen"] + df["tdt"])
    df["labour_dt"] = df["mov"] * df["cmov"] + df["cha"] * df["ccha"] + df["trs"] * df["ctrs"]
    df["cost_dt"] = df["mat_dt"] + df["labour_dt"]
    df["margin_dt"] = df["mat_dt"] * df["marge"] / 100
    df["commission_dt"] = df["cost_dt"] * (df["com1"] + df["com2"]) / 100
    df["freight_dt"] = df["trsv"] * df["ctrsv"]
    df["freight_ddu_dt"] = df["trsvd"] * df["ctrsvd"]
    df["eco_dt"] = df["eco"]

    base = df["cost_dt"] + df["margin_dt"] + df["commission_dt"]
    # Authoring screen: the rebate compounds on a base that already carries one
    # freight leg, then the second leg goes on after the eco-contribution.
    df["rebate_dt"] = (base + df["freight_dt"]) * df["rebait"] / 100
    df["price_dt"] = (base + df["freight_dt"] + df["rebate_dt"]
                      + df["eco_dt"] + df["freight_dt"])
    # Price-list screen: same, except the rebate's base excludes that first leg.
    df["price_dt_consult"] = (base + df["freight_dt"] + base * df["rebait"] / 100
                              + df["eco_dt"] + df["freight_dt"])
    # One freight leg, for reference — not a GPAO figure.
    df["price_dt_single"] = (base * (1 + df["rebait"] / 100) + df["eco_dt"]
                             + df["freight_dt"])
    df["second_leg_dt"] = df["price_dt"] - df["price_dt_single"]

    # The screen quotes in USD and EUR off the costing sheet's own stored rates,
    # not today's — a sheet written in 2016 still divides by its 2016 rate.
    for ccy, col in (("usd", "cusd"), ("eur", "ceur")):
        df[f"price_{ccy}"] = np.where(df[col] > 0, df["price_dt"] / df[col], np.nan)

    df["margin_on_price_pct"] = np.where(
        df["price_dt"] > 0, (df["price_dt"] - df["cost_dt"]) / df["price_dt"] * 100, np.nan)
    df["no_packing"] = df["packing"].isna()
    return df.sort_values("article").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def price_accuracy(year: int) -> pd.DataFrame:
    """The price list against what customers were actually invoiced, per model.

    This is the check that decides whether the price list is usable at all.
    Columns: `asp` (revenue ÷ units for `year`), `price_dt` (the list price),
    `prxnach` (the field findings §4 flagged as stale) and the signed percentage
    error of each against `asp`.

    Result on 2026, over the 283 models carrying both figures: the list price's
    median absolute error is **7.1 %** and it lands within ±10 % on 60 % of
    models; `prxnach`'s median absolute error is **35.3 %**, within ±10 % on 2 %.
    Coverage is the list price's weakness, not accuracy — only 45 % of models
    invoiced in 2026 have a `costing_nc` row at all."""
    act = erp._q(f"""
        SELECT DF.[article] AS article, SUM(DF.[qte]*DF.[prx]*F.[cours]) AS revenue,
               SUM(DF.[qte]) AS units, MAX(N.[prxnach]) AS prxnach
        FROM [facture] F, [facture_det] DF, [nomachat] N
        WHERE F.[numf] = DF.[fact] AND DF.[article] = N.[codnach] AND F.[flag] <> 1
          AND DF.[typ] IN ('O', 'I')
          AND F.[datf] BETWEEN '{year}0101' AND '{year}1231'
        GROUP BY DF.[article] HAVING SUM(DF.[qte]) > 0""")
    if act.empty:
        return act
    for c in ("revenue", "units", "prxnach"):
        act[c] = pd.to_numeric(act[c], errors="coerce")
    act["asp"] = act["revenue"] / act["units"]

    pl = load_price_list()
    cols = ["article", "price_dt", "cost_dt", "libnach", "cusnach"]
    out = act.merge(pl[cols] if not pl.empty else pd.DataFrame(columns=cols),
                    on="article", how="left")
    out["err_list_pct"] = np.where(out["asp"] > 0,
                                   (out["price_dt"] - out["asp"]) / out["asp"] * 100, np.nan)
    out["err_prxnach_pct"] = np.where(
        (out["asp"] > 0) & (out["prxnach"] > 0),
        (out["prxnach"] - out["asp"]) / out["asp"] * 100, np.nan)
    return out


# --------------------------------------------------------------------------
# The defects, as text the tab renders
# --------------------------------------------------------------------------

UNQUOTED_DEFECT = N_(
    "**A component with no current price is re-quoted at zero.** The GPAO builds "
    "the new quotation as `ISNULL(FP.prxcmdf · DN.qtendach, 0)`, so a part whose "
    "`prxcmdf` was never filled in contributes **nothing** to the new cost while "
    "still contributing its full stored cost to the old one. The re-quotation "
    "then reports a saving that is really a hole in the part master.\n\n"
    "This is not a rounding matter — it changes the **sign** of the answer. The "
    "figures beside each model show the GPAO's reading and the same comparison "
    "with unquoted components dropped from both sides."
)

CURRENCY_DEFECT = N_(
    "**The quote currency is recorded but never used.** `fpiece` carries the "
    "current order price in `prxcmdf` and its currency in `devcmdf`, but "
    "`frmAnalyseCoutMatNC` converts the new quotation at the rate for "
    "`Devfpiec` — the part's *purchase* currency — and never reads `devcmdf` at "
    "all. Where the two disagree, the new price is converted at the wrong rate.\n\n"
    "Honouring `devcmdf` instead would be worse, not better: on many of these "
    "parts the number did not change when the currency label did (a chain at "
    "61,923 YEN re-quoted at 61,923 \"USD\"), which is a mis-set dropdown rather "
    "than a re-denomination — and converting it would value that one chain at "
    "182,000 DT. So the dashboard reproduces the GPAO and lists the conflicting "
    "parts below as a data-quality queue. They need fixing at source; neither "
    "reading is right until they are."
)

SALE_FX_DEFECT = N_(
    "**The sale price is not the price that was invoiced.** `getInfoFacture` "
    "takes the most recent invoice for the article **ever** — not one inside the "
    "report's year, and with no cancelled-invoice filter — and converts it at "
    "**today's** rate rather than the invoice's own `facture.cours`.\n\n"
    "Both bite. Run the report for a past year and the variance is measured "
    "against a price from *after* that year: on a 2023 run that is **60 % of "
    "models**, on 2024 **48 %**. And **469 articles** have a cancelled invoice "
    "(`flag = 1`) as their most recent, so their sale price is a voided one. "
    "Running the current year is safe on the first count and not the second.\n\n"
    "Its currency CASE also covers only `EUR` and `USD`, falling through to "
    "`ELSE 1` — a yen invoice would be read as dinars. No invoice in this "
    "database is in yen, so that branch is latent rather than live."
)

TIE_DEFECT = N_(
    "**The \"last invoice\" lookup is not reproducible.** `TOP 1 … ORDER BY "
    "F.datf DESC` has no tiebreaker, and the tie happens at two levels: several "
    "invoices can share a model's last invoice date, and a single invoice can "
    "carry the same article on more than one line at different prices — **41 "
    "articles do**. Either way the sale price, and every percentage derived "
    "from it, moves between two runs of the same report. This dashboard orders "
    "by date, then invoice number, then price, so the figure holds still."
)

DOUBLE_FREIGHT = N_(
    "**The sale freight leg is counted twice, on purpose.** Every price this "
    "screen derives adds `trsv · ctrsv` once inside the sub-total and again at "
    "the end. It is not an oversight — the costing screen's own source brackets "
    "the second pass with `'BEGIN ADD 2 fois FOB IN Tot2'` — but nothing on the "
    "price list says so, and a reader comparing a list price to a freight quote "
    "will not find the second leg. It is worth a median **1.34 %** of price, "
    "and more than 5 % on 99 of 10,934 models."
)

REBATE_SPLIT = N_(
    "**The authoring screen and the price list disagree about the rebate.** "
    "`frmCostingTMP3` applies `rebait` to a base that includes the first freight "
    "leg; `frmConsultPrixNC` applies it to a base that excludes it. So the price "
    "the costing sheet was signed off at and the price the list reports differ "
    "by `trsv · ctrsv · rebait/100` whenever a rebate is set. On current "
    "revisions that is **2 models of 10,934** and at most 11.15 DT, so it is "
    "recorded rather than acted on — but it means there is no single stored "
    "price in this ERP, only two derivations of one."
)

STALE_PRXNACH = N_(
    "**Where the sell price should come from.** `nomachat.prxnach` — the field "
    "the rest of the dashboard falls back on — misses realised average selling "
    "price by a median of **35 %** and lands within ±10 % on **2 %** of models. "
    "The price derived from `costing_nc` lands within ±10 % on **60 %**, median "
    "error **7.1 %**. Its weakness is coverage, not accuracy: fewer than half "
    "the models invoiced this year have a costing sheet at all."
)
