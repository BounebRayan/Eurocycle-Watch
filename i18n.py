"""Translation for the Management view.

The catalogue lives in `translations/fr.json`, keyed by the **English source
string** rather than by an abstract key. That keeps the call sites readable —
`ctx.t("Revenue by country")` says what it renders — and means an untranslated
string degrades to English instead of showing a raw key.

Strings with numbers in them are templates with named placeholders, translated
whole and then `.format()`ed, so the French can reorder the sentence:

    ctx.t("{lo}–{hi} cumulative. Top {n}.").format(lo=..., hi=..., n=12)

`tf()` wraps that pattern and swallows a bad placeholder rather than raising in
the middle of a render.

To add a language: drop a `translations/<code>.json` beside `fr.json` and add it
to `LANGUAGES`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Display name -> code. Each name is written in its own language, so the
# selector reads the same whichever one is active.
LANGUAGES = {"English": "en", "Français": "fr"}
DEFAULT_LANG = "en"

_DIR = Path(__file__).parent / "translations"


@lru_cache(maxsize=8)
def _catalogue(lang: str) -> dict:
    path = _DIR / f"{lang}.json"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def N_(s: str) -> str:
    """Mark a string for translation without translating it here.

    For source strings defined away from where they render — a module-level
    lookup table, say, which is built before any `Ctx` exists. The string is
    returned unchanged; `ctx.t()` translates it at the point of use. The marker
    is what keeps it visible to the extractor and the coverage test."""
    return s


def t(s: str, lang: str = DEFAULT_LANG) -> str:
    """Translate one string. Unknown strings pass through unchanged."""
    if lang == DEFAULT_LANG:
        return s
    return _catalogue(lang).get(s, s)


def tf(s: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Translate a template, then fill its named placeholders.

    A translation that mistypes a placeholder would otherwise raise `KeyError`
    mid-render and blank the tab, so a bad fill falls back to the English
    template rather than taking the page down with it."""
    try:
        return t(s, lang).format(**kwargs)
    except (KeyError, IndexError, ValueError):
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return s


def missing(lang: str, strings) -> list[str]:
    """Source strings with no translation yet — used by the i18n coverage test."""
    cat = _catalogue(lang)
    return [s for s in strings if s not in cat]
