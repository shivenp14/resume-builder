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
    python = next(row for row in seen["requirements"] if row["text"].casefold() == "python")
    assert str(python["requirement_id"]) in seen["evidence_by_requirement"]


def test_structured_unsupported_status_is_distinct_from_legacy_array_status(client):
    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    comparison = client.get(f"/applications/{application['id']}/comparison").json()

    docker = next(row for row in comparison["requirements"] if row["text"].casefold() == "docker")
    legacy = next(row for row in comparison["unsupported"] if row["requirement"].casefold() == "docker")
    assert docker["classification"] == "unsupported"
    assert docker["status"] == "unsupported"
    assert legacy["status"] == "unresolved"


def test_generated_proposal_persists_requirement_evidence_associations(client, monkeypatch):
    from backend.app.services.llm_schemas import ProposalOutput

    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    python = next(row for row in client.get(f"/applications/{application['id']}/requirements").json()
                  if row["text"].casefold() == "python")
    evidence_id = python["evidence_links"][0]["id"]

    class Provider:
        def generate_proposal(self, context):
            assert str(python["id"]) in context["evidence_by_requirement"]
            return ProposalOutput(
                selected_entries=[],
                bullet_changes=[],
                warnings=[],
                rationale="",
                requirement_ids=[python["id"]],
                requirement_evidence=[{
                    "requirement_id": python["id"],
                    "evidence_ids": [evidence_id],
                }],
            )

    monkeypatch.setattr(main, "_provider", lambda: Provider())
    generated = client.post(f"/applications/{application['id']}/proposals/generate")
    assert generated.status_code == 200
    assert generated.json()["payload"]["requirement_evidence"] == [{
        "requirement_id": python["id"],
        "evidence_ids": [evidence_id],
    }]


def test_proposal_rejects_evidence_from_a_different_requirement(client):
    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    requirements = client.get(f"/applications/{application['id']}/requirements").json()
    python = next(row for row in requirements if row["text"].casefold() == "python")
    docker = next(row for row in requirements if row["text"].casefold() == "docker")
    evidence_id = python["evidence_links"][0]["id"]
    response = client.post(f"/applications/{application['id']}/proposals", json={
        "payload": {"requirement_evidence": [{
            "requirement_id": docker["id"], "evidence_ids": [evidence_id],
        }]},
    })
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


def test_evidence_projection_changes_stale_existing_proposal(client):
    application, _, _, _ = _application(client)
    client.post(f"/applications/{application['id']}/analyze")
    proposal = client.post(f"/applications/{application['id']}/proposals", json={
        "payload": {"selected_entries": [], "bullet_changes": []},
    }).json()
    with main.SessionLocal() as session:
        link = session.query(main.RequirementEvidenceLink).first()
        link.note = "edited evidence note"
        session.commit()
    approved = client.post(f"/proposals/{proposal['id']}/approve")
    assert approved.status_code == 409
    assert "source data changed" in approved.json()["detail"]


def test_old_analysis_replay_does_not_reactivate_superseded_requirements(client, monkeypatch):
    application, _, _, _ = _application(client)
    outputs = iter([
        {"schema_version": "1.0", "requirements": ["Python"], "keywords": [],
         "technologies": ["python"], "responsibilities": [], "preferred_qualifications": []},
        {"schema_version": "1.0", "requirements": ["Docker"], "keywords": [],
         "technologies": ["docker"], "responsibilities": [], "preferred_qualifications": []},
    ])
    class Provider:
        def analyze(self, _):
            return next(outputs)
    monkeypatch.setattr(main, "_provider", lambda: Provider())
    first = client.post(f"/applications/{application['id']}/analyze", json={"idempotency_key": "first"}).json()
    first_id = first["requirements"][0]["id"]
    client.post(f"/applications/{application['id']}/analyze", json={"idempotency_key": "second"})
    with main.SessionLocal() as session:
        row = session.get(main.JobRequirement, first_id)
        assert row.text == "Python" and row.is_active is False
    replay = client.post(f"/applications/{application['id']}/analyze", json={"idempotency_key": "first"})
    assert replay.status_code == 200
    with main.SessionLocal() as session:
        row = session.get(main.JobRequirement, first_id)
        assert row.text == "Python" and row.is_active is False


def test_v1_provider_output_is_normalized_to_structured_requirements(client, monkeypatch):
    application, _, _, _ = _application(client)
    class Provider:
        def analyze(self, _):
            return {"schema_version": "1.0", "requirements": ["Python"],
                    "keywords": ["python"], "technologies": ["python"],
                    "responsibilities": [], "preferred_qualifications": []}
    monkeypatch.setattr(main, "_provider", lambda: Provider())
    result = client.post(f"/applications/{application['id']}/analyze").json()
    assert result["schema_version"] == "2.0"
    assert result["requirements"][0]["text"] == "Python"
    assert result["requirements"][0]["requirement_id"] == result["requirements"][0]["id"]


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
