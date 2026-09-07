"""Strict, versioned wire schemas for Codex resume operations."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class JobAnalysisOutput(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    requirements: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    preferred_qualifications: list[str] = Field(default_factory=list)

class BulletChange(StrictModel):
    bullet_id: int
    proposed_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)

class ProposalEntry(StrictModel):
    content_item_id: int
    bullet_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_duplicate_bullets(self):
        if len(self.bullet_ids) != len(set(self.bullet_ids)):
            raise ValueError("proposal entry contains duplicate bullet IDs")
        return self

class ProposalOutput(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    selected_entries: list[ProposalEntry] = Field(default_factory=list)
    bullet_changes: list[BulletChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rationale: str = ""

    @model_validator(mode="after")
    def no_duplicate_selection_or_changes(self):
        entry_ids = [entry.content_item_id for entry in self.selected_entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("proposal contains duplicate selected entries")
        change_ids = [change.bullet_id for change in self.bullet_changes]
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("proposal contains duplicate bullet changes")
        return self
