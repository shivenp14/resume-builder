from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.services import simplify_listings as listings


@pytest.fixture(autouse=True)
def clean_cache():
    with listings._cache_lock:
        listings._cache.clear()
        listings._refreshing.clear()
        listings._refresh_generation.clear()
        listings._refresh_waiters.clear()
        listings._refresh_failures.clear()
    yield
    with listings._cache_lock:
        listings._cache.clear()
        listings._refreshing.clear()
        listings._refresh_generation.clear()
        listings._refresh_waiters.clear()
        listings._refresh_failures.clear()


def row(**overrides):
    value = {
        "id": "abc-123",
        "company_name": "Example Co",
        "title": "Software Intern",
        "locations": ["New York, NY", " Remote ", None],
        "date_posted": 1_700_000_000,
        "active": True,
        "is_visible": True,
        "url": "https://jobs.example.com/role",
        "company_url": "https://simplify.jobs/p/example",
        "source": "Simplify",
    }
    value.update(overrides)
    return value


def configure(monkeypatch, *, namespace="summer27"):
    monkeypatch.setenv(listings.FEED_URL_ENV, "https://raw.githubusercontent.com/org/repo/dev/listings.json")
    monkeypatch.setenv(listings.NAMESPACE_ENV, namespace)


def test_normalizes_listing_and_namespaces_identity(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(listings, "_fetch_feed", lambda url: json.dumps([row(category="Software Engineering")]).encode())

    result = listings.get_listings()

    assert result == [{
        "id": "summer27:abc-123",
        "company": "Example Co",
        "position": "Software Intern",
        "locations": ["New York, NY", "Remote"],
        "date_posted": 1_700_000_000,
        "category": "Software Engineering",
        "application_url": "https://jobs.example.com/role",
        "simplify_url": "https://simplify.jobs/p/example",
        "source": "Simplify",
        "active": True,
    }]


def test_absent_optional_fields_normalize_to_none(monkeypatch):
    configure(monkeypatch)
    item = row()
    item.pop("company_url")
    monkeypatch.setattr(listings, "_fetch_feed", lambda url: json.dumps([item]).encode())

    result = listings.get_listings()

    assert result[0]["simplify_url"] is None
    assert result[0]["category"] is None


def test_only_active_visible_and_well_formed_rows_are_returned(monkeypatch):
    configure(monkeypatch)
    hidden = row(id="hidden", is_visible=False)
    inactive = row(id="inactive", active=False)
    missing_url = row(id="missing-url", url="javascript:alert(1)")
    invalid_timestamp = row(id="invalid-date", date_posted=True)
    malformed = {"id": "no-title"}
    monkeypatch.setattr(
        listings,
        "_fetch_feed",
        lambda url: json.dumps([hidden, inactive, missing_url, invalid_timestamp, malformed, "bad row", row()]).encode(),
    )

    result = listings.get_listings()

    assert [item["id"] for item in result] == ["summer27:abc-123"]


def test_duplicate_ids_keep_first_valid_occurrence(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(
        listings,
        "_fetch_feed",
        lambda url: json.dumps([row(), row(company_name="Later Duplicate")]).encode(),
    )

    result = listings.get_listings()

    assert len(result) == 1
    assert result[0]["company"] == "Example Co"


def test_feed_failure_is_clear_and_is_not_cached(monkeypatch):
    configure(monkeypatch)
    calls = 0

    def fetch(url):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise listings.ListingsFeedError("temporary outage")
        return json.dumps([row()]).encode()

    monkeypatch.setattr(listings, "_fetch_feed", fetch)
    with pytest.raises(listings.ListingsFeedError, match="temporary outage"):
        listings.get_listings()

    assert listings.get_listings()[0]["id"] == "summer27:abc-123"
    assert calls == 2


def test_successful_feed_is_cached_and_callers_cannot_mutate_cache(monkeypatch):
    configure(monkeypatch)
    calls = 0

    def fetch(url):
        nonlocal calls
        calls += 1
        return json.dumps([row()]).encode()

    monkeypatch.setattr(listings, "_fetch_feed", fetch)
    first = listings.get_listings()
    first[0]["company"] = "Changed by caller"
    first[0]["locations"].append("Changed by caller")
    second = listings.get_listings()

    assert calls == 1
    assert second[0]["company"] == "Example Co"
    assert second[0]["locations"] == ["New York, NY", "Remote"]


def test_cache_expires_after_ttl(monkeypatch):
    configure(monkeypatch)
    now = 10.0
    calls = 0

    def monotonic():
        return now

    def fetch(url):
        nonlocal calls
        calls += 1
        return json.dumps([row()]).encode()

    monkeypatch.setattr(listings.time, "monotonic", monotonic)
    monkeypatch.setattr(listings, "_fetch_feed", fetch)
    listings.get_listings()
    now += listings.CACHE_TTL_SECONDS + 1
    listings.get_listings()

    assert calls == 2


def test_concurrent_cache_misses_share_single_feed_fetch(monkeypatch):
    configure(monkeypatch)
    worker_count = 6
    start = threading.Barrier(worker_count + 1)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    calls = 0

    def fetch(url):
        nonlocal calls
        calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=3)
        return json.dumps([row()]).encode()

    monkeypatch.setattr(listings, "_fetch_feed", fetch)

    def get_after_barrier():
        start.wait(timeout=3)
        return listings.get_listings()

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(get_after_barrier) for _ in range(worker_count)]
        start.wait(timeout=3)
        assert fetch_started.wait(timeout=3)
        release_fetch.set()
        results = [future.result(timeout=3) for future in futures]

    assert calls == 1
    assert all(result[0]["id"] == "summer27:abc-123" for result in results)


def test_concurrent_waiters_share_failed_refresh(monkeypatch):
    configure(monkeypatch)
    worker_count = 6
    start = threading.Barrier(worker_count + 1)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    calls = 0

    def fetch(url):
        nonlocal calls
        calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=3)
        raise listings.ListingsFeedError("upstream unavailable")

    monkeypatch.setattr(listings, "_fetch_feed", fetch)

    def get_after_barrier():
        start.wait(timeout=3)
        return listings.get_listings()

    key = ("https://raw.githubusercontent.com/org/repo/dev/listings.json", "summer27")
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(get_after_barrier) for _ in range(worker_count)]
        start.wait(timeout=3)
        assert fetch_started.wait(timeout=3)
        waiter_key = (key, 1)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with listings._cache_condition:
                if listings._refresh_waiters.get(waiter_key) == worker_count - 1:
                    break
            time.sleep(0.005)
        else:
            pytest.fail("concurrent callers did not join the in-flight refresh")
        release_fetch.set()
        errors = []
        for future in futures:
            with pytest.raises(listings.ListingsFeedError, match="upstream unavailable") as exc:
                future.result(timeout=3)
            errors.append(str(exc.value))

    assert calls == 1
    assert errors == ["upstream unavailable"] * worker_count

    # A later, independent call starts a new generation and can recover.
    monkeypatch.setattr(listings, "_fetch_feed", lambda url: json.dumps([row()]).encode())
    assert listings.get_listings()[0]["id"] == "summer27:abc-123"


@pytest.mark.parametrize(
    "url",
    [
        "http://raw.githubusercontent.com/org/repo/listings.json",
        "https://127.0.0.1/private.json",
        "https://user:pass@raw.githubusercontent.com/org/repo/listings.json",
        "https://example.com/listings.json",
    ],
)
def test_feed_url_must_be_configured_public_github_host(monkeypatch, url):
    configure(monkeypatch)
    monkeypatch.setenv(listings.FEED_URL_ENV, url)

    with pytest.raises(listings.ListingsFeedError, match="configured listings feed"):
        listings.get_listings()


@pytest.mark.parametrize("body", [b"not json", b"{}"])
def test_malformed_feed_fails_clearly(monkeypatch, body):
    configure(monkeypatch)
    monkeypatch.setattr(listings, "_fetch_feed", lambda url: body)

    with pytest.raises(listings.ListingsFeedError, match="listings feed"):
        listings.get_listings()


def test_http_fetch_enforces_size_limit(monkeypatch):
    client_class = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 32)
        )
        return client_class(**kwargs)

    monkeypatch.setattr(listings.httpx, "Client", client_factory)
    monkeypatch.setattr(listings, "MAX_RESPONSE_BYTES", 16)

    with pytest.raises(listings.ListingsFeedError, match="size limit"):
        listings._fetch_feed("https://raw.githubusercontent.com/org/repo/listings.json")


def test_realistic_large_feed_over_10mb_is_accepted(monkeypatch):
    url = "https://raw.githubusercontent.com/org/repo/listings.json"
    padding = "x" * (11 * 1024 * 1024)
    body = json.dumps([row(notes=padding)]).encode()
    client_class = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, content=body)
        )
        return client_class(**kwargs)

    monkeypatch.setattr(listings.httpx, "Client", client_factory)

    result = listings._load_listings(url, "summer27")

    assert [item["id"] for item in result] == ["summer27:abc-123"]


class RepeatingByteStream(httpx.SyncByteStream):
    def __init__(self, size: int):
        self.size = size

    def __iter__(self):
        remaining = self.size
        chunk = b"x" * (1024 * 1024)
        while remaining:
            current = chunk[: min(remaining, len(chunk))]
            yield current
            remaining -= len(current)


def test_feed_larger_than_hard_limit_is_rejected(monkeypatch):
    url = "https://raw.githubusercontent.com/org/repo/listings.json"
    client_class = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(
                200, stream=RepeatingByteStream(listings.MAX_RESPONSE_BYTES + 1)
            )
        )
        return client_class(**kwargs)

    monkeypatch.setattr(listings.httpx, "Client", client_factory)

    with pytest.raises(listings.ListingsFeedError, match="size limit"):
        listings._fetch_feed(url)
