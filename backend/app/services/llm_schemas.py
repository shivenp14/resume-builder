"""Strict, versioned wire schemas for Codex resume operations."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

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

class ProposalOutput(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    selected_entries: list[ProposalEntry] = Field(default_factory=list)
    bullet_changes: list[BulletChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    rationale: str = ""
