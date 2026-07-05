"""Seat-map geometry: turn a :class:`SeatPlan` plus a set of occupied seats
into the two things Third Wheel cares about -- who is sitting *alone*, and
which empty seat is *right next to them*."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Seat, SeatPlan


@dataclass
class Cluster:
    """A maximal run of occupied seats sitting together in one row."""

    seats: list[Seat]

    @property
    def size(self) -> int:
        return len(self.seats)

    @property
    def row_name(self) -> str:
        return self.seats[0].row_name

    def describe(self) -> str:
        kind = {1: "a lone single", 2: "a couple"}.get(self.size, f"{self.size} together")
        labels = ", ".join(s.label for s in self.seats)
        return f"{kind} in row {self.row_name} (seat{'s' if self.size > 1 else ''} {labels})"


def _row_index(plan: SeatPlan) -> dict[int, list[Seat]]:
    rows: dict[int, list[Seat]] = {}
    for s in plan.seats:
        rows.setdefault(s.row, []).append(s)
    for seats in rows.values():
        seats.sort(key=lambda s: s.col)
    return rows


def find_clusters(plan: SeatPlan, occupied: set[tuple[int, int]]) -> list[Cluster]:
    """Group occupied seats into horizontally-contiguous runs within a row."""
    clusters: list[Cluster] = []
    for _, seats in sorted(_row_index(plan).items()):
        run: list[Seat] = []
        prev_col: int | None = None
        for s in seats:
            if (s.row, s.col) not in occupied:
                prev_col = None
                if run:
                    clusters.append(Cluster(run))
                    run = []
                continue
            if prev_col is not None and s.col != prev_col + 1:
                clusters.append(Cluster(run))
                run = []
            run.append(s)
            prev_col = s.col
        if run:
            clusters.append(Cluster(run))
    return clusters


def is_isolated(
    plan: SeatPlan,
    cluster: Cluster,
    occupied: set[tuple[int, int]],
    col_margin: int = 2,
) -> bool:
    """True if the cluster has personal space: no *other* occupant sits within
    one row and ``col_margin`` seats of the cluster's footprint. Someone on the
    far side of the same row doesn't count -- only people right around them do."""
    rows = _row_index(plan)
    lo = min(s.col for s in cluster.seats) - col_margin
    hi = max(s.col for s in cluster.seats) + col_margin
    row = cluster.seats[0].row
    members = set(cluster.seats)
    for dr in (-1, 0, 1):
        for s in rows.get(row + dr, []):
            if s in members:
                continue
            if (s.row, s.col) in occupied and lo <= s.col <= hi:
                return False
    return True


def adjacent_seat(
    plan: SeatPlan, cluster: Cluster, occupied: set[tuple[int, int]]
) -> Seat | None:
    """The bookable empty seat immediately to the left or right of the cluster
    in the same row -- the maximally awkward third-wheel position. Prefers the
    right side, falls back to left."""
    by_coord = plan.by_coord()
    row = cluster.seats[0].row
    right = max(s.col for s in cluster.seats)
    left = min(s.col for s in cluster.seats)
    for col in (right + 1, left - 1):
        seat = by_coord.get((row, col))
        if seat and seat.bookable and (row, col) not in occupied:
            return seat
    return None


@dataclass
class ThirdWheelPick:
    cluster: Cluster
    seat: Seat


def find_third_wheel(
    plan: SeatPlan,
    occupied: set[tuple[int, int]],
    sizes: "set[int] | tuple[int, ...]" = (1, 2),
) -> ThirdWheelPick | None:
    """Best target in a room: an isolated group whose size is in ``sizes``
    (e.g. ``{1}`` for a lone single, ``{2}`` for a couple, ``{1, 2}`` for
    either) that has a free seat right beside it.

    Crucially this looks at *each* isolated cluster independently, so a room
    that holds both a couple and a lonely single still yields the single when
    ``sizes == {1}`` -- the presence of a couple elsewhere no longer hides it.
    """
    allowed = set(sizes)
    candidates: list[ThirdWheelPick] = []
    for cluster in find_clusters(plan, occupied):
        if cluster.size not in allowed:
            continue
        if not is_isolated(plan, cluster, occupied):
            continue
        seat = adjacent_seat(plan, cluster, occupied)
        if seat is not None:
            candidates.append(ThirdWheelPick(cluster, seat))
    if not candidates:
        return None
    # When several qualify, a lone single is the more tragic (better) target.
    candidates.sort(key=lambda p: p.cluster.size)
    return candidates[0]


def render_ascii(
    plan: SeatPlan,
    occupied: set[tuple[int, int]],
    highlight: Seat | None = None,
) -> str:
    """A quick terminal view. ``#`` occupied, ``.`` empty, ``X`` the suggested
    third-wheel seat, space = no seat / aisle."""
    rows = _row_index(plan)
    if not rows:
        return "(no seats)"
    all_cols = [s.col for s in plan.seats]
    lo, hi = min(all_cols), max(all_cols)
    lines = ["   " + "SCREEN".center(hi - lo + 1, "-")]
    for r in sorted(rows):
        seats = {s.col: s for s in rows[r]}
        row_name = rows[r][0].row_name
        cells = []
        for c in range(lo, hi + 1):
            s = seats.get(c)
            if s is None:
                cells.append(" ")
            elif highlight and s.row == highlight.row and s.col == highlight.col:
                cells.append("X")
            elif (r, c) in occupied:
                cells.append("#")
            else:
                cells.append("." if s.bookable else " ")
        lines.append(f"{row_name:>3} " + "".join(cells))
    return "\n".join(lines)
