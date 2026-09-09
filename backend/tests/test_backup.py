"""Focused safety tests for SQLite/artifact backups."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import zipfile

import pytest

from backend.app.services import backup as backup_service
from backend.app.services.backup import (
    BackupError,
    create_backup,
    list_backups,
    read_backup_manifest,
    restore_backup,
)


def _database(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS records (value TEXT NOT NULL)")
        connection.execute("DELETE FROM records")
        connection.execute("INSERT INTO records(value) VALUES (?)", (value,))


def _database_value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM records").fetchone()[0]


def test_backup_is_versioned_and_contains_checksums_for_database_and_artifacts(tmp_path: Path):
    database = tmp_path / "data" / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    database.parent.mkdir()
    generated.mkdir()
    _database(database, "before")
    artifact = generated / "applications" / "7" / "revision-001" / "resume.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"pdf bytes")

    first = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    second = create_backup(database_path=database, generated_root=generated, backup_root=backups)

    assert first["backup_version"] == 1
    assert second["backup_version"] == 2
    assert first["filename"] != second["filename"]
    manifest = read_backup_manifest(Path(first["path"]))
    assert manifest["database"]["path"] == "database/app.db"
    assert manifest["artifacts"]["count"] == 1
    assert {entry["path"] for entry in manifest["files"]} == {
        "database/app.db",
        "artifacts/applications/7/revision-001/resume.pdf",
    }
    listed = list_backups(backup_root=backups)
    assert [record["backup_version"] for record in listed] == [2, 1]
    assert all(record["schema_version"] == "1" for record in listed)

    with zipfile.ZipFile(first["path"]) as archive:
        assert _database_value_from_bytes(archive.read("database/app.db")) == "before"
        assert archive.read("artifacts/applications/7/revision-001/resume.pdf") == b"pdf bytes"


def _database_value_from_bytes(value: bytes) -> str:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db") as temporary:
        temporary.write(value)
        temporary.flush()
        return _database_value(Path(temporary.name))


def test_backup_rejects_symlinks_and_tampered_members(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (generated / "escape.txt").symlink_to(outside)

    with pytest.raises(BackupError, match="symbolic links"):
        create_backup(database_path=database, generated_root=generated, backup_root=backups)

    (generated / "escape.txt").unlink()
    result = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    tampered = backups / "tampered.zip"
    with zipfile.ZipFile(result["path"]) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "database/app.db":
                payload += b"tampered"
            target.writestr(info, payload)
    with pytest.raises(BackupError, match="checksum mismatch"):
        read_backup_manifest(tampered)


def test_manifest_schema_and_malformed_entries_are_rejected(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    result = create_backup(
        database_path=database,
        generated_root=generated,
        backup_root=backups,
        schema_version="future-schema",
    )
    with pytest.raises(BackupError, match="schema version"):
        read_backup_manifest(result["path"])

    def rewrite_manifest(name: str, mutate):
        rewritten = backups / name
        with zipfile.ZipFile(result["path"]) as source, zipfile.ZipFile(rewritten, "w") as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == "manifest.json":
                    import json

                    manifest = json.loads(payload)
                    mutate(manifest)
                    payload = json.dumps(manifest).encode()
                target.writestr(info, payload)
        return rewritten

    invalid_timestamp = rewrite_manifest("invalid-timestamp.zip", lambda manifest: manifest.update({"schema_version": "1", "created_at": "not-a-date"}))
    with pytest.raises(BackupError, match="creation timestamp"):
        read_backup_manifest(invalid_timestamp)

    invalid_database_size = rewrite_manifest(
        "invalid-database-size.zip",
        lambda manifest: manifest.update({"schema_version": "1", "database": {**manifest["database"], "size": 0}}),
    )
    with pytest.raises(BackupError, match="database size"):
        read_backup_manifest(invalid_database_size)

    malformed = backups / "malformed.zip"
    with zipfile.ZipFile(result["path"]) as source, zipfile.ZipFile(malformed, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "manifest.json":
                import json

                manifest = json.loads(payload)
                manifest["schema_version"] = "1"
                manifest["files"] = [{"path": ["not", "a", "string"], "size": 0, "sha256": "0" * 64}]
                payload = json.dumps(manifest).encode()
            target.writestr(info, payload)
    with pytest.raises(BackupError, match="non-string file path"):
        read_backup_manifest(malformed)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(backup_service, "MAX_TOTAL_MEMBER_BYTES", 1)
        with pytest.raises(BackupError, match="aggregate size"):
            read_backup_manifest(result["path"], expected_schema_version="future-schema")
        monkeypatch.setattr(backup_service, "MAX_TOTAL_MEMBER_BYTES", 1024 * 1024 * 1024)
        monkeypatch.setattr(backup_service, "MAX_TOTAL_COMPRESSED_BYTES", 1)
        with pytest.raises(BackupError, match="compressed archive"):
            read_backup_manifest(result["path"], expected_schema_version="future-schema")
    finally:
        monkeypatch.undo()


def test_backup_version_allocation_is_serialized(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")

    def make_backup():
        return create_backup(database_path=database, generated_root=generated, backup_root=backups)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: make_backup(), range(4)))
    assert sorted(result["backup_version"] for result in results) == [1, 2, 3, 4]


def test_restore_uses_staging_and_rolls_back_when_post_restore_fails(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "original")
    (generated / "resume.pdf").write_text("original artifact")
    result = create_backup(database_path=database, generated_root=generated, backup_root=backups)

    _database(database, "changed")
    (generated / "resume.pdf").write_text("changed artifact")
    restored = restore_backup(
        archive_path=result["path"],
        database_path=database,
        generated_root=generated,
        backup_root=backups,
        offline=True,
    )
    assert restored["restored"] is True
    assert _database_value(database) == "original"
    assert (generated / "resume.pdf").read_text() == "original artifact"

    _database(database, "changed again")
    (generated / "resume.pdf").write_text("changed again")
    with pytest.raises(BackupError, match="restore failed"):
        restore_backup(
            archive_path=result["path"],
            database_path=database,
            generated_root=generated,
            backup_root=backups,
            offline=True,
            post_restore=lambda: (_ for _ in ()).throw(RuntimeError("migration failure")),
        )
    assert _database_value(database) == "changed again"
    assert (generated / "resume.pdf").read_text() == "changed again"

    original_read_member = backup_service._read_member

    def tamper_staged_member(archive, info, destination):
        original_read_member(archive, info, destination)
        if info.filename == "database/app.db":
            destination.write_bytes(destination.read_bytes() + b"tampered")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(backup_service, "_read_member", tamper_staged_member)
        with pytest.raises(BackupError, match="staged backup checksum"):
            restore_backup(
                archive_path=result["path"],
                database_path=database,
                generated_root=generated,
                backup_root=backups,
                offline=True,
            )
    finally:
        monkeypatch.undo()
    assert _database_value(database) == "changed again"
    assert (generated / "resume.pdf").read_text() == "changed again"


def test_restore_requires_explicit_offline_mode(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    result = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    with pytest.raises(BackupError, match="offline-only"):
        restore_backup(
            archive_path=result["path"],
            database_path=database,
            generated_root=generated,
            backup_root=backups,
        )
