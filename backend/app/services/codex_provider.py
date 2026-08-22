"""Local Codex CLI provider; deliberately does not use an application API key."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Callable
from pydantic import ValidationError
from .llm_prompts import analysis_prompt, proposal_prompt
from .llm_schemas import JobAnalysisOutput, ProposalOutput

MODEL = "gpt-5.6-luna"
REASONING = "low"
SERVICE_TIER = "default"  # Explicitly normal/default; never fast or priority.

class CodexProviderError(RuntimeError):
    """Safe, user-facing provider failure without command output or credentials."""

@dataclass(frozen=True)
class CodexSettings:
    timeout_seconds: float = 120.0
    executable: str = "codex"

Runner = Callable[..., subprocess.CompletedProcess[str]]

class CodexProvider:
    model = MODEL
    reasoning_effort = REASONING
    service_tier = SERVICE_TIER

    def __init__(self, settings: CodexSettings | None = None, runner: Runner | None = None):
        self.settings = settings or CodexSettings(
            timeout_seconds=float(os.getenv("CODEX_TIMEOUT_SECONDS", "120")),
            executable=os.getenv("CODEX_EXECUTABLE", "codex"),
        )
        self._runner = runner or subprocess.run

    def _run(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not shutil.which(self.settings.executable):
            raise CodexProviderError("Codex CLI is unavailable")
        # --ephemeral prevents session persistence. Auth remains supplied by the signed-in
        # CLI; no API key is read or forwarded. --sandbox read-only prevents file mutation.
        # Keep the caller's auth environment intact. --ignore-user-config excludes behavioral
        # user config while CODEX_HOME is left untouched so signed-in auth remains available.
        # --ephemeral and read-only sandbox
        # prevent session/worktree mutation; behavioral defaults are pinned by argv below.
        env = os.environ.copy()
        for name in ("OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            env.pop(name, None)
        with tempfile.TemporaryDirectory(prefix="resume-codex-") as temp_dir:
            schema_path = os.path.join(temp_dir, "output-schema.json")
            output_path = os.path.join(temp_dir, "last-message.json")
            with open(schema_path, "w", encoding="utf-8") as schema_file:
                json.dump(schema, schema_file)
            command = [self.settings.executable, "exec", "--model", MODEL,
                       "-c", 'model_reasoning_effort="low"',
                       "-c", 'service_tier="default"', "--ignore-user-config", "--strict-config",
                       "--sandbox", "read-only", "--ephemeral", "--output-schema", schema_path,
                       "--output-last-message", output_path]
            try:
                result = self._runner(command, input=prompt, text=True, capture_output=True,
                                      timeout=self.settings.timeout_seconds, env=env, check=False)
                raw = result.stdout
                if os.path.exists(output_path):
                    try:
                        with open(output_path, encoding="utf-8") as output_file:
                            raw = output_file.read()
                    except OSError:
                        pass
            except FileNotFoundError as exc:
                raise CodexProviderError("Codex CLI is unavailable") from exc
            except subprocess.TimeoutExpired as exc:
                raise CodexProviderError("Codex request timed out") from exc
            except OSError as exc:
                raise CodexProviderError("Codex process could not be started") from exc
        if result.returncode != 0:
            raise CodexProviderError("Codex request failed; verify local Codex authentication")
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CodexProviderError("Codex returned invalid structured output") from exc
        if not isinstance(payload, dict):
            raise CodexProviderError("Codex returned invalid structured output")
        return payload

    @staticmethod
    def _codex_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Adapt Pydantic JSON Schema to Codex structured-output requirements."""
        normalized = json.loads(json.dumps(schema))

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                properties = node.get("properties")
                if isinstance(properties, dict):
                    node["required"] = list(properties)
                    node["additionalProperties"] = False
                for value in node.values(): visit(value)
            elif isinstance(node, list):
                for value in node: visit(value)

        visit(normalized)
        return normalized

    def analyze(self, job_description: str) -> JobAnalysisOutput:
        schema = self._codex_schema(JobAnalysisOutput.model_json_schema())
        try:
            return JobAnalysisOutput.model_validate(self._run(analysis_prompt(job_description), schema))
        except ValidationError as exc:
            raise CodexProviderError("Codex output failed analysis validation") from exc

    def generate_proposal(self, context: dict[str, Any]) -> ProposalOutput:
        schema = self._codex_schema(ProposalOutput.model_json_schema())
        try:
            return ProposalOutput.model_validate(self._run(proposal_prompt(context), schema))
        except ValidationError as exc:
            raise CodexProviderError("Codex output failed proposal validation") from exc
