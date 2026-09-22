"""Operations — making the bikes: plan attainment, the unmoved-stock engine,
and what the components cost to buy.
"""
from __future__ import annotations

from . import production, builder, supply
from i18n import N_
from ._shell import begin, tabbed


def render() -> None:
    got = begin()
    if got is None:
        return
    scope, ctx = got

    tabbed(ctx, "mgmt_operations_tab", [
        (N_("Production"), lambda: production.render(scope, ctx)),
        (N_("Build from stock"), lambda: builder.render(ctx)),
        (N_("Supply & cost"), lambda: supply.render(scope, ctx)),
    ])
