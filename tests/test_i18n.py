"""Every string the UI passes to `ctx.t()` / `ctx.tf()` must exist in each
catalogue, and templates must keep their placeholders.

Without this, a new heading silently renders in English inside an otherwise
French page — the failure is invisible unless you read every tab in both
languages.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIEWS = ROOT / "views" / "management"

import sys
sys.path.insert(0, str(ROOT))
import i18n  # noqa: E402

PLACEHOLDER = re.compile(r"\{(\w+)\}")


#  ctx.t / ctx.tf in the tabs, the sidebar's local `tr()` and i18n.tf in
#  __init__.py (both run before Ctx exists), and `N_()` deferred markers.
TRANSLATE_CALLS = ("t", "tf", "tr", "N_")


def _translated_strings() -> set[str]:
    """First argument of every translation call in the management views."""
    found: set[str] = set()

    class V(ast.NodeVisitor):
        def visit_Call(self, node):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in TRANSLATE_CALLS and node.args:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.add(a.value)
            # A widget with `format_func=ctx.t` renders each option through the
            # catalogue while keeping the English value for its own logic, so the
            # option list is translatable text even though it is never a `t()` arg.
            if any(k.arg == "format_func" and _is_translator(k.value) for k in node.keywords):
                for a in node.args:
                    if isinstance(a, (ast.List, ast.Tuple)):
                        found.update(e.value for e in a.elts
                                     if isinstance(e, ast.Constant) and isinstance(e.value, str))
            self.generic_visit(node)

    # `gpao_activity.py` holds the Activity tab's section titles, descriptions
    # and caveats as data. They reach the UI through `ctx.t(sec.english)`, whose
    # argument is a variable, so they are marked with `N_()` at the point of
    # definition and have to be collected from there.
    for path in (sorted(VIEWS.glob("*.py"))
                 + [ROOT / "erp.py", ROOT / "finance_pack.py", ROOT / "gpao_activity.py",
                    ROOT / "gpao_landed.py", ROOT / "gpao_requote.py",
                    ROOT / "gpao_exchange.py"]):
        V().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def _is_translator(node) -> bool:
    """`ctx.t` / `ctx.tf` / a bare `t` passed as a callable, not called."""
    if isinstance(node, ast.Attribute):
        return node.attr in TRANSLATE_CALLS
    return isinstance(node, ast.Name) and node.id in TRANSLATE_CALLS


NON_DEFAULT = [c for c in i18n.LANGUAGES.values() if c != i18n.DEFAULT_LANG]


@pytest.mark.parametrize("lang", NON_DEFAULT)
def test_every_ui_string_is_translated(lang):
    missing = sorted(i18n.missing(lang, _translated_strings()))
    assert not missing, (
        f"{len(missing)} string(s) have no {lang} translation in "
        f"translations/{lang}.json:\n  " + "\n  ".join(repr(m) for m in missing))


@pytest.mark.parametrize("lang", NON_DEFAULT)
def test_placeholders_survive_translation(lang):
    """A template whose translation drops or renames a placeholder would raise
    mid-render; `tf()` swallows it, so the mistake would show up as a stray
    English sentence rather than an error. Catch it here instead."""
    bad = []
    for src, dst in i18n._catalogue(lang).items():
        if set(PLACEHOLDER.findall(src)) != set(PLACEHOLDER.findall(dst)):
            bad.append(src)
    assert not bad, "placeholder mismatch in:\n  " + "\n  ".join(repr(b) for b in bad)


@pytest.mark.parametrize("lang", NON_DEFAULT)
def test_catalogue_has_no_dead_entries(lang):
    """Entries for strings the UI no longer uses — harmless, but they rot."""
    dead = sorted(set(i18n._catalogue(lang)) - _translated_strings())
    assert not dead, ("translations/%s.json has entries no view uses:\n  " % lang
                      + "\n  ".join(repr(d) for d in dead))
