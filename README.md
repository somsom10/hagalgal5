# Third Wheel 🍿🚶‍♂️

> Find a cinema room where a single or a couple is sitting **almost alone** — and
> buy yourself the seat **right next to them**.

A joke tool. That actually works. It scans Israeli **Planet / Yes Planet**
cinemas, finds the loneliest screenings of the day, and (given the seat layout)
points at the exact empty seat beside an isolated single or couple.

It is the evil twin of those "find an empty cinema room" tools: instead of
solitude, it optimises for maximum social awkwardness.

## What actually works today

Everything below runs against Planet's **public, unauthenticated** APIs — the
same JSON the cinema's own website fetches:

| Capability | Data source | Status |
|---|---|---|
| List all cinemas | quickbook API | ✅ open |
| Every screening + `availabilityRatio` for a date | quickbook API | ✅ open |
| Room capacity + full seat geometry (rows, seats, X/Y) | ticketing API | ✅ open |
| **How many** people are in a room (→ find lonely rooms) | derived | ✅ works |
| **Exactly which** seats are taken (→ auto-pick the adjacent seat) | `/seats/seats-status` | 🔒 gated |

The per-seat occupancy endpoint requires an authenticated booking session and
returns `403` to anonymous callers. **Third Wheel does not try to defeat that.**
So:

- `scan` finds the lonely rooms for real (room-level head-count from
  `availabilityRatio × capacity`) and hands you the booking link.
- `seatmap` renders the real room layout and runs the third-wheel seat picker.
  With live occupancy it would auto-select the seat next to the lonely party;
  until that data is wired up, `--simulate` demonstrates the picker on the real
  geometry.

## The website — הגלגל 5

The whole thing also runs as a Hebrew, right-to-left, horror-parody **website**
(the on-screen name is **הגלגל 5**). It wraps the same engine in a small FastAPI
app: pick a date, get the day's loneliest couple-screenings ranked, open any
room's live seat map with the awkward third-wheel seat glowing, and pull a
random movie "fun fact" to enrich the neighbouring couple's date.

The site targets **couples only**. The engine and CLI can also hunt lone
singles, but pointing a public website at one person sitting alone is not
parody, it's menacing — so the web backend deliberately has no "single" target.

```bash
.venv/bin/pip install -r requirements.txt
python -m third_wheel.web            # → http://127.0.0.1:8000
python -m third_wheel.web --host 0.0.0.0 --port 8080   # expose it
```

**Being gentle on Planet's servers** is built in, since a public site could get
real traffic:

- The expensive per-date scan (list every cinema, enrich the emptiest rooms) is
  run **once per date** and shared by all visitors for a 10-minute window.
  Changing a filter (single / couple / crowd threshold) re-filters in memory and
  triggers **no** new upstream requests.
- **Single-flight**: concurrent visitors on a cold date wait on one scan.
- **Stale-while-revalidate**: once warm, a stale cache is served instantly while
  one background thread refreshes it — nobody waits on the network, and upstream
  load is capped at one enrichment pass per date per window regardless of
  traffic. Seat plans are additionally cached to disk (`.cache/`).
- A lenient per-IP token bucket stops a single client spinning the endpoints.

The site writes an **anonymized access log** (combined log format, IPv4 last
octet / IPv6 tail zeroed before hitting disk) to `.logs/access.log`, so
`goaccess .logs/access.log --log-format=COMBINED` shows traffic and referrers
without any tracker or personal data.

**Fun facts** live in
[`third_wheel/web/data/fun_facts.json`](third_wheel/web/data/fun_facts.json) —
one Hebrew string per fact. Edit that file to add your own; it's hot-reloaded on
save, no restart needed.

The seat map is honest about its data: live per-seat occupancy is gated (see
below), so the exact taken seats are **simulated from the real head count on the
real room geometry**, and the UI says so in Hebrew.

## Install

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

## Use

```bash
# Which cinemas do we know about?
python -m third_wheel cinemas

# Rank today's loneliest screenings (rooms with 1–4 people).
python -m third_wheel scan --date 2026-07-02 --max-occupants 4 --top 10

# Show a room's seat map and the third-wheel pick (X = sit here).
# Live seat status is gated, so --simulate demos the picker on the real layout.
python -m third_wheel --cache .cache seatmap --presentation 273965 --simulate
```

Example `scan` output:

```
 1. לצאת מהמשחק
    פלאנט באר שבע · אולם 10 · 2026-07-02 19:15
    1 of 155 seats sold — you'd have 153 empty seats to yourself
    book: https://tickets5.planetcinema.co.il/api/order/276386?lang=he
```

Example `seatmap` pick:

```
 10 ..........  ..#X....
🪑 Target: a lone single in row 10 (seat 106)
👉 Sit in seat 105 — right next to them.
```

## How it fits together

```
providers/planet.py   talks to Planet's quickbook + ticketing APIs → models
models.py             Cinema / Showtime / SeatPlan / Seat
engine.py             ranks screenings by a "loneliness" score
seatmap.py            clusters occupied seats, tests isolation, picks the
                      adjacent seat, renders an ASCII map
occupancy.py          room-level estimate (open) + live seat status (gated stub)
                      + a labelled simulator for demos/tests
cli.py                cinemas / scan / seatmap
web/                  FastAPI site (הגלגל 5): cached scan service, seat-map +
                      fun-fact JSON APIs, and the Hebrew RTL front end
```

The `Provider` protocol (`providers/base.py`) is the only thing the engine
sees, so adding **Rav-Hen** (same Vista platform, different tenant) or
**Cinema City** (separate system) later is a drop-in.

## Loneliness score

A room scores higher when it's big, nearly empty, and holds *few* people — one
lonely single beats a chatty group of four. A totally empty room scores zero:
there's nobody to third-wheel.

## Please don't be a menace

This is a gag. Be decent:

- Don't actually sit next to strangers who'd be uncomfortable. Consent isn't a
  seat-map field.
- Only public, unauthenticated read endpoints are used; nothing here bypasses
  login or anti-bot protection. Respect Planet's Terms of Service and don't
  hammer their servers (`--cache` reuses seat plans).
- No personal data about other customers is collected — only anonymous seat
  counts and room layouts.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Geometry tests run against a real captured seat plan
(`tests/fixture_seatplan.json`, a 204-seat ScreenX room) plus small synthetic
rooms for exact adjacency assertions. No network needed.

## Credits

- Seat-map marker (grasping claw) and the branding wheel/spinner icons are by
  **Lorc**, from [game-icons.net](https://game-icons.net), licensed **CC BY 3.0**.
  The site footer carries the required attribution.
- `assets/og-image.png` is the 1200×630 social-share card (Open Graph /
  Twitter). The `/` route rewrites the card's URL to an absolute one per request
  so link previews resolve on whatever host it's served from.
