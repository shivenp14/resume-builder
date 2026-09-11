"""Regression coverage for long-running provider calls and request isolation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app import main


def test_slow_analysis_does_not_block_unrelated_application_list(monkeypatch):
    """A provider call must not hold the request-wide database barrier.

    The test deliberately leaves analysis paused after its initial run row has
    been committed, then issues an unrelated read on another worker thread.
    Before the barrier is narrowed this second request waits for the provider
    and times out; after the fix it returns while analysis is still paused.
    """
    setup_client = TestClient(main.app)
    resume = setup_client.post("/base-resumes", json={"name": "Concurrency"}).json()
    application = setup_client.post(
        "/applications",
        json={
            "company": "Acme",
            "position": "Engineer",
            "job_description": "Need Python",
            "base_resume_id": resume["id"],
        },
    ).json()

    provider_started = threading.Event()
    release_provider = threading.Event()

    class BlockingProvider:
        def analyze(self, job_description):
            provider_started.set()
            if not release_provider.wait(timeout=5):
                raise AssertionError("test did not release the provider")
            return main.analyze_text(job_description)

        def generate_proposal(self, context):
            return {
                "schema_version": "1.0",
                "selected_entries": [],
                "bullet_changes": [],
                "warnings": [],
                "rationale": "",
            }

    monkeypatch.setattr(main, "_provider", lambda: BlockingProvider())

    def run_analysis():
        with TestClient(main.app) as client:
            return client.post(f"/applications/{application['id']}/analyze")

    def list_applications():
        with TestClient(main.app) as client:
            return client.get("/applications")

    with ThreadPoolExecutor(max_workers=2) as executor:
        analysis_future = executor.submit(run_analysis)
        assert provider_started.wait(timeout=2), "analysis did not reach provider"

        list_future = executor.submit(list_applications)
        try:
            response = list_future.result(timeout=1)
        except TimeoutError:
            pytest.fail("unrelated application list was blocked by slow analysis")
        finally:
            release_provider.set()

        assert response.status_code == 200
        analysis_response = analysis_future.result(timeout=5)
        assert analysis_response.status_code == 200
