"""Provider interface.

A provider knows how to talk to one cinema chain's booking backend and hand
back normalized :mod:`third_wheel.models` objects. Today only Planet / Yes
Planet is implemented (Rav-Hen runs the same platform; Cinema City is a
separate system) but the engine only ever sees this interface.
"""

from __future__ import annotations

from typing import Protocol

from ..models import Cinema, SeatPlan, Showtime


class Provider(Protocol):
    #: Human-readable chain name, e.g. "Planet".
    name: str

    def cinemas(self) -> list[Cinema]:
        ...

    def showtimes(self, cinema: Cinema, date: str) -> list[Showtime]:
        """All screenings at ``cinema`` on ``date`` (YYYY-MM-DD)."""
        ...

    def enrich(self, showtime: Showtime) -> Showtime:
        """Populate ``venue_id``, ``seatplan_id`` and ``capacity`` in place.

        Kept separate from :meth:`showtimes` because it costs one extra request
        per screening, so the engine only calls it for ranked candidates.
        """
        ...

    def seat_plan(self, showtime: Showtime) -> SeatPlan:
        """The physical seat layout for ``showtime`` (rows, seats, geometry)."""
        ...
