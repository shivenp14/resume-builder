"""Offline contract tests for the Codex-backed optimization workflow.

The provider is deliberately replaced with a recording fake.  These tests
must never require an API key, network access, or a signed-in CLI session.
"""

import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _fake_result(**payload):
    return SimpleNamespace(**payload)


class RecordingProvider:
    model = "gpt-5.6-luna"
    reasoning_effort = "low"
    service_tier = "default"

    def __init__(self):
        self.calls = []

    def analyze(self, payload, **kwargs):
        self.calls.append(("analyze", payload, kwargs))
        return {"requirements": ["Python"], "keywords": ["python"],
                "technologies": ["python"], "responsibilities": [],
                "preferred_qualifications": []}

    def generate_proposal(self, context):
        self.calls.append(("proposal", context))
        return {"schema_version": "1.0", "selected_entries": [], "bullet_changes": [],
                "warnings": ["Unresolved requirement: Docker"], "rationale": ""}


@pytest.fixture
def client():
    from backend.app.main import app
    return TestClient(app)


@pytest.fixture
def codex_backend(monkeypatch):
    """Install the fake using either the provider factory or singleton hook."""
    from backend.app import main

    fake = RecordingProvider()
    if hasattr(main, "_provider"):
        monkeypatch.setattr(main, "_provider", lambda: fake)
    for name in ("llm_provider", "provider", "codex_provider"):
        if hasattr(main, name):
            monkeypatch.setattr(main, name, fake)
            break
    else:
        # The implementation contract is a factory; this branch gives a clear
        # failure if it has not been wired yet while keeping tests offline.
        monkeypatch.setattr(main, "get_llm_provider", lambda: fake, raising=False)
    return fake


def _application(client):
    resume = client.post("/base-resumes", json={"name": "Primary"}).json()
    return client.post("/applications", json={
        "company": "Example", "position": "Engineer",
        "job_description": "Python engineer; Docker preferred.",
        "base_resume_id": resume["id"],
    }).json()


def test_provider_defaults_are_luna_low_reasoning_normal_speed():
    from backend.app import main

    provider_cls = getattr(main, "CodexProvider", None)
    if provider_cls is None:
        pytest.fail("CodexProvider is required")
    provider = provider_cls()
    assert provider.model == "gpt-5.6-luna"
    assert provider.reasoning_effort == "low"
    assert getattr(provider, "service_tier", "default") == "default"
    assert getattr(provider, "api_key", None) in (None, "")


def test_provider_command_pins_default_tier_and_strips_api_key(monkeypatch):
    from backend.app.services.codex_provider import CodexProvider, CodexSettings

    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        output_path = command[command.index("--output-last-message") + 1]
        with open(output_path, "w", encoding="utf-8") as output:
            json.dump({"schema_version":"1.0", "requirements":[], "keywords":[],
                       "technologies":[], "responsibilities":[],
                       "preferred_qualifications":[]}, output)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-forwarded")
    provider = CodexProvider(CodexSettings(executable="/usr/bin/true"), runner=runner)
    provider.analyze("Python role")
    command = observed["command"]
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="low"' in command
    assert 'service_tier="default"' in command
    assert not any("fast" in part or "priority" in part for part in command)
    assert "--ignore-user-config" in command
    assert "OPENAI_API_KEY" not in observed["env"]


def test_provider_schema_marks_every_property_required_for_codex():
    from backend.app.services.codex_provider import CodexProvider
    from backend.app.services.llm_schemas import JobAnalysisOutput, ProposalOutput

    schema = CodexProvider._codex_schema(JobAnalysisOutput.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    proposal_schema = CodexProvider._codex_schema(ProposalOutput.model_json_schema())
    assert "requirement_evidence" in proposal_schema["properties"]
    assert set(proposal_schema["required"]) == set(proposal_schema["properties"])


def test_analysis_uses_fake_and_persists_audit_run(client, codex_backend):
    application = _application(client)
    response = client.post(f"/applications/{application['id']}/analyze")
    assert response.status_code == 200
    assert response.json()["technologies"] == ["python"]
    runs = client.get(f"/applications/{application['id']}/optimization-runs")
    assert runs.status_code == 200
    assert runs.json()[0]["model"] == "gpt-5.6-luna"
    assert codex_backend.calls[0][0] == "analyze"


def test_provider_failure_is_503_without_partial_analysis(client, codex_backend):
    def fail(**kwargs):
        raise RuntimeError("codex unavailable")
    codex_backend.analyze = fail
    application = _application(client)
    response = client.post(f"/applications/{application['id']}/analyze")
    assert response.status_code == 503
    assert client.get(f"/applications/{application['id']}/analysis").status_code == 404


def test_proposal_generation_keeps_unresolved_requirements_as_warnings(client, codex_backend):
    application = _application(client)
    assert client.post(f"/applications/{application['id']}/analyze").status_code == 200
    assert client.post(f"/applications/{application['id']}/missing-confirmations").status_code == 200
    response = client.post(f"/applications/{application['id']}/proposals/generate")
    assert response.status_code == 200
    assert "Unresolved requirement: Docker" in response.json()["payload"]["warnings"]
    runs = client.get(f"/applications/{application['id']}/optimization-runs").json()
    assert runs[0]["operation"] == "proposal"
    assert codex_backend.calls[-1][0] == "proposal"


def test_locked_and_unsupported_claims_are_rejected(client):
    item = client.post("/content-items", json={"type": "experience", "title": "Verified"}).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={
        "text": "Built APIs", "is_locked": True}).json()
    resume = client.post("/base-resumes", json={"name": "Primary"}).json()
    application = client.post("/applications", json={"company": "X", "position": "Y",
        "job_description": "Python", "base_resume_id": resume["id"]}).json()
    locked = client.post(f"/applications/{application['id']}/proposals", json={"payload": {
        "bullet_changes": [{"bullet_id": bullet["id"], "proposed_text": "Built 99 APIs"}]}})
    assert locked.status_code == 422
    unknown = client.post(f"/applications/{application['id']}/proposals", json={"payload": {
        "selected_entries": [{"content_item_id": item["id"], "bullet_ids": [99999]}]}})
    assert unknown.status_code == 422
