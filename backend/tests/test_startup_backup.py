"""Startup must preserve existing local data before schema changes."""

from __future__ import annotations

import sqlite3
import zipfile
from contextlib import contextmanager

import pytest

from backend.app import main
from backend.app.services.backup import BackupError, DATABASE_MEMBER, read_backup_manifest


def _create_nonempty_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE saved_data (value TEXT)")
        connection.execute("INSERT INTO saved_data VALUES ('keep me')")


def test_startup_backs_up_before_creating_schema(tmp_path, monkeypatch, caplog):
    root = tmp_path / "workspace"
    database = root / "data" / "app.db"
    generated = root / "generated"
    generated.mkdir(parents=True)
    _create_nonempty_database(database)
    events = []

    monkeypatch.setattr(main, "ROOT", root)
    monkeypatch.setattr(main, "DB_PATH", database)

    def record_backup(**kwargs):
        events.append(("backup", kwargs))
        return {
            "path": str(root / "data" / "backups" / "test.zip"),
            "retention_removed": 3,
            "retention_count": 10,
            "retention_bytes": 12345,
        }

    monkeypatch.setattr(main, "create_backup_archive", record_backup)
    monkeypatch.setattr(
        main.Base.metadata,
        "create_all",
        lambda _engine: events.append(("schema", None)),
    )

    with caplog.at_level("INFO", logger="uvicorn.error"):
        main._initialize_database_schema()

    assert [event[0] for event in events] == ["backup", "schema"]
    assert events[0][1]["database_path"] == database
    assert events[0][1]["generated_root"] == generated
    assert events[0][1]["backup_root"] == root / "data" / "backups"
    assert events[0][1]["skip_if_unchanged"] is True
    assert "Starting automatic backup" in caplog.text
    assert "Automatic backup succeeded" in caplog.text
    assert "retention removed 3 old backup(s), leaving 10 valid archive(s) totaling 12345 bytes" in caplog.text


def test_startup_lock_covers_backup_schema_and_integrity_migrations(tmp_path, monkeypatch):
    events = []

    @contextmanager
    def record_lock(root):
        events.append(("lock_enter", root))
        try:
            yield
        finally:
            events.append(("lock_exit", root))

    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "database_startup_lock", record_lock)
    monkeypatch.setattr(main, "_backup_existing_database_before_schema_changes", lambda: events.append(("backup", None)))
    monkeypatch.setattr(main.Base.metadata, "create_all", lambda _engine: events.append(("schema", None)))
    monkeypatch.setattr(main, "_apply_sqlite_integrity_migrations", lambda: events.append(("migrations", None)))

    main._initialize_application_storage()

    assert [event[0] for event in events] == ["lock_enter", "backup", "schema", "migrations", "lock_exit"]


def test_startup_skips_backup_for_empty_database(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    database = root / "data" / "app.db"
    database.parent.mkdir(parents=True)
    database.touch()
    events = []

    monkeypatch.setattr(main, "ROOT", root)
    monkeypatch.setattr(main, "DB_PATH", database)
    monkeypatch.setattr(
        main,
        "create_backup_archive",
        lambda **_kwargs: events.append("backup"),
    )
    monkeypatch.setattr(
        main.Base.metadata,
        "create_all",
        lambda _engine: events.append("schema"),
    )

    main._initialize_database_schema()

    assert events == ["schema"]


def test_backup_failure_aborts_startup_without_changing_database(tmp_path, monkeypatch, caplog):
    root = tmp_path / "workspace"
    database = root / "data" / "app.db"
    _create_nonempty_database(database)
    original_bytes = database.read_bytes()
    schema_calls = []

    monkeypatch.setattr(main, "ROOT", root)
    monkeypatch.setattr(main, "DB_PATH", database)

    def fail_backup(**_kwargs):
        raise BackupError("backup storage unavailable")

    monkeypatch.setattr(main, "create_backup_archive", fail_backup)
    monkeypatch.setattr(
        main.Base.metadata,
        "create_all",
        lambda _engine: schema_calls.append("called"),
    )

    with pytest.raises(RuntimeError, match="validated backup.*backup storage unavailable"):
        main._initialize_database_schema()

    assert database.read_bytes() == original_bytes
    assert schema_calls == []
    assert "Automatic backup failed; aborting startup" in caplog.text


def test_startup_backup_contains_database_snapshot_and_generated_artifact(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    database = root / "data" / "app.db"
    generated = root / "generated"
    artifact = generated / "applications" / "7" / "resume.pdf"
    _create_nonempty_database(database)
    artifact.parent.mkdir(parents=True)
    artifact_bytes = b"generated resume artifact from before migration"
    artifact.write_bytes(artifact_bytes)

    monkeypatch.setattr(main, "ROOT", root)
    monkeypatch.setattr(main, "DB_PATH", database)

    # Use the actual archive service while keeping the entire workspace under
    # tmp_path; the schema initializer is tested separately above.
    main._backup_existing_database_before_schema_changes()

    backups = list((root / "data" / "backups").glob("*.zip"))
    assert len(backups) == 1
    backup_path = backups[0]
    manifest = read_backup_manifest(backup_path)
    assert manifest["artifacts"]["count"] == 1
    assert any(entry["path"] == "artifacts/applications/7/resume.pdf" for entry in manifest["files"])

    snapshot = tmp_path / "snapshot.db"
    with zipfile.ZipFile(backup_path) as archive:
        snapshot.write_bytes(archive.read(DATABASE_MEMBER))
        assert archive.read("artifacts/applications/7/resume.pdf") == artifact_bytes

    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM saved_data").fetchone() == ("keep me",)


def test_startup_logs_when_unchanged_backup_is_skipped(tmp_path, monkeypatch, caplog):
    root = tmp_path / "workspace"
    database = root / "data" / "app.db"
    _create_nonempty_database(database)
    monkeypatch.setattr(main, "ROOT", root)
    monkeypatch.setattr(main, "DB_PATH", database)

    with caplog.at_level("INFO", logger="uvicorn.error"):
        main._backup_existing_database_before_schema_changes()
        main._backup_existing_database_before_schema_changes()

    assert len(list((root / "data" / "backups").glob("*.zip"))) == 1
    assert "Automatic backup skipped: data is unchanged" in caplog.text
