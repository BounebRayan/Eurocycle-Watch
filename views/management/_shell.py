"""Everything every Management page needs before it can draw anything.

The six pages share one sidebar — currency, language, bikes-only, year range,
distributor — and one `scope` frame derived from it. `begin()` is the single
place that builds them, and the single place a `Ctx` is constructed.

Two things here are load-bearing and easy to break:

* **`persist_state="session"` on every sidebar widget.** A widget's element id
  always folds in the active script hash (`streamlit/elements/lib/utils.py:261`),
  even when you give it a `key`. The same key on two pages is therefore two
  different widgets, and without `persist_state` the filters would reset to
  their defaults on every navigation inside Management. Add a control here and
  it needs the same treatment.

* **`data_end` comes from the full `sales` frame, not from `scope`.** The
  data's own end date is what makes a year partial, and it mustn't move when
  the user narrows the year range or picks a distributor — otherwise
  `Ctx.partial_year`, `like_for_like()` and `ytd_month` drift between pages.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import erp
import i18n
from theme import palette
from ._common import Ctx


def _not_connected(msg: str, lang: str) -> None:
    def tr(s: str) -> str:
        return i18n.t(s, lang)

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


def current_lang() -> str:
    """The selected language, readable before the sidebar has been built.

    Every label on the page needs the language the user picked on the previous
    run — including the language selector's own label, and the page titles in
    `_nav`, which are built before any page body runs."""
    return i18n.LANGUAGES.get(st.session_state.get("mgmt_lang"), i18n.DEFAULT_LANG)


def begin() -> tuple[pd.DataFrame, Ctx] | None:
    """ERP guard, shared sidebar, sales load, `scope` and `Ctx`.

    Returns None when the page has nothing to draw — ERP unreachable, no rows
    for 2019+, or filters that match nothing — having already rendered the
    explanation. The caller returns immediately on None."""
    lang = current_lang()

    def tr(s: str) -> str:
        return i18n.t(s, lang)

    ok, msg = erp.status()
    if not ok:
        _not_connected(msg, lang)
        return None

    P = palette()
    fx = erp.load_fx()

    with st.sidebar:
        st.header(tr("Management"))
        ccy = st.selectbox(tr("Currency"), erp.DISPLAY_CCY, index=0,
                           key="mgmt_ccy", persist_state="session",
                           help=tr("All amounts are computed in DT from each invoice's own "
                                   "FX rate, then shown here converted at the latest rate."))
        st.selectbox(tr("Language"), list(i18n.LANGUAGES),
                     key="mgmt_lang", persist_state="session",
                     help=tr("Translates the whole Management view. Table values, model "
                             "names and ERP column names stay as the ERP holds them."))
        bikes_only = st.toggle(tr("Bikes only"), value=True,
                               key="mgmt_bikes", persist_state="session",
                               help=tr("Exclude spare-part / accessory lines "
                                       "(nomachat.isBike = 0)."))
        st.divider()

    sales = erp.load_sales(start_year=2019, bikes_only=bikes_only)
    if sales.empty:
        st.title(tr("Management"))
        st.warning(tr("No sales rows returned from the ERP for 2019+."))
        return None

    years = sorted(sales["yr"].unique())
    with st.sidebar:
        yr_lo, yr_hi = st.select_slider(tr("Years"), options=years,
                                        value=(max(years[0], years[-1] - 5), years[-1]),
                                        key="mgmt_years", persist_state="session")
        dists = ["(all)"] + sorted(d for d in sales["distributor"].unique() if d != "(unmapped)")
        sel_dist = st.multiselect(tr("Distributor"), dists, default=["(all)"],
                                  key="mgmt_dists", persist_state="session")
        st.caption(i18n.tf("FX (DT per unit): €{eur} · ${usd}\n\nDistributor = the "
                           "customer each model is designed for (`nomachat.cusnach`).",
                           lang, eur=f"{fx['EUR']:.3f}", usd=f"{fx['USD']:.3f}"))

    scope = sales[(sales["yr"] >= yr_lo) & (sales["yr"] <= yr_hi)].copy()
    if "(all)" not in sel_dist and sel_dist:
        scope = scope[scope["distributor"].isin(sel_dist)]
    if scope.empty:
        st.title(tr("Management"))
        st.info(tr("No rows match the current filters."))
        return None

    dist_label = (tr("all distributors") if "(all)" in sel_dist or not sel_dist
                  else ", ".join(sel_dist))
    # From the full sales set, not `scope` — see the module docstring.
    ctx = Ctx(ccy=ccy, fx=fx, P=P, years=years, yr_lo=int(yr_lo), yr_hi=int(yr_hi),
              dist_label=dist_label, data_end=sales["datf"].max(), lang=lang)
    return scope, ctx


def tabbed(ctx: Ctx, key: str, spec: list[tuple[str, object]]) -> None:
    """A page's tab row, where only the open tab's body runs.

    `spec` is `[(english_label, render_callable), ...]`. Plain `st.tabs` runs
    every tab body on every rerun — with thirteen tabs on one page that meant
    changing the currency re-ran the landed-cost, re-quotation and dead-stock
    engines to draw results nobody was looking at. `on_change="rerun"` makes the
    row a widget whose containers expose `.open`, so the cost of a page is the
    cost of the tab you are actually on. The trade is a server round-trip when
    you switch tabs, which is cheap next to what it saves here.

    Labels are translated for display; the English names stay the key for
    `?tab=` deep links, so a link keeps working across a language switch."""
    from . import _nav

    english = [name for name, _ in spec]
    labels = [ctx.t(name) for name in english]
    containers = st.tabs(labels, key=key, on_change="rerun",
                         default=_nav.wanted_tab(labels, english))
    for container, (_, render_fn) in zip(containers, spec):
        if container.open:
            with container:
                render_fn()
