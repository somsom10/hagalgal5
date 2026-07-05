"""Engine tests with a fake provider -- no network."""

from third_wheel.engine import _loneliness, scan
from third_wheel.models import Cinema, Showtime


class FakeProvider:
    name = "Fake"

    def __init__(self, shows):
        self._shows = shows

    def cinemas(self):
        return [Cinema("1", "Fake Cinema", "Testville")]

    def showtimes(self, cinema, date):
        return list(self._shows)

    def enrich(self, s):
        s.venue_id, s.seatplan_id, s.capacity = "1", "1", 200
        return s

    def seat_plan(self, s):  # unused here
        raise NotImplementedError


def _show(ratio, name="Film"):
    return Showtime(
        presentation_id=name,
        cinema_id="1",
        cinema_name="Fake Cinema",
        film_name=name,
        starts_at="2026-07-02T20:00:00",
        auditorium="Hall 1",
        availability_ratio=ratio,
        booking_link=f"https://tickets5.planetcinema.co.il/api/order/{name}?lang=he",
    )


def test_scan_selects_lonely_rooms_only():
    # 200-seat room: 0.99 avail -> 2 sold (lonely); 0.5 -> 100 sold (crowded);
    # 1.0 -> empty (excluded, nobody to third-wheel).
    shows = [_show(0.99, "Lonely"), _show(0.5, "Packed"), _show(1.0, "Empty")]
    opps = scan(FakeProvider(shows), "2026-07-02", max_occupants=4)
    names = [o.showtime.film_name for o in opps]
    assert names == ["Lonely"]
    assert opps[0].seats_sold == 2


def test_loneliness_prefers_fewer_occupants():
    # In the same room, one occupant should outscore three.
    assert _loneliness(1, 200) > _loneliness(3, 200)
    # An empty room scores zero (no target).
    assert _loneliness(0, 200) == 0.0
