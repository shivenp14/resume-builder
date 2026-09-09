"""Contracts for durable structured requirements and source evidence links."""

import pytest
from fastapi.testclient import TestClient

from backend.app import main


@pytest.fixture
def client():
    return TestClient(main.app)


def _application(client):
    skill = client.post("/skills", json={"name": "Python", "aliases": ["py"], "verified": True}).json()
    item = client.post("/content-items", json={
        "type": "experience", "title": "Builder", "skill_ids": [skill["id"]],
    }).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={
        "text": "Built Python APIs", "supporting_facts": ["Python"],
    }).json()
    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    assert client.post(f"/base-resumes/{resume['id']}/entries", json={
        "content_item_id": item["id"], "selected_bullet_ids": [bullet["id"]],
    }).status_code == 200
    application = client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Build Python services. Docker is preferred.",
        "base_resume_id": resume["id"],
    }).json()
    return application, skill, item, bullet


def test_analysis_persists_structured_requirements_with_stable_ids(client):
    application, _, _, _ = _application(client)
    first = client.post(f"/applications/{application['id']}/analyze").json()
    assert first["schema_version"] == "2.0"
    assert all(isinstance(requirement["id"], int) for requirement in first["requirements"])
    ids = {requirement["key"]: requirement["id"] for requirement in first["requirements"]}

    second = client.post(f"/applications/{application['id']}/analyze").json()
    assert {requirement["key"]: requirement["id"] for requirement in second["requirements"]} == ids
    rows = client.get(f"/applications/{application['id']}/requirements").json()
    assert {row["id"] for row in rows} == set(ids.values())


def test_evidence_links_require_verified_normalized_sources(client):
    application, skill, item, bullet = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    requirement = next(row for row in client.get(f"/applications/{application['id']}/requirements").json() if row["text"].casefold() == "python")

    linked = client.post(
        f"/applications/{application['id']}/requirements/{requirement['id']}/evidence",
        json={"skill_id": skill["id"], "content_item_id": item["id"]},
    )
    # Analysis creates the same normalized-skill link idempotently; a second
    # manual link is rejected rather than creating ambiguous duplicate proof.
    assert linked.status_code == 409
    listed = client.get(f"/applications/{application['id']}/requirements/{requirement['id']}/evidence").json()
    assert listed and listed[0]["skill_id"] == skill["id"]
    assert client.post(
        f"/applications/{application['id']}/requirements/{requirement['id']}/evidence",
        json={"bullet_id": bullet["id"]},
    ).status_code == 200
    assert client.post(
        f"/applications/{application['id']}/requirements/{requirement['id']}/evidence",
        json={"bullet_id": 99999},
    ).status_code == 404


def test_comparison_confirmations_and_proposal_context_use_requirement_ids(client, monkeypatch):
    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    comparison = client.get(f"/applications/{application['id']}/comparison").json()
    docker = next(record for record in comparison["unsupported"] if record["requirement"] == "docker")
    confirmations = client.post(f"/applications/{application['id']}/missing-confirmations").json()
    record = next(row for row in confirmations if row["requirement"] == "docker")
    assert record["requirement_id"] == docker["requirement_id"]
    assert client.post(f"/applications/{application['id']}/confirmations", json={
        "requirement_id": docker["requirement_id"], "decision": "reject",
    }).status_code == 200

    seen = {}
    class Provider:
        def generate_proposal(self, context):
            seen.update(context)
            return {"selected_entries": [], "bullet_changes": [], "warnings": [], "rationale": ""}

    monkeypatch.setattr(main, "_provider", lambda: Provider())
    generated = client.post(f"/applications/{application['id']}/proposals/generate")
    assert generated.status_code == 200
    assert any(row["requirement_id"] == docker["requirement_id"] for row in seen["requirements"])
    assert seen["confirmations"][0]["requirement_id"] == docker["requirement_id"]


def test_requirement_backfill_is_safe_and_idempotent(client):
    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    with main.SessionLocal() as session:
        analysis = session.query(main.JobAnalysis).filter_by(application_id=application["id"]).one()
        analysis.requirements = ["Python"]
        session.commit()
    main.RequirementEvidenceLink.__table__.drop(main.engine)
    main.JobRequirement.__table__.drop(main.engine)
    main._apply_sqlite_integrity_migrations()
    main._apply_sqlite_integrity_migrations()
    with main.SessionLocal() as session:
        rows = session.query(main.JobRequirement).filter_by(application_id=application["id"]).all()
        assert [row.text for row in rows].count("Python") == 1
        assert len({row.normalized_key for row in rows}) == len(rows)
