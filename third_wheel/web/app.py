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
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


def _today() -> str:
    return dt.date.today().isoformat()


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
    date: str = Query(default_factory=_today),
    top: int = Query(default=12, ge=1, le=30),
):
    if not _valid_date(date):
        return JSONResponse({"error": "bad_date"}, status_code=400)
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
    # Warm today's scan in the background so the first visitor isn't left
    # waiting on a cold network pass.
    threading.Thread(target=scanner.warm, args=(_today(),), daemon=True).start()
