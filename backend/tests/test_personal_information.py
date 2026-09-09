"""Contracts for typed personal information and legacy contact migration."""

from backend.app import main
from backend.tests.test_api import client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


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
    assert "personal_information" not in snapshot

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


def test_profile_privacy_unlink_and_non_render_fields_do_not_enter_snapshot():
    profile = client.post("/personal-information", json={
        "name": "Canonical", "email": "canonical@example.com",
        "summary": "Private summary", "notes": "Private notes",
    }).json()
    resume = client.post("/base-resumes", json={
        "name": "Primary", "personal_information_id": profile["id"],
        "layout_settings": {"contact": {"name": "Legacy fallback"}},
    }).json()
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Build software", "base_resume_id": resume["id"],
    }).json()
    snapshot = client.get(f"/applications/{application['id']}/snapshot").json()
    assert snapshot["contact"] == {"name": "Canonical", "email": "canonical@example.com"}
    assert "personal_information" not in snapshot
    assert "summary" not in snapshot["contact"]
    assert "notes" not in snapshot["contact"]
    with main.SessionLocal() as session:
        context = main._verified_context(session.get(main.Application, application["id"]), session)
        assert "contact" not in context["base_snapshot"]
        assert "personal_information" not in context["base_snapshot"]

    preserved = client.patch(f"/base-resumes/{resume['id']}", json={
        "name": "Renamed primary", "template_id": "default", "section_order": [],
        "layout_settings": {"contact": {"name": "Legacy fallback"}},
    })
    assert preserved.status_code == 200
    assert preserved.json()["personal_information_id"] == profile["id"]

    unlinked = client.patch(f"/base-resumes/{resume['id']}", json={
        "name": "Primary", "template_id": "default", "section_order": [],
        "layout_settings": {"contact": {"name": "Legacy fallback"}},
        "personal_information_id": None,
    })
    assert unlinked.status_code == 200
    assert unlinked.json()["personal_information_id"] is None
    assert "contact" not in unlinked.json()["layout_settings"]
    assert client.get(f"/applications/{application['id']}/snapshot").json()["contact"] == {}
    # A restart/backfill pass must not infer a new link from the old contact
    # payload after an explicit unlink.
    main._apply_sqlite_integrity_migrations()
    with main.SessionLocal() as session:
        base = session.get(main.BaseResume, resume["id"])
        assert base.personal_information_id is None
        assert session.query(main.PersonalInformation).count() == 1


def test_legacy_contact_does_not_stale_canonical_profile_proposal():
    profile = client.post("/personal-information", json={"name": "Canonical"}).json()
    resume = client.post("/base-resumes", json={
        "name": "Primary", "personal_information_id": profile["id"],
        "layout_settings": {"contact": {"name": "Old legacy value"}},
    }).json()
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer", "job_description": "Build software",
        "base_resume_id": resume["id"],
    }).json()
    proposal = client.post(f"/applications/{application['id']}/proposals", json={
        "payload": {"selected_entries": [], "bullet_changes": []},
    }).json()
    with main.SessionLocal() as session:
        base = session.get(main.BaseResume, resume["id"])
        settings = dict(base.layout_settings)
        settings["contact"] = {"name": "Changed legacy value"}
        base.layout_settings = settings
        session.commit()
    assert client.post(f"/proposals/{proposal['id']}/approve").status_code == 200


def test_pre_feature_sqlite_schema_gets_personal_column_and_backfill(tmp_path, monkeypatch):
    """Exercise startup migration against a database made before profiles."""
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    main.Base.metadata.create_all(legacy_engine)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("DROP TABLE personal_information")
        connection.exec_driver_sql("ALTER TABLE base_resumes DROP COLUMN personal_information_id")
        connection.exec_driver_sql(
            "INSERT INTO base_resumes (name, template_id, section_order, layout_settings) "
            "VALUES (?, ?, ?, ?)",
            ("Legacy", "default", "[]", '{"contact":{"name":"Migrated"}}'),
        )
    monkeypatch.setattr(main, "engine", legacy_engine)
    main._apply_sqlite_integrity_migrations()

    session_local = sessionmaker(bind=legacy_engine, expire_on_commit=False)
    with session_local() as session:
        base = session.query(main.BaseResume).one()
        assert base.personal_information_id is not None
        profile = session.get(main.PersonalInformation, base.personal_information_id)
        assert profile.name == "Migrated"
        assert session.query(main.PersonalInformation).count() == 1
