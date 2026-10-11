"""The sections of the app, in pivot order. `app.py` registers one route per entry.

Each section is three files with the slug as their name: `pages/<slug>.py` (the view model),
`templates/<slug>.html`, and optionally `static/sections/<slug>.css` and `charts/<slug>.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    slug: str
    title: str  # shown in the pivot, lowercase

    @property
    def path(self) -> str:
        return f"/{self.slug}"


SECTIONS = (
    Section("now", "now"),
    Section("forecast", "forecast"),
    Section("plan", "plan"),
    Section("habits", "habits"),
    Section("compare", "compare"),
    Section("math", "math"),
    Section("data", "data"),
)

#: Where each tile group on the Start screen leads.
TILE_LINKS = {
    "right now": "/now",
    "projections": "/forecast",
    "pace": "/plan",
    "day by day": "/forecast",
    "this week": "/habits",
    "habits": "/habits",
}
