from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from backend.app import main


client = TestClient(main.app)


def _listing(**overrides):
    item = {
        "id": "simplify-summer2027-internships:acme-1",
        "company": "Acme",
        "position": "Software Intern",
        "locations": ["New York, NY", "Remote"],
        "date_posted": 1_800_000_000,
        "category": "Software Engineering",
        "application_url": "https://jobs.example.test/acme-1",
        "simplify_url": "https://simplify.jobs/p/acme-1",
        "source": "Simplify",
        "active": True,
    }
    item.update(overrides)
    return item


def _create_imported_application(listing=None):
    listing = listing or _listing()
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    response = client.post("/applications", json={
        "company": listing["company"],
        "position": listing["position"],
        "job_description": "Reviewed job description",
        "base_resume_id": resume["id"],
        "job_url": listing["application_url"],
        "source_listing_id": listing["id"],
        "simplify_url": listing["simplify_url"],
        "source": listing["source"],
    })
    return response


def test_jobs_search_filter_pagination_and_feed_failure(monkeypatch):
    monkeypatch.setattr(main, "get_listings", lambda: [
        _listing(), _listing(id="simplify:other", company="Other", category="Data", locations=["Boston"]),
        _listing(id="simplify:inactive", active=False),
    ])
    response = client.get("/jobs", params={"search": "acme", "location": "remote", "page": 1, "page_size": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["application_id"] is None
    assert body["items"][0]["progress"] == "not_imported"
    assert body["categories"] == ["Data", "Software Engineering"]

    monkeypatch.setattr(main, "get_listings", lambda: (_ for _ in ()).throw(main.ListingsFeedError("offline")))
    failed = client.get("/jobs")
    assert failed.status_code == 503
    assert failed.json()["detail"] == "Simplify job listings are temporarily unavailable"


def test_import_is_deduplicated_and_url_schemes_are_checked(monkeypatch):
    listing = _listing()
    monkeypatch.setattr(main, "get_listings", lambda: [listing])
    imported = _create_imported_application(listing)
    assert imported.status_code == 200
    assert imported.json()["source_listing_id"] == listing["id"]
    assert imported.json()["simplify_url"] == listing["simplify_url"]

    second = _create_imported_application(listing)
    assert second.status_code == 409
    assert second.json()["detail"]["application_id"] == imported.json()["id"]

    refreshed = client.get("/jobs").json()["items"][0]
    assert refreshed["application_id"] == imported.json()["id"]
    assert refreshed["progress"] == "resume_in_progress"

    bad = client.post("/jobs/description", json={"url": "file:///etc/passwd"})
    assert bad.status_code == 422


def test_blank_source_listing_ids_remain_manual_applications():
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    payload = {
        "company": "Manual",
        "position": "Engineer",
        "job_description": "Reviewed description",
        "base_resume_id": resume["id"],
        "source_listing_id": "   ",
    }
    first = client.post("/applications", json=payload)
    second = client.post("/applications", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["source_listing_id"] is None
    assert second.json()["source_listing_id"] is None


def test_employer_url_secondary_match_links_manual_app_and_survives_id_changes(monkeypatch):
    listing = _listing(application_url="https://JOBS.example.test:443/acme-1?utm_source=feed")
    monkeypatch.setattr(main, "get_listings", lambda: [listing])
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    manual = client.post("/applications", json={
        "company": listing["company"],
        "position": listing["position"],
        "job_description": "Reviewed job description",
        "base_resume_id": resume["id"],
        "job_url": "https://jobs.example.test/acme-1?utm_campaign=old",
    })
    assert manual.status_code == 200

    item = client.get("/jobs").json()["items"][0]
    assert item["application_id"] == manual.json()["id"]
    assert item["progress"] == "resume_in_progress"

    # A refreshed upstream ID still resolves to the same employer job.
    changed_id = dict(listing, id="simplify-summer2027-internships:renamed-upstream-id")
    monkeypatch.setattr(main, "get_listings", lambda: [changed_id])
    duplicate = _create_imported_application(changed_id)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["application_id"] == manual.json()["id"]


def test_shared_employer_url_does_not_match_a_different_role(monkeypatch):
    listing = _listing(position="Data Intern")
    monkeypatch.setattr(main, "get_listings", lambda: [listing])
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    other_role = client.post("/applications", json={
        "company": listing["company"],
        "position": "Software Intern",
        "job_description": "Different role description",
        "base_resume_id": resume["id"],
        "job_url": listing["application_url"],
    })
    assert other_role.status_code == 200

    item = client.get("/jobs").json()["items"][0]
    assert item["application_id"] is None
    assert item["progress"] == "not_imported"
    assert _create_imported_application(listing).status_code == 200


def test_progress_filter_applies_before_pagination(monkeypatch):
    listings = [
        _listing(id="simplify:first", company="First"),
        _listing(id="simplify:second", company="Second"),
    ]
    monkeypatch.setattr(main, "get_listings", lambda: listings)
    _create_imported_application(listings[1])
    response = client.get("/jobs", params={"progress": "resume_in_progress", "page": 1, "page_size": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == listings[1]["id"]


def test_jobs_sort_newest_first_across_filtered_pages(monkeypatch):
    oldest = _listing(id="listing-z", company="Oldest", date_posted=10)
    tie_b = _listing(id="listing-b", company="Tie B", date_posted=30)
    newest = _listing(id="listing-a", company="Newest", date_posted=30)
    listings = [oldest, tie_b, newest]
    monkeypatch.setattr(main, "get_listings", lambda: listings)
    _create_imported_application(oldest)
    _create_imported_application(newest)

    page_one = client.get("/jobs", params={
        "search": "intern", "category": "Software Engineering", "location": "Remote",
        "progress": "resume_in_progress", "page": 1, "page_size": 1,
    }).json()
    page_two = client.get("/jobs", params={
        "search": "intern", "category": "Software Engineering", "location": "Remote",
        "progress": "resume_in_progress", "page": 2, "page_size": 1,
    }).json()
    assert page_one["total"] == page_two["total"] == 2
    assert page_one["items"][0]["id"] == newest["id"]
    assert page_two["items"][0]["id"] == oldest["id"]

    unfiltered = client.get("/jobs", params={"page_size": 3}).json()
    assert [item["id"] for item in unfiltered["items"]] == ["listing-a", "listing-b", "listing-z"]


def test_revision_pdf_download_is_attachment_and_enforces_ownership():
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    applications = []
    for company in ("Acme", "Other"):
        applications.append(client.post("/applications", json={
            "company": company,
            "position": "Engineer",
            "job_description": "Reviewed description",
            "base_resume_id": resume["id"],
        }).json())
    owner_id, foreign_id = [application["id"] for application in applications]
    revision_dir = Path("generated/applications") / str(owner_id) / "revision-001"
    pdf_path = revision_dir / "resume.pdf"
    latex_path = revision_dir / "resume.tex"
    (main.ROOT / revision_dir).mkdir(parents=True, exist_ok=True)
    (main.ROOT / latex_path).write_text("% generated")
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with (main.ROOT / pdf_path).open("wb") as output:
        writer.write(output)
    with main.SessionLocal() as session:
        revision = main.Revision(
            application_id=owner_id, revision_number=1, resume_json={},
            latex_path=str(latex_path), pdf_path=str(pdf_path), page_count=1,
            status="generated", generated_at=datetime.now(timezone.utc),
        )
        session.add(revision)
        session.commit()
        revision_id = revision.id

    download = client.get(f"/applications/{owner_id}/revisions/{revision_id}/download.pdf")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/pdf"
    assert "attachment" in download.headers["content-disposition"]
    assert download.content.startswith(b"%PDF-")

    unknown = client.get(f"/applications/{owner_id}/revisions/999999/download.pdf")
    foreign = client.get(f"/applications/{foreign_id}/revisions/{revision_id}/download.pdf")
    assert unknown.status_code == 404
    assert foreign.status_code == 422

    outside_path = main.ROOT / "outside.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with outside_path.open("wb") as output:
        writer.write(output)
    with main.SessionLocal() as session:
        session.get(main.Revision, revision_id).pdf_path = str(outside_path)
        session.commit()
    escaped = client.get(f"/applications/{owner_id}/revisions/{revision_id}/download.pdf")
    assert escaped.status_code == 404



def test_job_progress_tracks_generated_and_submitted_revision(monkeypatch):
    listing = _listing()
    monkeypatch.setattr(main, "get_listings", lambda: [listing])
    application = _create_imported_application(listing).json()
    latex_path = Path("generated/applications") / str(application["id"]) / "revision-001" / "resume.tex"
    pdf_path = latex_path.with_suffix(".pdf")
    (main.ROOT / latex_path).parent.mkdir(parents=True, exist_ok=True)
    (main.ROOT / latex_path).write_text("% generated")
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with (main.ROOT / pdf_path).open("wb") as output:
        writer.write(output)
    with main.SessionLocal() as session:
        revision = main.Revision(
            application_id=application["id"], revision_number=1, resume_json={},
            latex_path=str(latex_path), pdf_path=str(pdf_path),
            page_count=1, status="generated", generated_at=datetime.now(timezone.utc),
        )
        session.add(revision)
        session.commit()
        revision_id = revision.id
    item = client.get("/jobs").json()["items"][0]
    assert item["progress"] == "ready_to_apply"

    with main.SessionLocal() as session:
        record = session.get(main.Application, application["id"])
        record.status = "withdrawn"
        session.commit()
    item = client.get("/jobs").json()["items"][0]
    assert item["progress"] == "closed"
    assert client.get("/jobs", params={"progress": "closed"}).json()["total"] == 1

    with main.SessionLocal() as session:
        record = session.get(main.Application, application["id"])
        record.status = "draft"
        record.submitted_revision_id = revision_id
        session.commit()
    item = client.get("/jobs").json()["items"][0]
    assert item["progress"] == "applied"

    with main.SessionLocal() as session:
        record = session.get(main.Application, application["id"])
        record.status = "rejected"
        session.commit()
    item = client.get("/jobs").json()["items"][0]
    assert item["progress"] == "closed"


def test_description_endpoint_returns_scraper_result_and_fallback(monkeypatch):
    class Result:
        status = "low_confidence"
        text = "Please review this extracted text."
        title = "Software Intern"
        source_url = "https://jobs.example.test/role"
        reason = "short_description"

    async def successful_fetch(url):
        return Result()

    monkeypatch.setattr(main, "fetch_job_description_async", successful_fetch)
    response = client.post("/jobs/description", json={"url": "https://jobs.example.test/role"})
    assert response.status_code == 200
    assert response.json() == {
        "status": "low_confidence", "text": "Please review this extracted text.",
        "title": "Software Intern", "source_url": "https://jobs.example.test/role",
        "reason": "short_description",
    }

    async def failed_fetch(url):
        raise RuntimeError("network failed")

    monkeypatch.setattr(main, "fetch_job_description_async", failed_fetch)
    fallback = client.post("/jobs/description", json={"url": "https://jobs.example.test/role"})
    assert fallback.status_code == 200
    assert fallback.json()["status"] == "unavailable"
    assert fallback.json()["text"] == ""
