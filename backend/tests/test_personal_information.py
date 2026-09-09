"""Contracts for typed personal information and legacy contact migration."""

from backend.app import main
from backend.tests.test_api import client


def test_personal_information_crud_and_snapshot_contact():
    profile = client.post("/personal-information", json={
        "name": "Shiven Pandya",
        "email": "shiven@example.com",
        "location": "Hoboken, NJ",
        "linkedin": "linkedin.com/in/shiven",
    })
    assert profile.status_code == 200
    record = profile.json()
    assert record["name"] == "Shiven Pandya"

    resume = client.post("/base-resumes", json={
        "name": "Primary", "personal_information_id": record["id"],
    }).json()
    assert resume["personal_information_id"] == record["id"]
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Build software", "base_resume_id": resume["id"],
    }).json()
    snapshot = client.get(f"/applications/{application['id']}/snapshot").json()
    assert snapshot["contact"]["name"] == "Shiven Pandya"
    assert snapshot["contact"]["linkedin"] == "https://linkedin.com/in/shiven"
    assert snapshot["personal_information"]["id"] == record["id"]

    changed = client.patch(f"/personal-information/{record['id']}", json={"phone": "555-0100"})
    assert changed.status_code == 200
    assert client.get(f"/personal-information/{record['id']}").json()["phone"] == "555-0100"
    assert client.delete(f"/personal-information/{record['id']}").status_code == 409


def test_profile_changes_make_proposals_stale():
    profile = client.post("/personal-information", json={"name": "Before"}).json()
    resume = client.post("/base-resumes", json={
        "name": "Primary", "personal_information_id": profile["id"],
    }).json()
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Build software", "base_resume_id": resume["id"],
    }).json()
    proposal = client.post(f"/applications/{application['id']}/proposals", json={
        "payload": {"selected_entries": [], "bullet_changes": []},
    }).json()
    assert client.patch(f"/personal-information/{profile['id']}", json={"name": "After"}).status_code == 200
    assert client.post(f"/proposals/{proposal['id']}/approve").status_code == 409


def test_legacy_contacts_backfill_once():
    session_local = main.SessionLocal
    with session_local() as session:
        base = main.BaseResume(
            name="Legacy",
            layout_settings={"contact": {"name": "Legacy Person", "email": "legacy@example.com"}},
        )
        session.add(base)
        session.commit()
        assert base.personal_information_id is None
        assert main._backfill_legacy_contacts(session) == 1
        session.commit()
        first_id = base.personal_information_id
        assert first_id is not None
        assert main._backfill_legacy_contacts(session) == 0
        session.commit()
        assert session.query(main.PersonalInformation).count() == 1
        assert session.get(main.PersonalInformation, first_id).email == "legacy@example.com"
