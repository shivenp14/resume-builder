"""Keep API tests isolated from the developer's local database and artifacts."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import main


@pytest.fixture(autouse=True)
def isolated_backend(tmp_path, monkeypatch):
    """Run each test against a fresh SQLite database and output directory."""
    test_root = tmp_path / "resume-builder"
    test_root.mkdir()
    test_db = test_root / "data" / "app.db"
    test_db.parent.mkdir()
    test_generated = test_root / "generated"
    test_generated.mkdir()

    test_engine = create_engine(
        f"sqlite:///{test_db}",
        connect_args={"check_same_thread": False},
    )
    test_session_local = sessionmaker(bind=test_engine, expire_on_commit=False)
    main.Base.metadata.create_all(test_engine)

    # Route functions resolve these globals at request time, so the existing
    # module-level TestClient instances use the isolated resources as well.
    # The API intentionally returns repository-relative artifact paths. Change
    # into the isolated workspace so those paths remain valid to the tests
    # without touching the developer's real generated/ directory.
    monkeypatch.chdir(test_root)
    monkeypatch.setattr(main, "ROOT", test_root)
    monkeypatch.setattr(main, "GENERATED", test_generated)
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "SessionLocal", test_session_local)
    class DeterministicTestProvider:
        def analyze(self, job_description):
            return main.analyze_text(job_description)

        def generate_proposal(self, context):
            return {"schema_version":"1.0","selected_entries":[],"bullet_changes":[],"warnings":[],"rationale":""}

    monkeypatch.setattr(main, "_provider", lambda: DeterministicTestProvider())
    generated_mount = next(route for route in main.app.routes if route.path == "/generated")
    monkeypatch.setattr(generated_mount.app, "directory", test_generated)
    try:
        yield
    finally:
        test_engine.dispose()
