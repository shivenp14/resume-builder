"""Semantic comparison helpers for immutable resume revision snapshots.

Revision snapshots are JSON documents, but a raw JSON diff is noisy for a
resume: ordering of object keys and generation provenance are not content
changes.  This module keeps the comparison pure and side-effect free so the
API can calculate a diff on demand without creating another persisted record.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


_SEMANTIC_BULLET_FIELDS = (
    "content_item_id",
    "text",
    "source_text",
    "supporting_facts",
    "is_locked",
    "was_rewritten",
)
_SEMANTIC_ITEM_FIELDS = ("title",)


def _key(value: Any) -> str:
    """Use one stable key for integer and string IDs in imported snapshots."""

    return str(value).strip()


def _index(records: Any, *, identity: str) -> dict[str, dict[str, Any]]:
    """Index a snapshot collection without mutating it.

    Malformed snapshots should not make a comparison endpoint crash while
    walking nested values.  Repeated IDs are represented as distinct indexed
    records so the caller still receives a useful diff for legacy snapshots.
    Normal API-created revisions have already passed snapshot validation.
    """

    if not isinstance(records, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        raw_id = record.get(identity)
        if raw_id is None:
            continue
        key = _key(raw_id)
        # A duplicate is malformed, but retaining both entries gives a stable
        # comparison rather than silently dropping a source record.
        if key in result:
            key = f"{key}#{position}"
        result[key] = record
    return result


def _normalized(value: Any) -> Any:
    """Return a comparison-safe value with irrelevant ordering normalized."""

    if isinstance(value, dict):
        return {key: _normalized(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _semantic_record(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: deepcopy(record.get(field)) for field in fields if field in record}


def _change(kind: str, entity: str, record_id: str | None, path: str, before: Any, after: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "entity": entity,
        "id": record_id,
        "path": path,
        "before": deepcopy(before),
        "after": deepcopy(after),
    }


def _collection_changes(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    entity: str,
    fields: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for record_id in sorted(set(after) - set(before)):
        record = deepcopy(after[record_id])
        added.append(_change("added", entity, record_id.split("#", 1)[0], f"{entity}[{record_id}]", None, record))
    for record_id in sorted(set(before) - set(after)):
        record = deepcopy(before[record_id])
        removed.append(_change("removed", entity, record_id.split("#", 1)[0], f"{entity}[{record_id}]", record, None))
    for record_id in sorted(set(before) & set(after)):
        old = _semantic_record(before[record_id], fields)
        new = _semantic_record(after[record_id], fields)
        if _normalized(old) != _normalized(new):
            changed.append(
                _change(
                    "changed",
                    entity,
                    record_id.split("#", 1)[0],
                    f"{entity}[{record_id}]",
                    old,
                    new,
                )
            )
    return added, removed, changed


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic semantic diff between two resume snapshots.

    The returned values are deep copies.  A caller can therefore safely hand
    the result to a client or mutate it in a test without changing either
    revision's JSON document.  ``provenance`` is intentionally excluded: it
    describes how a revision was generated, not what appears in the resume.
    """

    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    # Contact fields are semantic but do not have a stable record ID.
    old_contact = before.get("contact") if isinstance(before.get("contact"), dict) else {}
    new_contact = after.get("contact") if isinstance(after.get("contact"), dict) else {}
    for field in sorted(set(old_contact) | set(new_contact)):
        old = old_contact.get(field)
        new = new_contact.get(field)
        if field not in old_contact:
            added.append(_change("added", "contact", field, f"contact.{field}", None, new))
        elif field not in new_contact:
            removed.append(_change("removed", "contact", field, f"contact.{field}", old, None))
        elif _normalized(old) != _normalized(new):
            changed.append(_change("changed", "contact", field, f"contact.{field}", old, new))

    item_changes = _collection_changes(
        _index(before.get("content_items"), identity="id"),
        _index(after.get("content_items"), identity="id"),
        entity="content_item",
        fields=_SEMANTIC_ITEM_FIELDS,
    )
    bullet_changes = _collection_changes(
        _index(before.get("bullets"), identity="id"),
        _index(after.get("bullets"), identity="id"),
        entity="bullet",
        fields=_SEMANTIC_BULLET_FIELDS,
    )
    added.extend(item_changes[0] + bullet_changes[0])
    removed.extend(item_changes[1] + bullet_changes[1])
    changed.extend(item_changes[2] + bullet_changes[2])

    # Entries describe source selection and ordering.  Their nested rendered
    # section entries are intentionally ignored to avoid reporting one bullet
    # edit twice (once as a bullet and once as a section).
    old_entries = _index(before.get("entries"), identity="content_item_id")
    new_entries = _index(after.get("entries"), identity="content_item_id")
    entry_changes = _collection_changes(
        old_entries,
        new_entries,
        entity="entry",
        fields=("bullet_ids",),
    )
    added.extend(entry_changes[0])
    removed.extend(entry_changes[1])
    changed.extend(entry_changes[2])

    # Section order and headings affect the rendered document.  Compare the
    # stable layout fields only; section entries are represented by entries and
    # bullets above.
    old_sections = _index(before.get("sections"), identity="key")
    new_sections = _index(after.get("sections"), identity="key")
    section_changes = _collection_changes(
        old_sections,
        new_sections,
        entity="section",
        fields=("title", "skill_groups"),
    )
    added.extend(section_changes[0])
    removed.extend(section_changes[1])
    changed.extend(section_changes[2])

    # Preserve the meaningful ordering of sections and entries as an explicit
    # change even when their members are unchanged.
    old_section_order = [record.get("key") for record in before.get("sections", []) if isinstance(record, dict)]
    new_section_order = [record.get("key") for record in after.get("sections", []) if isinstance(record, dict)]
    if old_section_order != new_section_order and old_section_order and new_section_order:
        changed.append(_change("changed", "section_order", None, "sections", old_section_order, new_section_order))

    old_entry_order = [record.get("content_item_id") for record in before.get("entries", []) if isinstance(record, dict)]
    new_entry_order = [record.get("content_item_id") for record in after.get("entries", []) if isinstance(record, dict)]
    if old_entry_order != new_entry_order and old_entry_order and new_entry_order:
        changed.append(_change("changed", "entry_order", None, "entries", old_entry_order, new_entry_order))

    # Make output ordering stable across SQLite/query ordering and JSON key
    # ordering.  ``changes`` is convenient for clients that do not need the
    # three-way buckets, while the buckets make filtering straightforward.
    sort_key = lambda value: (value["entity"], value["path"], value["kind"])
    added.sort(key=sort_key)
    removed.sort(key=sort_key)
    changed.sort(key=sort_key)
    all_changes = [*added, *removed, *changed]
    return {
        "changed": bool(all_changes),
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "total": len(all_changes),
        },
        "added": added,
        "removed": removed,
        # ``modified`` is the conventional name used by diff consumers;
        # ``changed_items`` remains descriptive for clients that distinguish
        # the top-level boolean ``changed`` from modified records.
        "modified": changed,
        "changed_items": changed,
        "changes": all_changes,
    }


# Descriptive alias for callers that prefer an explicit function name.
compare_revision_snapshots = compare_snapshots
