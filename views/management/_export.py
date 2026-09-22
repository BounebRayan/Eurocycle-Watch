"""Excel export for the sections worth taking away.

**Why Excel and not PDF.** The GPAO's own Activity report exports to `.xlsx`
and nothing else (`frmActiviteComp1.bbiExport_ItemClick` → `ExportToXlsx`), and
the spreadsheet `tests/test_gpao_activity.py` ties us to is one of those
exports. Excel is the workflow that already exists here; it is also what a
reader does something *with*, rather than just reads. Adding a PDF engine would
be a new dependency serving a habit nobody has.

**Every workbook opens on an "About" sheet**, and that is the point of this
module rather than a flourish. The figures on these pages are hedged — this
block is company-wide, that one doesn't follow the sidebar, these three sections
run short because `detart` ends early, this total reproduces a GPAO defect on
purpose. A spreadsheet that arrives in someone's inbox carrying the numbers but
none of the hedges is a way of showing something wrong. So the caveats travel
with the file, along with the window, the filters and the currency that produced
it.
"""
from __future__ import annotations

import datetime as dt
import io
import re

import pandas as pd
import streamlit as st

from ._common import Ctx

# Excel's own limits: 31 characters, and none of : \ / ? * [ ]
_BAD = re.compile(r"[:\\/?*\[\]]")


def _sheet_name(name: str, used: set[str]) -> str:
    clean = _BAD.sub("-", name).strip() or "Sheet"
    clean = clean[:31]
    if clean not in used:
        used.add(clean)
        return clean
    for n in range(2, 100):                      # de-duplicate: "Name 2", "Name 3"
        suffix = f" {n}"
        candidate = clean[:31 - len(suffix)] + suffix
        if candidate not in used:
            used.add(candidate)
            return candidate
    used.add(clean)
    return clean


def _about(title: str, meta: list[tuple[str, str]], notes: list[str],
           ctx: Ctx, stamp: str) -> pd.DataFrame:
    rows = [(ctx.t("Report"), title),
            (ctx.t("Generated"), stamp),
            (ctx.t("Currency"), ctx.ccy)]
    rows += [(k, v) for k, v in meta]
    if notes:
        rows.append(("", ""))
        rows.append((ctx.t("Read this with the figures"), ""))
        rows += [("", n) for n in notes]
    return pd.DataFrame(rows, columns=[ctx.t("Field"), ctx.t("Value")])


def _autofit(ws, df: pd.DataFrame) -> None:
    """Column widths from the content, capped so a long caveat doesn't produce
    a 600-character column."""
    from openpyxl.utils import get_column_letter

    for i, col in enumerate(df.columns, start=1):
        longest = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, longest + 2), 60)


@st.cache_data(ttl=900, max_entries=12, show_spinner=False)
def _build(sheets: dict[str, pd.DataFrame], title: str, meta: tuple, notes: tuple,
           ccy: str, lang: str, stamp: str, _ctx: Ctx) -> bytes:
    """The cached half of `workbook`.

    `st.download_button` needs its bytes up front, so without this the workbook
    is rebuilt on every rerun of the tab — measured at ~500 ms for the activity
    report's sixteen sheets, which is most of what lazy tabs just saved. Every
    argument that changes the file is part of the key; `_ctx` is excluded from
    hashing by its underscore and carries only the translator, whose language is
    already keyed on. `stamp` is the generation time to the minute, so the About
    sheet can never be more than a minute stale."""
    return _workbook(sheets, title=title, meta=list(meta), notes=list(notes), ctx=_ctx,
                     stamp=stamp)


def workbook(sheets: dict[str, pd.DataFrame], *, title: str, ctx: Ctx,
             meta: list[tuple[str, str]] | None = None,
             notes: list[str] | None = None) -> bytes:
    """One `.xlsx` in memory: an About sheet, then one sheet per table."""
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return _build(sheets, title, tuple(meta or ()), tuple(notes or ()),
                  ctx.ccy, ctx.lang, stamp, ctx)


def _workbook(sheets: dict[str, pd.DataFrame], *, title: str, ctx: Ctx,
              meta: list[tuple[str, str]], notes: list[str], stamp: str) -> bytes:
    from openpyxl.styles import Font

    buf = io.BytesIO()
    used: set[str] = set()
    written: list[tuple[str, pd.DataFrame]] = []
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        about = _about(title, meta, notes, ctx, stamp)
        name = _sheet_name(ctx.t("About"), used)
        about.to_excel(xl, sheet_name=name, index=False)
        written.append((name, about))
        for label, df in sheets.items():
            if df is None or df.empty:
                continue
            name = _sheet_name(label, used)
            df.to_excel(xl, sheet_name=name, index=False)
            written.append((name, df))
        for name, df in written:
            ws = xl.book[name]
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(bold=True)
            _autofit(ws, df)
    buf.seek(0)
    return buf.read()


def download(label: str, sheets: dict[str, pd.DataFrame], filename: str, *,
             ctx: Ctx, title: str, key: str,
             meta: list[tuple[str, str]] | None = None,
             notes: list[str] | None = None,
             help: str | None = None) -> None:
    """An Excel download button, or nothing when there is nothing to export.

    The workbook is built on every rerun because `st.download_button` needs its
    bytes up front. That is cheap here — the frames are already in memory,
    having just been drawn — but it is the reason this takes assembled
    DataFrames rather than a callback that would re-query."""
    live = {k: v for k, v in sheets.items() if v is not None and not v.empty}
    if not live:
        return
    try:
        data = workbook(live, title=title, ctx=ctx, meta=meta, notes=notes)
    except Exception as exc:                     # a failed export must never
        st.caption(ctx.tf("Export unavailable: {err}", err=str(exc)[:120]))  # take the page down
        return
    st.download_button(
        label, data=data, file_name=filename, key=key, icon=":material/download:",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        help=help or ctx.t("Excel workbook. The first sheet records the filters, the "
                           "period and the caveats these figures come with."))


def scope_meta(ctx: Ctx, **extra: str) -> list[tuple[str, str]]:
    """The sidebar state, so an exported file says what produced it."""
    meta = [(ctx.t("Years"), f"{ctx.yr_lo}–{ctx.yr_hi}"),
            (ctx.t("Distributor"), ctx.dist_label)]
    if ctx.data_end is not None and not pd.isna(ctx.data_end):
        meta.append((ctx.t("Data ends"), f"{ctx.data_end:%d %b %Y}"))
    meta += list(extra.items())
    return meta
