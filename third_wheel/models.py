"""Core data structures shared across Third Wheel.

These are deliberately plain dataclasses so they are trivial to serialize,
cache to disk, and reason about in tests without any live network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cinema:
    id: str
    name: str
    city: str = ""
    booking_url: str = ""


@dataclass
class Showtime:
    """A single screening. ``presentation_id`` is the ticketing-system id used
    for seat plans and booking; it equals the quickbook event id."""

    presentation_id: str
    cinema_id: str
    cinema_name: str
    film_name: str
    starts_at: str  # ISO 8601, local time
    auditorium: str
    availability_ratio: float  # fraction of seats still available (0..1)
    booking_link: str
    # Filled in lazily from the ticketing API when we price out a candidate.
    venue_id: str | None = None
    seatplan_id: str | None = None
    capacity: int | None = None

    @property
    def seats_available(self) -> int | None:
        if self.capacity is None:
            return None
        return round(self.capacity * self.availability_ratio)

    @property
    def seats_sold(self) -> int | None:
        if self.capacity is None:
            return None
        return self.capacity - self.seats_available


@dataclass(frozen=True)
class Seat:
    """One physical seat. ``row`` / ``col`` are integer grid coordinates used
    for adjacency; ``row_name`` / ``seat_name`` are what the human sees."""

    row: int
    col: int
    row_name: str
    seat_name: str
    bookable: bool = True

    @property
    def label(self) -> str:
        # "row-seat" to stay unambiguous when both parts are numeric
        # (e.g. row 12 seat 13 -> "12-13", not "1213").
        return f"{self.row_name}-{self.seat_name}"


@dataclass
class SeatPlan:
    venue_id: str
    seatplan_id: str
    section_name: str
    capacity: int
    seats: list[Seat] = field(default_factory=list)

    def by_coord(self) -> dict[tuple[int, int], Seat]:
        return {(s.row, s.col): s for s in self.seats}
