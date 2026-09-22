"""Commercial — what we sell, to whom, and what the shelf does with it.

Four tabs that all answer a question about the range and its customers. The
margin-bridge drill sits at the top of Models rather than on its own: it names
the models behind the movement the Overview page's waterfall shows.
"""
from __future__ import annotations

from . import models, customers, valuechain, activity, bridge
from i18n import N_
from ._shell import begin, tabbed


def render() -> None:
    got = begin()
    if got is None:
        return
    scope, ctx = got

    def _models() -> None:
        bridge.drill_section(scope, ctx)
        models.render(scope, ctx)

    tabbed(ctx, "mgmt_commercial_tab", [
        (N_("Models"), _models),
        (N_("Customers"), lambda: customers.render(scope, ctx)),
        (N_("Value chain"), lambda: valuechain.render(scope, ctx)),
        (N_("Activity report"), lambda: activity.render(ctx)),
    ])
