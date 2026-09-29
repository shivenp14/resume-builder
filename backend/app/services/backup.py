"""Safe, versioned backups for the local Resume Builder store.

Backups are deliberately self-contained ZIP files.  A backup contains a
SQLite online-backup snapshot (rather than a byte copy that may be mid-WAL),
the generated artifact tree, and a manifest with checksums for every payload.
The restore path validates the complete archive in a private staging
directory before replacing any live file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
import fcntl
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
from typing import Any, Callable
import uuid
import zipfile


BACKUP_FORMAT = "resume-builder-backup"
BACKUP_FORMAT_VERSION = 1
BACKUP_SCHEMA_VERSION = "1"
BACKUP_PREFIX = "resume-backup-v"
BACKUP_LOCK_FILENAME = ".resume-backup.lock"
DATABASE_STARTUP_LOCK_FILENAME = ".database-startup.lock"
MANIFEST_NAME = "manifest.json"
DATABASE_MEMBER = "database/app.db"
ARTIFACT_PREFIX = "artifacts/"
MAX_ARCHIVE_ENTRIES = 100_000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_COMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_BACKUP_ARCHIVES = 10
MAX_BACKUP_TOTAL_BYTES = 512 * 1024 * 1024

_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.zip$")
_MANAGED_BACKUP_FILENAME = re.compile(
    rf"^{re.escape(BACKUP_PREFIX)}(\d+)-\d{{8}}T\d{{6}}Z-[0-9a-f]{{10}}\.zip$"
)
_BACKUP_CREATION_LOCK = threading.Lock()


class BackupError(RuntimeError):
    """An archive could not be safely created, inspected, or restored."""


@contextmanager
def _backup_process_lock(backup_root: Path):
    """Serialize backup allocation/publication/retention across processes."""
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"could not prepare backup directory: {exc}") from exc
    if backup_root.is_symlink() or not backup_root.is_dir():
        raise BackupError("backup root must be a real directory")

    lock_path = backup_root / BACKUP_LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BackupError(f"could not open backup coordination lock: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("backup coordination lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise BackupError(f"could not acquire backup coordination lock: {exc}") from exc
        # Recheck after waiting: the storage directory must not have become a
        # symlink while another process held the lock.
        if backup_root.is_symlink() or not backup_root.is_dir():
            raise BackupError("backup root must be a real directory")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            # Closing the descriptor also releases the lock.
            pass
        os.close(descriptor)


@contextmanager
def database_startup_lock(workspace_root: str | Path):
    """Serialize startup migrations with backups from every app process.

    Manual backup callers acquire this same lock before the backup service's
    own lock, so retention cannot prune a startup snapshot mid-migration.
    """
    data_root = Path(workspace_root) / "data"
    try:
        data_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"could not prepare database startup lock directory: {exc}") from exc
    if data_root.is_symlink() or not data_root.is_dir():
        raise BackupError("database startup lock directory must be a real directory")

    lock_path = data_root / DATABASE_STARTUP_LOCK_FILENAME
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BackupError(f"could not open database startup lock: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BackupError("database startup lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise BackupError(f"could not acquire database startup lock: {exc}") from exc
        if data_root.is_symlink() or not data_root.is_dir():
            raise BackupError("database startup lock directory must be a real directory")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, candidate: Path) -> Path:
    """Return a candidate's relative path, rejecting escapes and symlinks."""
    root = root.resolve()
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise BackupError("path is outside the backup scope") from exc
    if candidate.is_symlink():
        raise BackupError("symbolic links are not allowed in backups")
    return relative


def _assert_safe_member(name: str) -> None:
    path = Path(name)
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise BackupError("archive contains an unsafe path")
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError("archive contains an unsafe path")


def _assert_sqlite_integrity(path: Path) -> None:
    try:
        with sqlite3.connect(path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise BackupError("SQLite integrity check failed")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise BackupError("SQLite foreign-key integrity check failed")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"SQLite snapshot is invalid: {exc}") from exc


def _copy_sqlite_snapshot(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file() or source_path.is_symlink():
        raise BackupError("SQLite database does not exist or is a symbolic link")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
            source.backup(destination)
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"could not snapshot SQLite database: {exc}") from exc
    _assert_sqlite_integrity(destination_path)


def _artifact_files(generated_root: Path) -> list[tuple[Path, str]]:
    if not generated_root.exists():
        return []
    if generated_root.is_symlink() or not generated_root.is_dir():
        raise BackupError("generated artifact root must be a real directory")
    files: list[tuple[Path, str]] = []
    for directory, dirnames, filenames in os.walk(generated_root, followlinks=False):
        directory_path = Path(directory)
        for dirname in dirnames:
            candidate = directory_path / dirname
            if candidate.is_symlink():
                raise BackupError("symbolic links are not allowed in generated artifacts")
        for filename in filenames:
            candidate = directory_path / filename
            if candidate.is_symlink() or not candidate.is_file():
                raise BackupError("generated artifacts must be regular files")
            relative = _safe_relative(generated_root, candidate)
            files.append((candidate, ARTIFACT_PREFIX + relative.as_posix()))
    return sorted(files, key=lambda item: item[1])


def _next_backup_version(backup_root: Path) -> int:
    versions: list[int] = []
    for path in backup_root.glob(f"{BACKUP_PREFIX}*.zip"):
        match = re.match(rf"{re.escape(BACKUP_PREFIX)}(\d+)-", path.name)
        if match:
            versions.append(int(match.group(1)))
    return max(versions, default=0) + 1


def _prune_backups(backup_root: Path) -> dict[str, int]:
    """Keep the newest valid managed backups within count and size limits.

    Unknown files, symlinks, and malformed archives are left untouched. The
    caller holds the thread and filesystem locks and invokes this only after
    the new archive has been validated and published.
    """
    if backup_root.is_symlink() or not backup_root.is_dir():
        raise BackupError("backup root must be a real directory")

    candidates: list[dict[str, Any]] = []
    try:
        children = list(backup_root.iterdir())
    except OSError as exc:
        raise BackupError(f"could not inspect backups for retention: {exc}") from exc

    for path in children:
        match = _MANAGED_BACKUP_FILENAME.fullmatch(path.name)
        if not match:
            continue
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                continue
            # Retention recognizes structurally valid archives across schema
            # versions, even when this app version cannot currently restore
            # one of those snapshots.
            manifest = read_backup_manifest(path, expected_schema_version=None)
            version = int(match.group(1))
            if manifest["backup_version"] != version:
                continue
            candidates.append({
                "path": path,
                "version": version,
                "created_at": datetime.fromisoformat(manifest["created_at"]),
                "size": metadata.st_size,
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            })
        except BackupError:
            # Keep malformed or otherwise unrecognized archives for manual
            # inspection; they do not count toward managed retention limits.
            continue
        except OSError as exc:
            raise BackupError(f"could not inspect backup {path.name}: {exc}") from exc

    candidates.sort(
        key=lambda item: (item["version"], item["created_at"], item["path"].name),
        reverse=True,
    )
    keep: list[dict[str, Any]] = []
    kept_bytes = 0
    for item in candidates:
        if len(keep) >= MAX_BACKUP_ARCHIVES:
            break
        # Always retain the newest valid archive, even if it exceeds the
        # aggregate limit by itself. Older archives are then removed.
        if keep and kept_bytes + item["size"] > MAX_BACKUP_TOTAL_BYTES:
            break
        keep.append(item)
        kept_bytes += item["size"]

    keep_paths = {item["path"] for item in keep}
    removed = 0
    for item in candidates:
        path = item["path"]
        if path in keep_paths:
            continue
        try:
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode)
                    or (current.st_dev, current.st_ino) != (item["device"], item["inode"])):
                raise BackupError(f"backup changed during retention: {path.name}")
            path.unlink()
            removed += 1
        except OSError as exc:
            # Stop on the first failure. The newly published archive remains
            # untouched and startup can fail closed without further deletion.
            raise BackupError(f"could not prune backup {path.name}: {exc}") from exc

    return {"retention_removed": removed, "retention_count": len(keep), "retention_bytes": kept_bytes}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=2).encode("utf-8")


def _newest_valid_managed_backup(backup_root: Path) -> tuple[Path, dict[str, Any]] | None:
    """Return the newest structurally valid managed archive across schemas."""
    candidates: list[tuple[int, Path]] = []
    for path in backup_root.iterdir():
        match = _MANAGED_BACKUP_FILENAME.fullmatch(path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    for version, path in candidates:
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                continue
            manifest = read_backup_manifest(path, expected_schema_version=None)
            if manifest["backup_version"] == version:
                return path, manifest
        except (BackupError, OSError):
            # An invalid or incompatible archive is not a trustworthy baseline.
            continue
    return None


def _same_snapshot(manifest: dict[str, Any], files: list[dict[str, Any]]) -> bool:
    def inventory(entries: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
        return sorted((entry["path"], entry["size"], entry["sha256"]) for entry in entries)

    return inventory(manifest["files"]) == inventory(files)


def create_backup(*, database_path: str | Path, generated_root: str | Path, backup_root: str | Path,
                  schema_version: str = BACKUP_SCHEMA_VERSION,
                  skip_if_unchanged: bool = False) -> dict[str, Any]:
    """Create and atomically publish a validated, versioned backup archive.

    Version allocation and publication are serialized within this process so
    concurrent requests cannot select the same sequence number.
    """
    with _BACKUP_CREATION_LOCK:
        with _backup_process_lock(Path(backup_root)):
            return _create_backup(
                database_path=database_path,
                generated_root=generated_root,
                backup_root=backup_root,
                schema_version=schema_version,
                skip_if_unchanged=skip_if_unchanged,
            )


def _create_backup(*, database_path: str | Path, generated_root: str | Path, backup_root: str | Path,
                   schema_version: str, skip_if_unchanged: bool = False) -> dict[str, Any]:
    """Internal implementation; callers should use :func:`create_backup`."""
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise BackupError("schema version must be a non-empty string")
    database = Path(database_path)
    generated = Path(generated_root)
    backups = Path(backup_root)
    backups.mkdir(parents=True, exist_ok=True)
    if backups.is_symlink():
        raise BackupError("backup root must not be a symbolic link")

    stage_dir = Path(tempfile.mkdtemp(prefix=".backup-", dir=backups))
    snapshot_path = stage_dir / "app.db"
    try:
        _copy_sqlite_snapshot(database, snapshot_path)
        files: list[dict[str, Any]] = []
        database_digest = _sha256(snapshot_path)
        files.append({"path": DATABASE_MEMBER, "size": snapshot_path.stat().st_size, "sha256": database_digest})
        artifact_files = _artifact_files(generated)
        staged_artifacts: list[tuple[Path, str]] = []
        for source, member in artifact_files:
            staged = stage_dir / member
            staged.parent.mkdir(parents=True, exist_ok=True)
            # Copy each artifact exactly once into the staging tree. Hashing
            # and ZIP compression then both read that immutable copy, so an
            # external renderer cannot change the archive mid-export.
            before = source.stat()
            shutil.copy2(source, staged)
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise BackupError(f"artifact changed while being staged: {member}")
            staged_artifacts.append((staged, member))
            files.append({"path": member, "size": staged.stat().st_size, "sha256": _sha256(staged)})

        if skip_if_unchanged:
            previous = _newest_valid_managed_backup(backups)
            if (previous
                    and previous[1]["schema_version"] == schema_version
                    and _same_snapshot(previous[1], files)):
                retention = _prune_backups(backups)
                return {
                    "skipped": True,
                    "reason": "unchanged",
                    "filename": previous[0].name,
                    "path": str(previous[0]),
                    "backup_version": previous[1]["backup_version"],
                    "format_version": previous[1]["format_version"],
                    "schema_version": previous[1]["schema_version"],
                    "created_at": previous[1]["created_at"],
                    "size": previous[0].stat().st_size,
                    "artifact_count": len(artifact_files),
                    **retention,
                }

        version = _next_backup_version(backups)
        created_at = _utc_now().isoformat()
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        filename = f"{BACKUP_PREFIX}{version:04d}-{stamp}-{uuid.uuid4().hex[:10]}.zip"
        final_path = backups / filename
        stage_archive = stage_dir / filename
        manifest = {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_FORMAT_VERSION,
            "schema_version": schema_version,
            "backup_version": version,
            "created_at": created_at,
            "database": {"path": DATABASE_MEMBER, "size": snapshot_path.stat().st_size, "sha256": database_digest},
            "artifacts": {"root": ARTIFACT_PREFIX.rstrip("/"), "count": len(artifact_files)},
            "files": files,
        }
        with zipfile.ZipFile(stage_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST_NAME, _json_bytes(manifest))
            archive.write(snapshot_path, DATABASE_MEMBER)
            for source, member in staged_artifacts:
                archive.write(source, member)
        # Re-open and verify the archive before it becomes visible to callers.
        read_backup_manifest(stage_archive, expected_schema_version=schema_version)
        os.replace(stage_archive, final_path)
        result = {
            "filename": filename,
            "path": str(final_path),
            "backup_version": version,
            "format_version": BACKUP_FORMAT_VERSION,
            "schema_version": schema_version,
            "created_at": created_at,
            "size": final_path.stat().st_size,
            "sha256": _sha256(final_path),
            "artifact_count": len(artifact_files),
        }
        # Prune only after the validated archive is durably visible. A
        # retention error propagates to startup; the new recovery copy remains.
        result.update(_prune_backups(backups))
        return result
    except (OSError, zipfile.BadZipFile) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"could not create backup: {exc}") from exc
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination: Path) -> None:
    if info.file_size > MAX_MEMBER_BYTES:
        raise BackupError("archive member exceeds the maximum permitted size")
    # Reject Unix symlink entries even though the exporter never creates them.
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise BackupError("symbolic links are not allowed in backups")
    _assert_safe_member(info.filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info, "r") as source, destination.open("wb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    if destination.stat().st_size != info.file_size:
        raise BackupError("archive member size changed while extracting")


@contextmanager
def _archive_context(archive_path: str | Path | zipfile.ZipFile):
    """Yield a ZIP reader, preserving an already-open reader when supplied."""
    if isinstance(archive_path, zipfile.ZipFile):
        yield archive_path
    else:
        with zipfile.ZipFile(archive_path, "r") as archive:
            yield archive


def read_backup_manifest(archive_path: str | Path | zipfile.ZipFile, *, expected_schema_version: str | None = BACKUP_SCHEMA_VERSION) -> dict[str, Any]:
    """Validate an archive and return its manifest without modifying live data."""
    if not isinstance(archive_path, zipfile.ZipFile):
        archive_path = Path(archive_path)
        if not archive_path.is_file() or archive_path.is_symlink():
            raise BackupError("backup archive does not exist")
        try:
            if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise BackupError("backup archive exceeds the maximum permitted size")
        except OSError as exc:
            raise BackupError(f"could not inspect backup archive: {exc}") from exc
    try:
        with _archive_context(archive_path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise BackupError("archive contains too many files")
            total_uncompressed = sum(info.file_size for info in infos)
            if total_uncompressed > MAX_TOTAL_MEMBER_BYTES:
                raise BackupError("archive members exceed the aggregate size limit")
            total_compressed = sum(info.compress_size for info in infos)
            if total_compressed > MAX_TOTAL_COMPRESSED_BYTES:
                raise BackupError("compressed archive members exceed the aggregate size limit")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise BackupError("archive contains duplicate paths")
            for info in infos:
                _assert_safe_member(info.filename)
                if info.file_size > MAX_MEMBER_BYTES:
                    raise BackupError("archive member exceeds the maximum permitted size")
                if info.filename == MANIFEST_NAME and info.file_size > MAX_MANIFEST_BYTES:
                    raise BackupError("backup manifest exceeds the maximum permitted size")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise BackupError("symbolic links are not allowed in backups")
            if MANIFEST_NAME not in names or DATABASE_MEMBER not in names:
                raise BackupError("backup manifest or database is missing")
            try:
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError("backup manifest is not valid JSON") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
                raise BackupError("unsupported backup format")
            if (isinstance(manifest.get("format_version"), bool)
                    or manifest.get("format_version") != BACKUP_FORMAT_VERSION):
                raise BackupError("unsupported backup format version")
            schema_version = manifest.get("schema_version")
            if not isinstance(schema_version, str) or not schema_version.strip():
                raise BackupError("backup manifest has no schema version")
            if expected_schema_version is not None and schema_version != expected_schema_version:
                raise BackupError("backup schema version is incompatible with this application")
            if (not isinstance(manifest.get("backup_version"), int)
                    or isinstance(manifest.get("backup_version"), bool)
                    or manifest["backup_version"] < 1):
                raise BackupError("backup manifest has an invalid version")
            if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
                raise BackupError("backup manifest has no creation timestamp")
            try:
                created_at = datetime.fromisoformat(manifest["created_at"])
            except (TypeError, ValueError) as exc:
                raise BackupError("backup manifest has an invalid creation timestamp") from exc
            if created_at.tzinfo is None:
                raise BackupError("backup manifest timestamp must include a timezone")
            database_metadata = manifest.get("database")
            if not isinstance(database_metadata, dict) or database_metadata.get("path") != DATABASE_MEMBER:
                raise BackupError("backup manifest has invalid database metadata")
            files = manifest.get("files")
            if not isinstance(files, list) or not files or len(files) > MAX_ARCHIVE_ENTRIES:
                raise BackupError("backup manifest has no file inventory")
            expected: dict[str, dict[str, Any]] = {}
            for entry in files:
                if not isinstance(entry, dict):
                    raise BackupError("backup manifest contains a malformed file entry")
                member = entry.get("path")
                if not isinstance(member, str):
                    raise BackupError("backup manifest contains a non-string file path")
                if member in expected:
                    raise BackupError("backup manifest contains duplicate file paths")
                expected[member] = entry
            if DATABASE_MEMBER not in expected:
                raise BackupError("backup manifest has an invalid file inventory")
            database_entry = expected[DATABASE_MEMBER]
            database_size = database_metadata.get("size")
            if (not isinstance(database_size, int)
                    or isinstance(database_size, bool)
                    or database_size < 0
                    or database_size != database_entry.get("size")):
                raise BackupError("database size is inconsistent in manifest")
            allowed = {MANIFEST_NAME, *expected}
            if set(names) != allowed:
                raise BackupError("archive contents do not match its manifest")
            if expected[DATABASE_MEMBER].get("sha256") != manifest.get("database", {}).get("sha256"):
                raise BackupError("database checksum is inconsistent in manifest")
            artifact_entries = [entry for entry in files if entry["path"].startswith(ARTIFACT_PREFIX)]
            artifacts_metadata = manifest.get("artifacts")
            if not isinstance(artifacts_metadata, dict) or artifacts_metadata.get("root") != ARTIFACT_PREFIX.rstrip("/"):
                raise BackupError("backup manifest has invalid artifact metadata")
            if (not isinstance(artifacts_metadata.get("count"), int)
                    or isinstance(artifacts_metadata.get("count"), bool)
                    or artifacts_metadata.get("count") < 0
                    or artifacts_metadata.get("count") != len(artifact_entries)):
                raise BackupError("backup artifact count does not match manifest")
            for entry in files:
                member = entry.get("path")
                if not isinstance(member, str) or member == MANIFEST_NAME:
                    raise BackupError("backup manifest contains an unsafe file path")
                if not (member == DATABASE_MEMBER or member.startswith(ARTIFACT_PREFIX)):
                    raise BackupError("backup manifest contains an unsafe file path")
                _assert_safe_member(member)
                if (not isinstance(entry.get("size"), int)
                        or isinstance(entry.get("size"), bool)
                        or entry["size"] < 0):
                    raise BackupError("backup manifest contains an invalid file size")
                if entry["size"] > MAX_MEMBER_BYTES:
                    raise BackupError("backup manifest member exceeds the maximum permitted size")
                if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    raise BackupError("backup manifest contains an invalid checksum")
                info = archive.getinfo(member)
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "r") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                        size += len(chunk)
                if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
                    raise BackupError(f"checksum mismatch for {member}")
            return manifest
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError, IndexError,
            NotImplementedError, RuntimeError, RecursionError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"could not read backup archive: {exc}") from exc


def list_backups(*, backup_root: str | Path) -> list[dict[str, Any]]:
    root = Path(backup_root)
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise BackupError("backup root must be a real directory")
    records: list[dict[str, Any]] = []
    for archive in sorted(root.iterdir(), key=lambda path: path.name, reverse=True):
        if not archive.is_file() or archive.is_symlink() or not _SAFE_FILENAME.fullmatch(archive.name):
            continue
        try:
            manifest = read_backup_manifest(archive)
        except BackupError:
            # A partially copied or manually corrupted archive is never
            # presented as a usable backup.
            continue
        records.append({
            "filename": archive.name,
            "backup_version": manifest["backup_version"],
            "format_version": manifest["format_version"],
            "schema_version": manifest["schema_version"],
            "created_at": manifest["created_at"],
            "size": archive.stat().st_size,
            "sha256": _sha256(archive),
            "artifact_count": manifest.get("artifacts", {}).get("count", 0),
        })
    return records


def _validated_archive_filename(filename: str) -> str:
    if not isinstance(filename, str) or not _SAFE_FILENAME.fullmatch(filename):
        raise BackupError("invalid backup filename")
    if Path(filename).name != filename:
        raise BackupError("invalid backup filename")
    return filename


@contextmanager
def _open_archive_nofollow(path: Path):
    """Open a backup by descriptor so a later pathname swap cannot replace it."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError(f"could not open backup archive safely: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("backup archive must be a regular file")
        if metadata.st_size > MAX_ARCHIVE_BYTES:
            raise BackupError("backup archive exceeds the maximum permitted size")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _validate_staged_payload(stage_dir: Path, manifest: dict[str, Any]) -> None:
    """Re-check extracted bytes before any live file is replaced."""
    total = 0
    for entry in manifest["files"]:
        member = entry["path"]
        payload = stage_dir / member
        if payload.is_symlink() or not payload.is_file():
            raise BackupError(f"staged backup member is not a regular file: {member}")
        size = payload.stat().st_size
        total += size
        if size != entry["size"] or _sha256(payload) != entry["sha256"]:
            raise BackupError(f"staged backup checksum mismatch for {member}")
    if total > MAX_TOTAL_MEMBER_BYTES:
        raise BackupError("staged backup members exceed the aggregate size limit")


def restore_backup(
    *,
    archive_path: str | Path,
    database_path: str | Path,
    generated_root: str | Path,
    backup_root: str | Path,
    offline: bool = False,
    schema_version: str = BACKUP_SCHEMA_VERSION,
    post_restore: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Restore a validated backup using staged extraction and rollback.

    Restore is intentionally offline-only. The caller must stop the API and
    dispose all SQLAlchemy connections before setting ``offline=True``;
    replacing a live SQLite file behind pooled connections is unsafe.
    """
    if not offline:
        raise BackupError("restore is offline-only; stop the API and pass offline=True")
    archive = Path(archive_path)
    database = Path(database_path)
    generated = Path(generated_root)
    backups = Path(backup_root)
    try:
        _validated_archive_filename(archive.name)
        _safe_relative(backups, archive)
        if backups.is_symlink() or not backups.is_dir():
            raise BackupError("backup root must be a real directory")
    except (BackupError, ValueError) as exc:
        raise BackupError("backup archive must be a file in the backup directory") from exc
    stage_dir = Path(tempfile.mkdtemp(prefix=".restore-", dir=backups))
    rollback_dir = Path(tempfile.mkdtemp(prefix=".rollback-", dir=backups))
    old_db = rollback_dir / "app.db"
    old_generated = rollback_dir / "generated"
    old_sidecars: list[tuple[Path, Path]] = []
    old_db_moved = False
    old_generated_moved = False
    db_replaced = False
    generated_replaced = False
    try:
        staged_db = stage_dir / DATABASE_MEMBER
        # Validate and extract through one descriptor-backed ZIP reader. A
        # pathname swap after validation therefore cannot make extraction use
        # a different archive. The staged checks below also detect in-place
        # mutation between manifest validation and member reads.
        with _open_archive_nofollow(archive) as archive_stream:
            with zipfile.ZipFile(archive_stream, "r") as source:
                manifest = read_backup_manifest(source, expected_schema_version=schema_version)
                for entry in manifest["files"]:
                    member = entry["path"]
                    _read_member(source, source.getinfo(member), stage_dir / member)
        _validate_staged_payload(stage_dir, manifest)
        _assert_sqlite_integrity(staged_db)
        staged_artifacts = stage_dir / ARTIFACT_PREFIX.rstrip("/")
        staged_artifacts.mkdir(parents=True, exist_ok=True)
        if generated.exists() and (generated.is_symlink() or not generated.is_dir()):
            raise BackupError("generated artifact root must be a real directory")
        if generated.exists():
            # Do not silently move a live artifact tree containing links to
            # external paths into rollback storage.
            _artifact_files(generated)
        if database.exists() and database.is_symlink():
            raise BackupError("database path must not be a symbolic link")
        if database.exists() and not database.is_file():
            raise BackupError("database path must be a regular file")
        database.parent.mkdir(parents=True, exist_ok=True)
        backups.mkdir(parents=True, exist_ok=True)
        # A live SQLite connection may leave -wal/-shm sidecars. Keep them in
        # the rollback set so an unsuccessful restore can return the exact
        # prior filesystem state.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            if sidecar.exists():
                destination = rollback_dir / sidecar.name
                os.replace(sidecar, destination)
                old_sidecars.append((destination, sidecar))
        if database.exists():
            os.replace(database, old_db)
            old_db_moved = True
        try:
            os.replace(staged_db, database)
            db_replaced = True
        except OSError:
            # The rollback branch below restores old_db even though the new
            # database never became visible.
            raise
        if generated.exists():
            os.replace(generated, old_generated)
            old_generated_moved = True
        try:
            os.replace(staged_artifacts, generated)
            generated_replaced = True
        except OSError:
            # As with the database, an atomic replace can fail after the old
            # directory has already moved into the rollback set.
            raise
        if post_restore is not None:
            post_restore()
        return {
            "filename": archive.name,
            "backup_version": manifest["backup_version"],
            "restored": True,
            "artifact_count": manifest.get("artifacts", {}).get("count", 0),
        }
    except Exception as exc:
        # Roll back each independently replaced target. The guard variables
        # ensure a failure before a replacement does not delete user data.
        try:
            if generated_replaced or old_generated_moved:
                if generated_replaced and generated.exists():
                    shutil.rmtree(generated)
                if old_generated.exists():
                    os.replace(old_generated, generated)
            if db_replaced or old_db_moved:
                if db_replaced and database.exists():
                    database.unlink()
                if old_db.exists():
                    os.replace(old_db, database)
            for saved, original in old_sidecars:
                if saved.exists():
                    os.replace(saved, original)
        except OSError as rollback_error:
            raise BackupError(f"restore failed and rollback was incomplete: {rollback_error}") from exc
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"restore failed: {exc}") from exc
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(rollback_dir, ignore_errors=True)
