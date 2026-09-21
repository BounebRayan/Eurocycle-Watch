"""Exchange-rate variation — what the dinar did to the sales book.

Ports **`10-Financier/05-Direction generale/frmExchangeRate.vb`**, captioned
_"Variation du taux de change"_. It is the fourth of the management screens
`docs/gpao-parity.md` set out to port, and the one the Actions tab needed: every
sale Eurocycles makes is invoiced in EUR or USD (DT invoices are 5 of 2,213 over
2024-26) while `facture_det.mat` — the cost side — is stored in dinar. So a
model sold at an unchanged foreign price, to the same customer, in the same
volume, still moves its dinar margin when the rate moves. Nothing else in the
dashboard could separate that from a commercial loss.

**What the screen does.** Take every non-cancelled EUR/USD invoice in a window,
group by customer x currency x calendar month, and revalue the *same*
foreign-currency total at two rates:

    Cours (2) = the month's own average devisesc rate
    Cours (1) = the previous month's average        (January: the 31/12 rate)
    Ecart     = TotalDev * Cours(2)  -  TotalDev * Cours(1)

Volume and price are identical on both legs, so the whole `Ecart` is exchange
rate. There are two grids — by customer and by currency — and a TOTAL band that
revalues the year at the closing rate against the opening one.

**The screen cannot be summed.** Its monthly columns measure each month against
its own predecessor; its TOTAL band measures the year's whole volume against the
opening rate. They are different questions and they disagree — for EUR 2026 they
disagree in sign. See `MONTHLY_TOTAL_DEFECT` and `docs/gpao-parity.md` §8.

**Four defects are reproduced and flagged**; see the `*_DEFECT` constants.

`fx_split` is the correction the rest of the dashboard consumes. It does not use
this screen's month-average rates at all — it uses `facture.cours`, the rate
actually booked on each invoice, because that is what `erp.load_sales` turns
into `line_rev_dt` and therefore what every margin in the Management view is
already built from. Re-deriving the split from a monthly average would not
reconcile against the tabs it is meant to correct.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import erp
from i18n import N_

# `devisesc` is a daily table with one row per calendar day and a column per
# currency (`ceur`, `cusd`, `yen`). This screen reads EUR and USD only — its
# WHERE clause excludes every other currency, so the rate CASE never falls
# through. `gpao_requote` documents the ELSE 1 the other screens rely on.
CURRENCIES = ("EUR", "USD")

_RATE_COL = {"EUR": "ceur", "USD": "cusd"}


# --------------------------------------------------------------- defects ---
MONTHLY_TOTAL_DEFECT = N_(
    "**The monthly columns and the TOTAL band measure different things.** Each "
    "month's `Ecart` compares that month's sales against the *previous month's* "
    "rate, so the twelve columns are twelve independent month-on-month "
    "movements. The TOTAL band instead revalues the **whole year's** volume at "
    "the closing rate against the opening one. Summing the monthly columns "
    "therefore does not give the total the same grid reports beside them."
)

BAND_OFFSET_DEFECT = N_(
    "**Month columns are mislabelled unless the window starts in January.** The "
    "grid captions its bands by walking forward from the start date "
    "(`datedeb.AddMonths(j)`), but the data is filed by calendar month "
    "(`DATEPART(Month, datf)`). Start the window in March and March's figures "
    "land in the column captioned MAY, while the first two columns stand empty. "
    "The screen opens on 1 January, so its default view is correct and the "
    "defect only fires when someone moves the date."
)

AVERAGE_RATE_DEFECT = N_(
    "**Neither leg uses the rate the invoice was actually booked at.** The "
    "screen prices both legs from monthly averages of `devisesc`, while "
    "`facture.cours` — the rate every revenue figure in the GPAO and in this "
    "dashboard is computed from — is the rate stored on the invoice itself. So "
    "this screen's dinar totals cannot be reconciled against any other screen's "
    "revenue."
)

# A month with no `devisesc` row at all divides by COUNT() = 0 and is handed
# back as rate 0, which would report the month's entire turnover as exchange
# variation. It does not fire on this restore — `devisesc` has a row for every
# day of 2024-26 — so it is recorded as latent rather than measured.
ZERO_RATE_DEFECT = N_(
    "**A month with no rate quoted is priced at zero, not skipped.** The rate "
    "subqueries end `ELSE 0`, so a gap in `devisesc` would revalue that month's "
    "sales at nothing and report the whole turnover as exchange variation. "
    "`devisesc` is complete for 2024-26, so this is latent here."
)


# ------------------------------------------------------------ the screen ---
def _gpao_sql(start: str, end: str, *, by_customer: bool) -> str:
    """`frmExchangeRate.GetListAchatsALL`'s own query.

    Kept as one string so `tests/test_gpao_exchange.py` can run the GPAO's SQL
    character for character against the port. The screen builds two variants of
    it — with the customer columns and without — which is the `by_customer`
    switch here. `start`/`end` are `yyyyMMdd`, as the VB formats them.

    The month-average subqueries are written `SUM(x)/COUNT(x)` rather than
    `AVG(x)`, and that is not a quirk to tidy up: SQL Server rejects a
    correlated aggregate spanning more than one outer column, which is what
    `AVG` over a CASE on `F.dev` would be. The division is the workaround."""
    sel = ("F.[clif] AS CodeCLT, (SELECT C.[libcli] FROM [client] C "
           "WHERE C.[codecli] = F.[clif]) as NameCLT, ") if by_customer else ""
    out = "[CodeCLT], [NameCLT], " if by_customer else ""
    grp = "[CodeCLT], [NameCLT], " if by_customer else ""
    order = "[CodeCLT], " if by_customer else ""
    # `Cours3112` is a scalar for the whole window: the last rate quoted on or
    # before the month end preceding the start date. The VB passes
    # `datedeb.AddMonths(-1)`, so for a calendar year it is the 31/12 rate.
    prev_month_end = (pd.Timestamp(start) - pd.DateOffset(months=1)).strftime("%Y%m%d")
    return f"""
        SELECT {out}[Devise], [Cours3112], SUM([TotalDev]) AS TotalDev,
               MAX([Cours]) AS [Cours], MAX([CoursM1]) AS [CoursM1],
               [StartDate], [EndDate], [MonthIndexGlb]
        FROM (
          SELECT {sel}F.[dev] AS Devise
          , (SELECT TOP 1 CASE WHEN F.[dev] = 'EUR' THEN D.[ceur]
                ELSE CASE WHEN F.[dev] = 'USD' THEN D.[cusd] END END
             FROM [devisesc] D WHERE D.[dat] <= EOMONTH('{prev_month_end}')
             ORDER BY D.[dat] DESC) AS Cours3112
          , ISNULL(DF.[qte]*DF.[prx], 0) as TotalDev
          , (SELECT CASE WHEN F.[dev] = 'EUR'
                THEN CASE WHEN COUNT(D.[ceur]) > 0
                     THEN ISNULL(SUM(D.[ceur]), 0)/COUNT(D.[ceur]) ELSE 0 END
                ELSE CASE WHEN F.[dev] = 'USD'
                     THEN CASE WHEN COUNT(D.[cusd]) > 0
                          THEN ISNULL(SUM(D.[cusd]), 0)/COUNT(D.[cusd]) ELSE 0 END
                     END END AS nbr
             FROM [devisesc] D WHERE [dat] BETWEEN
                CAST(CAST(YEAR(F.[datf]) AS VARCHAR)+'-'+CAST(MONTH(F.[datf]) AS VARCHAR)+'-01' AS DATE)
                AND EOMONTH(F.[datf])) AS Cours
          , (SELECT CASE WHEN F.[dev] = 'EUR'
                THEN CASE WHEN COUNT(D.[ceur]) > 0
                     THEN ISNULL(SUM(D.[ceur]), 0)/COUNT(D.[ceur]) ELSE 0 END
                ELSE CASE WHEN F.[dev] = 'USD'
                     THEN CASE WHEN COUNT(D.[cusd]) > 0
                          THEN ISNULL(SUM(D.[cusd]), 0)/COUNT(D.[cusd]) ELSE 0 END
                     END END AS nbr
             FROM [devisesc] D WHERE [dat] BETWEEN
                CAST(CAST(YEAR(DATEADD(MONTH,-1, F.[datf])) AS VARCHAR)+'-'+CAST(MONTH(DATEADD(MONTH,-1, F.[datf])) AS VARCHAR)+'-01' AS DATE)
                AND EOMONTH(DATEADD(MONTH,-1, F.[datf]))) AS CoursM1
          , CAST(CAST(YEAR(F.[datf]) AS VARCHAR)+'-'+CAST(MONTH(F.[datf]) AS VARCHAR)+'-01' AS DATE) AS StartDate,
            EOMONTH(F.[datf]) AS EndDate, DATEPART(Month, F.[datf]) AS MonthIndexGlb
          FROM [facture] F, [facture_det] DF
          WHERE F.[numf] = DF.[fact] AND F.[flag] <> 1
            AND (F.[dev] = 'EUR' OR F.[dev] = 'USD')
            AND F.[datf] BETWEEN '{start}' AND '{end}'
            AND ISNULL([cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE'
        )TAB
        GROUP BY {grp}[Devise], [Cours3112], [StartDate], [EndDate], [MonthIndexGlb]
        ORDER BY {order}[Devise], [MonthIndexGlb]"""


def _ecart(df: pd.DataFrame) -> pd.DataFrame:
    """The VB's per-row arithmetic, applied to the query's output.

    `frmExchangeRate` does this in its render loop, one grid cell at a time:
    month 1 compares against `Cours3112`, every other month against `CoursM1`.
    Reproduced here including the `Ecart (%)` guard, which returns 0 rather than
    NULL when the first leg is zero — so a month priced at a missing rate reads
    as a 0 % move beside a full-turnover `Ecart`. See `ZERO_RATE_DEFECT`."""
    if df.empty:
        return df
    d = df.copy()
    d["mo"] = d["MonthIndexGlb"].astype(int)
    d["rate_prev"] = np.where(d["mo"] == 1, d["Cours3112"], d["CoursM1"]).astype(float)
    d["rate_cur"] = d["Cours"].astype(float)
    d["total_ccy"] = d["TotalDev"].astype(float)
    d["total_dt_prev"] = d["total_ccy"] * d["rate_prev"]
    d["total_dt_cur"] = d["total_ccy"] * d["rate_cur"]
    d["ecart"] = d["total_dt_cur"] - d["total_dt_prev"]
    d["ecart_pct"] = np.where(d["total_dt_prev"] != 0,
                              d["ecart"] / d["total_dt_prev"] * 100, 0.0)
    return d


def _window(year: int) -> tuple[str, str]:
    """The screen's default window: the whole calendar year."""
    return f"{year}0101", f"{year}1231"


@st.cache_data(ttl=1800, show_spinner="Loading exchange-rate variation…")
def load_fx_variation(year: int, *, by_customer: bool = True) -> pd.DataFrame:
    """The GPAO grid, long rather than wide — one row per group per month.

    The screen renders this as twelve banded month blocks across; a long frame
    is what the tab and the tests want, and the reshape is lossless. Columns:
    `dev`, `mo`, `total_ccy`, `rate_prev`, `rate_cur`, `total_dt_prev`,
    `total_dt_cur`, `ecart`, `ecart_pct`, plus `code`/`customer` when
    `by_customer`."""
    start, end = _window(year)
    raw = erp._q(_gpao_sql(start, end, by_customer=by_customer))
    if raw.empty:
        return pd.DataFrame()
    d = _ecart(raw)
    keep = ["dev", "mo", "total_ccy", "rate_prev", "rate_cur",
            "total_dt_prev", "total_dt_cur", "ecart", "ecart_pct"]
    d = d.rename(columns={"Devise": "dev"})
    if by_customer:
        d = d.rename(columns={"CodeCLT": "code", "NameCLT": "customer"})
        d["customer"] = d["customer"].fillna("(unnamed)").astype(str).str.strip()
        keep = ["code", "customer"] + keep
    return d[keep].sort_values(keep[:2] if by_customer else ["dev", "mo"]).reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner=False)
def year_total(year: int) -> pd.DataFrame:
    """The grid's TOTAL band: the year's volume revalued closing vs opening.

    This is the screen's *other* answer, and it is not the sum of the monthly
    columns — see `MONTHLY_TOTAL_DEFECT`. The VB reads the opening rate from the
    same `Cours3112` scalar the grid carries and the closing rate from
    `getDevises`, which takes the last quote on or before the window end."""
    start, end = _window(year)
    rows = []
    for dev in CURRENCIES:
        col = _RATE_COL[dev]
        # `yyyyMMdd`, as the VB formats every date it interpolates. This server
        # runs a French locale, where an ISO `yyyy-MM-dd` string is parsed
        # day-first and 2026-12-31 lands out of range.
        opening = erp._q(
            f"SELECT TOP 1 [{col}] AS r FROM [devisesc] WHERE [dat] <= EOMONTH(:d) "
            "ORDER BY [dat] DESC",
            d=(pd.Timestamp(start) - pd.DateOffset(months=1)).strftime("%Y%m%d"))
        closing = erp._q(f"SELECT TOP 1 [{col}] AS r FROM [devisesc] WHERE [dat] <= :d "
                         "ORDER BY [dat] DESC", d=pd.Timestamp(end).strftime("%Y%m%d"))
        vol = erp._q("""
            SELECT ISNULL(SUM(DF.[qte]*DF.[prx]), 0) AS total_ccy
            FROM [facture] F, [facture_det] DF
            WHERE F.[numf] = DF.[fact] AND F.[flag] <> 1 AND F.[dev] = :dev
              AND F.[datf] BETWEEN :s AND :e
              AND ISNULL(F.[cndrgl], '') <> 'WITHOUT COMMERCIAL VALUE'""",
            dev=dev, s=start, e=end)
        if opening.empty or closing.empty:
            continue
        op, cl = float(opening.iloc[0]["r"]), float(closing.iloc[0]["r"])
        tot = float(vol.iloc[0]["total_ccy"])
        rows.append({"dev": dev, "total_ccy": tot, "rate_open": op, "rate_close": cl,
                     "total_dt_open": tot * op, "total_dt_close": tot * cl,
                     "ecart": tot * (cl - op),
                     "ecart_pct": ((cl - op) / op * 100) if op else 0.0})
    return pd.DataFrame(rows)


# ------------------------------------------------------------ correction ---
def booked_rates(scope: pd.DataFrame) -> pd.DataFrame:
    """Turnover-weighted average of the rate actually booked, per year x currency.

    Weighted by turnover in the invoice currency, so it is the rate that
    reproduces the year's dinar revenue from its foreign-currency total — not a
    plain mean of the daily quotes, which would drift from it whenever sales
    cluster in part of the year. `scope` is an `erp.load_sales` frame."""
    d = scope.copy()
    d["rev_ccy"] = d["qte"] * d["prx"]
    g = d.groupby(["yr", "dev"]).agg(rev_dt=("line_rev_dt", "sum"),
                                     rev_ccy=("rev_ccy", "sum")).reset_index()
    g["rate"] = np.where(g["rev_ccy"] != 0, g["rev_dt"] / g["rev_ccy"], 1.0)
    return g[["yr", "dev", "rate", "rev_ccy", "rev_dt"]]


def fx_split(scope: pd.DataFrame, yr: int, prev: int, *,
             group: str = "article") -> pd.DataFrame:
    """Split a year-on-year margin move into exchange rate and everything else.

    The question the Actions tab's erosion rule asks — "this model held its
    volume and lost margin, so was it price or was it cost?" — has a third
    answer the rule never offered: neither, the dinar moved. `line_rev_dt` is
    `qte * prx * facture.cours`, translated at the invoice's own rate, while
    `line_cogs_dt` is `facture_det.mat`, already in dinar. The two sides of the
    margin therefore do not move together when the rate does.

    The split reprices **both** years at one rate — year `prev`'s booked rate —
    line by line in each line's own currency, so a model that shifted between
    EUR and USD is handled by construction rather than excluded:

        rev_const[y]    = SUM(qte * prx * rate_prev[line currency])   for y in (prev, yr)
        margin_const[y] = 1 - cogs[y] / rev_const[y]
        drop_pp_ex_fx   = margin_const[prev] - margin_const[yr]
        fx_pp           = drop_pp - drop_pp_ex_fx

    Repricing only `yr` and leaving `prev` at its reported margin would look
    equivalent and is not: `line_rev_dt` uses each *invoice's* own `cours`, so a
    model whose sales cluster in months when the rate ran above the year's
    average carries that timing in its reported margin. Holding one rate across
    both years cancels it, and makes the split return exactly zero when a year
    is compared against itself.

    `fx_pp` is the points of the year-on-year margin drop explained by the rate
    — positive when the rate move deepened the reported loss, negative when it
    masked one. `drop_pp_ex_fx` is what is left, and it is the figure a
    commercial conversation should start from.

    Returns one row per `group` value present in **both** years.

    **NaN is a real answer here.** A model can appear in a year with credit
    notes and no units — `BF 2424260` carries two 2025 lines totalling zero
    units, DT 104 of revenue and *negative* COGS, which reports as a 115 %
    margin and a 75 pp "drop". Constant-rate revenue for such a year is zero, so
    the margin is undefined and the split returns NaN rather than a number that
    would rank near the top of any list sorted by damage. Callers filter on
    `units`/`units_prev`; the NaN is the backstop for the ones that don't."""
    need = {"yr", "dev", "qte", "prx", "line_rev_dt", "line_cogs_dt", group}
    missing = need - set(scope.columns)
    if missing:
        raise KeyError(f"fx_split needs columns {sorted(missing)}")

    rates = booked_rates(scope).set_index(["yr", "dev"])["rate"]
    prev_rate = {dev: rates.get((prev, dev), np.nan) for dev in scope["dev"].unique()}

    d = scope[scope["yr"].isin([yr, prev])].copy()
    d["rate_prev_yr"] = d["dev"].map(prev_rate)
    # A currency that did not trade in `prev` has no rate to hold constant; the
    # honest reading is to leave those lines at their booked value, which scores
    # them as zero FX effect rather than inventing a rate for them.
    d["rev_dt_const"] = np.where(d["rate_prev_yr"].notna(),
                                 d["qte"] * d["prx"] * d["rate_prev_yr"],
                                 d["line_rev_dt"])

    agg = (d.groupby([group, "yr"])
             .agg(rev=("line_rev_dt", "sum"), rev_const=("rev_dt_const", "sum"),
                  cogs=("line_cogs_dt", "sum"), units=("qte", "sum"))
             .reset_index())
    cur = agg[agg["yr"] == yr].drop(columns="yr")
    old = agg[agg["yr"] == prev].drop(columns="yr")
    m = cur.merge(old, on=group, suffixes=("", "_prev"))
    if m.empty:
        return m

    def _pct(margin_num, den):
        return np.where(den > 0, margin_num / den * 100, np.nan)

    m["margin_pct"] = _pct(m["rev"] - m["cogs"], m["rev"])
    m["margin_pct_prev"] = _pct(m["rev_prev"] - m["cogs_prev"], m["rev_prev"])
    # Both years at the one rate, so the comparison carries no translation —
    # neither the year-on-year move nor either year's internal sales timing.
    m["margin_pct_const"] = _pct(m["rev_const"] - m["cogs"], m["rev_const"])
    m["margin_pct_const_prev"] = _pct(m["rev_const_prev"] - m["cogs_prev"],
                                      m["rev_const_prev"])

    m["drop_pp"] = m["margin_pct_prev"] - m["margin_pct"]
    m["drop_pp_ex_fx"] = m["margin_pct_const_prev"] - m["margin_pct_const"]
    m["fx_pp"] = m["drop_pp"] - m["drop_pp_ex_fx"]
    # Valued on the year's own revenue, the same basis the erosion rule states.
    m["fx_dt"] = m["fx_pp"] / 100 * m["rev"]
    return m


@st.cache_data(ttl=1800, show_spinner=False)
def booked_vs_average(year: int) -> pd.DataFrame:
    """How far each invoice's booked rate sits from the month average the screen
    prices with — the measurement behind `AVERAGE_RATE_DEFECT`."""
    inv = erp._q("""
        SELECT F.[numf], YEAR(F.[datf]) AS yr, MONTH(F.[datf]) AS mo,
               F.[dev], F.[cours]
        FROM [facture] F
        WHERE F.[flag] <> 1 AND F.[dev] IN ('EUR','USD') AND YEAR(F.[datf]) = :y""", y=year)
    if inv.empty:
        return pd.DataFrame()
    avg = erp._q("""
        SELECT YEAR([dat]) AS yr, MONTH([dat]) AS mo,
               SUM([ceur])/COUNT([ceur]) AS EUR, SUM([cusd])/COUNT([cusd]) AS USD
        FROM [devisesc] WHERE YEAR([dat]) = :y
        GROUP BY YEAR([dat]), MONTH([dat])""", y=year)
    if avg.empty:
        return pd.DataFrame()
    long = (avg.melt(id_vars=["yr", "mo"], value_vars=list(CURRENCIES),
                     var_name="dev", value_name="avg_rate")
               .astype({"yr": int, "mo": int}))
    d = inv.astype({"yr": int, "mo": int}).merge(long, on=["yr", "mo", "dev"], how="left")
    d["gap_pct"] = (d["cours"] / d["avg_rate"] - 1) * 100
    return d
