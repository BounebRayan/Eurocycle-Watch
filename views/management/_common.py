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


def like_for_like(df: pd.DataFrame, ctx: Ctx) -> pd.DataFrame:
    """Trim the partial newest year *and its comparison year* to the same
    elapsed months, so a year-over-year delta compares like with like.

    Without this, an eight-month 2026 lands against a full 2025 and every YoY
    reads as a collapse. Returns `df` untouched when the newest year is complete.

    Use it **only** for the delta, never for displayed totals — it would shrink
    the prior year's headline figures too. Needs `yr` and `mo` columns
    (`erp.load_sales` provides both)."""
    p = ctx.partial_year
    if p is None or "mo" not in df.columns:
        return df
    affected = df["yr"].isin([p, p - 1])
    return df[~affected | (df["mo"] <= ctx.ytd_month)]


def partial_year_note(ctx: Ctx) -> str | None:
    """One-line caption explaining the like-for-like cut, or None if not needed."""
    p = ctx.partial_year
    if p is None:
        return None
    month = ctx.data_end.strftime("%B")
    return (f"{p} runs to {ctx.data_end:%d %b %Y}. Year-on-year figures compare "
            f"January–{month} {p} against January–{month} {p - 1}, so a part year "
            "isn't measured against a full one. Totals shown elsewhere are the "
            "full period.")


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
UNFILTERED_NOTE = (
    "**{what} is company-wide.** These figures come from production, purchasing "
    "and cash tables that don't carry a customer or bike/part dimension, so the "
    "**Distributor** and **Bikes only** filters in the sidebar do not apply here. "
    "The year range does."
)


def db_notice(db: str, what: str) -> None:
    """Shown in a tab whose extra database isn't attached."""
    st.info(
        f"**{what} needs the `{db}` database**, which isn't attached to this "
        f"SQL Server instance.\n\n"
        f"Restore it next to `eurocycles_db` (same `RESTORE ... WITH MOVE` step) "
        f"and reload — the rest of the Management view works without it."
    )


def yoy(cur_v, prev_v, *, pct: bool = False) -> str | None:
    if prev_v is None or pd.isna(prev_v) or prev_v == 0 or pd.isna(cur_v):
        return None
    if pct:
        return f"{cur_v - prev_v:+.1f} pp"
    return f"{(cur_v - prev_v) / abs(prev_v) * 100:+.0f}% YoY"
