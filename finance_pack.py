"""The monthly P&L, as exported from the accounting system.

**Why this is a committed file and not a query.** Of the ten P&L sections, only
salaries exist in the ERP (`chargeprs`). Cost of sales, external services,
taxes, depreciation, financial charges and other income come from an accounting
system this project cannot reach — see docs §12c. Without the pack there is no
accounting gross margin and no net-profit figure anywhere in the dashboard, and
the margin the ERP alone can show runs several points optimistic (§12d). So the
pack is committed here and parsed at runtime.

**It is a snapshot, and the UI says so.** To update, drop a newer export into
`data/finance/`. The sheet carries **no year of its own** — the columns are
month names and nothing else — so the year comes from the filename
(`pl-<year>.xlsx`). A file not matching that pattern is ignored rather than
silently dated wrong, which is the failure that would actually matter.

Shape of the sheet: a `Code:` row opens each section, its member lines follow,
and a `999` row closes it with that section's total. Columns are
`Code | Désignation | <month…> | Total | %`, with only as many month columns as
the pack covers.

**Section totals are read from the `999` rows, not summed from the members.**
Cost of sales is why: four of its lines (`60`/`70` raw stock, `80`/`90`
finished-goods stock) are month-end *balances*, not flows, so adding them up
produces nonsense — a naive sum of the section comes out at 825M against a real
23.8M. The pack's own subtotal already applies the right arithmetic
(purchases + opening stock − closing stock − finished-goods movement), so we
take it and verify the walk down to net profit against the pack's printed
`P.B.I.T` / `Total Net` rows instead. `check()` is that verification.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

from i18n import N_

PACK_DIR = Path(__file__).parent / "data" / "finance"
_PACK_RE = re.compile(r"^pl-(\d{4})\.xlsx$", re.IGNORECASE)

_MONTHS = {m: i for i, m in enumerate(
    ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
     "août", "septembre", "octobre", "novembre", "décembre"], start=1)}

# Section code -> (English label, sign). `+1` comes in, `-1` goes out. The pack
# labels them in French; these are our own English names for them, and `N_`
# marks each for translation without translating it here — the views apply
# `ctx.t()` where the label renders.
SECTIONS: dict[str, tuple[str, int]] = {
    "010": (N_("Revenue"), +1),
    "020": (N_("Cost of sales"), -1),
    "030": (N_("Non-stocked purchases"), -1),
    "040": (N_("External services"), -1),
    "050": (N_("Other external charges"), -1),
    "060": (N_("Taxes & duties"), -1),
    "070": (N_("Salaries & charges"), -1),
    "080": (N_("Depreciation"), -1),
    "090": (N_("Financial charges"), -1),
    "100": (N_("Other income"), +1),
}
COST_SECTIONS = [lbl for _c, (lbl, sign) in SECTIONS.items()
                 if sign == -1 and lbl != "Cost of sales"]

# Lines that are balances rather than flows, so they never sum across months.
# Kept out of the member breakdown so a reader can't total the column by eye
# and get a different answer from the section header.
_BALANCE_LINES = {"020": {"60", "70", "80", "90"}}


def available_years() -> list[int]:
    """Years with a pack on disk, newest first."""
    if not PACK_DIR.exists():
        return []
    return sorted((int(m.group(1)) for p in PACK_DIR.iterdir()
                   if (m := _PACK_RE.match(p.name))), reverse=True)


def _sheet(year: int):
    path = PACK_DIR / f"pl-{year}.xlsx"
    if not path.exists():
        return None
    import openpyxl
    return openpyxl.load_workbook(path, data_only=True)["Sheet"]


@st.cache_data(ttl=3600, show_spinner=False)
def load_pl(year: int) -> pd.DataFrame:
    """Tidy P&L for one year, one row per section x line x month.

    Columns: `month`, `section_code`, `section`, `sign`, `line_code`, `line`,
    `is_total`, `amount_dt`. `is_total` marks the section's own `999` subtotal —
    filter to it for section figures, filter it out for the breakdown. Balance
    lines (see `_BALANCE_LINES`) are dropped entirely."""
    ws = _sheet(year)
    if ws is None:
        return pd.DataFrame()
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return pd.DataFrame()

    header = [str(c).strip().lower() if c else "" for c in rows[0]]
    month_cols = {i: _MONTHS[h] for i, h in enumerate(header) if h in _MONTHS}

    out: list[dict] = []
    code = label = None
    for row in rows[1:]:
        first = str(row[0] or "").strip()
        if first.startswith("Code:"):
            code = first.split(";")[0].split(":")[1].strip()
            label = (first.split("Désignation:")[1].strip()
                     if "Désignation:" in first else first)
            continue
        # The pack ends with P.B.I.T / tax / net rows that carry no line code.
        # They belong to no section — without this they land in whichever
        # section was open last (Other income) and inflate it.
        if code is None or not first or not row[1]:
            continue
        if first == "9999":                      # a percentage-of-revenue line
            continue
        if first in _BALANCE_LINES.get(code, ()):
            continue
        section, sign = SECTIONS.get(code, (label, -1))
        for idx, mon in month_cols.items():
            val = row[idx] if idx < len(row) else None
            if not isinstance(val, (int, float)) or val == 0:
                continue
            out.append({
                "month": pd.Timestamp(year=year, month=mon, day=1),
                "section_code": code, "section": section, "sign": sign,
                "line_code": first, "line": str(row[1]).strip(),
                "is_total": first == "999", "amount_dt": float(val),
            })
    return pd.DataFrame(out)


def printed_totals(year: int) -> dict:
    """The pack's own `P.B.I.T` / tax / `Total Net` rows, from the Total column."""
    ws = _sheet(year)
    if ws is None:
        return {}
    found: dict[str, float] = {}
    for row in ws.iter_rows(values_only=True):
        label = str(row[1] or "")
        total = row[8] if len(row) > 8 else None
        if not isinstance(total, (int, float)):
            continue
        if label.startswith("P.B.I.T"):
            found["pbit"] = float(total)
        elif label.startswith("Impôts sur les bénéfices"):
            found["tax"] = float(total)
        elif label.startswith("Total Net"):
            found["net"] = float(total)
    return found


@st.cache_data(ttl=3600, show_spinner=False)
def summary(year: int) -> dict:
    """The P&L walked down to net profit. `{}` when there is no pack.

    Tax comes from the pack's own printed row rather than a rate held here, so
    a future pack that changes the rate needs no code change."""
    df = load_pl(year)
    if df.empty:
        return {}
    totals = df[df["is_total"]]
    by_section = {lbl: float(totals.loc[totals["section"] == lbl, "amount_dt"].sum())
                  for _c, (lbl, _s) in SECTIONS.items()
                  if (totals["section"] == lbl).any()}

    revenue = by_section.get("Revenue", 0.0)
    cos = by_section.get("Cost of sales", 0.0)
    other_income = by_section.get("Other income", 0.0)
    gross = revenue - cos
    opex = sum(by_section.get(lbl, 0.0) for lbl in COST_SECTIONS)
    pbit = gross - opex + other_income

    printed = printed_totals(year)
    tax = printed.get("tax", 0.0)

    months = sorted(df["month"].unique())
    pct = lambda v: v / revenue * 100 if revenue else float("nan")   # noqa: E731
    return {
        "year": year,
        "months": len(months),
        "period_end": pd.Timestamp(months[-1]).to_period("M").to_timestamp("M"),
        "revenue": revenue,
        "by_section": by_section,
        "cost_of_sales": cos,
        "gross_margin": gross, "gross_margin_pct": pct(gross),
        "operating_costs": opex, "operating_costs_pct": pct(opex),
        "other_income": other_income,
        "pbit": pbit, "pbit_pct": pct(pbit),
        "tax": tax,
        "net": pbit - tax, "net_pct": pct(pbit - tax),
    }


def monthly(year: int) -> pd.DataFrame:
    """Revenue, gross margin and net per month — the P&L walk, by month."""
    df = load_pl(year)
    if df.empty:
        return pd.DataFrame()
    t = df[df["is_total"]]
    wide = t.pivot_table(index="month", columns="section", values="amount_dt",
                         aggfunc="sum").fillna(0.0)
    for lbl in [l for _c, (l, _s) in SECTIONS.items()]:
        if lbl not in wide.columns:
            wide[lbl] = 0.0
    wide["gross_margin"] = wide["Revenue"] - wide["Cost of sales"]
    wide["operating_costs"] = wide[COST_SECTIONS].sum(axis=1)
    wide["pbit"] = wide["gross_margin"] - wide["operating_costs"] + wide["Other income"]
    return wide.reset_index()


def check(year: int) -> list[str]:
    """Where our walk disagrees with the pack's own printed totals.

    If these ever diverge, the pack's layout has changed under us and the
    figures on screen are no longer its figures. Driven by
    `tests/test_finance_pack.py`."""
    s = summary(year)
    if not s:
        return [f"no pack for {year}"]
    printed = printed_totals(year)
    problems = []
    for key in ("pbit", "tax", "net"):
        if key not in printed:
            problems.append(f"{year}: pack has no printed {key} row")
        elif abs(printed[key] - s[key]) > 1.0:      # a dinar of rounding is fine
            problems.append(f"{year}: {key} ours {s[key]:,.0f} vs pack {printed[key]:,.0f}")
    return problems
