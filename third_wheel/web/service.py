"""Server-side scanning service.

This is the layer that keeps Third Wheel from hammering Planet's servers. The
expensive work -- listing every cinema's screenings for a date and enriching the
emptiest ones with seat-plan capacity -- is done **once per date** and shared by
every visitor for a TTL window. The couple-isolation filter is then applied
cheaply in memory, so re-requests never trigger new network traffic.

Guarantees:
* **Single-flight**: concurrent requests for the same cold date wait on one scan
  instead of each launching their own.
* **Stale-while-revalidate**: once warm, a stale cache is served instantly while
  a single background thread refreshes it -- visitors never wait on the network.
* **Bounded upstream load**: at most one enrichment pass per date per TTL,
  regardless of how much traffic the site gets. Seat plans are also cached to
  disk by the provider.
"""

from __future__ import annotations

import datetime as _dt
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8: stdlib zoneinfo arrived in 3.9
    from backports.zoneinfo import ZoneInfo

import httpx

from ..engine import Opportunity, _loneliness
from ..models import Showtime
from ..occupancy import simulate_occupancy
from ..providers.planet import PlanetProvider
from ..seatmap import SeatPlan, find_third_wheel, render_ascii

# How long an enriched per-date scan stays fresh (seconds). Occupancy drifts
# slowly and this is a gag, so a generous window keeps us gentle upstream.
SCAN_TTL = 1800
# How emptiest-first screenings to enrich per date. The seat-plan lookups are
# the costly part, so this is the main lever on upstream load.
PREFILTER = 50
# A room only counts as "lonely" up to this many occupants; beyond it there's a
# crowd, not a third-wheel opportunity. (Replaces the old max-occupants slider.)
LONELY_MAX = 6


# The site targets couples, full stop. It used to also offer "a lone single",
# but pointing visitors at one person sitting alone crossed from parody into
# actually creepy, so that option was removed from the web backend on purpose
# (the generic engine/CLI still take arbitrary group sizes).
TARGET_SIZES = {2}   # only isolated groups of exactly this size qualify
TARGET_MIN_SOLD = 2  # fewer occupants than this can't contain a couple


def sim_seed(presentation_id: str) -> int:
    """Deterministic per-screening seed so a room simulates identically in the
    scan and in its seat map (and refreshes once a day, on Israel's clock)."""
    today = _dt.datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    return abs(hash((presentation_id, today))) % (2**31)


@dataclass
class _DateEntry:
    candidates: list[Showtime] = field(default_factory=list)
    fetched_at: float = 0.0
    error: str | None = None


# The cinema list basically never changes, so cache it for a long time and
# reuse the last good copy if a refresh gets throttled -- this is the endpoint
# Planet 429s first when we scan many dates.
CINEMAS_TTL = 6 * 3600


class Scanner:
    def __init__(self, cache_dir: str | Path | None = None, ttl: int = SCAN_TTL):
        self._provider = PlanetProvider(cache_dir=cache_dir)
        self._ttl = ttl
        self._entries: dict[str, _DateEntry] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._master = threading.Lock()
        self._refreshing: set[str] = set()
        self._cinemas: list | None = None
        self._cinemas_at: float = 0.0
        self._backoff_until: float = 0.0

    def _note_error(self, exc: Exception) -> None:
        """If Planet rate-limited us (429), stop hitting it for a cooldown so we
        don't prolong the block. Honours Retry-After when present."""
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None \
                and exc.response.status_code == 429:
            cooldown = 1200  # 20 min default
            retry = exc.response.headers.get("retry-after", "")
            if retry.isdigit():
                cooldown = max(60, min(3600, int(retry)))
            self._backoff_until = max(self._backoff_until, time.time() + cooldown)

    @property
    def backing_off(self) -> bool:
        return time.time() < self._backoff_until

    def _cinema_list(self):
        """Cinemas, cached for CINEMAS_TTL; on a throttled/failed refresh, reuse
        the last good copy rather than failing the whole scan."""
        now = time.time()
        if self._cinemas is not None and (now - self._cinemas_at) < CINEMAS_TTL:
            return self._cinemas
        try:
            cs = self._provider.cinemas()
        except Exception:
            if self._cinemas is not None:
                return self._cinemas  # stale but usable (e.g. 429 on refresh)
            raise
        self._cinemas = cs
        self._cinemas_at = now
        return cs

    # -- public --------------------------------------------------------------

    def opportunities(self, date: str, top: int = 12) -> tuple[list[Opportunity], bool]:
        """Ranked couple-adjacent opportunities for ``date``.

        A room qualifies only if its (simulated) occupancy actually contains an
        **isolated couple** with a free seat beside it. The simulation is cheap
        (CPU only, seat plans are already cached) so no extra upstream traffic.

        Returns ``(opportunities, stale)``.
        """
        entry, stale = self._get_candidates(date)
        opps: list[Opportunity] = []
        for s in entry.candidates:
            sold = s.seats_sold
            if sold is None or s.capacity is None:
                continue
            if sold < TARGET_MIN_SOLD or sold > LONELY_MAX:
                continue
            try:
                plan = self._provider.seat_plan(s)  # cached; no network
            except Exception:
                continue
            occupied = simulate_occupancy(
                plan, sold, seed=sim_seed(s.presentation_id), mode="couple"
            )
            pick = find_third_wheel(plan, occupied, sizes=TARGET_SIZES)
            if pick is None:
                continue  # no isolated couple with a seat beside it
            opps.append(
                Opportunity(
                    showtime=s,
                    seats_sold=sold,
                    capacity=s.capacity,
                    loneliness=_loneliness(sold, s.capacity),
                    beside_size=pick.cluster.size,
                )
            )
        opps.sort(key=lambda o: o.loneliness, reverse=True)
        return opps[:top], stale

    def seat_plan(self, presentation_id: str) -> SeatPlan:
        show = Showtime(
            presentation_id=presentation_id,
            cinema_id="", cinema_name="", film_name="",
            starts_at="", auditorium="", availability_ratio=0.9, booking_link="",
        )
        self._provider.enrich(show)
        return self._provider.seat_plan(show)

    def warm(self, date: str) -> None:
        """Best-effort pre-warm; swallow errors (called from a daemon thread)."""
        try:
            self._get_candidates(date)
        except Exception:
            pass

    def last_error(self, date: str) -> str | None:
        entry = self._entries.get(date)
        return entry.error if entry else None

    # -- internals -----------------------------------------------------------

    def _lock_for(self, date: str) -> threading.Lock:
        with self._master:
            return self._locks.setdefault(date, threading.Lock())

    def _get_candidates(self, date: str) -> tuple[_DateEntry, bool]:
        now = time.time()
        entry = self._entries.get(date)
        if entry and (now - entry.fetched_at) < self._ttl:
            return entry, False
        if entry:  # stale: serve now, refresh in the background (once).
            self._maybe_background_refresh(date)
            return entry, True
        # Cold: block on a single-flight scan shared by all waiters.
        lock = self._lock_for(date)
        with lock:
            entry = self._entries.get(date)
            if entry and (time.time() - entry.fetched_at) < self._ttl:
                return entry, False
            fresh = self._scan(date)
            self._entries[date] = fresh
            return fresh, False

    def _maybe_background_refresh(self, date: str) -> None:
        with self._master:
            if date in self._refreshing:
                return
            self._refreshing.add(date)

        def run() -> None:
            lock = self._lock_for(date)
            try:
                with lock:
                    self._entries[date] = self._scan(date)
            finally:
                with self._master:
                    self._refreshing.discard(date)

        threading.Thread(target=run, daemon=True, name=f"tw-refresh-{date}").start()

    def _scan(self, date: str) -> _DateEntry:
        """The expensive pass: list all screenings, enrich the emptiest slice."""
        if self.backing_off:
            return _DateEntry(candidates=[], fetched_at=time.time(), error="rate_limited")

        provider = self._provider
        all_shows: list[Showtime] = []
        try:
            cinemas = self._cinema_list()
        except Exception as exc:
            self._note_error(exc)
            return _DateEntry(candidates=[], fetched_at=time.time(),
                              error=f"{type(exc).__name__}: {exc}")

        for c in cinemas:
            if self.backing_off:
                break
            try:
                all_shows.extend(provider.showtimes(c, date))
            except Exception as exc:
                self._note_error(exc)  # a 429 here trips the loop-guard above
                continue

        all_shows.sort(key=lambda s: s.availability_ratio, reverse=True)
        slice_ = [s for s in all_shows if s.availability_ratio < 1.0][:PREFILTER]
        enriched: list[Showtime] = []
        for s in slice_:
            if self.backing_off:
                break
            try:
                provider.enrich(s)
            except Exception as exc:
                self._note_error(exc)
                continue
            if s.capacity and s.seats_sold is not None:
                enriched.append(s)

        error = "rate_limited" if (not enriched and self.backing_off) else None
        return _DateEntry(candidates=enriched, fetched_at=time.time(), error=error)


# -- serialisation helpers ---------------------------------------------------

# We deliberately link out to Planet's *general* site rather than the exact
# order page: it's a gag, and we don't want to make actually buying the seat
# next to a stranger a one-click affair.
PLANET_HOME = "https://www.planetcinema.co.il/"


def opportunity_json(o: Opportunity) -> dict:
    s = o.showtime
    seats_free = max(0, o.capacity - o.seats_sold - 1)
    return {
        "presentation_id": s.presentation_id,
        "film": s.film_name,
        "cinema": s.cinema_name,
        "auditorium": s.auditorium,
        "starts_at": s.starts_at,
        "seats_sold": o.seats_sold,
        "capacity": o.capacity,
        "seats_free_beside_you": seats_free,
        "emptiness": round(o.emptiness, 3),
        "loneliness": o.loneliness,
        "beside": "couple" if o.beside_size == 2 else None,
        "booking_link": PLANET_HOME,
    }


def seatmap_json(plan: SeatPlan, presentation_id: str, sold: int) -> dict:
    """A room grid plus the third-wheel pick, on clearly-simulated occupancy.

    Live per-seat occupancy is gated behind an authenticated booking session
    (see occupancy.py), so the exact seats are simulated from the real head
    count on the real geometry, and labelled as such to the visitor. The same
    seed used in the scan is reused here so the map matches the suggestion the
    visitor clicked.
    """
    sold = max(1, sold)
    occupied = simulate_occupancy(
        plan, sold, seed=sim_seed(presentation_id), mode="couple"
    )
    pick = find_third_wheel(plan, occupied, sizes=TARGET_SIZES)
    highlight = pick.seat if pick else None

    rows = {}
    for s in plan.seats:
        rows.setdefault(s.row, []).append(s)
    grid = []
    for r in sorted(rows):
        seats = sorted(rows[r], key=lambda s: s.col)
        cells = []
        for s in seats:
            if highlight and s.row == highlight.row and s.col == highlight.col:
                state = "pick"
            elif (s.row, s.col) in occupied:
                state = "taken"
            elif not s.bookable:
                state = "blocked"
            else:
                state = "free"
            cells.append({"col": s.col, "label": s.label, "state": state})
        grid.append({"row_name": seats[0].row_name, "cells": cells})

    return {
        "section": plan.section_name,
        "capacity": plan.capacity,
        "occupied_count": len(occupied),
        "simulated": True,
        "grid": grid,
        "ascii": render_ascii(plan, occupied, highlight=highlight),
        "pick": (
            {
                "seat": pick.seat.label,
                "target": _describe_he(pick.cluster),
                "target_size": pick.cluster.size,
            }
            if pick
            else None
        ),
    }


def _describe_he(cluster) -> str:
    """Hebrew description of the target cluster (the site is Hebrew; the CLI's
    ``Cluster.describe`` is English)."""
    labels = ", ".join(s.label for s in cluster.seats)
    row = cluster.row_name
    if cluster.size == 2:
        return f"זוג בשורה {row} (מושבים {labels})"
    return f"{cluster.size} יחד בשורה {row} (מושבים {labels})"


# -- tiny per-IP rate limiter ------------------------------------------------


class RateLimiter:
    """Token bucket per client key. Lenient by design -- the real protection is
    the shared per-date cache; this only stops a single client spinning."""

    def __init__(self, capacity: int = 20, refill_per_sec: float = 0.5):
        self._capacity = capacity
        self._refill = refill_per_sec
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.time()
        with self._lock:
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill)
            if tokens < cost:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - cost, now)
            return True
