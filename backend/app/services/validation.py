"""Validation helpers for AI proposals and resolved resume snapshots.

The API accepts proposal payloads as JSON dictionaries because proposals are
also persisted as an auditable artifact. Keep the checks here defensive:
malformed nested values should produce a useful validation error, not an
``AttributeError`` while walking the payload.
"""
from __future__ import annotations

import re
from typing import Any


class ValidationError(ValueError):
    """A proposal or snapshot is not safe to persist or render."""


# Common, unambiguous technology names. We do not reject every lowercase word
# in a rewrite (ordinary prose is lowercase too), but we do reject a known
# technology claim when it is absent from verified facts.
_TECHNOLOGY_TERMS = frozenset(
    {
        "airflow", "android", "angular", "ansible", "apollo", "aws", "azure",
        "c", "csharp", "cplusplus", "css", "datadog", "django", "docker",
        "electron", "elasticsearch", "fastapi", "firebase", "flask", "gcp",
        "git", "github", "gitlab", "go", "graphql", "hadoop", "java",
        "javascript", "jenkins", "jest", "kafka", "kotlin", "kubernetes",
        "lambda", "linux", "mongodb", "mysql", "nextjs", "node", "nodejs",
        "numpy", "openai", "pandas", "playwright", "postgres", "postgresql",
        "pytorch", "python", "rails", "react", "redis", "redux", "ruby",
        "rust", "s3", "snowflake", "sql", "terraform", "tensorflow",
        "typescript", "vercel", "vue",
    }
)

_NUMBER_RE = re.compile(
    r"(?<![\w])"
    r"(?:[$€£]?\d+(?:[.,]\d+)?(?:\s?[kKmMbB]|[xX])?%?|\d+\s*/\s*\d+)"
    r"(?![\w])"
)


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be a list")
    return value


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _require_id(value: Any, label: str) -> int | str:
    # Snapshots imported from older sources may contain string IDs. Proposal
    # payloads produced by the API use integer IDs, but both scalar forms are
    # safe to compare after normalization.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValidationError(f"{label} must be an integer or string id")
    ident = str(value).strip()
    if not ident:
        raise ValidationError(f"{label} is required")
    return value


def _items_by_id(items: Any, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(_require_list(items, label)):
        item = _require_object(raw_item, f"{label}[{index}]")
        if "id" not in item:
            raise ValidationError(f"{label}[{index}] is missing id")
        ident = str(_require_id(item["id"], f"{label}[{index}].id")).strip()
        if ident in result:
            raise ValidationError(f"{label} contains a missing or duplicate id")
        result[ident] = item
    return result


def _validate_string_list(value: Any, label: str) -> list[str]:
    values = _require_list(value, label)
    if any(not isinstance(item, str) for item in values):
        raise ValidationError(f"{label} must contain only strings")
    return values


def _validate_id_list(value: Any, label: str) -> list[int | str]:
    values = _require_list(value, label)
    result = []
    for index, item in enumerate(values):
        result.append(_require_id(item, f"{label}[{index}]"))
    identities = [str(item).strip() for item in result]
    if len(identities) != len(set(identities)):
        raise ValidationError(f"{label} contains duplicate ids")
    return result


def validate_resume_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate references and basic shape before rendering or persisting a revision."""
    if not isinstance(snapshot, dict):
        raise ValidationError("resume snapshot must be an object")
    items = _items_by_id(snapshot.get("content_items", []), "content_items")
    bullets = _items_by_id(snapshot.get("bullets", []), "bullets")
    entries = _require_list(snapshot.get("entries", []), "entries")
    for bullet in bullets.values():
        if "content_item_id" not in bullet:
            raise ValidationError(f"bullet {bullet.get('id', '<unknown>')} is missing content_item_id")
        if str(_require_id(bullet["content_item_id"], "bullet.content_item_id")) not in items:
            raise ValidationError(f"bullet {bullet['id']} references unknown content item")
    for index, raw_entry in enumerate(entries):
        entry = _require_object(raw_entry, f"entries[{index}]")
        if "content_item_id" not in entry:
            raise ValidationError(f"entries[{index}] is missing content_item_id")
        if str(_require_id(entry["content_item_id"], f"entries[{index}].content_item_id")) not in items:
            raise ValidationError("entry references unknown content item")
        bullet_ids = _validate_id_list(entry.get("bullet_ids", []), f"entries[{index}].bullet_ids")
        for bid in bullet_ids:
            if str(bid).strip() not in bullets:
                raise ValidationError(f"entry references unknown bullet {bid}")
    return snapshot


def _normalize_number(value: str) -> str:
    return re.sub(r"\s+", "", value).replace(",", ".").lower()


def _number_tokens(text: str) -> set[str]:
    return {_normalize_number(match.group(0)) for match in _NUMBER_RE.finditer(text)}


def _normalize_technology_token(value: str) -> str:
    # Handles names commonly written as C++, C#, Node.js, and Next.js.
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _technology_tokens(text: str) -> set[str]:
    tokens = set()
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9]*(?:[.+#-][A-Za-z0-9]+)*", text):
        normalized = _normalize_technology_token(match.group(0))
        if normalized in _TECHNOLOGY_TERMS:
            tokens.add(normalized)
    return tokens


def _validate_claims(source: dict[str, Any], proposed: str) -> None:
    source_facts = " ".join(
        [
            str(source.get("text", "")),
            *[str(value) for value in (source.get("supporting_facts") or [])],
            *[str(value) for value in (source.get("tags") or [])],
        ]
    )
    source_numbers = _number_tokens(source_facts)
    for token in _number_tokens(proposed):
        if token not in source_numbers:
            raise ValidationError(f"rewrite contains unsupported claim: {token}")

    source_technologies = _technology_tokens(source_facts)
    for token in _technology_tokens(proposed):
        if token not in source_technologies:
            raise ValidationError(f"rewrite contains unsupported claim: {token}")

    # Preserve the previous guard against introducing capitalized named
    # entities/claims, but compare case-insensitively so evidence may be
    # written as either ``Python`` or ``python``.
    source_words = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}", source_facts.lower()))
    for token in re.findall(r"\b[A-Z][A-Za-z0-9+#.-]{2,}\b", proposed):
        if token.lower() not in source_words and _normalize_technology_token(token) not in source_technologies:
            raise ValidationError(f"rewrite contains unsupported claim: {token}")


def validate_proposal(proposal: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed model output and references outside verified source data."""
    if not isinstance(proposal, dict):
        raise ValidationError("proposal must be an object")
    validate_resume_snapshot(snapshot)
    content = _items_by_id(snapshot.get("content_items", []), "content_items")
    bullet_map = _items_by_id(snapshot.get("bullets", []), "bullets")

    allowed = {
        "schema_version", "selected_entries", "bullet_changes", "warnings",
        "rationale", "source_fingerprint", "prompt_version",
    }
    unknown = sorted(set(proposal) - allowed)
    if unknown:
        raise ValidationError(f"proposal contains unsupported fields: {', '.join(unknown)}")
    if "schema_version" in proposal and proposal["schema_version"] != "1.0":
        raise ValidationError("proposal schema_version must be '1.0'")
    if "source_fingerprint" in proposal and not isinstance(proposal["source_fingerprint"], str):
        raise ValidationError("proposal source_fingerprint must be a string")
    if "prompt_version" in proposal and not isinstance(proposal["prompt_version"], str):
        raise ValidationError("proposal prompt_version must be a string")
    if "warnings" in proposal:
        _validate_string_list(proposal["warnings"], "proposal.warnings")
    if "rationale" in proposal and not isinstance(proposal["rationale"], str):
        raise ValidationError("proposal rationale must be a string")

    selected_entries = _require_list(proposal.get("selected_entries", []), "proposal.selected_entries")
    selected_content_ids: set[str] = set()
    for index, raw_entry in enumerate(selected_entries):
        entry = _require_object(raw_entry, f"proposal.selected_entries[{index}]")
        if "content_item_id" not in entry:
            raise ValidationError(f"proposal.selected_entries[{index}] is missing content_item_id")
        item_id = _require_id(entry["content_item_id"], f"proposal.selected_entries[{index}].content_item_id")
        item_key = str(item_id).strip()
        if item_key in selected_content_ids:
            raise ValidationError("proposal contains duplicate selected entries")
        selected_content_ids.add(item_key)
        if item_key not in content:
            raise ValidationError("proposal selects unknown content item")
        bullet_ids = _validate_id_list(
            entry.get("bullet_ids", []), f"proposal.selected_entries[{index}].bullet_ids"
        )
        for bid in bullet_ids:
            if str(bid).strip() not in bullet_map:
                raise ValidationError(f"proposal selects unknown bullet {bid}")

    bullet_changes = _require_list(proposal.get("bullet_changes", []), "proposal.bullet_changes")
    changed_bullets: set[str] = set()
    for index, raw_change in enumerate(bullet_changes):
        change = _require_object(raw_change, f"proposal.bullet_changes[{index}]")
        if "bullet_id" not in change:
            raise ValidationError(f"proposal.bullet_changes[{index}] is missing bullet_id")
        bullet_id = _require_id(change["bullet_id"], f"proposal.bullet_changes[{index}].bullet_id")
        bullet_key = str(bullet_id).strip()
        if bullet_key in changed_bullets:
            raise ValidationError("proposal contains duplicate bullet changes")
        changed_bullets.add(bullet_key)
        source = bullet_map.get(bullet_key)
        if source is None:
            raise ValidationError("rewrite references unknown bullet")
        proposed_text = change.get("proposed_text")
        if not isinstance(proposed_text, str) or not proposed_text.strip():
            raise ValidationError(f"proposal.bullet_changes[{index}].proposed_text must be a non-empty string")
        if "rationale" in change and not isinstance(change["rationale"], str):
            raise ValidationError(f"proposal.bullet_changes[{index}].rationale must be a string")
        if "evidence" in change:
            _validate_string_list(change["evidence"], f"proposal.bullet_changes[{index}].evidence")
        if source.get("is_locked") and proposed_text != source.get("text", ""):
            raise ValidationError(f"locked bullet {source['id']} cannot be rewritten")
        _validate_claims(source, proposed_text)
    return proposal
