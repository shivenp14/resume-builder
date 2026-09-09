"""Contracts for materializing confirmed requirements into verified sources."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client():
    return TestClient(main.app)


def _application(client):
    item = client.post("/content-items", json={
        "type": "experience", "title": "Builder",
    }).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={
        "text": "Built Python APIs", "supporting_facts": ["Python"],
    }).json()
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    client.post(f"/base-resumes/{resume['id']}/entries", json={
        "content_item_id": item["id"], "selected_bullet_ids": [bullet["id"]],
    })
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Build Docker services.",
        "base_resume_id": resume["id"],
    }).json()
    client.post(f"/applications/{application['id']}/analyze")
    docker = next(row for row in client.get(
        f"/applications/{application['id']}/requirements"
    ).json() if row["text"].casefold() == "docker")
    confirmation = next(row for row in client.post(
        f"/applications/{application['id']}/missing-confirmations"
    ).json() if row["requirement_id"] == docker["id"])
    return application, item, bullet, docker, confirmation


def test_confirmed_skill_is_verified_linked_and_idempotent(client):
    application, _, _, docker, confirmation = _application(client)
    payload = {
        "requirement_id": docker["id"],
        "decision": "confirmed",
        "idempotency_key": "docker-confirmation-1",
        "context": {"source_type": "skill", "name": "Docker"},
    }
    first = client.post(f"/applications/{application['id']}/confirmations", json=payload)
    assert first.status_code == 200, first.text
    materialization = first.json()["materialization"]
    assert materialization["source_type"] == "skill"
    assert materialization["requirement_id"] == docker["id"]
    assert materialization["provenance"]["confirmation_id"] == confirmation["id"]

    skills = client.get("/skills").json()
    docker_skill = next(skill for skill in skills if skill["name"] == "Docker")
    assert docker_skill["verified"] is True
    assert client.get(
        f"/applications/{application['id']}/requirements/{docker['id']}/evidence"
    ).json()[-1]["skill_id"] == docker_skill["id"]

    replay = client.post(f"/applications/{application['id']}/confirmations", json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["materialization"]["id"] == materialization["id"]
    assert len(client.get(
        f"/missing-confirmations/{confirmation['id']}/materializations"
    ).json()) == 1


def test_confirmed_bullet_requires_facts_and_records_provenance(client):
    application, item, _, docker, confirmation = _application(client)
    rejected = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed",
        "context": {"source_type": "bullet", "content_item_id": item["id"], "text": "Used Docker"},
    })
    assert rejected.status_code == 422
    assert client.get(f"/bullets/{item['id']}/versions").status_code == 200

    confirmed = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed",
        "context": {
            "source_type": "bullet", "content_item_id": item["id"],
            "text": "Used Docker in local services", "supporting_facts": ["Docker"],
        },
    })
    assert confirmed.status_code == 200, confirmed.text
    materialization = confirmed.json()["materialization"]
    assert materialization["source_type"] == "bullet"
    assert materialization["content_item_id"] == item["id"]
    bullets = client.get(f"/content-items/{item['id']}/bullets").json()
    created = next(bullet for bullet in bullets if bullet["id"] == materialization["bullet_id"])
    assert created["supporting_facts"] == ["Docker"]
    assert f"confirmed-requirement:{docker['id']}" in created["tags"]


def test_materialization_rejects_cross_application_and_reused_key(client):
    application, _, _, docker, confirmation = _application(client)
    first = client.post(f"/missing-confirmations/{confirmation['id']}/materialize", json={
        "source_type": "skill", "name": "Docker", "idempotency_key": "one",
    })
    assert first.status_code == 409

    assert client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed", "context": {},
    }).status_code == 200
    assert client.post(f"/missing-confirmations/{confirmation['id']}/materialize", json={
        "source_type": "skill", "name": "Docker", "idempotency_key": "one",
    }).status_code == 200
    conflict = client.post(f"/missing-confirmations/{confirmation['id']}/materialize", json={
        "source_type": "skill", "name": "Kubernetes", "idempotency_key": "one",
    })
    assert conflict.status_code == 409


def test_materialization_changes_verified_source_fingerprint(client):
    application, _, _, docker, confirmation = _application(client)
    before = client.get(f"/applications/{application['id']}/snapshot").json()
    # The materialization is intentionally performed through the typed route
    # after an explicit confirmation decision.
    assert client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed", "context": {"source_type": "skill", "name": "Docker"},
    }).status_code == 200
    after = client.get(f"/applications/{application['id']}/snapshot").json()
    assert before != after
    with main.SessionLocal() as session:
        app_record = session.get(main.Application, application["id"])
        assert main._verified_context(app_record, session)["confirmation_materializations"]


def test_existing_bullet_materialization_persists_bullet_id(client):
    application, item, bullet, docker, confirmation = _application(client)
    assert client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed", "context": {},
    }).status_code == 200
    response = client.post(
        f"/applications/{application['id']}/missing-confirmations/{confirmation['id']}/materialize",
        json={"source_type": "bullet", "bullet_id": bullet["id"], "idempotency_key": "existing-bullet"},
    )
    assert response.status_code == 200, response.text
    materialization = response.json()
    assert materialization["bullet_id"] == bullet["id"]
    assert materialization["source_id"] == bullet["id"]
    with main.SessionLocal() as session:
        row = session.query(main.ConfirmationMaterialization).filter_by(
            id=materialization["id"]
        ).one()
        assert row.bullet_id == bullet["id"]


def test_materialized_confirmation_cannot_be_demoted(client):
    application, _, _, docker, confirmation = _application(client)
    confirmed = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed", "context": {"source_type": "skill", "name": "Docker"},
    })
    assert confirmed.status_code == 200, confirmed.text
    demoted = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "rejected", "context": {},
    })
    assert demoted.status_code == 409
    alias_demoted = client.post(f"/applications/{application['id']}/confirmations", json={
        "requirement_id": docker["id"], "decision": "reject",
    })
    assert alias_demoted.status_code == 409


def test_legacy_confirmation_migration_installs_ownership_guards(tmp_path, monkeypatch):
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy-confirmations.db'}")
    with legacy.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, company VARCHAR(200), position VARCHAR(200), job_description TEXT, status VARCHAR(30), base_resume_id INTEGER, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE missing_confirmations (id INTEGER PRIMARY KEY, application_id INTEGER, requirement TEXT, status VARCHAR(20), context JSON, created_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO applications (id, company, position, job_description, status) VALUES (1, 'One', 'Engineer', 'x', 'draft'), (2, 'Two', 'Engineer', 'x', 'draft')"
        )
        connection.exec_driver_sql(
            "INSERT INTO missing_confirmations (id, application_id, requirement, status) VALUES (1, 1, 'legacy', 'unresolved')"
        )
    monkeypatch.setattr(main, "engine", legacy)
    main._apply_sqlite_integrity_migrations()
    with legacy.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(missing_confirmations)"))}
        assert "requirement_id" in columns
        connection.execute(text(
            "INSERT INTO job_requirements (id, application_id, normalized_key, text, category, priority, is_active, created_at, updated_at) VALUES (10, 1, 'one', 'One', 'required', 'required', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), (20, 2, 'two', 'Two', 'required', 'required', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE missing_confirmations SET requirement_id=20 WHERE id=1"))
        connection.execute(text("UPDATE missing_confirmations SET requirement_id=10 WHERE id=1"))
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO missing_confirmations (id, application_id, requirement, requirement_id, status) VALUES (2, 1, 'bad', 20, 'unresolved')"
            ))
        connection.execute(text(
            "INSERT INTO skills (id, name, aliases, verified) VALUES (1, 'Docker', '[]', 1)"
        ))
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO confirmation_materializations (id, confirmation_id, application_id, requirement_id, source_type, skill_id, idempotency_key, payload_hash, source_fingerprint, source_payload, created_at) VALUES (1, 1, 2, 10, 'skill', 1, 'bad', 'hash', 'fingerprint', '{}', CURRENT_TIMESTAMP)"
            ))


def test_same_application_requirement_reassignment_is_valid_before_materialization(client):
    application, _, _, docker, confirmation = _application(client)
    requirements = client.get(f"/applications/{application['id']}/requirements").json()
    other = next(row for row in requirements if row["id"] != docker["id"])
    reassigned = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "unresolved", "requirement_id": other["id"], "context": {},
    })
    assert reassigned.status_code == 200, reassigned.text
    restored = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "unresolved", "requirement_id": docker["id"], "context": {},
    })
    assert restored.status_code == 200, restored.text


def test_materialized_confirmation_ownership_is_immutable_in_sqlite(client):
    application, _, _, docker, confirmation = _application(client)
    materialized = client.patch(f"/missing-confirmations/{confirmation['id']}", json={
        "status": "confirmed", "context": {"source_type": "skill", "name": "Docker"},
    })
    assert materialized.status_code == 200, materialized.text
    requirements = client.get(f"/applications/{application['id']}/requirements").json()
    other = next(row for row in requirements if row["id"] != docker["id"])

    # The test fixture creates tables directly; install the additive migration
    # triggers explicitly before exercising the direct-DB boundary.
    main._apply_sqlite_integrity_migrations()
    with main.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE missing_confirmations SET requirement_id=:requirement_id WHERE id=:confirmation_id"
            ), {"requirement_id": other["id"], "confirmation_id": confirmation["id"]})
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE missing_confirmations SET application_id=999 WHERE id=:confirmation_id"
            ), {"confirmation_id": confirmation["id"]})
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "UPDATE job_requirements SET application_id=999 WHERE id=:requirement_id"
            ), {"requirement_id": docker["id"]})
