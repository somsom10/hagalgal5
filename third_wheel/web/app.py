"""הגלגל 5 — the Third Wheel website.

A thin FastAPI layer over the Third Wheel engine. It serves a Hebrew, RTL,
horror-parody single page plus three JSON endpoints (scan / seatmap / fun-fact).
All the anti-abuse machinery lives in :mod:`third_wheel.web.service`.
"""

from __future__ import annotations

import datetime as dt
import threading
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
    target: str = Query(default="both", pattern="^(single|couple|both)$"),
    top: int = Query(default=12, ge=1, le=30),
):
    if not _valid_date(date):
        return JSONResponse({"error": "bad_date"}, status_code=400)
    if not limiter.allow(_client_key(request), cost=2.0):
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    opps, stale = scanner.opportunities(date=date, target=target, top=top)
    return {
        "date": date,
        "target": target,
        "stale": stale,
        "error": scanner.last_error(date),
        "count": len(opps),
        "opportunities": [opportunity_json(o) for o in opps],
    }


@app.get("/api/seatmap")
def api_seatmap(
    request: Request,
    presentation: str = Query(..., min_length=1, max_length=32),
    sold: int = Query(default=2, ge=1, le=500),
    target: str = Query(default="both", pattern="^(single|couple|both)$"),
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
    return seatmap_json(plan, presentation_id=presentation, sold=sold, target=target)


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
    return HTMLResponse(html.replace("__BASE_URL__", base))


app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.on_event("startup")
def _prewarm() -> None:
    # Warm today's scan in the background so the first visitor isn't left
    # waiting on a cold network pass.
    threading.Thread(target=scanner.warm, args=(_today(),), daemon=True).start()
