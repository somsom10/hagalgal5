"""הגלגל 5 — the Third Wheel website.

A thin FastAPI layer over the Third Wheel engine. It serves a Hebrew, RTL,
horror-parody single page plus three JSON endpoints (scan / seatmap / fun-fact).
All the anti-abuse machinery lives in :mod:`third_wheel.web.service`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python 3.8: stdlib zoneinfo arrived in 3.9
    from backports.zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import funfacts
from .service import RateLimiter, Scanner, opportunity_json, seatmap_json

_HERE = Path(__file__).parent
_STATIC = _HERE / "static"
_ASSETS = _HERE.parent / "assets"
# Reuse the repo's on-disk seat-plan cache when present, so we never refetch a
# room we've already mapped.
_CACHE_DIR = _HERE.parent.parent / ".cache"

app = FastAPI(title="הגלגל 5 · Third Wheel", docs_url=None, redoc_url=None)

scanner = Scanner(cache_dir=_CACHE_DIR)
limiter = RateLimiter(capacity=30, refill_per_sec=0.5)

# ---------------------------------------------------------------- access log
# One combined-log-format line per request, so `goaccess .logs/access.log
# --log-format=COMBINED` answers "did anyone visit, and from where" without a
# tracker. IPs are truncated (IPv4 last octet / IPv6 tail zeroed) before they
# ever touch disk, keeping the footer's no-personal-data promise honest.
_LOG_DIR = _HERE.parent.parent / ".logs"

# %b month names are locale-dependent; goaccess needs the English ones.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _setup_access_log() -> logging.Logger:
    logger = logging.getLogger("third_wheel.access")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_DIR / "access.log",
            maxBytes=20_000_000, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


_access_log = _setup_access_log()


def _anonymize_ip(addr: str) -> str:
    if ":" in addr:  # IPv6: keep the routing prefix, zero the rest
        return ":".join(addr.split(":")[:3]) + "::"
    parts = addr.split(".")
    if len(parts) == 4:  # IPv4: drop the last octet
        return ".".join(parts[:3]) + ".0"
    return addr


@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    """Long browser caching for the static tree. CSS/JS carry a content-hash
    ?v= (immutable forever); images and icons don't, so they get a week."""
    response = await call_next(request)
    if request.url.path.startswith(("/static/", "/assets/")) and response.status_code == 200:
        if "v=" in request.url.query:
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["cache-control"] = "public, max-age=604800"
    return response


@app.middleware("http")
async def _log_request(request: Request, call_next):
    response = await call_next(request)
    now = dt.datetime.now(dt.timezone.utc)
    when = (f"{now.day:02d}/{_MONTHS[now.month - 1]}/{now.year}"
            f":{now:%H:%M:%S} +0000")
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    referer = request.headers.get("referer", "-").replace('"', "") or "-"
    agent = request.headers.get("user-agent", "-").replace('"', "") or "-"
    size = response.headers.get("content-length", "-")
    _access_log.info(
        f'{_anonymize_ip(_client_key(request))} - - [{when}] '
        f'"{request.method} {path} HTTP/1.1" {response.status_code} {size} '
        f'"{referer}" "{agent}"'
    )
    return response


# All dates run on the cinemas' clock, not the server's -- a UTC host would
# otherwise disagree with Israel about "today" for a couple of hours a night.
_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _israel_now() -> dt.datetime:
    return dt.datetime.now(_IL_TZ)


def _scan_date() -> str:
    """The earliest scannable date: tomorrow, Israel time.

    Same-day scanning is disabled on purpose (the inverse of a booking
    dark pattern): you can only plan ahead, and while you wait, every seat
    in "your" room stays on sale to the general public. Ambushes decay.
    """
    return (_israel_now().date() + dt.timedelta(days=1)).isoformat()


def _valid_date(date: str) -> bool:
    try:
        dt.date.fromisoformat(date)
        return True
    except ValueError:
        return False


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "anon"


@app.get("/api/scan")
def api_scan(
    request: Request,
    date: str = Query(default_factory=_scan_date),
    top: int = Query(default=12, ge=1, le=30),
):
    if not _valid_date(date):
        return JSONResponse({"error": "bad_date"}, status_code=400)
    if date < _scan_date():  # today or earlier: spontaneity is for couples
        # `earliest` lets a stale tab (or a client with a skewed clock) snap
        # its date picker to the server's idea of the first valid date.
        return JSONResponse(
            {"error": "date_too_soon", "earliest": _scan_date()}, status_code=400
        )
    if not limiter.allow(_client_key(request), cost=2.0):
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    opps, stale = scanner.opportunities(date=date, top=top)
    return {
        "date": date,
        "stale": stale,
        "error": scanner.last_error(date),
        "count": len(opps),
        "opportunities": [opportunity_json(o) for o in opps],
    }


@app.get("/api/seatmap")
def api_seatmap(
    request: Request,
    presentation: str = Query(..., min_length=1, max_length=32),
    sold: int = Query(default=2, ge=2, le=500),
):
    if not presentation.isdigit():
        return JSONResponse({"error": "bad_presentation"}, status_code=400)
    if not limiter.allow(_client_key(request), cost=1.0):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    try:
        plan = scanner.seat_plan(presentation)
    except Exception as exc:
        return JSONResponse(
            {"error": "seatplan_unavailable", "detail": f"{type(exc).__name__}"},
            status_code=502,
        )
    return seatmap_json(plan, presentation_id=presentation, sold=sold)


@app.get("/api/funfact")
def api_funfact(
    request: Request,
    exclude: str = Query(default=""),
    film: str = Query(default="", max_length=160),
):
    # IMDB + translation lookups can be slow-ish, so they cost more against the
    # limiter; results are cached per film so repeats are cheap.
    if not limiter.allow(_client_key(request), cost=1.0 if film else 0.5):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    fact, source = funfacts.random_fact(exclude=exclude or None, film=film or None)
    return {"fact": fact, "source": source, "film": film or None}


@app.get("/")
def index(request: Request):
    # Fill in an absolute base URL so Open Graph image/url tags resolve for
    # social scrapers (they reject relative URLs). Honour a proxy's forwarded
    # host/proto when present.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") \
        or (request.client.host if request.client else "localhost")
    base = f"{proto}://{host}"
    html = html.replace("__BASE_URL__", base).replace("__ASSET_V__", _asset_version())
    return HTMLResponse(html)


def _asset_version() -> str:
    """Content hash of the CSS/JS, appended to their URLs (?v=) so a changed
    file busts the CDN cache immediately -- the HTML itself is never cached."""
    h = hashlib.sha1()
    for name in ("style.css", "app.js"):
        try:
            h.update((_STATIC / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.on_event("startup")
def _prewarm() -> None:
    # Warm the earliest scannable date (tomorrow, Israel time) so the first
    # visitor isn't left waiting on a cold network pass — then re-warm just
    # after each Israel midnight, when "tomorrow" rolls over to a cold date.
    def run() -> None:
        scanner.warm(_scan_date())
        while True:
            now = _israel_now()
            next_midnight = (now + dt.timedelta(days=1)).replace(
                hour=0, minute=2, second=0, microsecond=0
            )
            time.sleep(max(60.0, (next_midnight - now).total_seconds()))
            scanner.warm(_scan_date())

    threading.Thread(target=run, daemon=True, name="tw-prewarm").start()
