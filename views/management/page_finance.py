"""Finance — the money, from the P&L walk down to net profit through to what
the sales commission costs.

**P&L** came over from the old Overview tab: it is the only block on the whole
view that answers "what did the company actually make", and it reads the
accounting pack rather than the ERP. The other three are ERP cash and cost.
"""
from __future__ import annotations

from . import finance
from i18n import N_
from ._shell import begin, tabbed


def render() -> None:
    got = begin()
    if got is None:
        return
    scope, ctx = got

    tabbed(ctx, "mgmt_finance_tab", [
        (N_("P&L"), lambda: finance.profitability(ctx)),
        (N_("Billings vs collections"), lambda: finance.billings_vs_collections(ctx)),
        (N_("Payroll"), lambda: finance.payroll(scope, ctx)),
        (N_("Commission"), lambda: finance.commission(scope, ctx)),
    ])
