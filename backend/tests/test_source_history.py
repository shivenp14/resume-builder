"""Focused coverage for append-only source and bullet histories."""

from fastapi.testclient import TestClient

from backend.app import main


client = TestClient(main.app)


def test_content_item_history_records_mutations_and_is_idempotent():
    item = client.post("/content-items", json={"type": "experience", "title": "Acme"}).json()

    first = client.get(f"/content-items/{item['id']}/versions")
    assert first.status_code == 200
    assert [(record["version_number"], record["action"]) for record in first.json()] == [(1, "created")]

    updated = client.patch(f"/content-items/{item['id']}", json={"title": "Acme Labs"})
    assert updated.status_code == 200
    # A no-op patch is not a mutation and must not create a fake version.
    assert client.patch(f"/content-items/{item['id']}", json={"title": "Acme Labs"}).status_code == 200
    assert client.delete(f"/content-items/{item['id']}").status_code == 200

    history = client.get(f"/content-items/{item['id']}/history").json()
    assert [record["action"] for record in history] == ["created", "updated", "archived"]
    assert history[1]["changed_fields"] == ["title"]
    assert history[1]["snapshot"]["title"] == "Acme Labs"
    assert client.get(f"/content-items/{item['id']}/versions/2").json() == history[1]


def test_bullet_history_survives_deletion():
    item = client.post("/content-items", json={"type": "project", "title": "Project"}).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={"text": "Built it"}).json()
    assert client.patch(f"/bullets/{bullet['id']}", json={"text": "Built it well"}).status_code == 200
    assert client.delete(f"/bullets/{bullet['id']}").json() == {"deleted": True}

    history = client.get(f"/bullets/{bullet['id']}/versions").json()
    assert [record["action"] for record in history] == ["created", "updated", "deleted"]
    assert history[-1]["text"] == "Built it well"
    assert client.get(f"/bullets/{bullet['id']}/history/1").json()["text"] == "Built it"


def test_history_migration_backfills_existing_rows_once():
    with main.SessionLocal() as session:
        item = main.ContentItem(type="experience", title="Imported")
        bullet = main.Bullet(text="Imported bullet")
        item.bullets.append(bullet)
        session.add(item)
        session.commit()
        item_id, bullet_id = item.id, bullet.id

    main._apply_sqlite_integrity_migrations()
    main._apply_sqlite_integrity_migrations()

    with main.SessionLocal() as session:
        item_versions = session.query(main.ContentItemVersion).filter_by(content_item_id=item_id).all()
        bullet_versions = session.query(main.BulletVersion).filter_by(bullet_id=bullet_id).all()
        assert len(item_versions) == len(bullet_versions) == 1
        assert item_versions[0].action == bullet_versions[0].action == "backfill"
