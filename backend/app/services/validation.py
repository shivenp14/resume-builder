"""Validation helpers for AI proposals and resolved resume snapshots."""
from __future__ import annotations

import re
from typing import Any

class ValidationError(ValueError):
    pass

def _items_by_id(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        ident = str(item.get("id", ""))
        if not ident or ident in result:
            raise ValidationError(f"{label} contains a missing or duplicate id")
        result[ident] = item
    return result

def validate_resume_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate references and basic shape before rendering or persisting a revision."""
    if not isinstance(snapshot, dict):
        raise ValidationError("resume snapshot must be an object")
    items = _items_by_id(snapshot.get("content_items", []), "content_items")
    bullets = _items_by_id(snapshot.get("bullets", []), "bullets")
    for bullet in bullets.values():
        if str(bullet.get("content_item_id", "")) not in items:
            raise ValidationError(f"bullet {bullet['id']} references unknown content item")
    for entry in snapshot.get("entries", []):
        if str(entry.get("content_item_id", "")) not in items:
            raise ValidationError("entry references unknown content item")
        for bid in entry.get("bullet_ids", []):
            if str(bid) not in bullets:
                raise ValidationError(f"entry references unknown bullet {bid}")
    return snapshot

def validate_proposal(proposal: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed model output and references outside verified source data."""
    validate_resume_snapshot(snapshot)
    content = _items_by_id(snapshot.get("content_items", []), "content_items")
    bullet_map = _items_by_id(snapshot.get("bullets", []), "bullets")
    for entry in proposal.get("selected_entries", []):
        if str(entry.get("content_item_id")) not in content:
            raise ValidationError("proposal selects unknown content item")
        for bid in entry.get("bullet_ids", []):
            if str(bid) not in bullet_map:
                raise ValidationError(f"proposal selects unknown bullet {bid}")
    for change in proposal.get("bullet_changes", []):
        source = bullet_map.get(str(change.get("bullet_id")))
        if source is None:
            raise ValidationError("rewrite references unknown bullet")
        if source.get("is_locked") and change.get("proposed_text", "") != source.get("text", ""):
            raise ValidationError(f"locked bullet {source['id']} cannot be rewritten")
        # New named technologies and numbers must be backed by source supporting facts.
        source_text = str(source.get("text", "")) + " " + " ".join(map(str, source.get("supporting_facts", [])))
        proposed = str(change.get("proposed_text", ""))
        for token in re.findall(r"\b(?:\d+(?:\.\d+)?%?|[A-Z][A-Za-z0-9+#.-]{2,})\b", proposed):
            if token.isdigit() or token.endswith("%") or (token[0].isupper() and token not in source_text):
                if token not in source_text and token not in str(source.get("tags", [])):
                    raise ValidationError(f"rewrite contains unsupported claim: {token}")
    return proposal
