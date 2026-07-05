"""The Third Wheel engine: scan a chain's screenings for a date and surface the
"loneliest" ones -- rooms where just a person or two are rattling around a big
auditorium, ripe for an uninvited third wheel."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Showtime
from .providers.base import Provider


@dataclass
class Opportunity:
    showtime: Showtime
    seats_sold: int
    capacity: int
    loneliness: float  # higher = lonelier / better target
    # Size of the isolated group you'd sit beside (1 = single, 2 = couple);
    # None when not computed (e.g. the CLI path).
    beside_size: int | None = None

    @property
    def emptiness(self) -> float:
        return 1.0 - (self.seats_sold / self.capacity) if self.capacity else 0.0


def _loneliness(seats_sold: int, capacity: int) -> float:
    """Reward big, nearly-empty rooms holding a handful of people; a totally
    empty room scores 0 (nobody to third-wheel) and a busy one scores low."""
    if capacity <= 0 or seats_sold <= 0:
        return 0.0
    emptiness = 1.0 - seats_sold / capacity
    # Penalise as the crowd grows; a single occupant is the sweetest target.
    crowd_penalty = 1.0 / seats_sold
    return round(emptiness * crowd_penalty * (capacity ** 0.5), 4)


def scan(
    provider: Provider,
    date: str,
    max_occupants: int = 4,
    top: int = 15,
    min_occupants: int = 1,
    prefilter: int = 60,
    progress=None,
) -> list[Opportunity]:
    """Find third-wheel opportunities across all of ``provider``'s cinemas on
    ``date``.

    ``max_occupants`` caps how crowded a room may be to still count as lonely.
    Because turning ``availabilityRatio`` into an absolute head-count needs the
    seat plan (one extra request each), we first rank every screening by raw
    emptiness and only enrich the emptiest ``prefilter`` of them.
    """
    cinemas = provider.cinemas()
    all_shows: list[Showtime] = []
    for c in cinemas:
        if progress:
            progress(f"listing {c.name}")
        try:
            all_shows.extend(provider.showtimes(c, date))
        except Exception as exc:  # one bad cinema shouldn't sink the scan
            if progress:
                progress(f"  ! {c.name}: {exc}")

    # Emptiest first (cheap, no extra requests), then enrich just the top slice.
    all_shows.sort(key=lambda s: s.availability_ratio, reverse=True)
    candidates = [s for s in all_shows if s.availability_ratio < 1.0][:prefilter]

    opportunities: list[Opportunity] = []
    for s in candidates:
        try:
            provider.enrich(s)
        except Exception as exc:
            if progress:
                progress(f"  ! enrich {s.film_name}: {exc}")
            continue
        sold = s.seats_sold
        if sold is None or s.capacity is None:
            continue
        if not (min_occupants <= sold <= max_occupants):
            continue
        opportunities.append(
            Opportunity(
                showtime=s,
                seats_sold=sold,
                capacity=s.capacity,
                loneliness=_loneliness(sold, s.capacity),
            )
        )

    opportunities.sort(key=lambda o: o.loneliness, reverse=True)
    return opportunities[:top]
