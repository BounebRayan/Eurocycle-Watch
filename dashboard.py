"""Entry point. Two groups of pages:

  Watch        the Apollo / Halfords watch (Halfords.com scrape, SQLite
               snapshot). Works everywhere.
  Management   the internal cost / margin / production picture from the
               Eurocycles ERP (SQL Server), across six pages. Local-only for
               now; each page shows an explainer when the ERP isn't reachable.

Run:  streamlit run dashboard.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Eurocycles Dashboard", layout="wide")

import i18n  # noqa: E402  (after set_page_config)
from views import distributor, management  # noqa: E402

# The Management pages are titled in the language its sidebar last selected, and
# `st.navigation` runs before any page body — so the language has to be read
# from session state here, exactly as `_shell.begin()` reads it.
lang = i18n.LANGUAGES.get(st.session_state.get("mgmt_lang"), i18n.DEFAULT_LANG)

nav = st.navigation({
    "Watch": [
        st.Page(distributor.render, title="Distributor watch",
                icon=":material/storefront:", url_path="distributor", default=True),
    ],
    i18n.t(management.GROUP, lang): list(management.pages(lang).values()),
})
nav.run()
