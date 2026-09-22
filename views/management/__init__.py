"""Management view — the internal picture from the Eurocycles ERP.

Where the Distributor view is one distributor's outward market, this is the
company's own numbers. Six pages, one shared sidebar (currency, language,
bikes-only, year range, distributor):

  Overview     the global view — scoreboard, net profit, why margin moved,
               and the top few things that need attention
  Actions      the full rule-driven to-do list
  Commercial   Models · Customers · Value chain · Activity report
  Operations   Production · Build from stock · Supply & cost
  Costing      Landed cost · Re-quotation · Exchange rate
  Finance      P&L · Billings vs collections · Payroll · Commission

It was one page of thirteen tabs until the row stopped fitting on a screen and
there was no answer to "what do I look at first". Two things make the split
work, and both are easy to undo by accident:

* `_shell.begin()` is the only place that builds the sidebar, loads
  `facture ⋈ facture_det ⋈ nomachat` and constructs a `Ctx`. Its widgets carry
  `persist_state="session"` — without that the filters reset on every
  navigation.
* `_nav.pages()` is the only place that knows what the six pages are, and this
  module re-exports just that, so `dashboard.py` can build the navigation
  without importing a page module. That is what keeps `_nav` free of an import
  cycle, since the page modules import `_nav` themselves.

See docs/eurocycles-erp-findings.md for the full data map.
"""
from __future__ import annotations

from ._nav import GROUP, pages

__all__ = ["GROUP", "pages"]
