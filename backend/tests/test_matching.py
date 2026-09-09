"""Focused, dependency-light contracts for deterministic matching rules."""

from types import SimpleNamespace as Record

from backend.app.services.matching import score_requirement


def _inputs(*, base=True, relationship=True, bullet_text="Built Python APIs", verified=True):
    skill = Record(id=1, name="Python", aliases=["py"], verified=verified)
    item = Record(id=1, title="Builder", summary="", organization="", tags=[])
    bullet = Record(id=1, content_item_id=1, text=bullet_text)
    requirement = Record(id=10, text="Build Python services")
    return dict(
        requirement=requirement,
        items=[item],
        bullets_by_item={1: [bullet]},
        base_entries=[Record(content_item_id=1)] if base else [],
        relationships_by_item={1: {1}} if relationship else {1: set()},
        skills=[skill],
    )


def test_verified_normalized_skill_is_well_represented_in_base():
    result = score_requirement(**_inputs())

    assert result["classification"] == "well_represented"
    assert result["score"] == 100
    assert result["matched_skills"] == ["Python"]
    assert "verified skill Python" in " ".join(result["match_reasons"])


def test_same_verified_skill_outside_base_is_library_only():
    result = score_requirement(**_inputs(base=False))

    assert result["classification"] == "library_only"
    assert result["score"] == 65
    assert result["matched_content_item_ids"] == [1]


def test_partial_text_evidence_is_weak_and_explainable():
    result = score_requirement(**_inputs(relationship=False))

    assert result["classification"] == "weakly_represented"
    assert 0 < result["score"] < 75
    assert result["matched_bullet_ids"] == [1]
    assert any("shares" in reason for reason in result["match_reasons"])


def test_unverified_skill_and_stale_relationship_do_not_match():
    result = score_requirement(**_inputs(verified=False, bullet_text="Built backend systems"))

    assert result["classification"] == "unsupported"
    assert result["score"] == 0
    assert result["matched_skills"] == []


def test_result_order_and_reasons_are_deterministic():
    inputs = _inputs()
    first = score_requirement(**inputs)
    second = score_requirement(**inputs)

    assert first == second


def test_explicit_evidence_is_strong_and_source_scoped():
    inputs = _inputs(relationship=False, bullet_text="Unrelated wording")
    inputs["evidence_links"] = [
        Record(
            id=42,
            source_type="bullet",
            content_item_id=1,
            bullet_id=1,
            skill_id=None,
        )
    ]
    result = score_requirement(**inputs)

    assert result["classification"] == "well_represented"
    assert result["evidence_ids"] == [42]
    assert result["score_breakdown"]["explicit_evidence_ids"] == [42]
