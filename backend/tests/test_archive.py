from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_archive_inventory_restore_preserves_existing_references():
    item = client.post(
        "/content-items",
        json={"type": "experience", "title": "Archiveable source"},
    ).json()
    bullet = client.post(
        f"/content-items/{item['id']}/bullets",
        json={"text": "Existing source evidence", "is_preferred": True},
    ).json()
    resume = client.post("/base-resumes", json={"name": "Archive policy"}).json()
    entry = client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "selected_bullet_ids": [bullet["id"]]},
    ).json()
    application = client.post(
        "/applications",
        json={
            "company": "Archive Co",
            "position": "Engineer",
            "job_description": "Build software",
            "base_resume_id": resume["id"],
        },
    ).json()

    assert client.delete(f"/content-items/{item['id']}").json()["archived"] is True
    assert item["id"] not in {record["id"] for record in client.get("/content-items").json()}
    archived = client.get("/content-items/archived").json()
    archived_record = next(record for record in archived if record["id"] == item["id"])
    assert archived_record["is_archived"] is True
    assert client.get("/content-items", params={"include_archived": True}).status_code == 200

    # Archival does not invalidate an existing base reference or snapshot.
    assert client.get(f"/base-resumes/{resume['id']}/entries").json()[0]["id"] == entry["id"]
    snapshot = client.get(f"/applications/{application['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["content_items"][0]["id"] == item["id"]

    restored = client.post(f"/content-items/{item['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["is_archived"] is False
    assert client.get(f"/content-items/{item['id']}/bullets").json()[0]["id"] == bullet["id"]


def test_archived_items_cannot_be_new_base_references_until_restored():
    item = client.post(
        "/content-items",
        json={"type": "project", "title": "Inactive project"},
    ).json()
    resume = client.post("/base-resumes", json={"name": "Active-only references"}).json()
    assert client.delete(f"/content-items/{item['id']}").status_code == 200
    rejected = client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "selected_bullet_ids": []},
    )
    assert rejected.status_code == 409
    assert client.post(f"/content-items/{item['id']}/restore").status_code == 200
    accepted = client.post(
        f"/base-resumes/{resume['id']}/entries",
        json={"content_item_id": item["id"], "selected_bullet_ids": []},
    )
    assert accepted.status_code == 200


def test_duplicate_copies_item_and_bullets_without_sharing_ids_or_references():
    item = client.post(
        "/content-items",
        json={
            "type": "project",
            "title": "Original project",
            "organization": "Acme",
            "tags": ["verified"],
        },
    ).json()
    bullet = client.post(
        f"/content-items/{item['id']}/bullets",
        json={
            "text": "Shipped a useful feature",
            "tags": ["impact"],
            "supporting_facts": ["release notes"],
            "is_locked": True,
            "is_preferred": True,
        },
    ).json()

    duplicate = client.post(
        f"/content-items/{item['id']}/duplicate",
        json={"title": "Copied project"},
    )
    assert duplicate.status_code == 200
    copied = duplicate.json()
    assert copied["id"] != item["id"]
    assert copied["title"] == "Copied project"
    assert copied["is_archived"] is False
    assert len(copied["bullets"]) == 1
    copied_bullet = copied["bullets"][0]
    assert copied_bullet["id"] != bullet["id"]
    assert copied_bullet["content_item_id"] == copied["id"]
    assert copied_bullet["text"] == bullet["text"]
    assert copied_bullet["tags"] == bullet["tags"]
    assert copied_bullet["supporting_facts"] == bullet["supporting_facts"]

    # Duplicating a source never alters its existing bullets or base entries.
    assert client.get(f"/content-items/{item['id']}/bullets").json()[0]["id"] == bullet["id"]
    assert client.get(f"/content-items/{copied['id']}/bullets").json()[0]["id"] == copied_bullet["id"]
