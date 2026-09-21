"""Management view — the internal picture from the Eurocycles ERP.

Where the Distributor view is one distributor's outward market, this is the
company's own numbers. Twelve tabs, one shared sidebar (currency, bikes-only, year
range, distributor). `facture ⋈ facture_det ⋈ nomachat` is loaded once here and
handed to the sales-driven tabs as `scope`; the Production / Supply / Finance
tabs run their own ERP queries.

See docs/eurocycles-erp-findings.md for the full data map.
"""
from __future__ import annotations

import streamlit as st

import erp
import i18n
from theme import palette
from ._common import Ctx
from . import (actions, overview, models, valuechain, customers, production, supply,
               finance, activity, landed, requote, exchange)


def _not_connected(msg: str) -> None:
    st.title(tr("Management"))
    st.info(
        f"**{msg}**\n\n"
        "The management view reads the Eurocycles ERP on SQL Server "
        "(`EC-RAYAN`). That database is local to the office "
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

    # Read before the widgets are built: every label below needs the language
    # that was picked on the previous run, including the selector's own.
    lang = i18n.LANGUAGES.get(st.session_state.get("mgmt_lang"), i18n.DEFAULT_LANG)

    def tr(s: str) -> str:
        return i18n.t(s, lang)

    with st.sidebar:
        st.header(tr("Management"))
        ccy = st.selectbox(tr("Currency"), erp.DISPLAY_CCY, index=0,
                           help=tr("All amounts are computed in DT from each invoice's own "
                                   "FX rate, then shown here converted at the latest rate."))
        st.selectbox(tr("Language"), list(i18n.LANGUAGES), key="mgmt_lang",
                     help=tr("Translates the whole Management view. Table values, model "
                             "names and ERP column names stay as the ERP holds them."))
        bikes_only = st.toggle(tr("Bikes only"), value=True,
                               help=tr("Exclude spare-part / accessory lines "
                                       "(nomachat.isBike = 0)."))
        st.divider()

    sales = erp.load_sales(start_year=2019, bikes_only=bikes_only)
    if sales.empty:
        st.title(tr("Management"))
        st.warning(tr("No sales rows returned from the ERP for 2019+."))
        return

    years = sorted(sales["yr"].unique())
    with st.sidebar:
        yr_lo, yr_hi = st.select_slider(tr("Years"), options=years,
                                        value=(max(years[0], years[-1] - 5), years[-1]))
        dists = ["(all)"] + sorted(d for d in sales["distributor"].unique() if d != "(unmapped)")
        sel_dist = st.multiselect(tr("Distributor"), dists, default=["(all)"])
        st.caption(i18n.tf("FX (DT per unit): €{eur} · ${usd}\n\nDistributor = the "
                           "customer each model is designed for (`nomachat.cusnach`).",
                           lang, eur=f"{fx['EUR']:.3f}", usd=f"{fx['USD']:.3f}"))

    scope = sales[(sales["yr"] >= yr_lo) & (sales["yr"] <= yr_hi)].copy()
    if "(all)" not in sel_dist and sel_dist:
        scope = scope[scope["distributor"].isin(sel_dist)]
    if scope.empty:
        st.title(tr("Management"))
        st.info(tr("No rows match the current filters."))
        return

    dist_label = (tr("all distributors") if "(all)" in sel_dist or not sel_dist
                  else ", ".join(sel_dist))
    # From the full sales set, not `scope` — the data's own end date is what makes
    # a year partial, and it mustn't move when the user narrows the filters.
    ctx = Ctx(ccy=ccy, fx=fx, P=P, years=years, yr_lo=int(yr_lo), yr_hi=int(yr_hi),
              dist_label=dist_label, data_end=sales["datf"].max(), lang=lang)

    (t_act, t_over, t_activity, t_models, t_chain, t_cust, t_prod, t_landed,
     t_requote, t_fx, t_supply, t_fin) = st.tabs(
        [tr("Actions"), tr("Overview"), tr("Activity report"), tr("Models"),
         tr("Value chain"), tr("Customers"), tr("Production"), tr("Landed cost"),
         tr("Re-quotation"), tr("Exchange rate"), tr("Supply & cost"), tr("Finance")]
    )
    with t_act:
        actions.render(scope, ctx)
    with t_over:
        overview.render(scope, ctx)
    with t_activity:
        activity.render(ctx)
    with t_models:
        models.render(scope, ctx)
    with t_chain:
        valuechain.render(scope, ctx)
    with t_cust:
        customers.render(scope, ctx)
    with t_prod:
        production.render(scope, ctx)
    with t_landed:
        landed.render(ctx)
    with t_requote:
        requote.render(ctx)
    with t_fx:
        exchange.render(ctx)
    with t_supply:
        supply.render(scope, ctx)
    with t_fin:
        finance.render(scope, ctx)
