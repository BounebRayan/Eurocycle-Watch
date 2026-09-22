"""Actions — the full to-do list, every finding with its evidence table.

The Overview page carries the top few as one-line rows; this is the whole list.
Both come from `actions.findings`, so they rank and size identically.
"""
from __future__ import annotations

import streamlit as st

from . import actions
from ._shell import begin


def render() -> None:
    got = begin()
    if got is None:
        return
    scope, ctx = got

    st.subheader(ctx.t("What needs attention")
                 + f" — {ctx.dist_label}, {ctx.yr_lo}–{ctx.yr_hi}")
    with st.spinner(ctx.t("Checking what needs attention…")):
        found = actions.findings(scope, ctx)
    if not found:
        actions.nothing_found(ctx)
        return
    actions.summary(found, ctx)
    actions.export(found, ctx)
    actions.blocks(found, ctx)
