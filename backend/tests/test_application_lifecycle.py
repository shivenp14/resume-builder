from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

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
    latex_path = Path(f"generated/applications/{application_id}/revision-001/resume.tex")
    pdf_path = Path(f"generated/applications/{application_id}/revision-001/resume.pdf")
    (main.ROOT / latex_path).parent.mkdir(parents=True, exist_ok=True)
    (main.ROOT / latex_path).write_text("% generated")
    (main.ROOT / pdf_path).write_bytes(b"%PDF-1.4 generated")
    with main.SessionLocal() as session:
        revision = main.Revision(
            application_id=application_id,
            revision_number=1,
            resume_json={"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []},
            latex_path=str(latex_path),
            pdf_path=str(pdf_path),
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


def test_applied_status_sets_applied_at_and_rejects_backward_transition():
    created = _application(status="applied")
    assert created["applied_at"]
    changed = client.patch(f"/applications/{created['id']}", json={"status": "draft"})
    assert changed.status_code == 409

    draft = _application()
    changed = client.patch(f"/applications/{draft['id']}", json={"status": "applied"})
    assert changed.status_code == 200
    assert changed.json()["applied_at"]


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


def test_submission_rejects_terminal_status_and_explicit_clear():
    application = _application()
    assert client.patch(f"/applications/{application['id']}", json={"status": "rejected"}).status_code == 200
    revision_id = _generated_revision(application["id"])
    response = client.post(f"/applications/{application['id']}/submit", json={"revision_id": revision_id})
    assert response.status_code == 409
    assert client.patch(f"/applications/{application['id']}", json={"submitted_revision_id": None}).status_code == 422


def test_submission_requires_artifacts_to_exist():
    application = _application()
    revision_id = _generated_revision(application["id"])
    with main.SessionLocal() as session:
        revision = session.get(main.Revision, revision_id)
        Path(main.ROOT / revision.latex_path).unlink()
    response = client.post(f"/applications/{application['id']}/submit", json={"revision_id": revision_id})
    assert response.status_code == 409
    assert "artifacts" in response.json()["detail"]


def test_legacy_migration_clears_invalid_submission_and_installs_ownership_guard(tmp_path, monkeypatch):
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with legacy.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE applications (id INTEGER PRIMARY KEY, company VARCHAR(200), position VARCHAR(200), job_description TEXT, status VARCHAR(30), base_resume_id INTEGER, created_at DATETIME, updated_at DATETIME)")
        connection.exec_driver_sql("CREATE TABLE revisions (id INTEGER PRIMARY KEY, application_id INTEGER, revision_number INTEGER, resume_json JSON, status VARCHAR(20))")
        connection.exec_driver_sql("CREATE TABLE base_entries (id INTEGER PRIMARY KEY, base_resume_id INTEGER, content_item_id INTEGER)")
        connection.exec_driver_sql("INSERT INTO applications (id, company, position, job_description, status) VALUES (1, 'Legacy', 'Engineer', 'x', 'draft')")
    monkeypatch.setattr(main, "engine", legacy)
    main._apply_sqlite_integrity_migrations()
    with legacy.begin() as connection:
        assert connection.execute(text("SELECT submitted_revision_id FROM applications WHERE id=1")).scalar() is None
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE applications SET submitted_revision_id=999 WHERE id=1"))
