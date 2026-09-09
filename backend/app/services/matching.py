"""Deterministic resume-to-requirement matching.

The matcher deliberately does not use an LLM or fuzzy string similarity.  A
comparison is a projection of the current verified source library, the active
structured requirements, and the evidence links attached to those
requirements.  Keeping this module free of SQLAlchemy models makes the rules
easy to test and keeps a comparison read-only.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .skills import SkillNormalizationError, normalize_skill_name


# Bump this when a scoring rule changes.  It is returned with every
# comparison, so clients can tell whether two scores were produced by the
# same deterministic ruleset.
SCORING_VERSION = "matching-v1"

# Scores are integer percentages in the public API.  A base-resume match with
# a strong signal is "well represented"; any concrete base-resume signal that
# is below that threshold is useful but weak.  Library-only evidence is kept
# separate from resume coverage even if its score is high.
WELL_REPRESENTED_THRESHOLD = 75
WEAKLY_REPRESENTED_THRESHOLD = 1

# Keep language markers attached to a token.  The final alternation handles
# ordinary words while the first two alternatives keep ``C++`` and ``C#``
# distinct from the bare token ``C``.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\+\+|#)|[a-z0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "of", "on", "or", "the", "to",
    "using", "with", "will", "you", "your", "this", "that", "work",
    "working", "build", "built", "building", "develop", "developed",
    "development", "experience",
}


def _tokens(value: Any) -> set[str]:
    """Return conservative comparison tokens, preserving ``c++``/``c#``."""

    if not isinstance(value, str):
        return set()
    return {
        token.casefold()
        for token in _TOKEN_RE.findall(value)
        if token.casefold() not in _STOPWORDS and len(token) > 1
    }


def _skill_phrase(value: Any) -> str:
    try:
        return normalize_skill_name(value)
    except (SkillNormalizationError, TypeError, ValueError):
        return ""


def _contains_phrase(text: Any, phrase: Any) -> bool:
    """Match a normalized skill/requirement phrase on token boundaries."""

    text_tokens = _tokens(text)
    phrase_tokens = _tokens(phrase)
    return bool(phrase_tokens) and phrase_tokens <= text_tokens


def _source_item_ids(base_entries: Iterable[Any]) -> set[int]:
    result: set[int] = set()
    for entry in base_entries:
        value = entry.get("content_item_id") if isinstance(entry, Mapping) else getattr(entry, "content_item_id", None)
        try:
            if value is not None:
                result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _item_text(item: Any) -> str:
    return " ".join(
        str(_get(item, field, "") or "")
        for field in ("title", "summary", "organization", "tags")
    )


def _bullet_texts(item: Any, bullets_by_item: Mapping[int, Iterable[Any]]) -> list[tuple[int, str]]:
    item_id = _get(item, "id")
    result: list[tuple[int, str]] = []
    for bullet in bullets_by_item.get(item_id, ()):
        bullet_id = _get(bullet, "id")
        text = str(_get(bullet, "text", "") or "")
        if bullet_id is not None:
            result.append((int(bullet_id), text))
    return result


def _skill_aliases(skill: Any) -> list[str]:
    values = [_get(skill, "name", "")]
    aliases = _get(skill, "aliases", ()) or ()
    values.extend(aliases)
    # Normalized aliases are preferred when available, but the legacy JSON
    # aliases are enough for this pure matcher and preserve old databases.
    return [value for value in values if isinstance(value, str) and value.strip()]


def _verified_skills(skills: Iterable[Any]) -> tuple[dict[int, Any], dict[str, set[int]]]:
    by_id: dict[int, Any] = {}
    index: dict[str, set[int]] = {}
    for skill in skills:
        if not bool(_get(skill, "verified", False)):
            continue
        skill_id = _get(skill, "id")
        if skill_id is None:
            continue
        skill_id = int(skill_id)
        by_id[skill_id] = skill
        for alias in _skill_aliases(skill):
            key = _skill_phrase(alias)
            if key:
                index.setdefault(key, set()).add(skill_id)
    return by_id, index


def _matching_skill_ids(requirement_text: str, skill_index: Mapping[str, set[int]]) -> set[int]:
    """Resolve only complete canonical/alias phrases mentioned by a requirement."""

    requirement_key = _skill_phrase(requirement_text)
    exact_ids = skill_index.get(requirement_key, set()) if requirement_key else set()
    # Legacy databases may contain the same alias on two skills.  An
    # ambiguous alias is not evidence for either skill; requiring a unique
    # owner makes classification deterministic and avoids accidental claims.
    found: set[int] = set(exact_ids) if len(exact_ids) == 1 else set()
    # A prose requirement generally contains a skill as one phrase rather than
    # matching the complete sentence.  Compare each index phrase against its
    # token set, not arbitrary substrings (``c`` must not match ``c++``).
    requirement_tokens = _tokens(requirement_text)
    for phrase, skill_ids in skill_index.items():
        phrase_tokens = _tokens(phrase)
        if phrase_tokens and phrase_tokens <= requirement_tokens and len(skill_ids) == 1:
            found.update(skill_ids)
    return found


def _overlap(requirement_text: str, source_text: str) -> float:
    required = _tokens(requirement_text)
    if not required:
        return 0.0
    return len(required & _tokens(source_text)) / len(required)


def _link_source(link: Any, items_by_id: Mapping[int, Any], bullets_by_id: Mapping[int, Any], verified_ids: set[int], base_ids: set[int], relationships_by_item: Mapping[int, set[int]]) -> tuple[bool, bool, str]:
    """Return (valid, in_base, human-readable source label)."""

    source_type = str(_get(link, "source_type", "") or "")
    content_item_id = _get(link, "content_item_id")
    bullet_id = _get(link, "bullet_id")
    skill_id = _get(link, "skill_id")
    try:
        if content_item_id is not None:
            content_item_id = int(content_item_id)
        if bullet_id is not None:
            bullet_id = int(bullet_id)
        if skill_id is not None:
            skill_id = int(skill_id)
    except (TypeError, ValueError):
        return False, False, "invalid evidence source"

    # A source can disappear from the active projection after archiving.  It
    # remains valid only when it is still part of the current base resume.
    if content_item_id is not None and content_item_id not in items_by_id:
        return False, False, "inactive content item"
    if bullet_id is not None:
        bullet = bullets_by_id.get(bullet_id)
        if bullet is None:
            return False, False, "missing bullet"
        bullet_item_id = _get(bullet, "content_item_id")
        if content_item_id is not None and int(bullet_item_id) != content_item_id:
            return False, False, "mismatched bullet"
        content_item_id = int(bullet_item_id)
    if skill_id is not None:
        if skill_id not in verified_ids:
            return False, False, "unverified skill"
        if content_item_id is not None and skill_id not in relationships_by_item.get(content_item_id, set()):
            return False, False, "stale skill relationship"
    if source_type not in {"bullet", "content_item", "skill"}:
        source_type = "bullet" if bullet_id is not None else ("skill" if skill_id is not None else "content_item")
    if bullet_id is not None:
        label = f"bullet {bullet_id}"
    elif content_item_id is not None:
        label = f"content item {content_item_id}"
    elif skill_id is not None:
        label = f"skill {skill_id}"
    else:
        return False, False, "missing evidence source"
    return True, bool(content_item_id in base_ids if content_item_id is not None else False), label


def score_requirement(
    requirement: Any,
    *,
    items: Iterable[Any],
    bullets_by_item: Mapping[int, Iterable[Any]],
    base_entries: Iterable[Any],
    relationships_by_item: Mapping[int, Iterable[int]],
    skills: Iterable[Any],
    evidence_links: Iterable[Any] = (),
) -> dict[str, Any]:
    """Score and classify one structured requirement.

    The result is intentionally JSON-ready and contains the signals used to
    reach the score.  It is safe to call repeatedly: no state is persisted or
    mutated by this function.
    """

    text = str(_get(requirement, "text", _get(requirement, "requirement", "")) or "").strip()
    base_ids = _source_item_ids(base_entries)
    item_rows = list(items)
    items_by_id = {int(_get(item, "id")): item for item in item_rows if _get(item, "id") is not None}
    bullets_by_item = {int(item_id): list(bullet_rows) for item_id, bullet_rows in bullets_by_item.items()}
    bullets_by_id = {
        int(_get(bullet, "id")): bullet
        for item_id, bullet_rows in bullets_by_item.items()
        for bullet in bullet_rows
        if _get(bullet, "id") is not None
    }
    relationships = {int(item_id): {int(skill_id) for skill_id in skill_ids} for item_id, skill_ids in relationships_by_item.items()}
    verified_skills, skill_index = _verified_skills(skills)
    verified_ids = set(verified_skills)
    requirement_skill_ids = _matching_skill_ids(text, skill_index)
    signals: list[dict[str, Any]] = []

    def add_signal(score: int, *, source: str, in_base: bool, reason: str, item_id: int | None = None, bullet_id: int | None = None, skill_ids: Iterable[int] = ()) -> None:
        signals.append({
            "score": max(0, min(100, int(score))),
            "source": source,
            "in_base_resume": bool(in_base),
            "reason": reason,
            "content_item_id": item_id,
            "bullet_id": bullet_id,
            "skill_ids": sorted({int(skill_id) for skill_id in skill_ids}),
        })

    for item_id, item in sorted(items_by_id.items()):
        in_base = item_id in base_ids
        item_skill_ids = relationships.get(item_id, set()) & verified_ids
        matched_skill_ids = item_skill_ids & requirement_skill_ids
        item_text = _item_text(item)
        item_overlap = _overlap(text, item_text)
        if matched_skill_ids:
            names = sorted(str(_get(verified_skills[sid], "name", sid)) for sid in matched_skill_ids)
            add_signal(100 if in_base else 65, source="normalized_skill", in_base=in_base, item_id=item_id,
                       skill_ids=matched_skill_ids,
                       reason=f"verified skill {', '.join(names)} is linked to {'the base resume' if in_base else 'the source library'}")
        for bullet_id, bullet_text in _bullet_texts(item, bullets_by_item):
            bullet_skill_ids = {
                skill_id for skill_id in requirement_skill_ids
                if any(_contains_phrase(bullet_text, alias) for alias in _skill_aliases(verified_skills[skill_id]))
            }
            phrase = _contains_phrase(bullet_text, text)
            overlap = _overlap(text, bullet_text)
            if bullet_skill_ids:
                # A bullet mention is useful evidence, but a linked normalized
                # skill relationship is the stronger signal.  This keeps an
                # unlinked mention explainably below the well-represented
                # threshold while still surfacing it as weak coverage.
                bullet_score = 85 if (in_base and bullet_skill_ids & item_skill_ids) else (70 if in_base else 55)
                add_signal(bullet_score, source="bullet_skill", in_base=in_base, item_id=item_id, bullet_id=bullet_id,
                           skill_ids=bullet_skill_ids,
                           reason=f"bullet {bullet_id} mentions a matched normalized skill")
            elif phrase:
                add_signal(80 if in_base else 50, source="bullet_phrase", in_base=in_base, item_id=item_id, bullet_id=bullet_id,
                           reason=f"requirement phrase appears in bullet {bullet_id}")
            elif overlap > 0:
                # Partial overlap is useful context but should not masquerade
                # as a verified match for a multi-token requirement.
                add_signal(round((55 if in_base else 35) * overlap), source="bullet_terms", in_base=in_base,
                           item_id=item_id, bullet_id=bullet_id,
                           reason=f"bullet {bullet_id} shares {round(overlap * 100)}% of requirement terms")
        if item_overlap > 0 and not matched_skill_ids:
            add_signal(round((55 if in_base else 35) * item_overlap), source="item_terms", in_base=in_base,
                       item_id=item_id, reason=f"content item shares {round(item_overlap * 100)}% of requirement terms")

    # Explicit evidence is a durable, user-visible assertion.  It outranks
    # inferred text matches but remains subject to current source ownership,
    # verified-skill, and active-library checks above.
    evidence_ids: list[int] = []
    for link in evidence_links:
        link_id = _get(link, "id")
        valid, in_base, label = _link_source(link, items_by_id, bullets_by_id, verified_ids, base_ids, relationships)
        if not valid:
            continue
        if link_id is not None:
            evidence_ids.append(int(link_id))
        source_type = str(_get(link, "source_type", "") or "")
        score = 100 if in_base else (70 if source_type == "skill" else 65)
        add_signal(score, source="explicit_evidence", in_base=in_base,
                   item_id=_get(link, "content_item_id"), bullet_id=_get(link, "bullet_id"),
                   skill_ids=[_get(link, "skill_id")] if _get(link, "skill_id") is not None else (),
                   reason=f"explicit evidence link {link_id} points to {label}")

    base_signals = [signal for signal in signals if signal["in_base_resume"]]
    library_signals = [signal for signal in signals if not signal["in_base_resume"]]
    score = max((signal["score"] for signal in signals), default=0)
    if base_signals:
        classification = "well_represented" if score >= WELL_REPRESENTED_THRESHOLD else "weakly_represented"
    elif library_signals:
        classification = "library_only"
    else:
        classification = "unsupported"
    if classification == "unsupported":
        legacy_status = "unresolved"
    elif classification == "library_only":
        legacy_status = "library_only"
    else:
        # ``represented`` is the status emitted by the original comparison
        # endpoint.  Retain it while exposing the finer-grained classification.
        legacy_status = "represented"

    matched_item_ids = sorted({signal["content_item_id"] for signal in signals if signal["content_item_id"] is not None})
    matched_bullet_ids = sorted({signal["bullet_id"] for signal in signals if signal["bullet_id"] is not None})
    matched_skill_ids = sorted({skill_id for signal in signals for skill_id in signal["skill_ids"]})
    matched_skill_names = sorted(str(_get(verified_skills[skill_id], "name", skill_id)) for skill_id in matched_skill_ids if skill_id in verified_skills)
    reasons = sorted({signal["reason"] for signal in signals})
    score_breakdown = {
        "best_signal": max((signal["score"] for signal in signals), default=0),
        "base_resume_signals": len(base_signals),
        "library_signals": len(library_signals),
        "explicit_evidence_ids": sorted(evidence_ids),
    }
    return {
        "status": legacy_status,
        "classification": classification,
        "match_classification": classification,
        "score": int(score),
        "match_score": int(score),
        "score_percent": int(score),
        "normalized_score": round(int(score) / 100, 3),
        "score_breakdown": score_breakdown,
        "matched_content_item_ids": matched_item_ids,
        "matched_bullet_ids": matched_bullet_ids,
        "matched_skill_ids": matched_skill_ids,
        "matched_skills": matched_skill_names,
        "match_reasons": reasons,
        "reasons": reasons,
        "evidence_ids": sorted(evidence_ids),
        "signals": sorted(signals, key=lambda signal: (signal["source"], signal["content_item_id"] or 0, signal["bullet_id"] or 0, signal["reason"])),
        "requirement_skill_ids": sorted(requirement_skill_ids),
    }


def classify_requirements(requirements: Iterable[Any], **kwargs: Any) -> list[dict[str, Any]]:
    """Score requirements in input order, preserving stable requirement IDs."""

    return [score_requirement(requirement, **kwargs) for requirement in requirements]
