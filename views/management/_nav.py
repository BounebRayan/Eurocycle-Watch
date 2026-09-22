"""The six Management pages as `st.Page` objects, built once and shared.

`dashboard.py` needs them to build the navigation; `actions.py` needs them to
turn a finding's evidence pointer into a real link. Neither can import the
other, so both come here, and the page functions are reached through
`importlib` at call time — the page modules import `_nav` themselves, and a
top-level import here would close the loop.

`st.Page` has to be constructed *inside* a script run: with no ScriptRunContext
it returns a hollow object that fails later with an `AttributeError` rather
than a useful error (see `streamlit/navigation/page.py`). Hence `pages()`
rather than a module-level constant.
"""
from __future__ import annotations

import importlib

import streamlit as st

import i18n
from i18n import N_

GROUP = N_("Management")

# key -> (module, url_path, English title, icon).
#
# The order is the order they appear in the sidebar, which is also the order
# they should be read in: what happened, what to do about it, then the four
# places the evidence lives. `url_path` is what `st.Page` hashes its identity
# on, so it must not change once anything links to it; "management" stays on
# Overview so the pre-split URL still lands somewhere sensible.
#
# Titles go through `N_` so the i18n coverage test sees them here, and are
# translated in `pages()` where the language is known.
_SPEC: dict[str, tuple[str, str, str, str]] = {
    "overview":   ("page_overview",   "management",
                   N_("Overview"),   ":material/insights:"),
    "actions":    ("page_actions",    "management-actions",
                   N_("Actions"),    ":material/checklist:"),
    "commercial": ("page_commercial", "management-commercial",
                   N_("Commercial"), ":material/sell:"),
    "operations": ("page_operations", "management-operations",
                   N_("Operations"), ":material/precision_manufacturing:"),
    "costing":    ("page_costing",    "management-costing",
                   N_("Costing"),    ":material/calculate:"),
    "finance":    ("page_finance",    "management-finance",
                   N_("Finance"),    ":material/account_balance:"),
}

_CACHE: dict[str, dict[str, "st.Page"]] = {}      # lang -> key -> Page


def _runner(module: str):
    """A zero-arg callable that renders one page, importing it on first call."""
    def run() -> None:
        importlib.import_module(f"{__package__}.{module}").render()
    run.__name__ = module           # so a traceback names the page
    return run


def pages(lang: str = i18n.DEFAULT_LANG) -> dict[str, "st.Page"]:
    """The six pages, titled in `lang`. Built once per language per process."""
    if lang not in _CACHE:
        _CACHE[lang] = {
            key: st.Page(_runner(mod), title=i18n.t(title, lang), icon=icon, url_path=url)
            for key, (mod, url, title, icon) in _SPEC.items()
        }
    return _CACHE[lang]


def page(key: str, lang: str = i18n.DEFAULT_LANG) -> "st.Page":
    return pages(lang)[key]


def link(key: str, ctx, *, label: str | None = None, tab: str | None = None,
         **kwargs) -> None:
    """A link to one of our pages, optionally opening a named tab on arrival.

    `tab` rides along as a query parameter; the target page reads it through
    `wanted_tab()` and hands it to `st.tabs(default=...)`. Without that the
    reader lands on the page but on its first tab, which for a pointer that
    says "the evidence is in Re-quotation" is most of the way to useless."""
    p = page(key, ctx.lang)
    st.page_link(p, label=label or p.title, icon=p.icon,
                 query_params={"tab": tab} if tab else None, **kwargs)


def wanted_tab(labels: list[str], english: list[str]) -> str | None:
    """The tab a `?tab=` link asked for, as the label `st.tabs` will show.

    Links carry the **English** tab name, because that is what the rules hold
    and what survives a language switch; the tab row is labelled in the
    reader's language. Returns None for a missing or unrecognised parameter, so
    a stale or hand-edited URL just opens the first tab."""
    want = st.query_params.get("tab")
    if not want:
        return None
    for label, name in zip(labels, english):
        if name == want:
            return label
    return None
