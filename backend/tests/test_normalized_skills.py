"""Contracts for canonical skill aliases and source relationships."""

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.main import ContentItemSkill, ContentItem, Skill, SkillAlias, app, normalize_skill_name


client = TestClient(app)


def test_aliases_are_normalized_and_collisions_are_rejected():
    created = client.post(
        "/skills",
        json={"name": " Python ", "aliases": ["py", " PY ", "python"], "verified": True},
    )
    assert created.status_code == 200
    skill = created.json()
    assert skill["name"] == "Python"
    assert skill["aliases"] == ["py"]

    assert client.post("/skills", json={"name": "python"}).status_code == 409
    assert client.post("/skills", json={"name": "Py"}).status_code == 409
    assert client.post("/skills", json={"name": "C++"}).status_code == 200
    assert client.post("/skills", json={"name": "C#"}).status_code == 200

    assert normalize_skill_name(" PY ") == "py"
    assert normalize_skill_name("C++") != normalize_skill_name("C#")


def test_content_relationship_resolves_alias_and_flows_into_context_and_snapshot():
    skill = client.post(
        "/skills", json={"name": "Python", "aliases": ["py"], "verified": True}
    ).json()
    item = client.post(
        "/content-items", json={"type": "experience", "title": "Builder"}
    ).json()
    linked = client.post(
        f"/content-items/{item['id']}/skills", json={"name": "PY", "source": "resume"}
    )
    assert linked.status_code == 200
    assert linked.json()["skill"]["name"] == "Python"
    assert linked.json()["source"] == "resume"
    assert client.post(
        f"/content-items/{item['id']}/skills", json={"skill_id": skill["id"]}
    ).status_code == 200

    resume = client.post("/base-resumes", json={"name": "Base"}).json()
    assert client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "entry_order": 1},
    ).status_code == 200
    application = client.post(
        "/applications",
        json={
            "company": "Example",
            "position": "Engineer",
            "job_description": "Python developer",
            "base_resume_id": resume["id"],
        },
    ).json()

    context = client.post(f"/applications/{application['id']}/analyze")
    assert context.status_code == 200
    # The source relationship is included in the provider context used by a
    # proposal run; a canonical snapshot exposes the stable relationship ID.
    snapshot = client.get(f"/applications/{application['id']}/snapshot").json()
    assert snapshot["entries"][0]["skill_ids"] == [skill["id"]]
    assert snapshot["sections"][0]["entries"][0]["skill_names"] == ["Python"]


def test_legacy_alias_and_tag_backfill_is_idempotent():
    with main.SessionLocal() as session:
        skill = Skill(name="Python", aliases=["py", "PY"] , verified=True)
        item = ContentItem(type="experience", title="Legacy", tags=["PY"])
        session.add_all([skill, item])
        session.commit()

    main._apply_sqlite_integrity_migrations()
    main._apply_sqlite_integrity_migrations()

    with main.SessionLocal() as session:
        assert session.query(SkillAlias).filter_by(skill_id=skill.id).count() == 1
        assert session.query(ContentItemSkill).filter_by(
            content_item_id=item.id, skill_id=skill.id
        ).count() == 1
