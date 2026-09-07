"""MVP contract tests for the trustworthy tailoring loop.

These tests intentionally exercise the application-scoped workflow rather than
the individual CRUD smoke endpoints.  They serve as a contract for the API
work needed to finish the MVP.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def _seed_library_and_base():
    item_response = client.post(
        "/content-items",
        json={"type": "experience", "title": "Verified Platform Intern", "organization": "Acme"},
    )
    assert item_response.status_code == 200
    item = item_response.json()
    bullet = client.post(
        f"/content-items/{item['id']}/bullets",
        json={
            "text": "Built a Python API used by 4 teams",
            "supporting_facts": ["Python", "4 teams"],
            "is_preferred": True,
        },
    ).json()
    resume = client.post("/base-resumes", json={"name": "Primary"}).json()
    entry = client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "selected_bullet_ids": [bullet["id"]], "entry_order": 1},
    )
    assert entry.status_code == 200
    return item, bullet, resume


def test_full_tailoring_flow_and_revision_artifacts():
    item, bullet, resume = _seed_library_and_base()
    application = client.post(
        "/applications",
        json={
            "company": "Acme",
            "position": "Software Engineer",
            "job_url": "https://example.test/jobs/1",
            "notes": "Referral",
            "job_description": "Build Python services. Docker experience preferred.",
            "base_resume_id": resume["id"],
        },
    ).json()
    analysis = client.post(f"/applications/{application['id']}/analyze")
    assert analysis.status_code == 200
    assert "python" in analysis.json()["technologies"]

    # Comparison and missing-information resolution are application-scoped.
    comparison = client.get(f"/applications/{application['id']}/comparison")
    assert comparison.status_code == 200
    assert "docker" in [x["requirement"].lower() for x in comparison.json()["unsupported"]]
    confirmations = client.post(f"/applications/{application['id']}/missing-confirmations")
    assert confirmations.status_code == 200
    confirmation = next(x for x in confirmations.json() if x["requirement"].lower() == "docker")
    decision = client.patch(
        f"/missing-confirmations/{confirmation['id']}",
        json={"status": "rejected", "context": {}},
    )
    assert decision.status_code == 200

    proposal = client.post(
        f"/applications/{application['id']}/proposals",
        json={
            "payload": {
                "selected_entries": [{"content_item_id": item["id"], "bullet_ids": [bullet["id"]]}],
                "bullet_changes": [],
                "warnings": ["Docker remains unsupported"],
            }
        },
    )
    assert proposal.status_code == 200
    proposal_id = proposal.json()["id"]
    assert client.post(f"/proposals/{proposal_id}/approve").status_code == 200

    generated = client.post(f"/applications/{application['id']}/generate",json={"proposal_id":proposal_id})
    assert generated.status_code == 200
    revision = generated.json()
    assert revision["revision_number"] == 1
    assert revision["page_count"] >= 1
    for key in ("resume_json", "latex_path", "pdf_path"):
        assert revision.get(key)
    assert Path(revision["latex_path"]).exists()
    assert Path(revision["pdf_path"]).exists()


def test_snapshot_and_ownership_constraints_are_rejected():
    item, bullet, resume = _seed_library_and_base()
    other_item, other_bullet, _ = _seed_library_and_base()
    app_record = client.post(
        "/applications",
        json={"company": "Test", "position": "Role", "job_description": "Python", "base_resume_id": resume["id"]},
    ).json()

    # A bullet from another content item may not be attached to this entry.
    bad_entry = client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "selected_bullet_ids": [other_bullet["id"]]},
    )
    assert bad_entry.status_code == 422

    # Final rendering must reject malformed references, not persist them.
    malformed = client.post(
        "/render",
        json={"snapshot": {"content_items": [{"id": "1"}], "bullets": [{"id": "2", "content_item_id": "999"}]}},
    )
    assert malformed.status_code == 422


def test_revision_is_immutable_after_source_edit():
    item, bullet, resume = _seed_library_and_base()
    application = client.post(
        "/applications",
        json={"company": "Immutable Co", "position": "Engineer", "job_description": "Python", "base_resume_id": resume["id"]},
    ).json()
    proposal = client.post(
        f"/applications/{application['id']}/proposals",
        json={"payload": {"selected_entries": [{"content_item_id": item["id"], "bullet_ids": [bullet["id"]]}], "bullet_changes": []}},
    ).json()
    assert client.post(f"/proposals/{proposal['id']}/approve").status_code == 200
    generated = client.post(f"/applications/{application['id']}/generate",json={"proposal_id":proposal["id"]})
    assert generated.status_code == 200
    first = generated.json()

    changed = client.patch(
        f"/bullets/{bullet['id']}",
        json={"text": "Changed after generation", "supporting_facts": [], "tags": [], "is_locked": False, "is_preferred": False},
    )
    assert changed.status_code == 200
    revisions = client.get(f"/applications/{application['id']}/revisions").json()
    assert revisions[0]["resume_json"] == first["resume_json"]


def test_project_skills_render_beside_project_title():
    item = client.post(
        "/content-items",
        json={
            "type": "project",
            "title": "Agora",
            "summary": "Electron, React, TypeScript, Playwright, Tesseract.js",
            "start_date": "Apr. 2026",
            "end_date": "Present",
        },
    ).json()
    resume = client.post("/base-resumes", json={"name": "Primary"}).json()
    client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "entry_order": 1},
    )
    application = client.post(
        "/applications",
        json={
            "company": "Example",
            "position": "Engineer",
            "job_description": "Build software",
            "base_resume_id": resume["id"],
        },
    ).json()

    snapshot = client.get(f"/applications/{application['id']}/snapshot").json()
    assert snapshot["sections"][0]["entries"][0]["skills"] == item["summary"]
    rendered = client.post("/render", json={"snapshot": snapshot})
    assert rendered.status_code == 200
    assert "Agora" in rendered.json()["latex"]
    assert "Electron, React, TypeScript, Playwright, Tesseract.js" in rendered.json()["latex"]
