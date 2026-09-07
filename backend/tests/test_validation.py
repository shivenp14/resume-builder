"""Focused contracts for defensive proposal validation."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from backend.app.services.llm_schemas import ProposalOutput
from backend.app.services.validation import ValidationError, validate_proposal


def _snapshot(*, text="Built services", supporting_facts=None, tags=None, locked=False):
    return {
        "content_items": [{"id": 1, "type": "experience", "title": "Example"}],
        "bullets": [{
            "id": 10,
            "content_item_id": 1,
            "text": text,
            "supporting_facts": supporting_facts or [],
            "tags": tags or [],
            "is_locked": locked,
        }],
        "entries": [{"content_item_id": 1, "bullet_ids": [10]}],
    }


def _proposal(**overrides):
    payload = {
        "selected_entries": [{"content_item_id": 1, "bullet_ids": [10]}],
        "bullet_changes": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("selected_entries", {"content_item_id": 1}, "must be a list"),
        ("bullet_changes", ["not an object"], "must be an object"),
        ("warnings", [42], "must contain only strings"),
    ],
)
def test_malformed_nested_payloads_raise_clear_validation_errors(field, value, message):
    with pytest.raises(ValidationError, match=message):
        validate_proposal(_proposal(**{field: value}), _snapshot())


def test_duplicate_entries_and_bullet_changes_are_rejected():
    with pytest.raises(ValidationError, match="duplicate selected entries"):
        validate_proposal(
            _proposal(selected_entries=[
                {"content_item_id": 1, "bullet_ids": [10]},
                {"content_item_id": 1, "bullet_ids": [10]},
            ]),
            _snapshot(),
        )

    change = {"bullet_id": 10, "proposed_text": "Built services"}
    with pytest.raises(ValidationError, match="duplicate bullet changes"):
        validate_proposal(_proposal(bullet_changes=[change, dict(change)]), _snapshot())


def test_duplicate_ids_are_rejected_by_provider_wire_schema():
    with pytest.raises(PydanticValidationError, match="duplicate selected entries"):
        ProposalOutput.model_validate({
            "selected_entries": [
                {"content_item_id": 1, "bullet_ids": []},
                {"content_item_id": 1, "bullet_ids": []},
            ],
            "bullet_changes": [],
        })

    with pytest.raises(PydanticValidationError, match="duplicate bullet IDs"):
        ProposalOutput.model_validate({
            "selected_entries": [{"content_item_id": 1, "bullet_ids": [10, 10]}],
            "bullet_changes": [],
        })


def test_decimal_and_lowercase_technology_claims_need_grounding():
    with pytest.raises(ValidationError, match="2.5"):
        validate_proposal(
            _proposal(bullet_changes=[{"bullet_id": 10, "proposed_text": "Improved latency by 2.5x"}]),
            _snapshot(),
        )
    with pytest.raises(ValidationError, match="kubernetes"):
        validate_proposal(
            _proposal(bullet_changes=[{"bullet_id": 10, "proposed_text": "Built a kubernetes platform"}]),
            _snapshot(),
        )


def test_lowercase_technology_and_decimal_are_allowed_when_verified():
    snapshot = _snapshot(
        text="Built a python service with 2.5x throughput",
        supporting_facts=["python", "2.5x throughput"],
    )
    validate_proposal(
        _proposal(bullet_changes=[{"bullet_id": 10, "proposed_text": "Built a python service with 2.5x throughput"}]),
        snapshot,
    )
