"""Shared helpers for the Management tabs.

`Ctx` carries everything the sidebar produced so each tab module takes just
`(scope, ctx)` (or `(ctx)` for tabs that run their own queries). Money helpers
wrap `erp`'s formatters; `db_notice` mirrors the connection-guard pattern for a
tab whose satellite database isn't attached.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

import erp
import i18n

TILE_H = 140


@dataclass
class Ctx:
    ccy: str
    fx: dict
    P: dict
    years: list
    yr_lo: int
    yr_hi: int
    dist_label: str
    data_end: pd.Timestamp          # newest invoice date in the loaded sales
    lang: str = i18n.DEFAULT_LANG

    def t(self, s: str) -> str:
        """Translate a string into the selected language."""
        return i18n.t(s, self.lang)

    def tf(self, s: str, /, **kwargs) -> str:
        """Translate a template, then fill its named placeholders.

        `s` is positional-only so a template can use `{s}` as a placeholder
        without colliding with the parameter name."""
        return i18n.tf(s, self.lang, **kwargs)

    @property
    def rate(self) -> float:
        return self.fx.get(self.ccy, 1.0)

    @property
    def partial_year(self) -> int | None:
        """The newest year in the data, if it is still in progress; else None.

        Derived from the data's own last invoice date, not the wall clock — a
        stale ERP restore shouldn't make a complete year look partial."""
        if self.data_end is None or pd.isna(self.data_end):
            return None
        return int(self.data_end.year) if self.data_end.month < 12 else None

    @property
    def ytd_month(self) -> int:
        """Last month with data (1-12)."""
        return 12 if self.data_end is None or pd.isna(self.data_end) else int(self.data_end.month)

    @property
    def ytd_day(self) -> int:
        """Day of month the data stops on (1-31).

        Needed because the last month is usually a part month: trimming a
        comparison to whole months still lands 19 days of one August against a
        full one. See `like_for_like`."""
        return 31 if self.data_end is None or pd.isna(self.data_end) else int(self.data_end.day)


def trim_to_date(df: pd.DataFrame, ctx: Ctx) -> pd.Series:
    """Rows on or before the data's own cut-off day, within their own year.

    Compares month-then-day rather than building a date, so 29 February in a
    leap year needs no special case."""
    m, d = df["datf"].dt.month, df["datf"].dt.day
    return (m < ctx.ytd_month) | ((m == ctx.ytd_month) & (d <= ctx.ytd_day))


def like_for_like(df: pd.DataFrame, ctx: Ctx) -> pd.DataFrame:
    """Trim the partial newest year *and its comparison year* to the same
    elapsed **days**, so a year-over-year delta compares like with like.

    Without this, an eight-month 2026 lands against a full 2025 and every YoY
    reads as a collapse. Returns `df` untouched when the newest year is complete.

    Trimming to whole months is not enough, and that is the subtle half: the
    newest month is itself usually a part month. On data ending 19 Aug 2026, a
    month-level cut compared 19 days of August 2026 against all 31 days of
    August 2025 — DT 2.0M of prior-year revenue with no counterpart, which
    overstated the revenue decline by 1.6 points and made the caption's promise
    that "a part year isn't measured against a full one" untrue of the part
    month. The cut is therefore the data's own day, in both years.

    Use it **only** for the delta, never for displayed totals — it would shrink
    the prior year's headline figures too. Needs `yr` and `datf`
    (`erp.load_sales` provides both); falls back to a month-level cut for a
    frame that carries `mo` but no dates."""
    p = ctx.partial_year
    if p is None:
        return df
    if "datf" not in df.columns:
        if "mo" not in df.columns:
            return df
        affected = df["yr"].isin([p, p - 1])
        return df[~affected | (df["mo"] <= ctx.ytd_month)]
    affected = df["yr"].isin([p, p - 1])
    return df[~affected | trim_to_date(df, ctx)]


def partial_year_note(ctx: Ctx) -> str | None:
    """One-line caption explaining the like-for-like cut, or None if not needed."""
    p = ctx.partial_year
    if p is None:
        return None
    return ctx.tf("{yr} runs to {end}. Year-on-year figures compare 1 January–{cut} in "
                  "both years, so neither a part year nor a part month is measured "
                  "against a full one. Totals shown elsewhere are the full period.",
                  yr=p, end=f"{ctx.data_end:%d %b %Y}", cut=f"{ctx.data_end:%d %B}",
                  prev=p - 1)


def sym(ccy: str) -> str:
    return erp.symbol(ccy)


def money(v, ctx: Ctx, dp: int = 0) -> str:
    return erp.fmt_money(v, ctx.ccy, ctx.fx, dp)


def tile(v, ctx: Ctx) -> str:
    return erp.fmt_money_compact(v, ctx.ccy, ctx.fx)


def compact(display_v, ccy: str) -> str:
    """Compact label for an amount already converted to the display currency."""
    s, a = erp.symbol(ccy), abs(display_v)
    if a >= 1e6:
        return f"{s}{display_v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{s}{display_v / 1e3:.0f}k"
    return f"{s}{display_v:,.0f}"


def to_disp(dt_amount, ctx: Ctx):
    """DT amount / column -> selected display currency."""
    return dt_amount / ctx.rate


# Shown at the top of tabs whose source tables carry no customer or bike/part
# dimension, so the sidebar's distributor and bikes-only filters can't apply.
# Without it the reader assumes every tab moves together when they filter.
UNFILTERED_NOTE = i18n.N_(
    "**{what} is company-wide.** These figures come from production, purchasing "
    "and cash tables that don't carry a customer or bike/part dimension, so the "
    "**Distributor** and **Bikes only** filters in the sidebar do not apply here. "
    "The year range does."
)


_DB_NOTICE = i18n.N_(
    "**{what} needs the `{db}` database**, which isn't attached to this "
    "SQL Server instance.\n\n"
    "Restore it next to `eurocycles_db` (same `RESTORE ... WITH MOVE` step) "
    "and reload — the rest of the Management view works without it."
)


def db_notice(db: str, what: str, ctx: "Ctx | None" = None) -> None:
    """Shown in a tab whose extra database isn't attached."""
    fmt = (ctx.tf if ctx else lambda s, **kw: s.format(**kw))
    st.info(fmt(_DB_NOTICE, what=what, db=db))


def yoy(cur_v, prev_v, *, pct: bool = False, ctx: Ctx | None = None) -> str | None:
    """Streamlit renders this inside its own delta chip, which parses the
    leading sign — so the number has to stay at the front, translated or not."""
    if prev_v is None or pd.isna(prev_v) or prev_v == 0 or pd.isna(cur_v):
        return None
    if pct:
        return f"{cur_v - prev_v:+.1f} pp"
    suffix = ctx.t("YoY") if ctx else "YoY"
    return f"{(cur_v - prev_v) / abs(prev_v) * 100:+.0f}% {suffix}"
