import socket
import asyncio
import time

import httpx
import pytest

from app.services import job_description as scraper


LONG_TEXT = " ".join(["Build reliable software with Python, collaborate with product and design, and improve services used by customers."] * 8)


@pytest.fixture(autouse=True)
def public_test_dns(monkeypatch):
    def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(scraper.socket, "getaddrinfo", resolve)


def client_for(handler):
    async def respond(request):
        result = handler(request)
        if asyncio.iscoroutine(result):
            return await result
        return result
    return httpx.MockTransport(respond)


def test_extracts_likely_job_section_and_ignores_navigation_scripts():
    markup = f"""
    <html><head><title>Software Engineer Intern</title>
    <script>document.body.innerText = 'wrong injected text';</script></head>
    <body><nav>{LONG_TEXT}</nav><main id="job-description"><h1>Role</h1><p>{LONG_TEXT}</p></main><footer>{LONG_TEXT}</footer></body></html>
    """
    transport = client_for(lambda request: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=markup))
    result = scraper.fetch_job_description("https://jobs.example/role", transport=transport)
    assert result.status == "ok"
    assert result.title == "Software Engineer Intern"
    assert "Build reliable software" in result.text
    assert "wrong injected text" not in result.text
    assert len(result.text) >= scraper.MIN_DESCRIPTION_CHARS


def test_pins_socket_destination_and_preserves_host_and_tls_identity():
    def respond(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "jobs.example"
        assert request.extensions["sni_hostname"] == "jobs.example"
        return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<main id='job-description'>{LONG_TEXT}</main>")
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(respond))
    assert result.status == "ok"


def test_prefers_schema_jobposting_description():
    markup = f'''<html><head><title>Role</title><script type="application/ld+json">
    {{"@context":"https://schema.org","@type":"JobPosting","title":"Backend Intern","description":"<p>{LONG_TEXT}</p>"}}
    </script></head><body><main>Apply now</main></body></html>'''
    transport = client_for(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup))
    result = scraper.fetch_job_description("https://jobs.example/role", transport=transport)
    assert result.status == "ok"
    assert result.title == "Backend Intern"
    assert len(result.text) >= scraper.MIN_DESCRIPTION_CHARS


def test_client_rendered_shell_is_low_confidence():
    markup = '<html><head><title>Open roles</title></head><body><div id="root"></div><script src="bundle.js"></script></body></html>'
    transport = client_for(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup))
    result = scraper.fetch_job_description("https://jobs.example/role", transport=transport)
    assert result.status == "low_confidence"
    assert result.text == ""


def test_long_generic_body_without_job_content_is_low_confidence():
    markup = f"<html><body>{LONG_TEXT}</body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "low_confidence"
    assert len(result.text) >= scraper.MIN_DESCRIPTION_CHARS


@pytest.mark.parametrize("container", ["main", "article"])
def test_generic_long_semantic_container_is_low_confidence(container):
    markup = f"<html><body><{container}>{LONG_TEXT}</{container}></body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "low_confidence"
    assert len(result.text) >= scraper.MIN_DESCRIPTION_CHARS


def test_hidden_job_heading_does_not_supply_content_signal():
    markup = f"<html><body><main>{LONG_TEXT}<div hidden><h2>Responsibilities</h2></div></main></body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "low_confidence"


def test_body_fallback_with_job_heading_is_usable():
    markup = f"<html><body><h2>Responsibilities</h2>{LONG_TEXT}</body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "ok"


def test_void_hidden_element_does_not_suppress_following_job_content():
    markup = f"<html><body><img hidden><main id='job-description'>{LONG_TEXT}</main></body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "ok"
    assert "Build reliable software" in result.text


def test_malformed_nested_skipped_tags_unwind_at_matching_parent():
    markup = f"<html><body><nav hidden>ignore<div>also ignore</nav><main id='job-description'>{LONG_TEXT}</main></body></html>"
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(
        lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=markup)
    ))
    assert result.status == "ok"
    assert "Build reliable software" in result.text


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/role", "http://localhost/role",
    "http://127.0.0.1/role", "http://169.254.169.254/latest/meta-data/",
    "http://internal.local/role", "http://user:pass@example.com/role", "http://example.com:8080/role",
])
def test_rejects_unsafe_urls_before_request(url):
    transport = client_for(lambda request: pytest.fail("unsafe URL was requested"))
    result = scraper.fetch_job_description(url, transport=transport)
    assert result.status == "unavailable"


def test_rechecks_redirect_target_and_blocks_private_redirect():
    def respond(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(respond))
    assert result.status == "unavailable"
    assert "private" in result.reason


def test_rejects_hostname_resolving_to_private_address(monkeypatch):
    def resolve(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.4", port))]
    monkeypatch.setattr(scraper.socket, "getaddrinfo", resolve)
    transport = client_for(lambda request: pytest.fail("private-resolving URL was requested"))
    result = scraper.fetch_job_description("https://jobs.example/role", transport=transport)
    assert result.status == "unavailable"
    assert "private" in result.reason


def test_redirect_chain_is_bounded():
    transport = client_for(lambda request: httpx.Response(302, headers={"location": "/again"}))
    result = scraper.fetch_job_description("https://jobs.example/role", transport=transport, max_redirects=1)
    assert result.status == "unavailable"
    assert "redirect limit" in result.reason


def test_follows_safe_redirect():
    def respond(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<main id='job-description'>{LONG_TEXT}</main>")
    result = scraper.fetch_job_description("https://jobs.example/old", transport=client_for(respond))
    assert result.status == "ok"
    assert result.source_url == "https://jobs.example/new"


def test_distinguishes_http_error_unsupported_content_and_oversize():
    cases = [
        (lambda req: httpx.Response(503), "HTTP 503"),
        (lambda req: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf"), "not HTML"),
        (lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text="x" * 1000), "size limit"),
    ]
    for handler, reason in cases:
        result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(handler), max_bytes=100)
        assert result.status == "unavailable"
        assert reason in result.reason


def test_network_failure_is_unavailable():
    def fail(request):
        raise httpx.ConnectTimeout("timeout")
    result = scraper.fetch_job_description("https://jobs.example/role", transport=client_for(fail))
    assert result.status == "unavailable"
    assert "ConnectTimeout" in result.reason


class SlowDripStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"<main id='job-description'>"
        for chunk in [LONG_TEXT[:100], LONG_TEXT[100:200], LONG_TEXT[200:300], LONG_TEXT[300:]]:
            await asyncio.sleep(0.04)
            yield chunk.encode()
        yield b"</main>"


def test_absolute_deadline_covers_redirect_and_slow_drip_body():
    async def respond(request):
        if request.url.path == "/old":
            await asyncio.sleep(0.04)
            return httpx.Response(302, headers={"location": "/role"})
        return httpx.Response(200, headers={"content-type": "text/html"}, stream=SlowDripStream())

    started = time.monotonic()
    result = scraper.fetch_job_description(
        "https://jobs.example/old", transport=client_for(respond), timeout=0.12,
    )
    elapsed = time.monotonic() - started
    assert result.status == "unavailable"
    assert "absolute deadline" in result.reason
    assert elapsed < 0.3
