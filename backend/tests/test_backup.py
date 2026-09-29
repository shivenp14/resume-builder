"""Focused safety tests for SQLite/artifact backups."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import time
import zipfile

import pytest

from backend.app.services import backup as backup_service
from backend.app.services.backup import (
    BackupError,
    create_backup,
    database_startup_lock,
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


def _create_backup_in_process(arguments: tuple[str, str, str]) -> dict:
    database, generated, backups = arguments
    return create_backup(database_path=database, generated_root=generated, backup_root=backups)


def _simulate_startup_process(arguments: tuple[str, str, str]) -> int:
    database, generated, backups = map(Path, arguments)
    with database_startup_lock(backups.parent.parent):
        result = create_backup(database_path=database, generated_root=generated, backup_root=backups)
        # Keep the startup critical section occupied after publishing the
        # pre-migration snapshot so competing processes cannot prune it.
        time.sleep(0.03)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE migration_state SET value = value + 1")
    return result["backup_version"]


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


def test_automatic_backup_skips_identical_sqlite_and_artifacts(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "same")
    artifact = generated / "resume.pdf"
    artifact.write_bytes(b"same artifact")

    first = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                          skip_if_unchanged=True)
    second = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                           skip_if_unchanged=True)

    assert first["backup_version"] == 1
    assert second["skipped"] is True
    assert second["reason"] == "unchanged"
    assert second["path"] == first["path"]
    assert second["backup_version"] == 1
    assert len(list(backups.glob("*.zip"))) == 1


def test_automatic_backup_detects_database_and_artifact_changes(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "before")
    artifact = generated / "resume.pdf"
    artifact.write_bytes(b"before")

    first = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                          skip_if_unchanged=True)
    _database(database, "database changed")
    second = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                           skip_if_unchanged=True)
    artifact.write_bytes(b"artifact changed")
    third = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                          skip_if_unchanged=True)

    assert [first["backup_version"], second["backup_version"], third["backup_version"]] == [1, 2, 3]
    assert not second.get("skipped")
    assert not third.get("skipped")


def test_manual_backup_forces_archive_and_invalid_previous_is_not_comparable(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "same")
    first = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                          skip_if_unchanged=True)

    manual = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    assert manual["backup_version"] == 2
    assert not manual.get("skipped")

    (backups / manual["filename"]).write_bytes(b"corrupted archive")
    (backups / first["filename"]).write_bytes(b"corrupted archive")
    after_corruption = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                                     skip_if_unchanged=True)
    assert after_corruption["backup_version"] == 3
    assert not after_corruption.get("skipped")


def test_incompatible_newest_archive_forces_compatible_backup_before_retention(tmp_path: Path, monkeypatch):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "same")

    prune = backup_service._prune_backups
    monkeypatch.setattr(backup_service, "_prune_backups", lambda _root: {})
    compatible = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                               schema_version="1")
    incompatible = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                                 schema_version="future-schema")
    monkeypatch.setattr(backup_service, "MAX_BACKUP_ARCHIVES", 1)
    monkeypatch.setattr(backup_service, "_prune_backups", prune)

    result = create_backup(database_path=database, generated_root=generated, backup_root=backups,
                           schema_version="1", skip_if_unchanged=True)

    assert not result.get("skipped")
    assert result["backup_version"] == incompatible["backup_version"] + 1
    assert read_backup_manifest(result["path"], expected_schema_version="1")["schema_version"] == "1"
    retained = list_backups(backup_root=backups)
    assert len(retained) == 1
    assert retained[0]["filename"] == result["filename"]
    assert not Path(compatible["path"]).exists()


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

    with pytest.raises(BackupError, match="regular files|symbolic links"):
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


def test_backup_creation_and_retention_are_serialized_across_processes(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")

    arguments = (str(database), str(generated), str(backups))
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_create_backup_in_process, [arguments] * 12))

    versions = sorted(result["backup_version"] for result in results)
    assert versions == list(range(1, 13))
    retained = list_backups(backup_root=backups)
    assert [item["backup_version"] for item in retained] == list(range(12, 2, -1))
    assert all((backups / item["filename"]).is_file() for item in retained)
    assert len(retained) <= backup_service.MAX_BACKUP_ARCHIVES
    assert sum(item["size"] for item in retained) <= backup_service.MAX_BACKUP_TOTAL_BYTES
    assert (backups / backup_service.BACKUP_LOCK_FILENAME).is_file()


def test_startup_lock_preserves_latest_pre_migration_snapshot_across_processes(tmp_path: Path):
    database = tmp_path / "data" / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "data" / "backups"
    database.parent.mkdir()
    generated.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE migration_state (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO migration_state VALUES (0)")

    arguments = (str(database), str(generated), str(backups))
    with ProcessPoolExecutor(max_workers=4) as executor:
        versions = sorted(executor.map(_simulate_startup_process, [arguments] * 12))

    assert versions == list(range(1, 13))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM migration_state").fetchone() == (12,)
    retained = list_backups(backup_root=backups)
    newest = retained[0]
    assert newest["backup_version"] == 12
    with zipfile.ZipFile(backups / newest["filename"]) as archive:
        snapshot = tmp_path / "latest-pre-migration.db"
        snapshot.write_bytes(archive.read("database/app.db"))
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT value FROM migration_state").fetchone() == (11,)


def test_backup_retention_enforces_count_and_preserves_newest_valid_archive(tmp_path: Path):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    created = [
        create_backup(database_path=database, generated_root=generated, backup_root=backups)
        for _ in range(12)
    ]

    retained = list_backups(backup_root=backups)
    assert len(retained) == backup_service.MAX_BACKUP_ARCHIVES
    assert retained[0]["backup_version"] == created[-1]["backup_version"]
    assert [item["backup_version"] for item in retained] == list(range(12, 2, -1))
    assert all(read_backup_manifest(backups / item["filename"]) for item in retained)


def test_backup_retention_enforces_size_cap_and_keeps_oversized_newest(tmp_path: Path, monkeypatch):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    prune = backup_service._prune_backups
    monkeypatch.setattr(backup_service, "_prune_backups", lambda _root: {})
    first = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    second = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    third = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    monkeypatch.setattr(backup_service, "_prune_backups", prune)
    monkeypatch.setattr(backup_service, "MAX_BACKUP_TOTAL_BYTES", second["size"] + third["size"])

    result = prune(backups)
    retained = list_backups(backup_root=backups)
    assert len(retained) == 2
    assert [item["backup_version"] for item in retained] == [3, 2]
    assert sum(item["size"] for item in retained) <= backup_service.MAX_BACKUP_TOTAL_BYTES
    assert read_backup_manifest(third["path"])["backup_version"] == 3
    assert result["retention_count"] == 2

    monkeypatch.setattr(backup_service, "MAX_BACKUP_TOTAL_BYTES", 1)
    monkeypatch.setattr(backup_service, "_prune_backups", lambda _root: {})
    oversized = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    monkeypatch.setattr(backup_service, "_prune_backups", prune)
    prune(backups)
    retained = list_backups(backup_root=backups)
    assert len(retained) == 1
    assert retained[0]["backup_version"] == oversized["backup_version"]
    assert Path(oversized["path"]).stat().st_size > backup_service.MAX_BACKUP_TOTAL_BYTES


def test_retention_leaves_unrecognized_malformed_and_symlink_files_untouched(tmp_path: Path, monkeypatch):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")
    first = create_backup(database_path=database, generated_root=generated, backup_root=backups)
    unknown = backups / "notes.zip"
    unknown.write_bytes(b"user file")
    malformed = backups / "resume-backup-v0000-20260928T120000Z-0123456789.zip"
    malformed.write_bytes(b"not a backup")
    link = backups / "resume-backup-v0001-20260928T120000Z-0123456789.zip"
    link.symlink_to(Path(first["path"]))
    monkeypatch.setattr(backup_service, "MAX_BACKUP_ARCHIVES", 1)
    result = create_backup(database_path=database, generated_root=generated, backup_root=backups)

    assert unknown.read_bytes() == b"user file"
    assert malformed.read_bytes() == b"not a backup"
    assert link.is_symlink()
    assert Path(result["path"]).exists()


def test_failed_backup_never_runs_retention(tmp_path: Path, monkeypatch):
    database = tmp_path / "missing.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    existing = backups / "unrelated.zip"
    backups.mkdir()
    existing.write_bytes(b"preserve")
    calls = []
    monkeypatch.setattr(backup_service, "_prune_backups", lambda _root: calls.append("prune"))

    with pytest.raises(BackupError, match="database does not exist"):
        create_backup(database_path=database, generated_root=generated, backup_root=backups)
    assert calls == []
    assert existing.read_bytes() == b"preserve"


def test_retention_failure_keeps_newly_published_archive(tmp_path: Path, monkeypatch):
    database = tmp_path / "app.db"
    generated = tmp_path / "generated"
    backups = tmp_path / "backups"
    generated.mkdir()
    _database(database, "safe")

    def fail_retention(_root):
        raise BackupError("retention unavailable")

    monkeypatch.setattr(backup_service, "_prune_backups", fail_retention)
    with pytest.raises(BackupError, match="retention unavailable"):
        create_backup(database_path=database, generated_root=generated, backup_root=backups)
    published = list(backups.glob("resume-backup-v*.zip"))
    assert len(published) == 1
    assert read_backup_manifest(published[0])["backup_version"] == 1


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
