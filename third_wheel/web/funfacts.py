"""Movie 'fun facts' to enrich the neighbouring couple's date.

Two sources:

1. **IMDB trivia** for the actual films showing today. Because Planet lists films
   by their Hebrew title and IMDB only matches English, the chain is:
   Hebrew title --(translate)--> English title --(IMDB suggestion)--> title id
   --(IMDB GraphQL)--> trivia --(translate)--> Hebrew. One short fact is shown at
   a time and credited to IMDB.

2. A **local fallback list** in ``data/fun_facts.json`` (hot-reloaded on save),
   used whenever a film can't be resolved or has no trivia.

Everything upstream is cached hard (per film, per translated string) so a busy
site never hammers IMDB or the translation endpoint.
"""

from __future__ import annotations

import json
import random
import re
import threading
import urllib.parse
from pathlib import Path

import httpx

_DATA = Path(__file__).parent / "data" / "fun_facts.json"

_lock = threading.Lock()
_cache: list[str] = []
_mtime: float | None = None

_FALLBACK = ["הפופקורן כבר קר, אבל האהבה בשיאה."]

# -- local fallback list -----------------------------------------------------


def _load() -> list[str]:
    """Return the fallback fact list, reloading from disk if the file changed."""
    global _cache, _mtime
    with _lock:
        try:
            mtime = _DATA.stat().st_mtime
        except OSError:
            return _FALLBACK
        if mtime != _mtime or not _cache:
            try:
                data = json.loads(_DATA.read_text(encoding="utf-8"))
                facts = [str(f).strip() for f in data.get("facts", []) if str(f).strip()]
                _cache = facts or _FALLBACK
                _mtime = mtime
            except (OSError, ValueError):
                return _cache or _FALLBACK
        return _cache


def count() -> int:
    return len(_load())


def _default_fact(exclude: str | None) -> str:
    facts = _load()
    if exclude and len(facts) > 1:
        facts = [f for f in facts if f != exclude] or facts
    return random.choice(facts)


# -- IMDB + translation ------------------------------------------------------

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.imdb.com",
    "Referer": "https://www.imdb.com/",
}
_client = httpx.Client(headers=_HTTP_HEADERS, timeout=12.0, follow_redirects=True)

_net_lock = threading.Lock()
_eng_trivia: dict[str, list[str]] = {}   # hebrew film title -> English trivia list
_tr_cache: dict[tuple[str, str, str], str] = {}  # (text, sl, tl) -> translation

_TRIVIA_QUERY = (
    "query($id:ID!,$first:Int!){title(id:$id){"
    "trivia(first:$first){edges{node{isSpoiler text{plainText}}}}}}"
)

# Skip cameo / director-cameo notes -- IMDB doesn't categorise trivia (every
# item comes back "Uncategorized"), so these are matched on the English text
# before translation.
_SKIP_RE = re.compile(r"\bcameos?\b", re.IGNORECASE)


def _translate(text: str, sl: str, tl: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    key = (text, sl, tl)
    if key in _tr_cache:
        return _tr_cache[key]
    try:
        r = _client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": text},
        )
        r.raise_for_status()
        out = "".join(seg[0] for seg in r.json()[0] if seg and seg[0]).strip()
    except Exception:
        return None
    if out:
        _tr_cache[key] = out
    return out or None


# Generic words that shouldn't drive a title match on their own -- otherwise a
# query like "Moana live action" matches *any* "... Live Action" title.
_TITLE_STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "live", "action", "movie", "film",
    "part", "le", "la", "el", "los", "las", "version", "edition", "special",
}


def _sig_tokens(s: str) -> set[str]:
    """Distinctive lowercase word tokens of a title (stopwords removed)."""
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _TITLE_STOPWORDS}


def _imdb_id(english_title: str) -> str | None:
    """Resolve an English title to an IMDB id, but only accept a suggestion
    that actually shares the query's distinctive words -- IMDB's fuzzy search
    will otherwise return a wildly wrong title (e.g. "Moana live action" ->
    "Legend of Korra Live Action"). Poor matches return None -> default facts.
    """
    url = (
        "https://v3.sg.media-imdb.com/suggestion/x/"
        f"{urllib.parse.quote(english_title)}.json?includeVideos=0"
    )
    try:
        r = _client.get(url)
        r.raise_for_status()
        items = r.json().get("d", [])
    except Exception:
        return None

    q = _sig_tokens(english_title)
    best_id, best_cov = None, 0.0
    for i in items:
        tid = str(i.get("id", ""))
        if not tid.startswith("tt"):
            continue
        if not q:  # nothing distinctive to match on -- take the top hit
            return tid
        cov = len(q & _sig_tokens(str(i.get("l", "")))) / len(q)
        if cov > best_cov:  # strictly greater keeps the highest-ranked on ties
            best_id, best_cov = tid, cov
        if best_cov == 1.0:
            break
    # Require at least half the query's distinctive words to appear in the title.
    return best_id if best_cov >= 0.5 else None


def _imdb_trivia(title_id: str, first: int = 25) -> list[str]:
    try:
        r = _client.post(
            "https://api.graphql.imdb.com/",
            json={"query": _TRIVIA_QUERY, "variables": {"id": title_id, "first": first}},
        )
        r.raise_for_status()
        edges = (
            (((r.json().get("data") or {}).get("title") or {}).get("trivia") or {})
            .get("edges")
            or []
        )
    except Exception:
        return []
    out: list[str] = []
    for e in edges:
        node = e.get("node") or {}
        if node.get("isSpoiler"):
            continue  # no spoilers -- it's a first date, not a plot summary
        t = ((node.get("text") or {}).get("plainText") or "").strip()
        if not (15 <= len(t) <= 320):
            continue  # keep facts short enough to read aloud on a date
        if _SKIP_RE.search(t):
            continue  # skip cameo / director-cameo notes
        out.append(t)
    return out


def _english_trivia(film_he: str) -> list[str]:
    """Resolve a Hebrew film title to its IMDB English trivia list (cached).

    Caches the result -- including an empty list -- so an unresolvable film is
    only attempted once per process and thereafter falls straight back to the
    local list.
    """
    key = film_he.strip()
    with _net_lock:
        if key in _eng_trivia:
            return _eng_trivia[key]
    trivia: list[str] = []
    try:
        english = _translate(key, "iw", "en") or key
        title_id = _imdb_id(english)
        if title_id:
            trivia = _imdb_trivia(title_id)
    except Exception:
        trivia = []
    with _net_lock:
        _eng_trivia[key] = trivia
    return trivia


def movie_fact(film_he: str, exclude: str | None = None) -> tuple[str, str]:
    """Return ``(fact_he, source)`` for a film.

    Tries a random IMDB trivia item translated to Hebrew; on any miss, falls
    back to the local list. ``source`` is ``"imdb"`` or ``"default"``.
    """
    english = _english_trivia(film_he)
    if english:
        pool = list(english)
        random.shuffle(pool)
        for item in pool:
            he = _translate(item, "en", "iw")
            if he and he != exclude:
                return he, "imdb"
        # translation unavailable but we have the original English fact
        if pool:
            return pool[0], "imdb"
    return _default_fact(exclude), "default"


def random_fact(exclude: str | None = None, film: str | None = None) -> tuple[str, str]:
    """A fact to show. With ``film`` set, prefers that film's IMDB trivia."""
    if film:
        return movie_fact(film, exclude=exclude)
    return _default_fact(exclude), "default"
