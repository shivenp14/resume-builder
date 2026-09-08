"""Contract tests for on-demand immutable revision comparisons."""

from copy import deepcopy

from fastapi.testclient import TestClient

from backend.app import main
from backend.app.services.revision_comparison import compare_snapshots


client = TestClient(main.app)


def _application() -> dict:
    resume = client.post("/base-resumes", json={"name": "Comparison base"}).json()
    return client.post(
        "/applications",
        json={
            "company": "Comparison Co",
            "position": "Engineer",
            "job_description": "Build APIs",
            "base_resume_id": resume["id"],
        },
    ).json()


def _revision(application_id: int, revision_number: int, snapshot: dict) -> main.Revision:
    with main.SessionLocal() as session:
        record = main.Revision(
            application_id=application_id,
            revision_number=revision_number,
            resume_json=deepcopy(snapshot),
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def test_semantic_diff_ignores_provenance_and_does_not_mutate_inputs():
    before = {
        "contact": {"name": "A", "email": "a@example.com"},
        "sections": [{"key": "experience", "title": "Experience", "entries": []}],
        "content_items": [{"id": 1, "title": "Acme"}],
        "bullets": [{"id": 7, "content_item_id": 1, "text": "Built API", "supporting_facts": []}],
        "entries": [{"content_item_id": 1, "bullet_ids": [7]}],
        "provenance": {"proposal_id": 1},
    }
    after = deepcopy(before)
    after["bullets"][0]["text"] = "Built reliable API"
    after["provenance"] = {"proposal_id": 2}
    original_before = deepcopy(before)
    original_after = deepcopy(after)

    diff = compare_snapshots(before, after)

    assert diff["changed"] is True
    assert diff["summary"] == {"added": 0, "removed": 0, "changed": 1, "total": 1}
    assert diff["changed_items"][0]["entity"] == "bullet"
    assert before == original_before
    assert after == original_after


def test_semantic_diff_captures_rendered_entry_metadata_without_bullet_noise():
    before = {
        "contact": {},
        "sections": [{
            "key": "experience",
            "title": "Experience",
            "entries": [{
                "content_item_id": 1,
                "title": "Engineer",
                "organization": "Acme",
                "location": "New York",
                "dates": "2024 -- 2025",
                "summary": "Platform engineering",
                "skills": "Python",
                "display_title": "Engineer",
                "bullets": [{"id": 7, "text": "Built API"}],
            }],
        }],
        "content_items": [{"id": 1, "title": "Engineer"}],
        "bullets": [{"id": 7, "content_item_id": 1, "text": "Built API"}],
        "entries": [{"content_item_id": 1, "bullet_ids": [7]}],
    }
    after = deepcopy(before)
    after["sections"][0]["entries"][0].update({
        "organization": "Acme Labs",
        "dates": "2025 -- Present",
        "summary": "Platform and reliability engineering",
    })

    diff = compare_snapshots(before, after)

    rendered = [item for item in diff["modified"] if item["entity"] == "rendered_entry"]
    assert len(rendered) == 1
    assert rendered[0]["before"]["organization"] == "Acme"
    assert rendered[0]["after"]["dates"] == "2025 -- Present"
    assert rendered[0]["after"]["summary"] == "Platform and reliability engineering"
    assert not [item for item in diff["modified"] if item["entity"] == "bullet"]
    assert diff["summary"] == {"added": 0, "removed": 0, "changed": 1, "total": 1}


def test_title_rename_does_not_duplicate_derived_display_title_change():
    before = {
        "contact": {},
        "sections": [{
            "key": "experience",
            "title": "Experience",
            "entries": [{
                "content_item_id": 1,
                "organization": "Acme",
                "display_title": "Engineer",
            }],
        }],
        "content_items": [{"id": 1, "title": "Engineer"}],
        "bullets": [],
        "entries": [{"content_item_id": 1, "bullet_ids": []}],
    }
    after = deepcopy(before)
    after["content_items"][0]["title"] = "Senior Engineer"
    after["sections"][0]["entries"][0]["display_title"] = "Senior Engineer"

    diff = compare_snapshots(before, after)

    assert [item["entity"] for item in diff["modified"]] == ["content_item"]
    assert diff["summary"] == {"added": 0, "removed": 0, "changed": 1, "total": 1}


def test_application_revision_comparison_is_owned_and_ephemeral():
    application = _application()
    first = _revision(
        application["id"],
        1,
        {
            "contact": {"name": "A"},
            "sections": [],
            "content_items": [{"id": 1, "title": "Acme"}],
            "bullets": [{"id": 7, "content_item_id": 1, "text": "Built API"}],
            "entries": [{"content_item_id": 1, "bullet_ids": [7]}],
        },
    )
    second = _revision(
        application["id"],
        2,
        {
            "contact": {"name": "A", "email": "a@example.com"},
            "sections": [],
            "content_items": [{"id": 1, "title": "Acme Labs"}],
            "bullets": [{"id": 7, "content_item_id": 1, "text": "Built reliable API"}],
            "entries": [{"content_item_id": 1, "bullet_ids": [7]}],
        },
    )

    response = client.get(
        f"/applications/{application['id']}/revisions/compare",
        params={"from_revision_id": first.id, "to_revision_id": second.id},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["from_revision"] == {"id": first.id, "revision_number": 1}
    assert payload["to_revision"] == {"id": second.id, "revision_number": 2}
    assert payload["summary"] == {"added": 1, "removed": 0, "changed": 2, "total": 3}
    assert {item["entity"] for item in payload["changed_items"]} == {"content_item", "bullet"}
    assert payload["modified"] == payload["changed_items"]
    assert payload["added"][0]["entity"] == "contact"
    assert client.get(f"/applications/{application['id']}/revisions").json()[0]["resume_json"]["contact"] == {"name": "A"}

    # Missing pairs and revisions from another application are rejected
    # without leaking revision existence across application boundaries.
    assert client.get(f"/applications/{application['id']}/revisions/compare").status_code == 422
    other = _application()
    other_revision = _revision(other["id"], 1, {"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []})
    cross = client.get(
        f"/applications/{application['id']}/revisions/compare",
        params={"from_revision_id": first.id, "to_revision_id": other_revision.id},
    )
    assert cross.status_code == 404


def test_path_and_revision_scoped_comparison_aliases():
    application = _application()
    first = _revision(application["id"], 1, {"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []})
    second = _revision(application["id"], 2, {"contact": {}, "sections": [], "content_items": [], "bullets": [], "entries": []})
    assert client.get(f"/applications/{application['id']}/revisions/{first.id}/compare/{second.id}").status_code == 200
    assert client.get(f"/revisions/{first.id}/compare/{second.id}").status_code == 200
