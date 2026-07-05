"""Where "who is actually sitting where" comes from.

There are two very different tiers of data:

* **Room-level** occupancy is free and open: every screening advertises an
  ``availabilityRatio``, so combined with the seat plan's capacity we know
  *how many* people are in the room -- enough to find the lonely screenings.

* **Seat-level** occupancy (the exact occupied seats) lives behind
  ``/seats/seats-status`` on the ticketing API, which requires a booking
  session and returns 403 to anonymous callers. We do **not** try to defeat
  that. :class:`LiveSeatStatus` documents the endpoint and fails loudly.

To let the geometry engine be exercised end-to-end without seat-level data,
:func:`simulate_occupancy` places the known number of occupants as a plausible
lonely couple/single. It is clearly labelled as simulated wherever used.
"""

from __future__ import annotations

import random

from .models import SeatPlan, Showtime


def simulate_occupancy(
    plan: SeatPlan,
    n_occupied: int,
    seed: int | None = None,
    mode: str = "both",
) -> set[tuple[int, int]]:
    """Place ``n_occupied`` people in the room, one group per row and biased
    toward the centre-back, so groups are spread apart and stay isolated.
    Deterministic when ``seed`` is given. For demos only -- not real data.

    ``mode`` shapes *who* the occupants are, to match what the visitor searched
    for so the map backs up the suggestion:

    * ``"couple"`` -- seat people as side-by-side pairs (a lone leftover single
      only when the count is odd), so you end up next to a couple.
    * ``"single"`` -- seat everyone as lone singles, each in its own row, so you
      end up next to a single. A room can hold several isolated singles.
    * ``"both"`` -- a realistic mix of couples and the odd single.
    """
    rng = random.Random(seed)
    rows: dict[int, list] = {}
    for s in plan.seats:
        if s.bookable:
            rows.setdefault(s.row, []).append(s)
    for seats in rows.values():
        seats.sort(key=lambda s: s.col)
    if not rows or n_occupied <= 0:
        return set()

    ordered_rows = sorted(rows)
    # Prefer the middle-to-back third of the room.
    back = ordered_rows[len(ordered_rows) // 3 :]
    occupied: set[tuple[int, int]] = set()
    remaining = n_occupied
    while remaining > 0 and back:
        if mode == "single":
            want_pair = False
        elif mode == "couple":
            want_pair = remaining >= 2
        else:  # both: mostly couples, sometimes a single
            want_pair = remaining >= 2 and rng.random() < 0.7

        row = rng.choice(back)
        back = [r for r in back if r != row]  # one group per row -> spread out
        seats = rows[row]
        if want_pair:
            # Only col-adjacent seats count, so the couple truly sits together
            # (consecutive columns = no aisle between them).
            pairs = [
                (seats[i], seats[i + 1])
                for i in range(len(seats) - 1)
                if seats[i + 1].col == seats[i].col + 1
            ]
            if not pairs:
                continue  # no side-by-side pair here; try another row
            a, b = rng.choice(pairs)
            occupied.add((a.row, a.col))
            occupied.add((b.row, b.col))
        else:
            s = rng.choice(seats)
            occupied.add((s.row, s.col))
        remaining = n_occupied - len(occupied)
    return occupied


class SeatStatusUnavailable(RuntimeError):
    """Raised when live seat-level occupancy cannot be obtained."""


class LiveSeatStatus:
    """Placeholder for real per-seat occupancy.

    The Planet ticketing API exposes ``GET /seats/seats-status?presentationId=<id>``
    which returns the occupied seats, but only within an authenticated booking
    session (it responds 403 otherwise). Wiring this up would require driving a
    real browser session and is intentionally left unimplemented; the estimate
    + simulate path above keeps the tool fully functional without it.
    """

    def occupied_seats(
        self, plan: SeatPlan, showtime: Showtime
    ) -> set[tuple[int, int]]:
        raise SeatStatusUnavailable(
            "Live per-seat occupancy requires an authenticated booking session "
            "(/seats/seats-status returns 403 to anonymous clients). Use "
            "room-level estimates instead."
        )
