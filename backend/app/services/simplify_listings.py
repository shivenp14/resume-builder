"""Read and normalize SimplifyJobs' public internship listings feed.

The feed URL and identity namespace are process configuration, never request
input. Responses are size- and time-bounded and cached in-process for 15
minutes. Invalid individual rows are skipped; an invalid feed fails with
``ListingsFeedError`` so callers can distinguish an upstream problem from an
empty list.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx


DEFAULT_FEED_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/"
    "dev/.github/scripts/listings.json"
)
DEFAULT_NAMESPACE = "simplify-summer2027-internships"
FEED_URL_ENV = "SIMPLIFY_LISTINGS_FEED_URL"
NAMESPACE_ENV = "SIMPLIFY_LISTINGS_NAMESPACE"
REQUEST_TIMEOUT_SECONDS = 8.0
TOTAL_FETCH_TIMEOUT_SECONDS = 30.0
MAX_RESPONSE_BYTES = 50_000_000
CACHE_TTL_SECONDS = 15 * 60


class ListingsFeedError(RuntimeError):
    """The configured public listings feed could not be fetched or parsed."""


_cache: dict[tuple[str, str], tuple[float, list[dict[str, object]]]] = {}
_cache_lock = threading.Lock()
_cache_condition = threading.Condition(_cache_lock)
_refreshing: set[tuple[str, str]] = set()
_refresh_generation: dict[tuple[str, str], int] = {}
_refresh_waiters: dict[tuple[tuple[str, str], int], int] = {}
_refresh_failures: dict[tuple[tuple[str, str], int], str] = {}


def _configured_feed_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ListingsFeedError("configured listings feed URL is invalid") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not (host == "githubusercontent.com" or host.endswith(".githubusercontent.com"))
    ):
        raise ListingsFeedError(
            "configured listings feed must be an HTTPS URL hosted on githubusercontent.com"
        )
    return value


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _public_http_url(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 80, 443)
    ):
        return None
    return text


def _normalize_row(
    row: object, *, namespace: str, seen_ids: set[str]
) -> dict[str, object] | None:
    if not isinstance(row, Mapping):
        return None

    upstream_id = _text(row.get("id"))
    company = _text(row.get("company_name"))
    position = _text(row.get("title"))
    application_url = _public_http_url(row.get("url"))
    date_posted = row.get("date_posted")
    if (
        upstream_id is None
        or company is None
        or position is None
        or application_url is None
        or isinstance(date_posted, bool)
        or not isinstance(date_posted, (int, float))
        or (isinstance(date_posted, float) and not math.isfinite(date_posted))
        or date_posted < 0
    ):
        return None
    # The feed schema specifies an integer Unix timestamp. Do not accept
    # fractional values that could conceal a malformed upstream row.
    if int(date_posted) != date_posted:
        return None
    if upstream_id in seen_ids:
        return None
    if row.get("active") is not True or row.get("is_visible") is not True:
        return None

    raw_locations = row.get("locations")
    locations = (
        [location for item in raw_locations if (location := _text(item))]
        if isinstance(raw_locations, list)
        else []
    )
    category = _text(row.get("category"))
    simplify_url = _public_http_url(row.get("company_url"))
    source = _text(row.get("source")) or namespace
    seen_ids.add(upstream_id)
    return {
        "id": f"{namespace}:{upstream_id}",
        "company": company,
        "position": position,
        "locations": locations,
        "date_posted": int(date_posted),
        "category": category,
        "application_url": application_url,
        "simplify_url": simplify_url,
        "source": source,
        "active": True,
    }


def _fetch_feed(url: str) -> bytes:
    deadline = time.monotonic() + TOTAL_FETCH_TIMEOUT_SECONDS
    try:
        with httpx.Client(
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "ResumeBuilderPublicListings/1.0"},
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise ListingsFeedError(
                        f"listings feed returned HTTP {response.status_code}"
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ListingsFeedError("listings feed exceeded the fetch time limit")
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ListingsFeedError("listings feed exceeded the response size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
    except ListingsFeedError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise ListingsFeedError(f"could not fetch listings feed: {exc}") from exc


def _load_listings(url: str, namespace: str) -> list[dict[str, object]]:
    body = _fetch_feed(url)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ListingsFeedError("listings feed did not contain valid JSON") from exc
    if not isinstance(payload, list):
        raise ListingsFeedError("listings feed JSON must be an array")

    result: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for row in payload:
        normalized = _normalize_row(row, namespace=namespace, seen_ids=seen_ids)
        if normalized is not None:
            result.append(normalized)
    return result


def _copy_listings(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [dict(item, locations=list(item["locations"])) for item in items]


def get_listings() -> list[dict[str, object]]:
    """Return normalized active, visible jobs from the configured public feed.

    Each dictionary has ``id`` (``<namespace>:<upstream-id>``), ``company``,
    ``position``, ``locations`` (a list of strings), ``date_posted`` (Unix
    seconds), ``category`` (string or ``None``), ``application_url``,
    ``simplify_url`` (string or ``None``), ``source`` (the upstream source
    label, falling back to the configured namespace), and ``active`` (always
    ``True``). Set
    ``SIMPLIFY_LISTINGS_FEED_URL`` and ``SIMPLIFY_LISTINGS_NAMESPACE`` to use a
    different public GitHub-hosted feed. Raises ``ListingsFeedError`` for a
    fetch, size, or feed-format failure. Returns a fresh list and dictionaries
    so callers cannot mutate cached data.
    """
    url = _configured_feed_url(os.getenv(FEED_URL_ENV, DEFAULT_FEED_URL))
    namespace = os.getenv(NAMESPACE_ENV, DEFAULT_NAMESPACE).strip()
    if not namespace or not re.fullmatch(r"[A-Za-z0-9._-]+", namespace):
        raise ListingsFeedError("configured listings namespace is invalid")

    key = (url, namespace)
    with _cache_condition:
        while True:
            now = time.monotonic()
            cached = _cache.get(key)
            if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
                return _copy_listings(cached[1])
            if key in _refreshing:
                generation = _refresh_generation[key]
                waiter_key = (key, generation)
                _refresh_waiters[waiter_key] = _refresh_waiters.get(waiter_key, 0) + 1
                try:
                    while key in _refreshing and _refresh_generation[key] == generation:
                        _cache_condition.wait()
                    failure = _refresh_failures.get(waiter_key)
                    if failure is not None:
                        raise ListingsFeedError(failure)
                finally:
                    remaining = _refresh_waiters[waiter_key] - 1
                    if remaining:
                        _refresh_waiters[waiter_key] = remaining
                    else:
                        _refresh_waiters.pop(waiter_key, None)
                        _refresh_failures.pop(waiter_key, None)
                continue

            generation = _refresh_generation.get(key, 0) + 1
            _refresh_generation[key] = generation
            _refreshing.add(key)
            refresh_key = (key, generation)
            break

    try:
        loaded = _load_listings(url, namespace)
    except BaseException as exc:
        # Always release waiters, including on cancellation-like exceptions.
        with _cache_condition:
            _refreshing.discard(key)
            if _refresh_waiters.get(refresh_key, 0):
                _refresh_failures[refresh_key] = str(exc) or exc.__class__.__name__
            _cache_condition.notify_all()
        raise

    with _cache_condition:
        _cache[key] = (time.monotonic(), loaded)
        _refreshing.discard(key)
        _cache_condition.notify_all()
    return _copy_listings(loaded)
