"""Third Wheel command line.

    python -m third_wheel cinemas
    python -m third_wheel scan --date 2026-07-02 --max-occupants 4
    python -m third_wheel seatmap --presentation 273965 --simulate
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from .engine import scan
from .models import Showtime
from .occupancy import LiveSeatStatus, SeatStatusUnavailable, simulate_occupancy
from .providers.planet import PlanetProvider
from .seatmap import find_third_wheel, render_ascii


def _today() -> str:
    return dt.date.today().isoformat()


def _progress(msg: str) -> None:
    print(f"  … {msg}", file=sys.stderr)


def cmd_cinemas(args) -> int:
    provider = PlanetProvider(cache_dir=args.cache)
    for c in provider.cinemas():
        print(f"{c.id:>6}  {c.name}  ({c.city})")
    return 0


def cmd_scan(args) -> int:
    provider = PlanetProvider(cache_dir=args.cache)
    print(f"Scanning {provider.name} for lonely rooms on {args.date} …", file=sys.stderr)
    opps = scan(
        provider,
        date=args.date,
        max_occupants=args.max_occupants,
        min_occupants=args.min_occupants,
        top=args.top,
        progress=_progress if args.verbose else None,
    )
    if not opps:
        print("No third-wheel opportunities found. Everyone's either alone or "
              "on a full date. Try another date or raise --max-occupants.")
        return 0

    print(f"\n🎬  Third Wheel — top {len(opps)} opportunities on {args.date}\n")
    for i, o in enumerate(opps, 1):
        s = o.showtime
        when = s.starts_at.replace("T", " ")[:16]
        print(f"{i:>2}. {s.film_name}")
        print(f"    {s.cinema_name} · {s.auditorium} · {when}")
        print(f"    {o.seats_sold} of {o.capacity} seats sold "
              f"— you'd have {o.capacity - o.seats_sold - 1} empty seats to yourself")
        if s.booking_link:
            print(f"    book: {s.booking_link}")
        print()
    print("Someone in each of these rooms is about to make a new friend. 💺👋")
    return 0


def cmd_seatmap(args) -> int:
    provider = PlanetProvider(cache_dir=args.cache)
    # A bare presentation id is enough to fetch the plan; booking link is
    # derived from the default ticketing host.
    show = Showtime(
        presentation_id=args.presentation,
        cinema_id="",
        cinema_name="",
        film_name="",
        starts_at="",
        auditorium="",
        availability_ratio=args.availability if args.availability is not None else 0.9,
        booking_link="",
    )
    provider.enrich(show)
    plan = provider.seat_plan(show)
    print(f"Room: {plan.section_name or plan.venue_id}  "
          f"capacity {plan.capacity}  ({len(plan.seats)} seats mapped)")

    if args.simulate:
        sold = show.seats_sold if show.seats_sold is not None else 2
        occupied = simulate_occupancy(plan, max(1, sold), seed=args.seed)
        tag = f"SIMULATED occupancy: {len(occupied)} seat(s)"
    else:
        try:
            occupied = LiveSeatStatus().occupied_seats(plan, show)
            tag = f"live occupancy: {len(occupied)} seat(s)"
        except SeatStatusUnavailable as exc:
            print(f"\n{exc}\nRe-run with --simulate to demo the seat picker.")
            return 2

    print(f"{tag}\n")
    pick = find_third_wheel(plan, occupied, sizes=set(range(1, args.max_group + 1)))
    highlight = pick.seat if pick else None
    print(render_ascii(plan, occupied, highlight=highlight))
    print()
    if pick:
        print(f"🪑 Target: {pick.cluster.describe()}")
        print(f"👉 Sit in seat {pick.seat.label} — right next to them.")
    else:
        print("No isolated single/couple with a free seat beside them. "
              "The room's either empty or too social.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="third_wheel",
        description="Find cinema rooms with a lonely single/couple and a seat "
                    "right next to them. A joke tool that actually works.",
    )
    p.add_argument("--cache", default=None, help="directory to cache seat plans")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cinemas", help="list known cinemas")
    c.set_defaults(func=cmd_cinemas)

    sc = sub.add_parser("scan", help="rank the loneliest screenings on a date")
    sc.add_argument("--date", default=_today(), help="YYYY-MM-DD (default: today)")
    sc.add_argument("--max-occupants", type=int, default=4,
                    help="max people in a room to still count as lonely")
    sc.add_argument("--min-occupants", type=int, default=1,
                    help="min people (1 = don't include totally empty rooms)")
    sc.add_argument("--top", type=int, default=15)
    sc.add_argument("-v", "--verbose", action="store_true")
    sc.set_defaults(func=cmd_scan)

    sm = sub.add_parser("seatmap", help="show a room's seat map + third-wheel pick")
    sm.add_argument("--presentation", required=True, help="presentation/event id")
    sm.add_argument("--simulate", action="store_true",
                    help="simulate occupancy (live seat status is gated)")
    sm.add_argument("--availability", type=float, default=None,
                    help="override availability ratio for simulation")
    sm.add_argument("--max-group", type=int, default=2)
    sm.add_argument("--seed", type=int, default=None)
    sm.set_defaults(func=cmd_seatmap)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
