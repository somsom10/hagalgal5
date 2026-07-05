"""Geometry tests. Uses a real Planet seat plan captured from the ticketing
API (tests/fixture_seatplan.json, venue 79 / a 204-seat ScreenX room) plus a
small hand-built room for exact adjacency assertions."""

import json
from pathlib import Path

from third_wheel.models import Seat, SeatPlan
from third_wheel.providers.planet import _parse_seat_plan
from third_wheel.seatmap import (
    adjacent_seat,
    find_clusters,
    find_third_wheel,
    is_isolated,
)

FIXTURE = Path(__file__).parent / "fixture_seatplan.json"


def _grid(rows: int, cols: int) -> SeatPlan:
    seats = [
        Seat(row=r, col=c, row_name=str(r), seat_name=str(c))
        for r in range(1, rows + 1)
        for c in range(1, cols + 1)
    ]
    return SeatPlan("v", "1", "test", rows * cols, seats)


def test_parses_real_plan():
    raw = json.loads(FIXTURE.read_text())
    plan = _parse_seat_plan(raw, "79", "1")
    assert plan.capacity == 204
    assert len(plan.seats) > 150
    # every seat has usable coordinates and labels
    assert all(isinstance(s.row, int) and s.seat_name for s in plan.seats)


def test_clusters_split_on_gap():
    plan = _grid(1, 10)
    occ = {(1, 2), (1, 3), (1, 7)}  # a couple, then a single, with a gap
    clusters = sorted(find_clusters(plan, occ), key=lambda c: c.size)
    assert [c.size for c in clusters] == [1, 2]


def test_adjacent_prefers_right_then_left():
    plan = _grid(1, 6)
    couple = [s for s in plan.seats if s.col in (3, 4)]
    from third_wheel.seatmap import Cluster

    cl = Cluster(couple)
    occ = {(1, 3), (1, 4)}
    seat = adjacent_seat(plan, cl, occ)
    assert seat is not None and seat.col == 5  # right side first

    # block the right; should fall back to the left
    occ2 = {(1, 3), (1, 4), (1, 5)}
    seat2 = adjacent_seat(plan, Cluster(couple), occ2)
    assert seat2 is not None and seat2.col == 2


def test_isolation_detects_neighbours():
    plan = _grid(3, 5)
    from third_wheel.seatmap import Cluster

    cluster = Cluster([s for s in plan.seats if s.row == 2 and s.col == 3])
    assert is_isolated(plan, cluster, {(2, 3)})
    # someone directly behind breaks isolation
    assert not is_isolated(plan, cluster, {(2, 3), (1, 3)})


def test_find_third_wheel_targets_the_single():
    plan = _grid(6, 8)
    # a couple in row 2 and a lone single far away in row 5
    occ = {(2, 4), (2, 5), (5, 2)}
    pick = find_third_wheel(plan, occ, sizes={1, 2})
    assert pick is not None
    assert pick.cluster.size == 1  # the single is the juicier target
    assert pick.seat.row == 5 and pick.seat.col in (1, 3)


def test_singles_search_finds_single_despite_a_couple():
    # A room holding BOTH a couple (row 2) and a lone single (row 5): asking
    # only for singles must still return the single, not filter the room out.
    plan = _grid(6, 8)
    occ = {(2, 4), (2, 5), (5, 2)}
    pick = find_third_wheel(plan, occ, sizes={1})
    assert pick is not None
    assert pick.cluster.size == 1
    assert pick.seat.row == 5


def test_couples_search_sits_next_to_the_couple():
    plan = _grid(6, 8)
    occ = {(2, 4), (2, 5), (5, 2)}
    pick = find_third_wheel(plan, occ, sizes={2})
    assert pick is not None
    assert pick.cluster.size == 2
    # the free seat is right beside the pair in row 2
    assert pick.seat.row == 2 and pick.seat.col in (3, 6)
