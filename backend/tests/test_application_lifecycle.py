from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app import main


client = TestClient(main.app)


def _application(**overrides):
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    payload = {
        "company": "Acme",
        "position": "Engineer",
        "job_description": "Build reliable services",
        "base_resume_id": resume["id"],
    }
    payload.update(overrides)
    return client.post("/applications", json=payload).json()


def _generated_revision(application_id, *, application=None):
    application = application or client.get(f"/applications/{application_id}").json()
    with main.SessionLocal() as session:
        revision = main.Revision(
            application_id=application_id,
            revision_number=1,
            resume_json={"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []},
            latex_path=f"generated/applications/{application_id}/revision-001/resume.tex",
            pdf_path=f"generated/applications/{application_id}/revision-001/resume.pdf",
            page_count=1,
            status="draft",
            generated_at=datetime.now(timezone.utc),
        )
        session.add(revision)
        session.commit()
        session.refresh(revision)
        return revision.id


def test_application_metadata_and_initial_status_history():
    application = _application(
        source="LinkedIn",
        location="New York, NY",
        employment_type="full-time",
        salary_range="$120k-$140k",
        contact_name="Recruiter",
        contact_email="recruiter@example.com",
        application_deadline="2026-10-01",
        follow_up_at="2026-10-08",
    )
    assert application["source"] == "LinkedIn"
    assert application["application_deadline"] == "2026-10-01"
    history = client.get(f"/applications/{application['id']}/status-history")
    assert history.status_code == 200
    assert [(row["from_status"], row["to_status"]) for row in history.json()] == [(None, "draft")]

    updated = client.post(f"/applications/{application['id']}/status", json={
        "status": "interviewing", "reason": "Recruiter screen scheduled"
    })
    assert updated.status_code == 200
    assert updated.json()["status"] == "interviewing"
    assert client.get(f"/applications/{application['id']}/history").json()[-1]["reason"] == "Recruiter screen scheduled"


def test_submission_tracks_generated_revision_and_status_transactionally():
    application = _application()
    revision_id = _generated_revision(application["id"])
    response = client.post(f"/applications/{application['id']}/submit", json={"revision_id": revision_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["submitted_revision_id"] == revision_id
    assert body["status"] == "applied"
    assert client.get(f"/applications/{application['id']}/submitted-revision").json()["id"] == revision_id
    history = client.get(f"/applications/{application['id']}/status-history").json()
    assert (history[-1]["from_status"], history[-1]["to_status"], history[-1]["reason"]) == ("draft", "applied", "revision submitted")


def test_submission_rejects_cross_application_and_ungenerated_revisions():
    first = _application(company="First")
    second = _application(company="Second")
    first_revision = _generated_revision(first["id"])
    assert client.post(f"/applications/{second['id']}/submit", json={"revision_id": first_revision}).status_code == 422

    with main.SessionLocal() as session:
        revision = main.Revision(
            application_id=second["id"], revision_number=1,
            resume_json={"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []},
            status="draft",
        )
        session.add(revision)
        session.commit()
        session.refresh(revision)
        ungenerated_id = revision.id
    response = client.post(f"/applications/{second['id']}/submit", json={"revision_id": ungenerated_id})
    assert response.status_code == 409
    assert client.get(f"/applications/{second['id']}").json()["submitted_revision_id"] is None
