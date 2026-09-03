"""Shared visual language for every view — palette, Plotly chrome, formatters.

Extracted from the original single-file dashboard so the distributor view and
the management view render as one system. Palette is the validated reference
instance from the dataviz skill: categorical slots are used in fixed order and
never cycled; single-series charts always take slot 0.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

LIGHT = {
    "surface": "#fcfcfb", "text_primary": "#0b0b0b", "text_secondary": "#52514e",
    "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
    "border": "rgba(11,11,11,0.10)",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
               "#008300", "#4a3aa7", "#e34948"],
    "down": "#2a78d6", "up": "#d03b3b",
    "pos": "#1baf7a", "neg": "#e34948",
}
DARK = {
    "surface": "#1a1a19", "text_primary": "#ffffff", "text_secondary": "#c3c2b7",
    "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
    "border": "rgba(255,255,255,0.10)",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
               "#008300", "#9085e9", "#e66767"],
    "down": "#3987e5", "up": "#d03b3b",
    "pos": "#199e70", "neg": "#e66767",
}


def active_theme() -> str:
    try:
        t = st.context.theme.type
        if t in ("light", "dark"):
            return t
    except Exception:
        pass
    base = st.get_option("theme.base")
    return base if base in ("light", "dark") else "light"


def palette() -> dict:
    return DARK if active_theme() == "dark" else LIGHT


def style_fig(fig, height: int = 320, xgrid: bool = False, ygrid: bool = True):
    """Recessive chrome: hairline solid grid one shade off the surface, no
    zerolines, transparent plot area so it sits on Streamlit's own surface."""
    P = palette()
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=13, color=P["text_secondary"]),
        margin=dict(l=4, r=4, t=4, b=4), height=height,
        hoverlabel=dict(bgcolor=P["surface"], bordercolor=P["border"],
                        font=dict(family=FONT, color=P["text_primary"], size=12)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    font=dict(color=P["text_secondary"], size=12),
                    bgcolor="rgba(0,0,0,0)"),
        showlegend=False,
    )
    fig.update_xaxes(showgrid=xgrid, gridcolor=P["grid"], gridwidth=1, zeroline=False,
                     linecolor=P["axis"], linewidth=1, automargin=True,
                     tickfont=dict(color=P["muted"], size=12), title_font=dict(size=12))
    fig.update_yaxes(showgrid=ygrid, gridcolor=P["grid"], gridwidth=1, zeroline=False,
                     linecolor="rgba(0,0,0,0)", automargin=True,
                     tickfont=dict(color=P["muted"], size=12), title_font=dict(size=12))
    return fig


def hbar_categories(fig, labels):
    """Plot a horizontal bar against a positional y axis, then relabel the ticks
    so same-named rows don't collapse into one stacked bar."""
    labels = list(labels)
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(labels))), ticktext=labels)
    return list(range(len(labels)))


def money_gbp(v) -> str:
    return "n/a" if pd.isna(v) else f"£{v:,.0f}"
