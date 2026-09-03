"""Read access to the Eurocycles ERP (SQL Server).

The management view runs off this. It is **local-first**: the ERP lives on a
developer's `(localdb)\\MSSQLLocalDB` and is not reachable from Streamlit Cloud,
so every entry point degrades gracefully — `status()` says whether we're
connected and the view shows an explainer instead of half a dashboard.

Connection string resolution order:
  1. st.secrets["erp"]["odbc"]      (Streamlit Cloud / shared config)
  2. env var ERP_ODBC
  3. local LocalDB default (below)

NOTE on the LocalDB default: this instance does **not** support encryption at
all, so the connection string the ERP ships (`Encrypt=True`) fails with
"encryption not supported". `Encrypt=no` is required.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import streamlit as st

DEFAULT_ODBC = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=(localdb)\\MSSQLLocalDB;DATABASE=eurocycles_db;"
    "Trusted_Connection=yes;Encrypt=no;"
)

# The management view reads the main ERP plus two satellite databases on the same
# instance: the costing/valuation engine (component price history) and the
# label-printing trace (unit throughput). Tabs that need one degrade to a notice
# when it isn't attached — see `db_present()`. `eurocycles_mfc` holds a clean
# model cross-reference but nothing reads it yet, so it isn't declared here.
MAIN_DB = "eurocycles_db"
CALC_DB = "eurocycles_db_calc"
LABEL_DB = "eurocycles_label"


# Base currency of the ERP. Sales are invoiced in USD/EUR/DT; every amount we
# compute is normalised to DT via the invoice's own `cours`, then re-displayed
# in the currency the user picks using a single reference rate per currency
# (the most recent invoice rate — see load_fx).
BASE_CCY = "DT"
DISPLAY_CCY = ["DT", "EUR", "USD"]


def _safe_pct(num, den):
    """Elementwise num/den*100, NaN where den <= 0. Works on Series or scalars."""
    return np.where(den > 0, num / den * 100, np.nan)


def _base_odbc_string() -> str:
    try:
        if "erp" in st.secrets and "odbc" in st.secrets["erp"]:
            return st.secrets["erp"]["odbc"]
    except Exception:
        pass
    return os.environ.get("ERP_ODBC", DEFAULT_ODBC)


def _odbc_string(db: str = MAIN_DB) -> str:
    """Base connection string with the catalog swapped to `db`. Every database
    lives on the one instance, so only `DATABASE=` changes."""
    base = _base_odbc_string()
    if re.search(r"DATABASE=", base, re.IGNORECASE):
        return re.sub(r"DATABASE=[^;]*", f"DATABASE={db}", base, flags=re.IGNORECASE)
    return base.rstrip(";") + f";DATABASE={db};"


@st.cache_resource(show_spinner=False)
def _engine(db: str = MAIN_DB):
    from sqlalchemy import create_engine
    url = "mssql+pyodbc:///?odbc_connect=" + quote_plus(_odbc_string(db))
    return create_engine(url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def status() -> tuple[bool, str]:
    """(reachable, human message). Cached for the process — a dead LocalDB
    won't come alive mid-session, and we don't want every rerun to eat the
    connect timeout."""
    try:
        import pyodbc  # noqa: F401
    except ModuleNotFoundError:
        return False, "`pyodbc` is not installed in this environment."
    try:
        from sqlalchemy import text
        with _engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True, "Connected to the Eurocycles ERP."
    except Exception as e:
        msg = str(e).split("\n")[0]
        return False, f"ERP not reachable from here — {msg}"


@lru_cache(maxsize=8)
def db_present(db: str) -> bool:
    """Is satellite database `db` attached? Cached — a missing DB won't appear
    mid-session. Tabs use this to show a 'restore it' notice instead of erroring."""
    try:
        from sqlalchemy import text
        with _engine(db).connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _q(sql: str, *, db: str = MAIN_DB, **params) -> pd.DataFrame:
    from sqlalchemy import text
    with _engine(db).connect() as c:
        return pd.read_sql(text(sql), c, params=params)


# --------------------------------------------------------------------- FX ---
@st.cache_data(ttl=3600, show_spinner=False)
def load_fx() -> dict:
    """DT per 1 unit of each currency, from the most recent invoice in that
    currency. `line_rev_dt / fx[ccy]` converts a DT amount into `ccy`."""
    df = _q("""
        SELECT dev, cours
        FROM facture f
        WHERE cours > 0 AND dev IN ('EUR','USD')
          AND datf = (SELECT MAX(datf) FROM facture f2 WHERE f2.dev = f.dev AND f2.cours > 0)
    """)
    fx = {"DT": 1.0}
    for _, r in df.iterrows():
        fx[r["dev"]] = float(r["cours"])
    fx.setdefault("EUR", 3.12)
    fx.setdefault("USD", 2.67)
    return fx


def convert(dt_amount, ccy: str, fx: dict):
    """DT -> display currency."""
    return dt_amount / fx.get(ccy, 1.0)


def symbol(ccy: str) -> str:
    return {"DT": "DT ", "EUR": "€", "USD": "$"}.get(ccy, "")


def fmt_money(dt_amount, ccy: str, fx: dict, decimals: int = 0) -> str:
    if pd.isna(dt_amount):
        return "n/a"
    v = convert(dt_amount, ccy, fx)
    return f"{symbol(ccy)}{v:,.{decimals}f}"


def fmt_money_compact(dt_amount, ccy: str, fx: dict) -> str:
    """Short form for metric tiles: DT 40.4M, €1.7M, $312k."""
    if pd.isna(dt_amount):
        return "n/a"
    v = convert(dt_amount, ccy, fx)
    sym = symbol(ccy)
    a = abs(v)
    if a >= 1e9:
        return f"{sym}{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{sym}{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sym}{v / 1e3:.0f}k"
    return f"{sym}{v:,.0f}"


# ------------------------------------------------------------------ sales ---
@st.cache_data(ttl=1800, show_spinner="Loading sales from the ERP…")
def load_sales(start_year: int = 2019, bikes_only: bool = True) -> pd.DataFrame:
    """One row per invoice line. Revenue and COGS are both in DT.

    - revenue  = qte * prx * cours   (matches invoice `ttc`)
    - COGS     = facture_det.mat     (ERP-costed, in DT, 100% populated)
    - distributor = the model's customer (nomachat.cusnach -> customer.libcust)
    """
    bike_filter = "AND n.isBike = 1" if bikes_only else ""
    df = _q(f"""
        SELECT
            f.numf, f.datf, YEAR(f.datf) AS yr, MONTH(f.datf) AS mo,
            f.dev, f.cours, f.clif,
            d.article, d.qte, d.prx,
            (d.qte * d.prx * f.cours) AS line_rev_dt,
            d.mat                     AS line_cogs_dt,
            n.brandnach, n.modnach, n.libnach, n.framenach, n.wheelnach,
            n.ebike, n.isBike, n.isArchived,
            cu.libcust AS distributor
        FROM facture f
        JOIN facture_det d ON d.fact = f.numf
        LEFT JOIN nomachat n ON n.codnach = d.article
        LEFT JOIN customer cu ON TRY_CONVERT(float, n.cusnach) = cu.codcust
        WHERE f.datf >= :start
          AND f.cours > 0
          AND (d.typ = 'O' OR d.typ IS NULL)
          {bike_filter}
    """, start=f"{start_year}-01-01")

    df["datf"] = pd.to_datetime(df["datf"])
    df["distributor"] = df["distributor"].fillna("(unmapped)").str.strip()
    df["brandnach"] = df["brandnach"].fillna("").str.strip()
    for c in ("modnach", "libnach", "framenach", "wheelnach"):
        df[c] = df[c].fillna("").str.strip()
    df["model_label"] = df["modnach"].where(df["modnach"] != "", df["libnach"])
    df["model_label"] = df["model_label"].where(df["model_label"] != "", df["article"])
    df["line_margin_dt"] = df["line_rev_dt"] - df["line_cogs_dt"]
    df["ebike"] = df["ebike"].fillna(0).astype(int)
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def load_daily_summary() -> pd.DataFrame:
    """The ERP's own daily operational scoreboard. Category meanings from
    DailySummaryCategory: CatId1 production/day, CatId8 total bikes produced,
    CatId9 total bikes invoiced, CatId6 stock at date, CatId7 consumption."""
    cats = _q("SELECT Id, Caption FROM DailySummaryCategory")
    ds = _q("""
        SELECT DocumentDate, CatId1, CatId6, CatId7, CatId8, CatId9, CatId10
        FROM DailySummary ORDER BY DocumentDate
    """)
    ds["DocumentDate"] = pd.to_datetime(ds["DocumentDate"])
    ds.attrs["captions"] = dict(zip(cats["Id"], cats["Caption"]))
    return ds


@st.cache_data(ttl=3600, show_spinner=False)
def load_costing_years() -> pd.DataFrame:
    """Planned cost build-up per model per year per volume bracket (see
    docs/eurocycles-erp-findings §6a).

    - `mat_cost_dt`   raw-material total, DT  (TOTAL I / `tdt`)
    - `conv_cost_dt`  labour + overhead + inbound freight, DT
    - `plan_full_cost_dt` = mat + conv  — the planned COGS to compare against the
      realised `facture_det.mat`
    - `plan_price_dt` = full cost + `tdt·marge/100`  (the cost-plus markup rule;
      commissions / outbound transport are applied in-form and not persisted, so
      this is the ex-works planned price, not the final invoice price)
    - `marge` is the **markup rate on material cost**, not an achieved margin.

    One row per article × year × `IdCondition`; caller picks a bracket."""
    df = _q("""
        SELECT coden AS article, an AS yr, IdCondition, marge,
               tdt AS mat_cost_dt,
               (trs*ctrs + mov*cmov + cha*ccha) AS conv_cost_dt
        FROM costing_nc
        WHERE saison = 'D' AND an >= 2018
    """)
    df["plan_full_cost_dt"] = df["mat_cost_dt"].fillna(0) + df["conv_cost_dt"].fillna(0)
    df["plan_price_dt"] = df["plan_full_cost_dt"] + df["mat_cost_dt"].fillna(0) * df["marge"].fillna(0) / 100
    df["plan_margin_pct"] = _safe_pct(df["plan_price_dt"] - df["plan_full_cost_dt"], df["plan_price_dt"])
    return df


# ----------------------------------------------------------- customers ---
@st.cache_data(ttl=1800, show_spinner=False)
def load_plan_vs_actual(start_year: int = 2022) -> pd.DataFrame:
    """Customer shipment plan (`planningprev` ⋈ `planningprev_det`) against what
    was actually invoiced (`facture` ⋈ `facture_det`), one row per
    customer × model × month, columns `planned_qty` / `actual_qty`.

    `planningprev.client` is a `client.codecli`; resolved to the parent customer
    (`client.codcust → customer.libcust`) so it lines up with the model-based
    distributor label used everywhere else."""
    plan = _q("""
        SELECT cu.libcust AS distributor, pd.reference AS article,
               DATEFROMPARTS(YEAR(pd.dat), MONTH(pd.dat), 1) AS month,
               SUM(pd.qte) AS planned_qty
        FROM planningprev p
        JOIN planningprev_det pd ON pd.ordre = p.ordre
        LEFT JOIN client cl ON LTRIM(RTRIM(cl.codecli)) = LTRIM(RTRIM(p.client))
        LEFT JOIN customer cu ON cu.codcust = TRY_CONVERT(float, cl.codcust)
        WHERE pd.dat >= :start AND pd.dat < :end AND pd.qte > 0
        GROUP BY cu.libcust, pd.reference,
                 DATEFROMPARTS(YEAR(pd.dat), MONTH(pd.dat), 1)
    """, start=f"{start_year}-01-01", end=f"{pd.Timestamp.now().year + 1}-01-01")
    act = _q("""
        SELECT cu.libcust AS distributor, d.article,
               DATEFROMPARTS(YEAR(f.datf), MONTH(f.datf), 1) AS month,
               SUM(d.qte) AS actual_qty
        FROM facture f
        JOIN facture_det d ON d.fact = f.numf
        LEFT JOIN client cl ON LTRIM(RTRIM(cl.codecli)) = LTRIM(RTRIM(f.clif))
        LEFT JOIN customer cu ON cu.codcust = TRY_CONVERT(float, cl.codcust)
        WHERE f.datf >= :start AND (d.typ = 'O' OR d.typ IS NULL)
        GROUP BY cu.libcust, d.article,
                 DATEFROMPARTS(YEAR(f.datf), MONTH(f.datf), 1)
    """, start=f"{start_year}-01-01")
    m = plan.merge(act, on=["distributor", "article", "month"], how="outer")
    m["planned_qty"] = m["planned_qty"].fillna(0)
    m["actual_qty"] = m["actual_qty"].fillna(0)
    m["month"] = pd.to_datetime(m["month"])
    m["distributor"] = m["distributor"].fillna("(unmapped)").str.strip()
    return m


# ---------------------------------------------------------- production ---
@st.cache_data(ttl=1800, show_spinner="Loading production orders…")
def load_production_orders(start_year: int = 2022) -> pd.DataFrame:
    """Planned vs declared units per production order (OF).

    `ordprevision` = the plan (one row per OF line, `qte` planned for ISO week
    `semaine`/`an`); `declarationprd` = shop-floor declarations against that OF
    (`id_o`), possibly several, summed here. `fermee` on the last declaration
    flags the OF closed."""
    df = _q("""
        SELECT o.id_o, o.codnach AS article, o.brandnach,
               o.an AS yr, o.semaine AS week, o.qte AS planned_qty,
               o.dat AS planned_date,
               COALESCE(d.declared_qty, 0) AS declared_qty,
               COALESCE(d.closed, 0) AS closed,
               d.last_declared
        FROM ordprevision o
        LEFT JOIN (
            SELECT id_o, SUM(qte) AS declared_qty,
                   MAX(CASE WHEN fermee = 'Oui' THEN 1 ELSE 0 END) AS closed,
                   MAX(dat) AS last_declared
            FROM declarationprd GROUP BY id_o
        ) d ON d.id_o = o.id_o
        WHERE o.an >= :yr AND o.qte > 0
    """, yr=start_year)
    df["planned_date"] = pd.to_datetime(df["planned_date"], errors="coerce")
    df["last_declared"] = pd.to_datetime(df["last_declared"], errors="coerce")
    df["brandnach"] = df["brandnach"].fillna("").str.strip()
    df["attainment"] = _safe_pct(df["declared_qty"], df["planned_qty"])
    return df


@st.cache_data(ttl=1800, show_spinner="Loading label throughput…")
def load_label_throughput(start_year: int = 2022) -> pd.DataFrame:
    """Serial-number label prints from `eurocycles_label` — the closest thing to
    true unit-level throughput. One row per OF with its brand/model, total qty,
    when the OF trace was opened (`of_opened`) and the first/last frame-label
    print (`first_print` / `last_print`). Lead time = last_print − of_opened."""
    df = _q("""
        SELECT t.Id, t.Id_o, t.Brand AS brand, t.Model AS model,
               t.ItemCode AS article, t.ItemOF AS of_no, t.TotalQty AS total_qty,
               t.sysCreatedDate AS of_opened,
               l.first_print, l.last_print, l.printed_units
        FROM OFTrace t
        JOIN (
            SELECT Id, MIN(PrintDate) AS first_print, MAX(PrintDate) AS last_print,
                   COUNT(CASE WHEN PrintDate IS NOT NULL THEN 1 END) AS printed_units
            FROM OFTraceLine GROUP BY Id
        ) l ON l.Id = t.Id
        WHERE t.sysCreatedDate >= :start
    """, db=LABEL_DB, start=f"{start_year}-01-01")
    for c in ("of_opened", "first_print", "last_print"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["brand"] = df["brand"].fillna("").str.strip()
    df["lead_days"] = (df["last_print"] - df["of_opened"]).dt.total_seconds() / 86400
    return df


# ------------------------------------------------------- supply & cost ---
@st.cache_data(ttl=3600, show_spinner="Loading component price history…")
def load_component_price_changes(start_year: int = 2019) -> pd.DataFrame:
    """Per-part price-change log from `eurocycles_db_calc.hisprix`
    (`ancprx → novprx`, in `novdev` currency). Junk dates filtered. One row per
    recorded change; caller aggregates to a monthly component-cost index."""
    df = _q("""
        SELECT code, cd AS part_code, lib AS part_label, dat AS changed,
               ancprx AS old_price, novprx AS new_price,
               NULLIF(LTRIM(RTRIM(novdev)), '') AS ccy
        FROM hisprix
        WHERE dat >= :start AND dat < DATEADD(year, 1, GETDATE())
          AND novprx > 0 AND ancprx > 0
    """, db=CALC_DB, start=f"{start_year}-01-01")
    df["changed"] = pd.to_datetime(df["changed"])
    df["ccy"] = df["ccy"].fillna("?")
    df["pct_change"] = _safe_pct(df["new_price"] - df["old_price"], df["old_price"])
    df["direction"] = pd.cut(df["pct_change"], [-1e9, -0.01, 0.01, 1e9],
                             labels=["down", "flat", "up"])
    return df


@st.cache_data(ttl=1800, show_spinner="Loading supplier invoices…")
def load_supplier_invoices(start_year: int = 2022) -> pd.DataFrame:
    """Landed component cost from supplier invoices (`facturef` ⋈ `fournisseur`).
    Header money is in `devfctf`; `·coursfctf` converts to DT. `goods_dt` is the
    line-summed goods value; the freight/customs/insurance buckets on the header
    (`tim` transit-insurance, `transit`, `femb` embarquement, `fdae`, `fsa`,
    `ass` assurance, `tax`) are returned per invoice, also DT."""
    df = _q("""
        SELECT f.numfctf, f.datfctf AS dat, YEAR(f.datfctf) AS yr,
               fr.Libfourn AS supplier, f.devfctf AS ccy, f.coursfctf AS rate,
               (f.tim + f.transit + f.femb + f.fdae + f.fsa + f.ass + f.tax) * f.coursfctf AS freight_dt,
               f.transit * f.coursfctf AS transit_dt,
               f.ass * f.coursfctf AS insurance_dt,
               f.tax * f.coursfctf AS customs_dt,
               g.goods_dt
        FROM facturef f
        LEFT JOIN fournisseur fr ON LTRIM(RTRIM(fr.Codefourn)) = LTRIM(RTRIM(f.frnfctf))
        LEFT JOIN (
            SELECT numdfctf, SUM(qtedfctf * prxdfctf) AS goods_native
            FROM facturef_det GROUP BY numdfctf
        ) gd ON gd.numdfctf = f.numfctf
        CROSS APPLY (SELECT gd.goods_native * f.coursfctf AS goods_dt) g
        WHERE f.datfctf >= :start
    """, start=f"{start_year}-01-01")
    df["dat"] = pd.to_datetime(df["dat"], errors="coerce")
    df["supplier"] = df["supplier"].fillna("(unknown)").str.strip()
    for c in ("goods_dt", "freight_dt", "transit_dt", "insurance_dt", "customs_dt"):
        df[c] = df[c].fillna(0)
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def load_supplier_disputes() -> pd.DataFrame:
    """Supplier quality / quantity claims (`SDisputes` ⋈ `SDisputesLine` ⋈
    `fourn`). `Price·Quantity` in `Currency` is the claimed value; converted to
    DT with the line's own rate isn't stored, so value is left native with the
    currency alongside (mostly DT / USD)."""
    df = _q("""
        SELECT s.Id, s.DocumentDate AS dat, fr.Libfourn AS supplier,
               l.ItemCode AS part_code, l.Currency AS ccy,
               l.Quantity AS qty, l.Price AS unit_price,
               (l.Price * l.Quantity) AS claim_value,
               l.IdCategory
        FROM SDisputes s
        JOIN SDisputesLine l ON l.Id = s.Id
        LEFT JOIN fournisseur fr ON LTRIM(RTRIM(fr.Codefourn)) = LTRIM(RTRIM(l.SupplierId))
    """)
    df["dat"] = pd.to_datetime(df["dat"], errors="coerce")
    df["supplier"] = df["supplier"].fillna("(unknown)").str.strip()
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def load_open_pos() -> pd.DataFrame:
    """Inbound component pipeline: purchase-order lines whose expected-arrival
    date (`commandf_det.eta`) is still ahead. `commandf.fermeecmdf` is not
    populated in this copy (always 0), so a forward `eta` is the best "not yet
    received" proxy. `line_value_dt = qte · prx · courscmdf`."""
    df = _q("""
        SELECT c.numcmdf, c.datcmdf AS ordered, fr.Libfourn AS supplier,
               d.artdcmdf AS part_code, d.qtedcmdf AS qty,
               (d.qtedcmdf * d.prxdcmdf * c.courscmdf) AS line_value_dt,
               d.eta AS eta
        FROM commandf c
        JOIN commandf_det d ON d.numdcmdf = c.numcmdf
        LEFT JOIN fournisseur fr ON LTRIM(RTRIM(fr.Codefourn)) = LTRIM(RTRIM(c.frncmdf))
        WHERE d.qtedcmdf > 0 AND d.eta >= CAST(GETDATE() AS date)
    """)
    df["eta"] = pd.to_datetime(df["eta"])
    df["ordered"] = pd.to_datetime(df["ordered"])
    df["supplier"] = df["supplier"].fillna("(unknown)").str.strip()
    return df


# ------------------------------------------------------------ finance ---
@st.cache_data(ttl=1800, show_spinner=False)
def load_receipts(start_year: int = 2022) -> pd.DataFrame:
    """Customer cash-in (`reglement` ⋈ `reglement_det`). `mntdr` in `devdr`,
    `·courdr` to DT. Junk `datdr` values (a handful outside 2010-now) dropped.
    No customer link exists on `reglement`, so this is company-wide only."""
    df = _q("""
        SELECT r.datr AS reg_date, rd.datdr AS paid_date,
               rd.mntdr AS amount, NULLIF(LTRIM(RTRIM(rd.devdr)), '') AS ccy,
               rd.courdr AS rate
        FROM reglement r
        JOIN reglement_det rd ON rd.numdr = r.numr
        WHERE rd.datdr >= :start AND rd.datdr < DATEADD(year, 1, GETDATE())
          AND rd.mntdr > 0
    """, start=f"{start_year}-01-01")
    df["paid_date"] = pd.to_datetime(df["paid_date"])
    df["rate"] = df["rate"].where(df["rate"] > 0, 1.0)
    df["amount_dt"] = df["amount"] * df["rate"]
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_commissions() -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(rates, agent_invoices)` for the sales-commission estimate.

    - `rates`: one row per year — the blended commission rate, the mean of
      `agent3.comd` across that year's agent × category rows. The schedule spans
      2019-2027 and barely moves (0.5-1.2%, mean ~0.81%). Years with no schedule
      are simply absent; the caller must not invent one for them.
    - `agent_invoices`: the invoice numbers (`agent1.numf`) that carry a sales
      agent. Returned as ids rather than a count so the caller can intersect them
      with whatever slice of sales is on screen — a company-wide *count* against
      a filtered denominator silently overstates the share.

    `agent3` rates vary by category (`codd`) and no model→category map exists
    locally, so the per-year mean is the best available rate. That makes the
    result an estimate; it is exact only in which invoices are commissionable."""
    rates = _q("""
        SELECT an AS yr, AVG(comd) AS rate_pct, COUNT(*) AS schedule_rows
        FROM agent3 GROUP BY an
    """)
    agent_invoices = _q("SELECT DISTINCT numf FROM agent1 WHERE numf IS NOT NULL")
    return rates, agent_invoices


@st.cache_data(ttl=1800, show_spinner=False)
def load_monthly_billings(start_year: int = 2019) -> pd.DataFrame:
    """Invoiced revenue (all lines, DT) per calendar month — a light query for
    the billings-vs-collections view, so it doesn't pay for a full `load_sales`."""
    df = _q("""
        SELECT DATEFROMPARTS(YEAR(f.datf), MONTH(f.datf), 1) AS month,
               SUM(d.qte * d.prx * f.cours) AS billed_dt
        FROM facture f
        JOIN facture_det d ON d.fact = f.numf
        WHERE f.datf >= :start AND f.cours > 0 AND (d.typ = 'O' OR d.typ IS NULL)
        GROUP BY DATEFROMPARTS(YEAR(f.datf), MONTH(f.datf), 1)
    """, start=f"{start_year}-01-01")
    df["month"] = pd.to_datetime(df["month"])
    return df.sort_values("month")
