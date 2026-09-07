"""Safety tests for the non-destructive checkpoint migration."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend import seed_checkpoints


@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    session_local = sessionmaker(bind=engine, expire_on_commit=False)
    main.Base.metadata.create_all(engine)
    checkpoint_root = tmp_path / "checkpoints"
    folder = checkpoint_root / "baseline-2026-07-27"
    folder.mkdir(parents=True)
    (folder / "resume.tex").write_text(
        r"""\begin{center}
  \textbf{\Huge \scshape Shiven Pandya} \\
  \href{mailto:shiven@example.com}{shiven@example.com}
\end{center}
"""
    )
    monkeypatch.setattr(seed_checkpoints, "SessionLocal", session_local)
    monkeypatch.setattr(seed_checkpoints, "CHECKPOINTS", checkpoint_root)
    return session_local


def _add_source(
    session,
    *,
    owner=None,
    contact=None,
    item_tags=None,
    bullet_tags=None,
    bullet_locked=True,
):
    settings = {"checkpoint": "baseline-2026-07-27"}
    if owner is not None:
        settings["owner"] = owner
    if contact is not None:
        settings["contact"] = contact
    base = main.BaseResume(name="Imported", layout_settings=settings)
    item = main.ContentItem(
        type="experience",
        title="Source item",
        tags=item_tags if item_tags is not None else ["checkpoint:baseline-2026-07-27"],
    )
    bullet = main.Bullet(
        text="Built a reliable API",
        tags=bullet_tags if bullet_tags is not None else ["checkpoint:baseline-2026-07-27"],
        is_locked=bullet_locked,
        supporting_facts=[],
    )
    item.bullets.append(bullet)
    session.add_all([base, item])
    session.flush()
    session.add(
        main.BaseEntry(
            base_resume_id=base.id,
            content_item_id=item.id,
            selected_bullet_ids=[bullet.id],
        )
    )
    session.commit()
    return base.id, item.id, bullet.id


def test_migration_requires_explicit_owner_and_checkpoint_tag(migration_db):
    session_local = migration_db
    with session_local() as session:
        owned = _add_source(session, owner="Shiven Pandya")
        # A matching contact name is not an ownership assertion.
        contact_only = _add_source(session, contact={"name": "Shiven Pandya"})
        untagged = _add_source(
            session,
            owner="Shiven Pandya",
            item_tags=["library:hand-authored"],
        )
        untagged_record = _add_source(
            session,
            owner="Shiven Pandya",
            bullet_tags=["library:hand-authored"],
        )

    assert seed_checkpoints.migrate_verified() is None

    with session_local() as session:
        owned_bullet = session.get(main.Bullet, owned[2])
        contact_only_bullet = session.get(main.Bullet, contact_only[2])
        untagged_bullet = session.get(main.Bullet, untagged[2])
        untagged_bullet_record = session.get(main.Bullet, untagged_record[2])
        assert owned_bullet.supporting_facts == [owned_bullet.text]
        assert owned_bullet.is_locked is False
        assert contact_only_bullet.supporting_facts == []
        assert contact_only_bullet.is_locked is True
        assert untagged_bullet.supporting_facts == []
        assert untagged_bullet.is_locked is True
        assert untagged_bullet_record.supporting_facts == []
        assert untagged_bullet_record.is_locked is True


def test_migration_is_idempotent_and_rewrites_locked_evidence_backed_bullets(migration_db):
    session_local = migration_db
    with session_local() as session:
        base_id, _, bullet_id = _add_source(session, owner="Shiven Pandya")
        bullet = session.get(main.Bullet, bullet_id)
        bullet.supporting_facts = ["Existing evidence"]
        session.commit()

    seed_checkpoints.migrate_verified()
    with session_local() as session:
        bullet = session.get(main.Bullet, bullet_id)
        base = session.get(main.BaseResume, base_id)
        assert bullet.supporting_facts == ["Existing evidence"]
        assert bullet.is_locked is False
        assert base.layout_settings["contact"]["name"] == "Shiven Pandya"

    # A second invocation must not mutate or duplicate evidence/contact data.
    seed_checkpoints.migrate_verified()
    with session_local() as session:
        bullet = session.get(main.Bullet, bullet_id)
        base = session.get(main.BaseResume, base_id)
        assert bullet.supporting_facts == ["Existing evidence"]
        assert bullet.is_locked is False
        assert base.layout_settings["contact"]["name"] == "Shiven Pandya"


def test_migration_rolls_back_all_changes_on_failure(migration_db, monkeypatch):
    session_local = migration_db
    with session_local() as session:
        base_id, _, bullet_id = _add_source(session, owner="Shiven Pandya")

    def fail_parse(_path):
        raise RuntimeError("bad checkpoint")

    monkeypatch.setattr(seed_checkpoints, "parse_contact", fail_parse)
    with pytest.raises(RuntimeError, match="bad checkpoint"):
        seed_checkpoints.migrate_verified()

    with session_local() as session:
        base = session.get(main.BaseResume, base_id)
        bullet = session.get(main.Bullet, bullet_id)
        assert "contact" not in base.layout_settings
        assert bullet.supporting_facts == []
        assert bullet.is_locked is True
