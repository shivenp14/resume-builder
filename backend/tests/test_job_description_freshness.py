"""Regression coverage for job-description edits and saved optimization data."""

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def _application():
    item = client.post(
        "/content-items",
        json={
            "type": "experience",
            "title": "Engineer",
            "organization": "Acme",
            "location": "Remote",
            "start_date": "2025",
            "end_date": "Present",
        },
    ).json()
    bullet = client.post(
        f"/content-items/{item['id']}/bullets",
        json={
            "text": "Built a Python API used by 4 teams",
            "supporting_facts": ["Built a Python API used by 4 teams"],
            "is_locked": False,
        },
    ).json()
    resume = client.post(
        "/base-resumes",
        json={
            "name": "Primary",
            "section_order": ["experience"],
            "layout_settings": {"contact": {"name": "Test Person"}},
        },
    ).json()
    assert client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={
            "content_item_id": item["id"],
            "selected_bullet_ids": [bullet["id"]],
            "entry_order": 0,
        },
    ).status_code == 200
    application = client.post(
        "/applications",
        json={
            "company": "Example",
            "position": "Software Engineer",
            "job_description": "Build Python services",
            "base_resume_id": resume["id"],
        },
    ).json()
    return item, bullet, application


def test_job_description_edit_invalidates_approved_proposal_and_requires_reanalysis():
    item, bullet, application = _application()
    application_id = application["id"]

    analysis = client.post(f"/applications/{application_id}/analyze")
    assert analysis.status_code == 200, analysis.text

    proposal = client.post(
        f"/applications/{application_id}/proposals",
        json={
            "payload": {
                "selected_entries": [
                    {"content_item_id": item["id"], "bullet_ids": [bullet["id"]]}
                ],
                "bullet_changes": [],
                "warnings": [],
                "rationale": "Tailored for the role",
            }
        },
    )
    assert proposal.status_code == 200, proposal.text
    proposal_id = proposal.json()["id"]
    approved = client.post(f"/proposals/{proposal_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    edited = client.patch(
        f"/applications/{application_id}",
        json={"job_description": "Build Rust services for data pipelines"},
    )
    assert edited.status_code == 200, edited.text

    saved = client.get(f"/applications/{application_id}/proposals")
    assert saved.status_code == 200, saved.text
    old = next(row for row in saved.json() if row["id"] == proposal_id)
    # The implementation may use a dedicated ``stale`` state or move the
    # proposal back to ``pending``; either way, the old approval must not
    # survive a job-description mutation.
    assert old["status"] != "approved"

    generation = client.post(
        f"/applications/{application_id}/generate",
        json={"proposal_id": proposal_id},
    )
    assert generation.status_code == 409, generation.text

    # Existing analysis was produced from JD A and cannot support a proposal
    # for JD B.  The endpoint must force a fresh analysis before calling the
    # proposal provider.
    stale_proposal_generation = client.post(
        f"/applications/{application_id}/proposals/generate"
    )
    assert stale_proposal_generation.status_code == 409, stale_proposal_generation.text

    fresh_analysis = client.post(f"/applications/{application_id}/analyze")
    assert fresh_analysis.status_code == 200, fresh_analysis.text
    fresh_proposal = client.post(
        f"/applications/{application_id}/proposals/generate"
    )
    assert fresh_proposal.status_code == 200, fresh_proposal.text
    assert fresh_proposal.json()["id"] != proposal_id


def test_analysis_result_is_discarded_when_job_description_changes_in_flight(monkeypatch):
    _, _, application = _application()
    application_id = application["id"]

    class MutatingProvider:
        def analyze(self, job_description):
            # Simulate another request editing the application while the
            # provider is running.  The analysis result must not be attached
            # to the newer JD identity.
            from backend.app import main

            with main.SessionLocal() as session:
                row = session.get(main.Application, application_id)
                row.job_description = "Changed while analysis was running"
                row.job_description_version += 1
                row.job_description_fingerprint = main._job_description_fingerprint(row.job_description)
                session.commit()
            return main.analyze_text(job_description)

    from backend.app import main

    monkeypatch.setattr(main, "_provider", lambda: MutatingProvider())
    response = client.post(f"/applications/{application_id}/analyze")
    assert response.status_code == 409, response.text
    assert "changed while analysis was running" in response.json()["detail"]

    with main.SessionLocal() as session:
        assert session.query(main.JobAnalysis).filter_by(application_id=application_id).count() == 0
