"""Management view — the internal picture from the Eurocycles ERP.

Where the Distributor view is one distributor's outward market, this is the
company's own numbers. Six tabs, one shared sidebar (currency, bikes-only, year
range, distributor). `facture ⋈ facture_det ⋈ nomachat` is loaded once here and
handed to the sales-driven tabs as `scope`; the Production / Supply / Finance
tabs run their own ERP queries.

See docs/eurocycles-erp-findings.md for the full data map.
"""
from __future__ import annotations

import streamlit as st

import erp
from theme import palette
from ._common import Ctx
from . import overview, models, customers, production, supply, finance


def _not_connected(msg: str) -> None:
    st.title("Management")
    st.info(
        f"**{msg}**\n\n"
        "The management view reads the Eurocycles ERP on SQL Server "
        "(`(localdb)\\MSSQLLocalDB`). That database is local to the office "
        "machine and isn't reachable from this deployment.\n\n"
        "To use it, run the dashboard where the ERP is:\n"
        "```\nstreamlit run dashboard.py\n```\n"
        "or point `ERP_ODBC` / `st.secrets[\"erp\"][\"odbc\"]` at a reachable "
        "SQL Server copy."
    )
    st.caption("The Distributor view works everywhere — it reads the committed SQLite snapshot.")


def render() -> None:
    ok, msg = erp.status()
    if not ok:
        _not_connected(msg)
        return

    P = palette()
    fx = erp.load_fx()

    with st.sidebar:
        st.header("Management")
        ccy = st.selectbox("Currency", erp.DISPLAY_CCY, index=0,
                           help="All amounts are computed in DT from each invoice's own "
                                "FX rate, then shown here converted at the latest rate.")
        bikes_only = st.toggle("Bikes only", value=True,
                               help="Exclude spare-part / accessory lines (nomachat.isBike = 0).")
        st.divider()

    sales = erp.load_sales(start_year=2019, bikes_only=bikes_only)
    if sales.empty:
        st.title("Management")
        st.warning("No sales rows returned from the ERP for 2019+.")
        return

    years = sorted(sales["yr"].unique())
    with st.sidebar:
        yr_lo, yr_hi = st.select_slider("Years", options=years,
                                        value=(max(years[0], years[-1] - 5), years[-1]))
        dists = ["(all)"] + sorted(d for d in sales["distributor"].unique() if d != "(unmapped)")
        sel_dist = st.multiselect("Distributor", dists, default=["(all)"])
        st.caption(f"FX (DT per unit): €{fx['EUR']:.3f} · ${fx['USD']:.3f}\n\n"
                   "Distributor = the customer each model is designed for "
                   "(`nomachat.cusnach`).")

    scope = sales[(sales["yr"] >= yr_lo) & (sales["yr"] <= yr_hi)].copy()
    if "(all)" not in sel_dist and sel_dist:
        scope = scope[scope["distributor"].isin(sel_dist)]
    if scope.empty:
        st.title("Management")
        st.info("No rows match the current filters.")
        return

    dist_label = ("all distributors" if "(all)" in sel_dist or not sel_dist
                  else ", ".join(sel_dist))
    # From the full sales set, not `scope` — the data's own end date is what makes
    # a year partial, and it mustn't move when the user narrows the filters.
    ctx = Ctx(ccy=ccy, fx=fx, P=P, years=years, yr_lo=int(yr_lo), yr_hi=int(yr_hi),
              dist_label=dist_label, data_end=sales["datf"].max())

    t_over, t_models, t_cust, t_prod, t_supply, t_fin = st.tabs(
        ["Overview", "Models", "Customers", "Production", "Supply & cost", "Finance"]
    )
    with t_over:
        overview.render(scope, ctx)
    with t_models:
        models.render(scope, ctx)
    with t_cust:
        customers.render(scope, ctx)
    with t_prod:
        production.render(scope, ctx)
    with t_supply:
        supply.render(scope, ctx)
    with t_fin:
        finance.render(scope, ctx)
