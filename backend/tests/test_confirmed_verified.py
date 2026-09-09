"""Contracts for materializing confirmed requirements into verified sources."""

import pytest
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
    return application, item, docker, confirmation


def test_confirmed_skill_is_verified_linked_and_idempotent(client):
    application, _, docker, confirmation = _application(client)
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
    application, item, docker, confirmation = _application(client)
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
    application, _, docker, confirmation = _application(client)
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
    application, _, docker, confirmation = _application(client)
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
