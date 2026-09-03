"""Entry point. Two views:

  Distributor  — the Apollo / Halfords watch (Halfords.com scrape, SQLite
                 snapshot). Works everywhere.
  Management   — the internal cost / margin / production picture from the
                 Eurocycles ERP (SQL Server). Local-only for now; shows an
                 explainer when the ERP isn't reachable.

Run:  streamlit run dashboard.py
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Eurocycles Dashboard", layout="wide")

from views import distributor, management  # noqa: E402  (after set_page_config)

nav = st.navigation({
    "Views": [
        st.Page(distributor.render, title="Distributor watch", icon=":material/storefront:",
                url_path="distributor", default=True),
        st.Page(management.render, title="Management", icon=":material/insights:",
                url_path="management"),
    ]
})
nav.run()
