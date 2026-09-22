"""Costing — what a bike actually costs to put on a boat.

The three GPAO costing ports: material plus freight as booked, the same bill of
materials re-priced at today's component prices, and the FX revaluation of the
sales those costs sat against. All three run their own queries and set their own
date windows, so none of them answers to the sidebar's distributor filter.
"""
from __future__ import annotations

from . import landed, requote, exchange
from i18n import N_
from ._shell import begin, tabbed


def render() -> None:
    got = begin()
    if got is None:
        return
    _, ctx = got

    tabbed(ctx, "mgmt_costing_tab", [
        (N_("Landed cost"), lambda: landed.render(ctx)),
        (N_("Re-quotation"), lambda: requote.render(ctx)),
        (N_("Exchange rate"), lambda: exchange.render(ctx)),
    ])
