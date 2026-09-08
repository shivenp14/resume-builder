"""Focused coverage for append-only source and bullet histories."""

from fastapi.testclient import TestClient

from backend.app import main
from backend import seed_checkpoints


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

    # Simulate a database created before source histories were introduced.
    main.BulletVersion.__table__.drop(main.engine)
    main.ContentItemVersion.__table__.drop(main.engine)
    main._apply_sqlite_integrity_migrations()
    main._apply_sqlite_integrity_migrations()

    with main.SessionLocal() as session:
        item_versions = session.query(main.ContentItemVersion).filter_by(content_item_id=item_id).all()
        bullet_versions = session.query(main.BulletVersion).filter_by(bullet_id=bullet_id).all()
        assert len(item_versions) == len(bullet_versions) == 1
        assert item_versions[0].action == bullet_versions[0].action == "backfill"


def test_seed_reset_clears_reused_id_history_and_seeds_new_rows(tmp_path, monkeypatch):
    checkpoint_root = tmp_path / "checkpoints"
    for slug in ("baseline-2026-07-27", "appian-2026-07-30"):
        folder = checkpoint_root / slug
        folder.mkdir(parents=True)
        (folder / "resume.tex").write_text(
            r"""\section{Experience}
\resumeSubheading{Imported Org}{2025}{Imported source}{NJ}
\resumeItem{Built imported systems}
"""
        )
        (folder / "resume.pdf").write_bytes(b"placeholder")
    (checkpoint_root / "appian-2026-07-30" / "job-details.txt").write_text("Python engineering internship")
    monkeypatch.setattr(seed_checkpoints, "SessionLocal", main.SessionLocal)
    monkeypatch.setattr(seed_checkpoints, "ROOT", tmp_path)
    monkeypatch.setattr(seed_checkpoints, "CHECKPOINTS", checkpoint_root)

    with main.SessionLocal() as session:
        item = main.ContentItem(type="experience", title="Old source")
        session.add(item)
        session.flush()
        bullet = main.Bullet(content_item_id=item.id, text="Old bullet")
        session.add(bullet)
        session.flush()
        main._append_content_version(session, item, action="created", changed_fields=list(main._CONTENT_VERSION_FIELDS))
        main._append_bullet_version(session, bullet, action="created", changed_fields=list(main._BULLET_VERSION_FIELDS))
        old_item_id, old_bullet_id = item.id, bullet.id
        session.commit()

    seed_checkpoints.seed()

    with main.SessionLocal() as session:
        # SQLite reuses the deleted IDs in this reset, so this asserts that the
        # new rows have fresh baseline histories rather than inheriting old text.
        new_item = session.get(main.ContentItem, old_item_id)
        new_bullet = session.get(main.Bullet, old_bullet_id)
        assert new_item is not None and new_item.title != "Old source"
        assert new_bullet is not None and new_bullet.text != "Old bullet"
        item_versions = session.query(main.ContentItemVersion).filter_by(content_item_id=new_item.id).all()
        bullet_versions = session.query(main.BulletVersion).filter_by(bullet_id=new_bullet.id).all()
        assert len(item_versions) == 1
        assert len(bullet_versions) == 1
        assert item_versions[0].snapshot["title"] == new_item.title
        assert bullet_versions[0].snapshot["text"] == new_bullet.text
