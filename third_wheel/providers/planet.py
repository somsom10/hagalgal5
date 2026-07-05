"""Planet / Yes Planet provider.

Planet (formerly Yes Planet) runs the Vista "MoreThanCinemas" web ticketing
stack. Two backends are involved:

* the public *quickbook* JSON API on the marketing site
  (``planetcinema.co.il/il/data-api-service/v1/quickbook/<tenant>``) which
  lists cinemas, screenings and a per-screening ``availabilityRatio`` with no
  authentication; and
* the *ticketing* API (host ``tickets5.planetcinema.co.il/api``) which serves
  presentation metadata and the full seat layout, also unauthenticated.

Only live per-seat occupancy (``/seats/seats-status``) is gated behind a
booking session and is intentionally not scraped here -- see
:mod:`third_wheel.occupancy`.

All endpoints and field names below were derived by observing the site's own
network traffic; nothing here bypasses authentication or anti-bot controls.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

from ..models import Cinema, Seat, SeatPlan, Showtime

QUICKBOOK_BASE = "https://www.planetcinema.co.il/il/data-api-service/v1/quickbook"
DEFAULT_TENANT = "10100"
LANG = "he_IL"

# A normal desktop browser UA. The quickbook and ticketing read endpoints are
# public; we identify ourselves plainly rather than trying to look evasive.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class PlanetProvider:
    name = "Planet"

    def __init__(
        self,
        tenant: str = DEFAULT_TENANT,
        cache_dir: str | Path | None = None,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
        min_spacing: float = 0.08,
    ) -> None:
        self.tenant = tenant
        self._client = client or httpx.Client(headers=_HEADERS, timeout=timeout)
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        # (venue_id, seatplan_id) -> SeatPlan, to avoid refetching shared rooms.
        self._seatplan_cache: dict[tuple[str, str], SeatPlan] = {}
        # Space out real network calls so a cold scan doesn't burst dozens of
        # requests at Planet at once (a friendlier request rate).
        self._min_spacing = min_spacing
        self._last_get = 0.0
        self._get_lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    def cinemas(self) -> list[Cinema]:
        # The "until" date only bounds which cinemas have upcoming events; a far
        # date simply returns every active cinema.
        url = (
            f"{QUICKBOOK_BASE}/{self.tenant}/cinemas/with-event/until/2100-01-01"
            f"?attr=&lang={LANG}"
        )
        body = self._get_json(url)["body"]
        out = []
        for c in body["cinemas"]:
            addr = c.get("addressInfo") or {}
            out.append(
                Cinema(
                    id=str(c["id"]),
                    name=c["displayName"],
                    city=addr.get("city", "") or "",
                    booking_url=c.get("bookingUrl", "") or "",
                )
            )
        return out

    def showtimes(self, cinema: Cinema, date: str) -> list[Showtime]:
        url = (
            f"{QUICKBOOK_BASE}/{self.tenant}/film-events/in-cinema/{cinema.id}"
            f"/at-date/{date}?attr=&lang={LANG}"
        )
        body = self._get_json(url)["body"]
        films = {f["id"]: f["name"] for f in body.get("films", [])}
        out = []
        for e in body.get("events", []):
            out.append(
                Showtime(
                    presentation_id=str(e["id"]),
                    cinema_id=cinema.id,
                    cinema_name=cinema.name,
                    film_name=films.get(e.get("filmId"), e.get("filmId", "?")),
                    starts_at=e["eventDateTime"],
                    auditorium=e.get("auditorium", "") or "",
                    availability_ratio=float(e.get("availabilityRatio", 0.0)),
                    booking_link=e.get("bookingLink", "") or "",
                )
            )
        return out

    def enrich(self, showtime: Showtime) -> Showtime:
        pres = self._presentation(showtime)
        showtime.venue_id = str(pres["venueId"])
        showtime.seatplan_id = str(pres["seatplanId"])
        plan = self.seat_plan(showtime)
        showtime.capacity = plan.capacity
        return showtime

    def seat_plan(self, showtime: Showtime) -> SeatPlan:
        if showtime.venue_id is None or showtime.seatplan_id is None:
            pres = self._presentation(showtime)
            showtime.venue_id = str(pres["venueId"])
            showtime.seatplan_id = str(pres["seatplanId"])

        key = (showtime.venue_id, showtime.seatplan_id)
        if key in self._seatplan_cache:
            return self._seatplan_cache[key]

        base = self._ticketing_base(showtime)
        url = (
            f"{base}/seats/seatplan"
            f"?seatplanId={showtime.seatplan_id}&venueId={showtime.venue_id}"
        )
        raw = self._get_json(url, cache_key=f"seatplan_{key[0]}_{key[1]}")
        plan = _parse_seat_plan(raw, showtime.venue_id, showtime.seatplan_id)
        self._seatplan_cache[key] = plan
        return plan

    # -- internals ----------------------------------------------------------

    def _presentation(self, showtime: Showtime) -> dict:
        base = self._ticketing_base(showtime)
        url = f"{base}/presentations/{showtime.presentation_id}"
        return self._get_json(url)["presentation"]

    def _ticketing_base(self, showtime: Showtime) -> str:
        """Derive the ticketing API root (e.g. ``https://tickets5...co.il/api``)
        from the screening's own booking link rather than hardcoding a shard."""
        link = showtime.booking_link
        if link:
            parts = urllib.parse.urlsplit(link)
            return f"{parts.scheme}://{parts.netloc}/api"
        return "https://tickets5.planetcinema.co.il/api"

    def _get_json(self, url: str, cache_key: str | None = None) -> dict:
        if cache_key and self._cache_dir:
            cached = self._cache_dir / f"{cache_key}.json"
            if cached.exists():
                return json.loads(cached.read_text())
        # Only real (cache-miss) fetches are throttled; keep a minimum gap
        # between consecutive network calls.
        with self._get_lock:
            wait = self._min_spacing - (time.monotonic() - self._last_get)
            if wait > 0:
                time.sleep(wait)
            self._last_get = time.monotonic()
        resp = self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if cache_key and self._cache_dir:
            (self._cache_dir / f"{cache_key}.json").write_text(json.dumps(data))
        return data


def _parse_seat_plan(raw: dict, venue_id: str, seatplan_id: str) -> SeatPlan:
    """Flatten Planet's nested ``sections -> groups -> rows -> seats`` into a
    flat list of :class:`Seat`, using ``YCoordinate``/``XCoordinate`` as the
    integer grid used for adjacency."""

    sections = raw.get("sections") or {}
    seats: list[Seat] = []
    section_name = ""
    capacity = 0
    for sec in sections.values():
        section_name = section_name or sec.get("SectionName", "") or ""
        capacity += int(sec.get("Capacity", 0) or 0)
        for group in (sec.get("groups") or {}).values():
            for row in (group.get("rows") or {}).values():
                row_name = str(row.get("RowName", "")).strip()
                for s in (row.get("seats") or {}).values():
                    y = s.get("YCoordinate")
                    x = s.get("XCoordinate")
                    if y is None or x is None:
                        continue
                    bookable = bool(s.get("isAvailable", True)) and not bool(
                        s.get("isBlocked", False)
                    )
                    seats.append(
                        Seat(
                            row=int(y),
                            col=int(x),
                            row_name=row_name or str(y),
                            seat_name=str(s.get("SeatName", "")).strip(),
                            bookable=bookable,
                        )
                    )
    if not capacity:
        capacity = len(seats)
    return SeatPlan(
        venue_id=str(venue_id),
        seatplan_id=str(seatplan_id),
        section_name=section_name,
        capacity=capacity,
        seats=seats,
    )
