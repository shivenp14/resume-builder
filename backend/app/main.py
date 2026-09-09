"""Local-first Resume Builder API.

The API deliberately keeps AI output as proposals: source records and revisions
are never silently changed by analysis or optimization.
"""
from datetime import datetime, timezone
import copy, hashlib, json, re
from pathlib import Path
from typing import Any, Literal
import uuid
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError
from sqlalchemy import create_engine, String, Text, Integer, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint, CheckConstraint, event, func, or_, text, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from .services.renderer import ResumeRenderer, count_pdf_pages
from .services.validation import ValidationError, validate_resume_snapshot, validate_proposal
from .services.codex_provider import CodexProvider, CodexProviderError, MODEL, REASONING
from .services.llm_prompts import PROMPT_VERSION
from .services.llm_schemas import SCHEMA_VERSION
from .services.revision_comparison import compare_snapshots
from .services.skills import SkillNormalizationError, display_skill_name, normalize_skill_name

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(connection, _record):
    """SQLite disables foreign-key enforcement unless every connection opts in."""
    connection.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class ContentItem(Base):
    __tablename__="content_items"
    id: Mapped[int]=mapped_column(primary_key=True); type: Mapped[str]=mapped_column(String(40)); title: Mapped[str]=mapped_column(String(200)); organization: Mapped[str|None]=mapped_column(String(200)); location: Mapped[str|None]=mapped_column(String(200)); start_date: Mapped[str|None]=mapped_column(String(30)); end_date: Mapped[str|None]=mapped_column(String(30)); summary: Mapped[str|None]=mapped_column(Text); tags: Mapped[list]=mapped_column(JSON,default=list); is_archived: Mapped[bool]=mapped_column(Boolean,default=False); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); updated_at: Mapped[datetime]=mapped_column(DateTime,default=now,onupdate=now)
    bullets: Mapped[list["Bullet"]]=relationship(cascade="all, delete-orphan")
    skill_relationships: Mapped[list["ContentItemSkill"]]=relationship(cascade="all, delete-orphan")
class Bullet(Base):
    __tablename__="bullets"
    id: Mapped[int]=mapped_column(primary_key=True); content_item_id: Mapped[int]=mapped_column(ForeignKey("content_items.id")); text: Mapped[str]=mapped_column(Text); tags: Mapped[list]=mapped_column(JSON,default=list); supporting_facts: Mapped[list]=mapped_column(JSON,default=list); is_locked: Mapped[bool]=mapped_column(Boolean,default=False); is_preferred: Mapped[bool]=mapped_column(Boolean,default=False)
class Skill(Base):
    __tablename__="skills"
    id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(120),unique=True); category: Mapped[str|None]=mapped_column(String(80)); aliases: Mapped[list]=mapped_column(JSON,default=list); notes: Mapped[str|None]=mapped_column(Text); verified: Mapped[bool]=mapped_column(Boolean,default=False)
    alias_rows: Mapped[list["SkillAlias"]]=relationship(cascade="all, delete-orphan")
    source_relationships: Mapped[list["ContentItemSkill"]]=relationship(cascade="all, delete-orphan")
class SkillAlias(Base):
    """A normalized, globally unique alternate name for a canonical skill."""
    __tablename__="skill_aliases"
    __table_args__=(UniqueConstraint("normalized_name",name="uq_skill_alias_normalized_name"),
                    UniqueConstraint("skill_id","normalized_name",name="uq_skill_alias_skill_name"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    skill_id: Mapped[int]=mapped_column(ForeignKey("skills.id",ondelete="CASCADE"),index=True)
    alias: Mapped[str]=mapped_column(String(120))
    normalized_name: Mapped[str]=mapped_column(String(120),index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class ContentItemSkill(Base):
    """Explicit source-library relationship between content and a skill."""
    __tablename__="content_item_skills"
    __table_args__=(UniqueConstraint("content_item_id","skill_id",name="uq_content_item_skill"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    content_item_id: Mapped[int]=mapped_column(ForeignKey("content_items.id",ondelete="CASCADE"),index=True)
    skill_id: Mapped[int]=mapped_column(ForeignKey("skills.id",ondelete="CASCADE"),index=True)
    source: Mapped[str]=mapped_column(String(40),default="manual")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)

# ``ContentSkill`` was the name used by an early local prototype.  Retaining
# the alias costs nothing and lets imports of that prototype keep working.
ContentSkill = ContentItemSkill
SkillRelationship = ContentItemSkill
SourceSkill = ContentItemSkill
class PersonalInformation(Base):
    """Typed, reusable contact/profile data for a resume.

    Older databases kept this data in ``base_resumes.layout_settings`` under
    ``contact``.  The legacy value remains readable for compatibility, while
    this row is now the canonical editable source for snapshots and proposals.
    """
    __tablename__="personal_information"
    id: Mapped[int]=mapped_column(primary_key=True)
    name: Mapped[str|None]=mapped_column(String(200),nullable=True)
    location: Mapped[str|None]=mapped_column(String(200),nullable=True)
    email: Mapped[str|None]=mapped_column(String(320),nullable=True)
    phone: Mapped[str|None]=mapped_column(String(80),nullable=True)
    linkedin: Mapped[str|None]=mapped_column(String(500),nullable=True)
    github: Mapped[str|None]=mapped_column(String(500),nullable=True)
    website: Mapped[str|None]=mapped_column(String(500),nullable=True)
    summary: Mapped[str|None]=mapped_column(Text,nullable=True)
    notes: Mapped[str|None]=mapped_column(Text,nullable=True)
    is_primary: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=now,onupdate=now)
class BaseResume(Base):
    __tablename__="base_resumes"
    id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(120)); template_id: Mapped[str]=mapped_column(String(80),default="default"); section_order: Mapped[list]=mapped_column(JSON,default=list); layout_settings: Mapped[dict]=mapped_column(JSON,default=dict); personal_information_id: Mapped[int|None]=mapped_column(ForeignKey("personal_information.id"),nullable=True)
class BaseEntry(Base):
    __tablename__="base_entries"
    __table_args__=(UniqueConstraint("base_resume_id", "content_item_id", name="uq_base_entry_resume_item"),)
    id: Mapped[int]=mapped_column(primary_key=True); base_resume_id: Mapped[int]=mapped_column(ForeignKey("base_resumes.id")); content_item_id: Mapped[int]=mapped_column(ForeignKey("content_items.id")); selected_bullet_ids: Mapped[list]=mapped_column(JSON,default=list); entry_order: Mapped[int]=mapped_column(Integer,default=0)
class Application(Base):
    __tablename__="applications"
    id: Mapped[int]=mapped_column(primary_key=True); company: Mapped[str]=mapped_column(String(200)); position: Mapped[str]=mapped_column(String(200)); job_url: Mapped[str|None]=mapped_column(String(500)); job_description: Mapped[str]=mapped_column(Text); notes: Mapped[str|None]=mapped_column(Text); status: Mapped[str]=mapped_column(String(30),default="draft"); base_resume_id: Mapped[int]=mapped_column(ForeignKey("base_resumes.id")); source: Mapped[str|None]=mapped_column(String(120)); location: Mapped[str|None]=mapped_column(String(200)); employment_type: Mapped[str|None]=mapped_column(String(80)); salary_range: Mapped[str|None]=mapped_column(String(120)); contact_name: Mapped[str|None]=mapped_column(String(200)); contact_email: Mapped[str|None]=mapped_column(String(320)); application_deadline: Mapped[str|None]=mapped_column(String(40)); applied_at: Mapped[str|None]=mapped_column(String(40)); follow_up_at: Mapped[str|None]=mapped_column(String(40)); submitted_revision_id: Mapped[int|None]=mapped_column(ForeignKey("revisions.id"),nullable=True); submitted_at: Mapped[datetime|None]=mapped_column(DateTime,nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); updated_at: Mapped[datetime]=mapped_column(DateTime,default=now,onupdate=now)
class ApplicationStatusHistory(Base):
    __tablename__="application_status_history"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); from_status: Mapped[str|None]=mapped_column(String(30),nullable=True); to_status: Mapped[str]=mapped_column(String(30)); reason: Mapped[str|None]=mapped_column(Text,nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
    @property
    def status(self): return self.to_status
    @property
    def changed_at(self): return self.created_at
class JobAnalysis(Base):
    __tablename__="job_analyses"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirements: Mapped[list]=mapped_column(JSON); keywords: Mapped[list]=mapped_column(JSON); technologies: Mapped[list]=mapped_column(JSON); responsibilities: Mapped[list]=mapped_column(JSON); preferred_qualifications: Mapped[list]=mapped_column(JSON); schema_version: Mapped[str]=mapped_column(String(40),default="2.0"); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class JobRequirement(Base):
    """A durable, application-scoped requirement extracted from a job.

    ``id`` is intentionally the public requirement identifier.  Re-analysis
    upserts by ``normalized_key`` so unchanged requirements retain the same
    id, even though each ``JobAnalysis`` row is an immutable audit record.
    """
    __tablename__="job_requirements"
    __table_args__=(UniqueConstraint("application_id", "normalized_key", name="uq_job_requirement_key"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    application_id: Mapped[int]=mapped_column(ForeignKey("applications.id",ondelete="CASCADE"),index=True)
    normalized_key: Mapped[str]=mapped_column(String(240))
    text: Mapped[str]=mapped_column(Text)
    category: Mapped[str]=mapped_column(String(40),default="required")
    priority: Mapped[str]=mapped_column(String(30),default="required")
    source_text: Mapped[str|None]=mapped_column(Text,nullable=True)
    is_active: Mapped[bool]=mapped_column(Boolean,default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=now,onupdate=now)
    evidence_links: Mapped[list["RequirementEvidenceLink"]]=relationship(cascade="all, delete-orphan")

    # These aliases make the typed model pleasant for callers that use the
    # vocabulary from the API contract rather than the storage column names.
    @property
    def requirement_id(self): return self.id
    @property
    def kind(self): return self.category
    @property
    def description(self): return self.text
    @property
    def stable_id(self): return self.normalized_key
    @property
    def requirement_type(self): return self.category
    @property
    def importance(self): return self.priority

class RequirementEvidenceLink(Base):
    """A validated link from one requirement to verified source evidence."""
    __tablename__="requirement_evidence_links"
    __table_args__=(UniqueConstraint("requirement_id", "content_item_id", "bullet_id", "skill_id", name="uq_requirement_evidence_source"),
                    CheckConstraint("content_item_id IS NOT NULL OR bullet_id IS NOT NULL OR skill_id IS NOT NULL", name="ck_requirement_evidence_has_source"),
                    CheckConstraint("source_type IN ('bullet', 'content_item', 'skill')", name="ck_requirement_evidence_source_type"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    requirement_id: Mapped[int]=mapped_column(ForeignKey("job_requirements.id",ondelete="CASCADE"),index=True)
    content_item_id: Mapped[int|None]=mapped_column(ForeignKey("content_items.id",ondelete="CASCADE"),nullable=True,index=True)
    bullet_id: Mapped[int|None]=mapped_column(ForeignKey("bullets.id",ondelete="CASCADE"),nullable=True,index=True)
    skill_id: Mapped[int|None]=mapped_column(ForeignKey("skills.id",ondelete="CASCADE"),nullable=True,index=True)
    source_type: Mapped[str]=mapped_column(String(30),default="bullet")
    excerpt: Mapped[str|None]=mapped_column(Text,nullable=True)
    note: Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
    @property
    def source_id(self): return self.bullet_id or self.content_item_id or self.skill_id

# Public aliases retain intuitive names for integrations and older prototypes.
JobRequirementEvidence = RequirementEvidenceLink
EvidenceLink = RequirementEvidenceLink
RequirementEvidence = RequirementEvidenceLink
JobRequirementLink = RequirementEvidenceLink
class Proposal(Base):
    __tablename__="proposals"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); payload: Mapped[dict]=mapped_column(JSON); status: Mapped[str]=mapped_column(String(20),default="pending"); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Revision(Base):
    __tablename__="revisions"
    __table_args__=(UniqueConstraint("application_id", "revision_number", name="uq_revision_application_number"),)
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); revision_number: Mapped[int]; resume_json: Mapped[dict]=mapped_column(JSON); latex_path: Mapped[str|None]=mapped_column(String(500)); pdf_path: Mapped[str|None]=mapped_column(String(500)); page_count: Mapped[int|None]; status: Mapped[str]=mapped_column(String(20),default="draft"); generated_at: Mapped[datetime|None]=mapped_column(DateTime,nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class MissingConfirmation(Base):
    __tablename__="missing_confirmations"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirement: Mapped[str]=mapped_column(Text); requirement_id: Mapped[int|None]=mapped_column(ForeignKey("job_requirements.id",ondelete="SET NULL"),nullable=True,index=True); status: Mapped[str]=mapped_column(String(20),default="unresolved"); context: Mapped[dict]=mapped_column(JSON,default=dict); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class ConfirmationMaterialization(Base):
    """Auditable materialization of a confirmed requirement into source data.

    A confirmation is an application-scoped decision, while the resulting
    skill/bullet is part of the shared verified source library.  Keeping this
    small join record makes that boundary explicit and gives retries a stable,
    durable idempotency key without relying on free-form confirmation JSON.
    """
    __tablename__="confirmation_materializations"
    __table_args__=(
        UniqueConstraint("application_id", "requirement_id", "idempotency_key", name="uq_confirmation_materialization_key"),
        CheckConstraint("source_type IN ('skill', 'bullet')", name="ck_confirmation_materialization_source_type"),
        CheckConstraint(
            "(source_type = 'skill' AND skill_id IS NOT NULL AND bullet_id IS NULL) "
            "OR (source_type = 'bullet' AND bullet_id IS NOT NULL AND content_item_id IS NOT NULL AND skill_id IS NULL)",
            name="ck_confirmation_materialization_source_shape",
        ),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    confirmation_id: Mapped[int]=mapped_column(ForeignKey("missing_confirmations.id",ondelete="CASCADE"),index=True)
    application_id: Mapped[int]=mapped_column(ForeignKey("applications.id",ondelete="CASCADE"),index=True)
    requirement_id: Mapped[int]=mapped_column(ForeignKey("job_requirements.id",ondelete="CASCADE"),index=True)
    source_type: Mapped[str]=mapped_column(String(20))
    content_item_id: Mapped[int|None]=mapped_column(ForeignKey("content_items.id",ondelete="CASCADE"),nullable=True,index=True)
    bullet_id: Mapped[int|None]=mapped_column(ForeignKey("bullets.id",ondelete="CASCADE"),nullable=True,index=True)
    skill_id: Mapped[int|None]=mapped_column(ForeignKey("skills.id",ondelete="CASCADE"),nullable=True,index=True)
    idempotency_key: Mapped[str]=mapped_column(String(200))
    payload_hash: Mapped[str]=mapped_column(String(64))
    source_fingerprint: Mapped[str]=mapped_column(String(64))
    result_fingerprint: Mapped[str|None]=mapped_column(String(64),nullable=True)
    source_payload: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)

# Public aliases make the typed join discoverable to integrations that use
# ``source record`` or ``materialization`` vocabulary.
ConfirmedSourceRecord = ConfirmationMaterialization
SourceMaterialization = ConfirmationMaterialization
class OptimizationRun(Base):
    __tablename__="optimization_runs"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); operation: Mapped[str]=mapped_column(String(40)); status: Mapped[str]=mapped_column(String(20),default="running"); model: Mapped[str]=mapped_column(String(100),default="gpt-5.6-luna"); reasoning_effort: Mapped[str]=mapped_column(String(20),default="low"); prompt_version: Mapped[str]=mapped_column(String(40),default="v1"); schema_version: Mapped[str]=mapped_column(String(40),default="v1"); idempotency_key: Mapped[str|None]=mapped_column(String(200)); input_payload: Mapped[dict]=mapped_column(JSON,default=dict); output_payload: Mapped[dict|None]=mapped_column(JSON); error: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); completed_at: Mapped[datetime|None]=mapped_column(DateTime)
class ContentItemVersion(Base):
    """Immutable point-in-time copy of a content item.

    This table deliberately does not have a foreign key to ``content_items``:
    a history row must remain readable even if a future maintenance workflow
    permanently removes its source row.
    """
    __tablename__="content_item_versions"
    __table_args__=(UniqueConstraint("content_item_id", "version_number", name="uq_content_item_version_number"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    content_item_id: Mapped[int]=mapped_column(Integer,index=True)
    version_number: Mapped[int]=mapped_column(Integer)
    action: Mapped[str]=mapped_column(String(30))
    type: Mapped[str]=mapped_column(String(40))
    title: Mapped[str]=mapped_column(String(200))
    organization: Mapped[str|None]=mapped_column(String(200))
    location: Mapped[str|None]=mapped_column(String(200))
    start_date: Mapped[str|None]=mapped_column(String(30))
    end_date: Mapped[str|None]=mapped_column(String(30))
    summary: Mapped[str|None]=mapped_column(Text)
    tags: Mapped[list]=mapped_column(JSON,default=list)
    is_archived: Mapped[bool]=mapped_column(Boolean,default=False)
    changed_fields: Mapped[list]=mapped_column(JSON,default=list)
    snapshot: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class BulletVersion(Base):
    """Immutable point-in-time copy of a bullet, including deleted bullets."""
    __tablename__="bullet_versions"
    __table_args__=(UniqueConstraint("bullet_id", "version_number", name="uq_bullet_version_number"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    bullet_id: Mapped[int]=mapped_column(Integer,index=True)
    content_item_id: Mapped[int]=mapped_column(Integer,index=True)
    version_number: Mapped[int]=mapped_column(Integer)
    action: Mapped[str]=mapped_column(String(30))
    text: Mapped[str]=mapped_column(Text)
    tags: Mapped[list]=mapped_column(JSON,default=list)
    supporting_facts: Mapped[list]=mapped_column(JSON,default=list)
    is_locked: Mapped[bool]=mapped_column(Boolean,default=False)
    is_preferred: Mapped[bool]=mapped_column(Boolean,default=False)
    changed_fields: Mapped[list]=mapped_column(JSON,default=list)
    snapshot: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
Base.metadata.create_all(engine)

_CONTENT_VERSION_FIELDS=("type","title","organization","location","start_date","end_date","summary","tags","is_archived")
_BULLET_VERSION_FIELDS=("text","tags","supporting_facts","is_locked","is_preferred")

def _iso(value: datetime|None) -> str|None:
    return value.isoformat() if value else None

def _content_snapshot(item: ContentItem) -> dict[str,Any]:
    return {"id":item.id,"type":item.type,"title":item.title,"organization":item.organization,
        "location":item.location,"start_date":item.start_date,"end_date":item.end_date,
        "summary":item.summary,"tags":list(item.tags or []),"is_archived":bool(item.is_archived),
        "created_at":_iso(item.created_at),"updated_at":_iso(item.updated_at)}

def _bullet_snapshot(bullet: Bullet) -> dict[str,Any]:
    return {"id":bullet.id,"content_item_id":bullet.content_item_id,"text":bullet.text,
        "tags":list(bullet.tags or []),"supporting_facts":list(bullet.supporting_facts or []),
        "is_locked":bool(bullet.is_locked),"is_preferred":bool(bullet.is_preferred)}

_PERSONAL_INFORMATION_FIELDS=("name","location","email","phone","linkedin","github","website","summary","notes","is_primary")

def _contact_to_personal_values(contact: Any) -> dict[str,Any]:
    """Normalize a legacy layout contact dictionary into typed columns."""
    contact = contact if isinstance(contact, dict) else {}
    return {
        "name": contact.get("name") or contact.get("full_name"),
        "location": contact.get("location"),
        "email": contact.get("email"),
        "phone": contact.get("phone"),
        "linkedin": contact.get("linkedin"),
        "github": contact.get("github"),
        "website": contact.get("website") or contact.get("portfolio"),
        "summary": contact.get("summary"),
        "notes": contact.get("notes"),
        "is_primary": bool(contact.get("is_primary", False)),
    }

def _personal_contact(personal: PersonalInformation|None, legacy: Any = None) -> dict[str,Any]:
    """Return the stable contact shape consumed by snapshots and templates."""
    if personal is None:
        values=_contact_to_personal_values(legacy)
    else:
        values={field:getattr(personal,field) for field in _PERSONAL_INFORMATION_FIELDS}
    # Metadata fields are useful in the source API, but should not leak into
    # the renderer's contact line.
    presentation={"name","location","email","phone","linkedin","github","website"}
    result={key:value for key,value in values.items() if key in presentation and value not in (None, "")}
    if personal is None and isinstance(legacy,dict) and legacy.get("linkedin_label"):
        result["linkedin_label"]=legacy["linkedin_label"]
    return result

def _normalize_requirement_key(value: Any) -> str:
    """Return a deterministic, whitespace/case-insensitive requirement key."""
    text_value = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return re.sub(r"[^\w\s+.#-]", "", text_value).strip()

def _requirement_payload(raw: Any, *, category: str = "required", source_text: str|None = None) -> dict[str,Any] | None:
    """Normalize v1 string output and v2 structured output to one shape."""
    if isinstance(raw, str):
        text_value = raw.strip()
        raw_value: dict[str,Any] = {}
    elif isinstance(raw, dict):
        raw_value = raw
        text_value = str(raw.get("text") or raw.get("requirement") or raw.get("description") or "").strip()
    else:
        return None
    if not text_value:
        return None
    item_category = str(raw_value.get("category") or raw_value.get("kind") or category).strip().casefold()
    if item_category not in {"required", "preferred", "responsibility", "technology", "keyword", "other"}:
        item_category = category if category in {"required", "preferred", "responsibility", "technology", "keyword", "other"} else "other"
    priority = str(raw_value.get("priority") or raw_value.get("importance") or ("preferred" if item_category == "preferred" else "required")).strip().casefold()
    if priority not in {"required", "preferred", "nice_to_have", "unknown"}:
        priority = "unknown"
    return {
        "text": text_value,
        "category": item_category,
        "priority": priority,
        "source_text": source_text or raw_value.get("source_text") or text_value,
        "normalized_key": _normalize_requirement_key(text_value),
    }

def _requirement_response(requirement: JobRequirement, s: Session, *, include_evidence: bool = True) -> dict[str,Any]:
    links = []
    if include_evidence:
        for link in s.query(RequirementEvidenceLink).filter_by(requirement_id=requirement.id).order_by(RequirementEvidenceLink.id).all():
            links.append(_evidence_response(link, s))
    return {
        "id": requirement.id,
        "requirement_id": requirement.id,
        "key": requirement.normalized_key,
        "requirement_key": requirement.normalized_key,
        "stable_id": requirement.normalized_key,
        "text": requirement.text,
        "requirement": requirement.text,
        "category": requirement.category,
        "kind": requirement.category,
        "requirement_type": requirement.category,
        "priority": requirement.priority,
        "importance": requirement.priority,
        "source_text": requirement.source_text,
        "is_active": bool(requirement.is_active),
        "evidence_links": links,
        "evidence": links,
        "created_at": requirement.created_at,
        "updated_at": requirement.updated_at,
    }

def _evidence_response(link: RequirementEvidenceLink, s: Session) -> dict[str,Any]:
    """Serialize a link with stable source identifiers and human context."""
    result = {
        "id": link.id,
        "requirement_id": link.requirement_id,
        "source_type": link.source_type,
        "content_item_id": link.content_item_id,
        "bullet_id": link.bullet_id,
        "skill_id": link.skill_id,
        "source_id": link.source_id,
        "excerpt": link.excerpt,
        "note": link.note,
        "created_at": link.created_at,
    }
    if link.bullet_id is not None:
        bullet = s.get(Bullet, link.bullet_id)
        if bullet:
            result["source"] = {"type": "bullet", "id": bullet.id, "text": bullet.text, "content_item_id": bullet.content_item_id}
    elif link.content_item_id is not None:
        item = s.get(ContentItem, link.content_item_id)
        if item:
            result["source"] = {"type": "content_item", "id": item.id, "title": item.title}
    elif link.skill_id is not None:
        skill = s.get(Skill, link.skill_id)
        if skill:
            result["source"] = {"type": "skill", "id": skill.id, "name": skill.name}
    return result

def _upsert_requirement(s: Session, application_id: int, payload: dict[str,Any]) -> JobRequirement:
    """Create/update a requirement without changing its durable integer id."""
    key = payload["normalized_key"]
    requirement = s.query(JobRequirement).filter_by(application_id=application_id, normalized_key=key).first()
    if requirement is None:
        requirement = JobRequirement(application_id=application_id, normalized_key=key)
        s.add(requirement)
    requirement.text = payload["text"]
    requirement.category = payload["category"]
    requirement.priority = payload["priority"]
    requirement.source_text = payload.get("source_text")
    requirement.is_active = True
    s.flush()
    return requirement

def _analysis_requirement_rows(analysis: JobAnalysis, s: Session, *, reactivate: bool = False) -> list[JobRequirement]:
    """Return durable rows for an analysis without replaying stale mutations.

    Reads default to ``reactivate=False`` so an older audit analysis can never
    re-enable or overwrite a requirement superseded by a later analysis.
    Migration code may opt into backfilling missing legacy rows, while fresh
    analysis writes upsert their own normalized rows explicitly.
    """
    rows: list[JobRequirement] = []
    values = analysis.requirements if isinstance(analysis.requirements, list) else []
    for raw in values:
        raw_id = raw.get("id") or raw.get("requirement_id") if isinstance(raw, dict) else None
        if raw_id is not None:
            try:
                requirement = s.get(JobRequirement, int(raw_id))
            except (TypeError, ValueError):
                requirement = None
            if requirement is not None and requirement.application_id == analysis.application_id:
                rows.append(requirement)
                continue
        payload = _requirement_payload(raw)
        if payload is None:
            continue
        requirement = s.query(JobRequirement).filter_by(
            application_id=analysis.application_id, normalized_key=payload["normalized_key"]
        ).first()
        if requirement is None and reactivate:
            requirement = _upsert_requirement(s, analysis.application_id, payload)
        if requirement is not None:
            if reactivate:
                requirement = _upsert_requirement(s, analysis.application_id, payload)
            rows.append(requirement)
    # A legacy analysis may have no structured ``requirements`` but still has
    # technology output.  Those technologies are useful stable requirements
    # for comparison and confirmation routes.
    existing_keys = {row.normalized_key for row in rows}
    for technology in analysis.technologies or []:
        payload = _requirement_payload(technology, category="technology")
        if payload and payload["normalized_key"] not in existing_keys:
            requirement = s.query(JobRequirement).filter_by(
                application_id=analysis.application_id, normalized_key=payload["normalized_key"]
            ).first()
            if requirement is None and reactivate:
                requirement = _upsert_requirement(s, analysis.application_id, payload)
            if requirement is not None:
                if reactivate:
                    requirement = _upsert_requirement(s, analysis.application_id, payload)
                rows.append(requirement)
                existing_keys.add(payload["normalized_key"])
    return rows

def _analysis_response(analysis: JobAnalysis, s: Session) -> dict[str,Any]:
    rows = _analysis_requirement_rows(analysis, s, reactivate=False)
    return {
        "id": analysis.id,
        "application_id": analysis.application_id,
        "schema_version": analysis.schema_version or "1.0",
        "requirements": [_requirement_response(row, s) for row in rows],
        "requirement_texts": [row.text for row in rows],
        "keywords": list(analysis.keywords or []),
        "technologies": list(analysis.technologies or []),
        "responsibilities": list(analysis.responsibilities or []),
        "preferred_qualifications": list(analysis.preferred_qualifications or []),
        "created_at": analysis.created_at,
    }

def _requirement_mentions_skill(text_value: str, skill: Skill, s: Session) -> bool:
    """Match a requirement to a canonical skill or alias without fuzzy scoring."""
    haystack = re.sub(r"[^a-z0-9+#]+", " ", text_value.casefold())
    candidates = [skill.name, *_skill_alias_values(skill, s)]
    for candidate in candidates:
        needle = re.sub(r"[^a-z0-9+#]+", " ", candidate.casefold()).strip()
        if needle and re.search(r"(?:^|\s)" + re.escape(needle) + r"(?:$|\s)", haystack):
            return True
    return False

def _sync_requirement_skill_evidence(s: Session, requirements: list[JobRequirement], application_id: int) -> None:
    """Create idempotent skill evidence links from verified source relations."""
    if not requirements:
        return
    skill_rows = s.query(Skill).filter_by(verified=True).order_by(Skill.id).all()
    if not s.get(Application, application_id):
        return
    for requirement in requirements:
        for skill in skill_rows:
            if not _requirement_mentions_skill(requirement.text, skill, s):
                continue
            relationships = s.query(ContentItemSkill).filter_by(skill_id=skill.id).order_by(ContentItemSkill.id).all()
            for relationship in relationships:
                exists = s.query(RequirementEvidenceLink.id).filter_by(
                    requirement_id=requirement.id,
                    content_item_id=relationship.content_item_id,
                    bullet_id=None,
                    skill_id=skill.id,
                ).first()
                if exists is None:
                    s.add(RequirementEvidenceLink(
                        requirement_id=requirement.id,
                        content_item_id=relationship.content_item_id,
                        skill_id=skill.id,
                        source_type="skill",
                        note="normalized verified skill relationship",
                    ))

def _backfill_legacy_contacts(session: Session) -> int:
    """Create/link one typed record for each legacy contact exactly once."""
    created=0
    for base in session.query(BaseResume).all():
        if base.personal_information_id:
            continue
        settings=dict(base.layout_settings or {})
        if "contact" not in settings:
            continue
        values=_contact_to_personal_values(settings.get("contact"))
        values["is_primary"]=bool(settings.get("primary", values.get("is_primary", False)))
        personal=PersonalInformation(**values)
        session.add(personal); session.flush()
        base.personal_information_id=personal.id
        created += 1
    return created

def _append_content_version(s: Session, item: ContentItem, *, action: str, changed_fields: list[str]) -> ContentItemVersion:
    """Append a source snapshot; callers must commit the enclosing mutation."""
    number=(s.query(func.max(ContentItemVersion.version_number))
        .filter_by(content_item_id=item.id).scalar() or 0)+1
    snapshot=_content_snapshot(item)
    version=ContentItemVersion(content_item_id=item.id,version_number=number,action=action,
        type=item.type,title=item.title,organization=item.organization,location=item.location,
        start_date=item.start_date,end_date=item.end_date,summary=item.summary,
        tags=list(item.tags or []),is_archived=bool(item.is_archived),
        changed_fields=list(changed_fields),snapshot=snapshot,created_at=now())
    s.add(version)
    return version

def _append_bullet_version(s: Session, bullet: Bullet, *, action: str, changed_fields: list[str]) -> BulletVersion:
    number=(s.query(func.max(BulletVersion.version_number))
        .filter_by(bullet_id=bullet.id).scalar() or 0)+1
    snapshot=_bullet_snapshot(bullet)
    version=BulletVersion(bullet_id=bullet.id,content_item_id=bullet.content_item_id,
        version_number=number,action=action,text=bullet.text,tags=list(bullet.tags or []),
        supporting_facts=list(bullet.supporting_facts or []),is_locked=bool(bullet.is_locked),
        is_preferred=bool(bullet.is_preferred),changed_fields=list(changed_fields),
        snapshot=snapshot,created_at=now())
    s.add(version)
    return version


def _skill_alias_values(skill: Skill, s: Session | None = None) -> list[str]:
    """Read normalized aliases while retaining the legacy JSON fallback."""

    values: list[str] = []
    if s is not None:
        rows = s.query(SkillAlias).filter_by(skill_id=skill.id).order_by(SkillAlias.id).all()
        values.extend(row.alias for row in rows)
    # A database can be read during a rolling upgrade before the alias
    # backfill has run.  The old JSON column remains the compatibility source
    # until normalized rows exist.
    values.extend(skill.aliases or [])
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            display = display_skill_name(value)
            key = normalize_skill_name(display)
        except SkillNormalizationError:
            continue
        if key not in seen:
            seen.add(key)
            result.append(display)
    return result


def _skill_name_index(s: Session) -> dict[str, set[int]]:
    """Build a collision-aware canonical/alias index for safe matching."""

    index: dict[str, set[int]] = {}
    for skill in s.query(Skill).order_by(Skill.id).all():
        try:
            index.setdefault(normalize_skill_name(skill.name), set()).add(skill.id)
        except SkillNormalizationError:
            continue
        for alias in _skill_alias_values(skill, s):
            try:
                index.setdefault(normalize_skill_name(alias), set()).add(skill.id)
            except SkillNormalizationError:
                continue
    return index


def _skill_for_name(value: object, s: Session) -> Skill | None:
    """Resolve a canonical or alias name only when it is unambiguous."""

    try:
        key = normalize_skill_name(value)
    except SkillNormalizationError:
        return None
    ids = _skill_name_index(s).get(key, set())
    if len(ids) != 1:
        return None
    return s.get(Skill, next(iter(ids)))


def _sync_skill_aliases(s: Session, skill: Skill, aliases: list[str], *, strict: bool) -> list[str]:
    """Validate and persist aliases in both normalized and legacy storage."""

    legacy_aliases = list(aliases)
    try:
        canonical_key = normalize_skill_name(skill.name)
    except SkillNormalizationError as exc:
        raise ValueError(str(exc)) from exc
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in aliases:
        try:
            alias = display_skill_name(raw)
            key = normalize_skill_name(alias)
        except SkillNormalizationError as exc:
            if strict:
                raise ValueError(str(exc)) from exc
            continue
        if key == canonical_key or key in seen:
            # Treating the canonical spelling as an idempotent alias avoids
            # creating a self-collision when clients submit a merged list.
            continue
        # A normalized alias is globally unique.  Checking the table and the
        # in-memory legacy JSON values makes this safe during a rolling
        # migration, before every row has been backfilled.
        owner = s.query(SkillAlias).filter_by(normalized_name=key).first()
        if owner is not None and owner.skill_id != skill.id:
            if strict:
                raise ValueError(f"skill alias collides with skill {owner.skill_id}")
            continue
        for other in s.query(Skill).filter(Skill.id != skill.id).all():
            other_keys: set[str] = set()
            # During compatibility backfill, later legacy aliases are not
            # owners yet; the deterministic first row should claim the
            # normalized alias and later rows should retain their JSON value
            # without creating an ambiguous normalized relationship.  Strict
            # API writes inspect both canonical names and legacy aliases.
            other_values = [other.name, *(other.aliases or [])] if strict else [other.name]
            for other_value in other_values:
                try:
                    other_keys.add(normalize_skill_name(other_value))
                except SkillNormalizationError:
                    continue
            if key in other_keys:
                if strict:
                    raise ValueError(f"skill alias collides with skill {other.id}")
                owner = True
                break
        if owner is True:
            continue
        seen.add(key)
        cleaned.append(alias)
        row = s.query(SkillAlias).filter_by(skill_id=skill.id, normalized_name=key).first()
        if row is None:
            s.add(SkillAlias(skill_id=skill.id, alias=alias, normalized_name=key))
        else:
            row.alias = alias
    # Remove normalized rows no longer present when a skill is edited.  The
    # legacy JSON list is updated below in the same transaction.
    desired_keys = set(seen)
    for row in s.query(SkillAlias).filter_by(skill_id=skill.id).all():
        if row.normalized_name not in desired_keys:
            s.delete(row)
    # Keep old API/database consumers working.  A compatibility migration must
    # never erase a legacy value merely because a second skill owns its
    # normalized spelling; the unresolved JSON value remains visible while the
    # normalized relationship is safely omitted.  API writes are strict and
    # receive the deterministic de-duplicated display list.
    skill.aliases = cleaned if strict else legacy_aliases
    return cleaned


def _skill_response(skill: Skill, s: Session) -> dict[str, Any]:
    try:
        normalized_name = normalize_skill_name(skill.name)
    except SkillNormalizationError:
        normalized_name = None
    return {
        "id": skill.id,
        "name": skill.name,
        "normalized_name": normalized_name,
        "category": skill.category,
        "aliases": _skill_alias_values(skill, s),
        "notes": skill.notes,
        "verified": skill.verified,
    }


def _skill_link_response(link: ContentItemSkill, s: Session) -> dict[str, Any]:
    skill = s.get(Skill, link.skill_id)
    return {
        "id": link.id,
        "content_item_id": link.content_item_id,
        "skill_id": link.skill_id,
        "source": link.source,
        "skill": _skill_response(skill, s) if skill else None,
    }

def _apply_sqlite_integrity_migrations() -> None:
    """Apply additive SQLite schema changes, indexes, and source-history backfills.

    SQLAlchemy's ``create_all`` never alters an existing SQLite table.  These
    migrations therefore add lifecycle columns one at a time and create the
    status-history table/indexes without rebuilding or replacing user data.
    Every operation is idempotent so startup can safely run repeatedly.
    """
    # ``create_all`` is intentionally additive.  This also makes the migration
    # safe to call against a database created by an older application version.
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        # A database made by an earlier version has the original tables but
        # not these nullable lifecycle fields.  SQLite supports ADD COLUMN,
        # which preserves all existing rows and is safe to repeat when guarded
        # by an inspector check.
        columns = {
            "source": "VARCHAR(120)",
            "location": "VARCHAR(200)",
            "employment_type": "VARCHAR(80)",
            "salary_range": "VARCHAR(120)",
            "contact_name": "VARCHAR(200)",
            "contact_email": "VARCHAR(320)",
            "application_deadline": "VARCHAR(40)",
            "applied_at": "VARCHAR(40)",
            "follow_up_at": "VARCHAR(40)",
            "submitted_revision_id": "INTEGER",
            "submitted_at": "DATETIME",
        }
        existing = {column["name"] for column in inspect(connection).get_columns("applications")}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE applications ADD COLUMN {name} {declaration}"))
        base_resume_columns = {column["name"] for column in inspect(connection).get_columns("base_resumes")}
        if "personal_information_id" not in base_resume_columns:
            connection.execute(text("ALTER TABLE base_resumes ADD COLUMN personal_information_id INTEGER"))
        revision_columns = {column["name"] for column in inspect(connection).get_columns("revisions")}
        if "generated_at" not in revision_columns:
            connection.execute(text("ALTER TABLE revisions ADD COLUMN generated_at DATETIME"))
        analysis_columns = {column["name"] for column in inspect(connection).get_columns("job_analyses")}
        if "schema_version" not in analysis_columns:
            connection.execute(text("ALTER TABLE job_analyses ADD COLUMN schema_version VARCHAR(40) DEFAULT '1.0'"))
        confirmation_columns = {column["name"] for column in inspect(connection).get_columns("missing_confirmations")}
        if "requirement_id" not in confirmation_columns:
            connection.execute(text("ALTER TABLE missing_confirmations ADD COLUMN requirement_id INTEGER"))
        # New durable requirement/evidence tables are additive and can be
        # created safely while an older process still reads legacy JSON.
        JobRequirement.__table__.create(connection, checkfirst=True)
        RequirementEvidenceLink.__table__.create(connection, checkfirst=True)
        # Confirmed source materializations are an additive audit table.  No
        # existing confirmation is auto-materialized during migration: legacy
        # rows contain free-form context and cannot be trusted as typed source
        # claims without an explicit user resubmission.
        ConfirmationMaterialization.__table__.create(connection, checkfirst=True)
        # A legacy SQLite table cannot gain a foreign key via ADD COLUMN.  A
        # table rebuild would risk user data, so install equivalent ownership
        # guards instead.  Invalid legacy application/revision pointers are
        # cleared before the guards are installed; the application remains
        # intact and can be resubmitted explicitly through the validated API.
        # Ownership triggers below additionally protect new confirmation and
        # materialization writes.  Existing legacy confirmation rows are
        # preserved as audit data; a later explicit edit must repair them
        # through the validated application-scoped API.
        connection.execute(text("""
            UPDATE applications
            SET submitted_revision_id = NULL
            WHERE submitted_revision_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM revisions AS r
                  WHERE r.id = applications.submitted_revision_id
                    AND r.application_id = applications.id
              )
        """))
        # ``create_all`` runs before this function for the normal startup
        # path.  This call also handles a pre-migration database that did not
        # yet have the new history table.
        ApplicationStatusHistory.__table__.create(connection, checkfirst=True)
        # Preserve a useful starting point for applications created before
        # status history existed.  The NOT EXISTS guard makes this backfill
        # idempotent and never duplicates an existing event.
        connection.execute(text("""
            INSERT INTO application_status_history (application_id, from_status, to_status, reason, created_at)
            SELECT a.id, NULL, a.status, 'application migrated', CURRENT_TIMESTAMP
            FROM applications AS a
            WHERE NOT EXISTS (
                SELECT 1 FROM application_status_history AS h
                WHERE h.application_id = a.id
            )
        """))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_revision_application_number_idx ON revisions (application_id, revision_number)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_base_entry_resume_item_idx ON base_entries (base_resume_id, content_item_id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_content_item_version_number_idx ON content_item_versions (content_item_id, version_number)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_bullet_version_number_idx ON bullet_versions (bullet_id, version_number)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_base_resumes_personal_information_id ON base_resumes (personal_information_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_application_status_history_application_id ON application_status_history (application_id, created_at)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_job_requirements_application_id ON job_requirements (application_id, id)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_job_requirement_key_idx ON job_requirements (application_id, normalized_key)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_requirement_evidence_links_requirement_id ON requirement_evidence_links (requirement_id, id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_confirmation_materializations_confirmation_id ON confirmation_materializations (confirmation_id, id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_confirmation_materializations_requirement_id ON confirmation_materializations (requirement_id, id)"))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS applications_submitted_revision_insert_guard
            BEFORE INSERT ON applications
            WHEN NEW.submitted_revision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM revisions AS r
                WHERE r.id = NEW.submitted_revision_id AND r.application_id = NEW.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'submitted revision must belong to application');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS applications_submitted_revision_update_guard
            BEFORE UPDATE OF submitted_revision_id ON applications
            WHEN NEW.submitted_revision_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM revisions AS r
                WHERE r.id = NEW.submitted_revision_id AND r.application_id = NEW.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'submitted revision must belong to application');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS revisions_submitted_revision_delete_guard
            BEFORE DELETE ON revisions
            WHEN EXISTS (
                SELECT 1 FROM applications AS a
                WHERE a.submitted_revision_id = OLD.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'cannot delete submitted revision');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS revisions_submitted_revision_owner_guard
            BEFORE UPDATE OF application_id ON revisions
            WHEN NEW.application_id != OLD.application_id AND EXISTS (
                SELECT 1 FROM applications AS a
                WHERE a.submitted_revision_id = OLD.id
            )
            BEGIN
                SELECT RAISE(ABORT, 'cannot reassign submitted revision');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS missing_confirmations_requirement_owner_insert_guard
            BEFORE INSERT ON missing_confirmations
            WHEN NEW.requirement_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM job_requirements AS r
                WHERE r.id = NEW.requirement_id
                  AND r.application_id = NEW.application_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmation requirement must belong to application');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS missing_confirmations_requirement_owner_update_guard
            BEFORE UPDATE OF application_id, requirement_id ON missing_confirmations
            WHEN NEW.requirement_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM job_requirements AS r
                WHERE r.id = NEW.requirement_id
                  AND r.application_id = NEW.application_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'confirmation requirement must belong to application');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS missing_confirmations_materialized_status_guard
            BEFORE UPDATE OF status ON missing_confirmations
            WHEN OLD.status = 'confirmed'
              AND NEW.status != 'confirmed'
              AND EXISTS (
                  SELECT 1 FROM confirmation_materializations AS m
                  WHERE m.confirmation_id = OLD.id
              )
            BEGIN
                SELECT RAISE(ABORT, 'materialized confirmation cannot be demoted');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS missing_confirmations_materialized_owner_guard
            BEFORE UPDATE OF application_id, requirement_id ON missing_confirmations
            WHEN EXISTS (
                    SELECT 1 FROM confirmation_materializations AS m
                    WHERE m.confirmation_id = OLD.id
                )
              AND (
                    NEW.application_id != OLD.application_id
                    OR NEW.requirement_id IS NOT OLD.requirement_id
                )
            BEGIN
                SELECT RAISE(ABORT, 'materialized confirmation ownership is immutable');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS confirmation_materializations_owner_insert_guard
            BEFORE INSERT ON confirmation_materializations
            WHEN NOT EXISTS (
                SELECT 1
                FROM missing_confirmations AS c
                JOIN job_requirements AS r ON r.id = NEW.requirement_id
                WHERE c.id = NEW.confirmation_id
                  AND c.application_id = NEW.application_id
                  AND c.requirement_id = NEW.requirement_id
                  AND r.application_id = NEW.application_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'materialization sources must share application and requirement ownership');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS confirmation_materializations_owner_update_guard
            BEFORE UPDATE OF confirmation_id, application_id, requirement_id ON confirmation_materializations
            WHEN NOT EXISTS (
                SELECT 1
                FROM missing_confirmations AS c
                JOIN job_requirements AS r ON r.id = NEW.requirement_id
                WHERE c.id = NEW.confirmation_id
                  AND c.application_id = NEW.application_id
                  AND c.requirement_id = NEW.requirement_id
                  AND r.application_id = NEW.application_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'materialization sources must share application and requirement ownership');
            END
        """))
        connection.execute(text("""
            CREATE TRIGGER IF NOT EXISTS job_requirements_confirmation_owner_guard
            BEFORE UPDATE OF id, application_id ON job_requirements
            WHEN EXISTS (
                    SELECT 1 FROM missing_confirmations AS c
                    WHERE c.requirement_id = OLD.id
                )
            BEGIN
                SELECT RAISE(ABORT, 'requirement ownership is referenced by a confirmation');
            END
        """))

    # Older databases have source rows but no history.  Seed one immutable
    # baseline snapshot per row; the existence check makes this backfill
    # idempotent and never rewrites existing history.
    MigrationSession=sessionmaker(bind=engine,expire_on_commit=False)
    with MigrationSession() as session:
        # Normalize legacy JSON aliases into the relational table.  Conflicts
        # are skipped rather than merged: preserving two user-created skills
        # is safer than guessing which canonical skill should own an alias.
        for skill in session.query(Skill).order_by(Skill.id).all():
            _sync_skill_aliases(session, skill, list(skill.aliases or []), strict=False)
        session.flush()
        # Older imports commonly stored skill names in content-item tags.
        # Only create a relationship when the tag resolves to exactly one
        # canonical/alias owner; arbitrary tags remain untouched.
        for item in session.query(ContentItem).order_by(ContentItem.id).all():
            for tag in item.tags or []:
                skill = _skill_for_name(tag, session)
                if skill is None:
                    continue
                if not session.query(ContentItemSkill.id).filter_by(
                    content_item_id=item.id, skill_id=skill.id
                ).first():
                    session.add(ContentItemSkill(
                        content_item_id=item.id, skill_id=skill.id,
                        source="tag-migration",
                    ))
        _backfill_legacy_contacts(session)
        # Backfill normalized requirement rows from legacy JSON analyses.  The
        # normalized key and unique index make this safe across repeated
        # startups and preserve the same integer id on re-analysis.  Legacy
        # analyses are immutable audit records: only the newest analysis for
        # each application may leave its rows active.  In particular, replaying
        # an older analysis during migration must not reactivate a superseded
        # requirement.
        analyses = session.query(JobAnalysis).order_by(JobAnalysis.id).all()
        latest_by_application = {analysis.application_id: analysis for analysis in analyses}
        latest_rows_by_application: dict[int, list[JobRequirement]] = {}
        for analysis in analyses:
            rows = _analysis_requirement_rows(analysis, session, reactivate=True)
            _sync_requirement_skill_evidence(session, rows, analysis.application_id)
            if rows:
                analysis.requirements = [
                    {"id": row.id, "requirement_id": row.id, "key": row.normalized_key,
                     "text": row.text, "category": row.category, "priority": row.priority,
                     "source_text": row.source_text}
                    for row in rows
                ]
            if latest_by_application.get(analysis.application_id) is analysis:
                latest_rows_by_application[analysis.application_id] = rows
            if not analysis.schema_version:
                analysis.schema_version = "1.0"
        for application_id, latest_rows in latest_rows_by_application.items():
            session.query(JobRequirement).filter_by(application_id=application_id).update(
                {"is_active": False}, synchronize_session=False
            )
            for row in latest_rows:
                row.is_active = True
        # Existing text-keyed confirmation rows are linked only on an exact
        # normalized match.  Ambiguous or unmatched legacy rows remain intact.
        for confirmation in session.query(MissingConfirmation).filter(MissingConfirmation.requirement_id.is_(None)).all():
            key = _normalize_requirement_key(confirmation.requirement)
            candidates = session.query(JobRequirement).filter_by(
                application_id=confirmation.application_id, normalized_key=key
            ).all()
            if len(candidates) == 1:
                confirmation.requirement_id = candidates[0].id
        for item in session.query(ContentItem).all():
            if not session.query(ContentItemVersion.id).filter_by(content_item_id=item.id).first():
                _append_content_version(session,item,action="backfill",changed_fields=list(_CONTENT_VERSION_FIELDS))
        for bullet in session.query(Bullet).all():
            if not session.query(BulletVersion.id).filter_by(bullet_id=bullet.id).first():
                _append_bullet_version(session,bullet,action="backfill",changed_fields=list(_BULLET_VERSION_FIELDS))
        session.commit()

_apply_sqlite_integrity_migrations()
def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()

class ItemIn(BaseModel): type:str; title:str; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]=Field(default_factory=list); skill_ids:list[int]=Field(default_factory=list)
class ItemPatch(BaseModel): type:str|None=None; title:str|None=None; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]|None=None; skill_ids:list[int]|None=None
class DuplicateItemIn(BaseModel): title:str|None=None
class BulletIn(BaseModel): text:str; tags:list[str]=Field(default_factory=list); supporting_facts:list[str]=Field(default_factory=list); is_locked:bool=False; is_preferred:bool=False
class SkillIn(BaseModel): name:str; category:str|None=None; aliases:list[str]=Field(default_factory=list); notes:str|None=None; verified:bool=False
class SkillPatch(BaseModel): name:str|None=None; category:str|None=None; aliases:list[str]|None=None; notes:str|None=None; verified:bool|None=None
class ContentItemSkillIn(BaseModel):
    skill_id:int|None=None
    name:str|None=None
    skill:str|None=None
    source:str="manual"
class SkillAliasIn(BaseModel): alias:str
class PersonalInformationIn(BaseModel):
    name:str|None=None
    location:str|None=None
    email:str|None=None
    phone:str|None=None
    linkedin:str|None=None
    github:str|None=None
    website:str|None=None
    summary:str|None=None
    notes:str|None=None
    is_primary:bool=False
class PersonalInformationPatch(BaseModel):
    name:str|None=None
    location:str|None=None
    email:str|None=None
    phone:str|None=None
    linkedin:str|None=None
    github:str|None=None
    website:str|None=None
    summary:str|None=None
    notes:str|None=None
    is_primary:bool|None=None
# Short names keep integrations ergonomic while the longer class name remains
# the canonical public contract.
PersonalInfo = PersonalInformation
Profile = PersonalInformation
PersonalInfoIn = PersonalInformationIn
PersonalInfoPatch = PersonalInformationPatch
class ResumeIn(BaseModel):
    name:str
    template_id:str="default"
    section_order:list[str]=Field(default_factory=list)
    layout_settings:dict=Field(default_factory=dict)
    personal_information_id:int|None=None
class EntryIn(BaseModel): content_item_id:int; selected_bullet_ids:list[int]=Field(default_factory=list); entry_order:int=0
class AppIn(BaseModel):
    company:str
    position:str
    job_description:str
    base_resume_id:int
    status:str="draft"
    job_url:str|None=None
    notes:str|None=None
    source:str|None=None
    location:str|None=None
    employment_type:str|None=None
    salary_range:str|None=None
    contact_name:str|None=None
    contact_email:str|None=None
    application_deadline:str|None=None
    applied_at:str|None=None
    follow_up_at:str|None=None

class AppPatch(BaseModel):
    company:str|None=None
    position:str|None=None
    job_description:str|None=None
    base_resume_id:int|None=None
    job_url:str|None=None
    notes:str|None=None
    status:str|None=None
    status_reason:str|None=None
    source:str|None=None
    location:str|None=None
    employment_type:str|None=None
    salary_range:str|None=None
    contact_name:str|None=None
    contact_email:str|None=None
    application_deadline:str|None=None
    applied_at:str|None=None
    follow_up_at:str|None=None
    submitted_revision_id:int|None=None

class StatusChangeIn(BaseModel):
    status:str
    reason:str|None=None

class SubmitRevisionIn(BaseModel):
    revision_id:int
    submitted_at:datetime|None=None
class EvidenceLinkIn(BaseModel):
    """A link to one or more concrete, verified source records."""
    source_type:str|None=None
    content_item_id:int|None=None
    bullet_id:int|None=None
    skill_id:int|None=None
    excerpt:str|None=None
    note:str|None=None

class ProposalIn(BaseModel): payload:dict[str,Any]
class RevisionIn(BaseModel): resume_json:dict[str,Any]
class SourceRecordIn(BaseModel):
    """Typed, user-authored source data for a confirmed requirement.

    The API accepts a few vocabulary aliases (``type``/``kind`` and
    ``skill_name``/``name``) for clients migrating from the older confirmation
    context shape.  Materialization normalizes them before persistence.
    Unknown fields are rejected at this trust boundary so model output or
    arbitrary JSON cannot silently become verified source data.
    """
    model_config=ConfigDict(extra="forbid")
    source_type: Literal["skill", "bullet"]|None=None
    type: str|None=None
    kind: str|None=None
    record_type: str|None=None
    source:dict[str,Any]|None=None
    record:dict[str,Any]|None=None
    skill:dict[str,Any]|None=None
    bullet:dict[str,Any]|None=None
    skill_id:int|None=None
    name:str|None=None
    skill_name:str|None=None
    aliases:list[str]=Field(default_factory=list)
    category:str|None=None
    notes:str|None=None
    content_item_id:int|None=None
    content_item:dict[str,Any]|None=None
    bullet_id:int|None=None
    text:str|None=None
    bullet_text:str|None=None
    supporting_facts:list[str]=Field(default_factory=list)
    evidence:list[str]=Field(default_factory=list)
    tags:list[str]=Field(default_factory=list)
    is_preferred:bool=False
    excerpt:str|None=None
    note:str|None=None
    idempotency_key:str|None=None

class ConfirmationIn(BaseModel):
    status:str
    context:dict[str,Any]=Field(default_factory=dict)
    requirement_id:int|None=None
    source:SourceRecordIn|None=None
    source_type:Literal["skill", "bullet"]|None=None
    idempotency_key:str|None=None
class ConfirmationDecisionIn(BaseModel):
    requirement: str|None = Field(default=None, min_length=1, max_length=1000)
    requirement_id: int|None=None
    decision: str|None=None
    status: str|None=None
    context: dict[str,Any]=Field(default_factory=dict)
    source:SourceRecordIn|None=None
    source_type:Literal["skill", "bullet"]|None=None
    idempotency_key:str|None=None
class OptimizationRequest(BaseModel): idempotency_key:str|None=None
class GenerateIn(BaseModel): proposal_id:int

# Public DTOs deliberately separate the API contract from the ORM tables.  In
# particular they stop SQLAlchemy implementation details from leaking into the
# generated OpenAPI document as the storage layer evolves.
class APIOut(BaseModel):
    model_config=ConfigDict(from_attributes=True)
class HealthOut(APIOut): status: Literal["ok"]
class MutationOut(APIOut): archived: bool|None=None; deleted: bool|None=None
class ContentItemOut(APIOut):
    id:int; type:str; title:str; organization:str|None; location:str|None; start_date:str|None; end_date:str|None; summary:str|None; tags:list[str]; is_archived:bool; created_at:datetime; updated_at:datetime
class BulletOut(APIOut):
    id:int; content_item_id:int; text:str; tags:list[str]; supporting_facts:list[str]; is_locked:bool; is_preferred:bool
class ContentItemWithBulletsOut(ContentItemOut): bullets:list[BulletOut] = Field(default_factory=list)
class SkillOut(APIOut): id:int; name:str; category:str|None; aliases:list[str]; notes:str|None; verified:bool; normalized_name:str|None=None
class ContentItemSkillOut(APIOut): id:int; content_item_id:int; skill_id:int; source:str; skill:dict[str,Any]|None=None
class PersonalInformationOut(APIOut):
    id:int; name:str|None; location:str|None; email:str|None; phone:str|None; linkedin:str|None; github:str|None; website:str|None; summary:str|None; notes:str|None; is_primary:bool; created_at:datetime; updated_at:datetime
class BaseResumeOut(APIOut): id:int; name:str; template_id:str; section_order:list[str]; layout_settings:dict[str,Any]; personal_information_id:int|None
class BaseEntryOut(APIOut): id:int; base_resume_id:int; content_item_id:int; selected_bullet_ids:list[int]; entry_order:int
class ApplicationOut(APIOut):
    id:int; company:str; position:str; job_url:str|None; job_description:str; notes:str|None; status:str; base_resume_id:int; source:str|None; location:str|None; employment_type:str|None; salary_range:str|None; contact_name:str|None; contact_email:str|None; application_deadline:str|None; applied_at:str|None; follow_up_at:str|None; submitted_revision_id:int|None; submitted_at:datetime|None; created_at:datetime; updated_at:datetime
class StatusHistoryOut(APIOut):
    id:int; application_id:int; from_status:str|None; to_status:str; status:str; reason:str|None; created_at:datetime; changed_at:datetime
class JobAnalysisOut(APIOut):
    id:int; application_id:int; schema_version:str="2.0"; requirements:list[dict[str,Any]]; requirement_texts:list[str]=Field(default_factory=list); keywords:list[str]; technologies:list[str]; responsibilities:list[str]; preferred_qualifications:list[str]; created_at:datetime
class EvidenceLinkOut(APIOut):
    id:int; requirement_id:int; source_type:str; source_id:int|None=None; content_item_id:int|None; bullet_id:int|None; skill_id:int|None; excerpt:str|None; note:str|None; source:dict[str,Any]|None=None; created_at:datetime
class JobRequirementOut(APIOut):
    id:int; requirement_id:int; key:str; requirement_key:str; stable_id:str; text:str; requirement:str; category:str; kind:str; requirement_type:str; priority:str; importance:str; source_text:str|None; is_active:bool; evidence_links:list[EvidenceLinkOut]=Field(default_factory=list); evidence:list[EvidenceLinkOut]=Field(default_factory=list); created_at:datetime; updated_at:datetime
class ProposalOut(APIOut): id:int; application_id:int; payload:dict[str,Any]; status:str; created_at:datetime
class RevisionOut(APIOut):
    id:int; application_id:int; revision_number:int; resume_json:dict[str,Any]; latex_path:str|None; pdf_path:str|None; page_count:int|None; status:str; generated_at:datetime|None; created_at:datetime
class RevisionReferenceOut(APIOut):
    id:int; revision_number:int
class RevisionDiffChangeOut(APIOut):
    kind:Literal["added","removed","changed"]; entity:str; id:str|None; path:str; before:Any=None; after:Any=None
class RevisionDiffSummaryOut(APIOut):
    added:int; removed:int; changed:int; total:int
class RevisionComparisonOut(APIOut):
    application_id:int; from_revision:RevisionReferenceOut; to_revision:RevisionReferenceOut
    changed:bool; summary:RevisionDiffSummaryOut
    added:list[RevisionDiffChangeOut]; removed:list[RevisionDiffChangeOut]; modified:list[RevisionDiffChangeOut]; changed_items:list[RevisionDiffChangeOut]; changes:list[RevisionDiffChangeOut]
class ConfirmationOut(APIOut):
    id:int; application_id:int; requirement:str; requirement_id:int|None=None; status:str; context:dict[str,Any]; created_at:datetime
    materializations:list[dict[str,Any]]=Field(default_factory=list)
    materialization:dict[str,Any]|None=None
    source_record:dict[str,Any]|None=None
class ConfirmationMaterializationOut(APIOut):
    id:int; materialization_id:int|None=None; confirmation_id:int; application_id:int; requirement_id:int; source_type:Literal["skill", "bullet"]
    source_id:int; source_record_id:int|None=None; content_item_id:int|None=None; bullet_id:int|None=None; skill_id:int|None=None
    idempotency_key:str; payload_hash:str; source_fingerprint:str; result_fingerprint:str|None=None
    source_payload:dict[str,Any]; provenance:dict[str,Any]; source:dict[str,Any]|None=None; source_record:dict[str,Any]|None=None; created_at:datetime
class OptimizationRunOut(APIOut):
    id:int; application_id:int; operation:str; status:str; model:str; reasoning_effort:str; prompt_version:str; schema_version:str; idempotency_key:str|None; input_payload:dict[str,Any]; output_payload:dict[str,Any]|None; error:str|None; created_at:datetime; completed_at:datetime|None
class ContentItemVersionOut(APIOut):
    id:int; content_item_id:int; version_number:int; version:int; action:str; type:str; title:str; organization:str|None; location:str|None; start_date:str|None; end_date:str|None; summary:str|None; tags:list[str]; is_archived:bool; changed_fields:list[str]; snapshot:dict[str,Any]; created_at:datetime
class BulletVersionOut(APIOut):
    id:int; bullet_id:int; content_item_id:int; version_number:int; version:int; action:str; text:str; tags:list[str]; supporting_facts:list[str]; is_locked:bool; is_preferred:bool; changed_fields:list[str]; snapshot:dict[str,Any]; created_at:datetime
class ComparisonOut(APIOut):
    well_represented:list[str]; weakly_represented:list[str]; library_only:list[str]; unsupported:list[dict[str,Any]]; requirements:list[dict[str,Any]]=Field(default_factory=list)
class SnapshotOut(APIOut):
    contact:dict[str,Any]; sections:list[dict[str,Any]]; content_items:list[dict[str,Any]]; bullets:list[dict[str,Any]]; entries:list[dict[str,Any]]; provenance:dict[str,Any]|None=None
class GenerationOut(RevisionOut): proposal_id:int; latex_url:str; pdf_url:str
class RenderOut(APIOut): latex:str

def _content_version_response(version: ContentItemVersion) -> dict[str,Any]:
    return {column.name:getattr(version,column.name) for column in ContentItemVersion.__table__.columns} | {
        "version":version.version_number}

def _bullet_version_response(version: BulletVersion) -> dict[str,Any]:
    return {column.name:getattr(version,column.name) for column in BulletVersion.__table__.columns} | {
        "version":version.version_number}

app=FastAPI(title="Resume Builder API",version="0.1.0")
GENERATED = ROOT / "generated"
GENERATED.mkdir(exist_ok=True)
app.mount("/generated", StaticFiles(directory=GENERATED), name="generated")
app.mount("/checkpoints", StaticFiles(directory=ROOT / "checkpoints"), name="checkpoints")
@app.get("/health",response_model=HealthOut)
def health(): return {"status":"ok"}
@app.post("/content-items",response_model=ContentItemOut)
def create_item(x:ItemIn,s:Session=Depends(db)):
    values=x.model_dump(); skill_ids=values.pop("skill_ids",[])
    if len(skill_ids)!=len(set(skill_ids)): raise HTTPException(422,"skill_ids must be unique")
    if any(not s.get(Skill,skill_id) for skill_id in skill_ids): raise HTTPException(404,"skill not found")
    o=ContentItem(**values); s.add(o); s.flush()
    for skill_id in skill_ids: s.add(ContentItemSkill(content_item_id=o.id,skill_id=skill_id,source="manual"))
    _append_content_version(s,o,action="created",changed_fields=list(_CONTENT_VERSION_FIELDS))
    s.commit(); s.refresh(o); return o
@app.get("/content-items",response_model=list[ContentItemOut])
def items(include_archived:bool=False, archived:bool|None=None, s:Session=Depends(db)):
    """List the active library by default, with explicit archive filters.

    ``include_archived`` provides an inventory view while ``archived=true``
    narrows the result to the archive.  The default remains backwards
    compatible: archived source records never appear in the active library.
    """
    query=s.query(ContentItem)
    if archived is True:
        query=query.filter_by(is_archived=True)
    elif archived is False or not include_archived:
        query=query.filter_by(is_archived=False)
    return query.order_by(ContentItem.id).all()

@app.get("/content-items/archived",response_model=list[ContentItemOut])
@app.get("/content-items/archive",response_model=list[ContentItemOut])
def archived_items(s:Session=Depends(db)):
    """Return the reversible archive inventory without mixing it into active data."""
    return s.query(ContentItem).filter_by(is_archived=True).order_by(ContentItem.id).all()

@app.get("/content-items/{id}",response_model=ContentItemOut)
def item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    return o
@app.get("/content-items/{id}/versions",response_model=list[ContentItemVersionOut])
@app.get("/content-items/{id}/history",response_model=list[ContentItemVersionOut],include_in_schema=False)
def content_item_versions(id:int,s:Session=Depends(db)):
    records=s.query(ContentItemVersion).filter_by(content_item_id=id).order_by(ContentItemVersion.version_number).all()
    if not records and not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    return [_content_version_response(record) for record in records]
@app.get("/content-items/{id}/versions/{version_number}",response_model=ContentItemVersionOut)
@app.get("/content-items/{id}/history/{version_number}",response_model=ContentItemVersionOut,include_in_schema=False)
def content_item_version(id:int,version_number:int,s:Session=Depends(db)):
    record=s.query(ContentItemVersion).filter_by(content_item_id=id,version_number=version_number).first()
    if not record: raise HTTPException(404,"content item version not found")
    return _content_version_response(record)
@app.patch("/content-items/{id}",response_model=ContentItemOut)
def edit_item(id:int,x:ItemPatch,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    values=x.model_dump(exclude_unset=True)
    skill_ids=values.pop("skill_ids",None)
    if skill_ids is not None:
        if len(skill_ids)!=len(set(skill_ids)): raise HTTPException(422,"skill_ids must be unique")
        if any(not s.get(Skill,skill_id) for skill_id in skill_ids): raise HTTPException(404,"skill not found")
    changed=[k for k,v in values.items() if getattr(o,k)!=v]
    for k,v in values.items(): setattr(o,k,v)
    if skill_ids is not None:
        existing_links=s.query(ContentItemSkill).filter_by(content_item_id=id).all()
        existing_by_skill={link.skill_id:link for link in existing_links}
        wanted=set(skill_ids)
        for link in existing_links:
            if link.skill_id not in wanted: s.delete(link)
        for skill_id in skill_ids:
            if skill_id not in existing_by_skill:
                s.add(ContentItemSkill(content_item_id=id,skill_id=skill_id,source="manual"))
    if changed:
        o.updated_at=now()
        s.flush()
        _append_content_version(s,o,action="updated",changed_fields=changed)
    s.commit(); return o
@app.delete("/content-items/{id}",response_model=MutationOut)
def archive_item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    if not o.is_archived:
        o.is_archived=True; o.updated_at=now(); s.flush()
        _append_content_version(s,o,action="archived",changed_fields=["is_archived"])
    s.commit(); return {"archived":True}

@app.post("/content-items/{id}/restore",response_model=ContentItemOut)
def restore_item(id:int,s:Session=Depends(db)):
    """Restore an archived item without changing its bullets or references."""
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    if o.is_archived:
        o.is_archived=False; o.updated_at=now(); s.flush()
        _append_content_version(s,o,action="restored",changed_fields=["is_archived"])
    s.commit(); s.refresh(o); return o

@app.post("/content-items/{id}/duplicate",response_model=ContentItemWithBulletsOut)
def duplicate_item(id:int,x:DuplicateItemIn|None=None,s:Session=Depends(db)):
    """Copy a source item and all bullets as one transaction.

    The duplicate is always active and receives new item/bullet IDs.  No base
    resume entries are copied: references are intentionally explicit so a
    duplicate cannot silently alter an existing resume.
    """
    source=s.get(ContentItem,id)
    if not source: raise HTTPException(404,"content item not found")
    title=(x.title if x and x.title is not None else f"{source.title} (Copy)")
    if not title.strip(): raise HTTPException(422,"duplicate title cannot be blank")
    try:
        # A savepoint keeps the item and every bullet atomic even though the
        # request session may already have an open transaction from the read.
        with s.begin_nested():
            duplicate=ContentItem(
                type=source.type, title=title,
                organization=source.organization, location=source.location,
                start_date=source.start_date, end_date=source.end_date,
                summary=source.summary, tags=copy.deepcopy(source.tags or []),
                is_archived=False,
            )
            s.add(duplicate); s.flush()
            _append_content_version(s,duplicate,action="created",changed_fields=list(_CONTENT_VERSION_FIELDS))
            copied_bullets=[]
            for bullet in source.bullets:
                copied=Bullet(
                    content_item_id=duplicate.id, text=bullet.text,
                    tags=copy.deepcopy(bullet.tags or []),
                    supporting_facts=copy.deepcopy(bullet.supporting_facts or []),
                    is_locked=bullet.is_locked, is_preferred=bullet.is_preferred,
                )
                s.add(copied); s.flush()
                _append_bullet_version(s,copied,action="created",changed_fields=list(_BULLET_VERSION_FIELDS))
                copied_bullets.append(copied)
            for link in _item_skill_links([source.id],s).get(source.id,[]):
                s.add(ContentItemSkill(content_item_id=duplicate.id,skill_id=link.skill_id,source=link.source))
            s.flush()
        s.commit(); s.refresh(duplicate)
        for bullet in copied_bullets: s.refresh(bullet)
        return {**duplicate.__dict__, "bullets": copied_bullets}
    except HTTPException:
        s.rollback(); raise
    except Exception:
        s.rollback()
        raise HTTPException(500,"could not duplicate content item; no changes were saved")

@app.post("/content-items/{id}/bullets",response_model=BulletOut)
def add_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    o=Bullet(content_item_id=id,**x.model_dump()); s.add(o); s.flush()
    _append_bullet_version(s,o,action="created",changed_fields=list(_BULLET_VERSION_FIELDS))
    s.commit(); s.refresh(o); return o
@app.get("/content-items/{id}/bullets",response_model=list[BulletOut])
def get_bullets(id:int,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    return s.query(Bullet).filter_by(content_item_id=id).all()
@app.get("/bullets/{id}/versions",response_model=list[BulletVersionOut])
@app.get("/bullets/{id}/history",response_model=list[BulletVersionOut],include_in_schema=False)
def bullet_versions(id:int,s:Session=Depends(db)):
    records=s.query(BulletVersion).filter_by(bullet_id=id).order_by(BulletVersion.version_number).all()
    if not records and not s.get(Bullet,id): raise HTTPException(404,"bullet not found")
    return [_bullet_version_response(record) for record in records]
@app.get("/bullets/{id}/versions/{version_number}",response_model=BulletVersionOut)
@app.get("/bullets/{id}/history/{version_number}",response_model=BulletVersionOut,include_in_schema=False)
def bullet_version(id:int,version_number:int,s:Session=Depends(db)):
    record=s.query(BulletVersion).filter_by(bullet_id=id,version_number=version_number).first()
    if not record: raise HTTPException(404,"bullet version not found")
    return _bullet_version_response(record)
@app.patch("/bullets/{id}",response_model=BulletOut)
def edit_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    values=x.model_dump()
    changed=[k for k,v in values.items() if getattr(o,k)!=v]
    for k,v in values.items(): setattr(o,k,v)
    if changed:
        s.flush()
        _append_bullet_version(s,o,action="updated",changed_fields=changed)
    s.commit(); return o
@app.delete("/bullets/{id}",response_model=MutationOut)
def delete_bullet(id:int,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    references=[entry.id for entry in s.query(BaseEntry).all() if id in (entry.selected_bullet_ids or [])]
    if references:
        raise HTTPException(409,"bullet is selected by one or more base resume entries; remove it from those entries first")
    # Append before deleting so the last state is retained.  History rows are
    # intentionally not foreign-keyed and therefore survive this deletion.
    _append_bullet_version(s,o,action="deleted",changed_fields=[])
    s.delete(o); s.commit(); return {"deleted":True}
@app.post("/skills",response_model=SkillOut)
def add_skill(x:SkillIn,s:Session=Depends(db)):
    try:
        name = display_skill_name(x.name)
        key = normalize_skill_name(name)
    except SkillNormalizationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if _skill_name_index(s).get(key):
        raise HTTPException(409,"skill name or alias already exists")
    o=Skill(name=name,category=x.category,notes=x.notes,verified=x.verified,aliases=[])
    s.add(o)
    try:
        s.flush()
        _sync_skill_aliases(s,o,x.aliases,strict=True)
        s.commit(); s.refresh(o)
    except (ValueError, IntegrityError) as exc:
        s.rollback()
        message = str(exc.orig) if isinstance(exc, IntegrityError) and exc.orig else str(exc)
        raise HTTPException(409, message or "skill name or alias already exists") from exc
    return _skill_response(o,s)
@app.get("/skills",response_model=list[SkillOut])
def skills(s:Session=Depends(db)): return [_skill_response(skill,s) for skill in s.query(Skill).order_by(Skill.id).all()]
@app.patch("/skills/{id}",response_model=SkillOut)
def edit_skill(id:int,x:SkillPatch,s:Session=Depends(db)):
    skill=s.get(Skill,id)
    if not skill: raise HTTPException(404,"skill not found")
    values=x.model_dump(exclude_unset=True)
    new_name=skill.name
    if "name" in values:
        try:
            new_name=display_skill_name(values.pop("name"))
            key=normalize_skill_name(new_name)
        except SkillNormalizationError as exc:
            raise HTTPException(422,str(exc)) from exc
        owners={owner for owner in _skill_name_index(s).get(key,set()) if owner != skill.id}
        if owners: raise HTTPException(409,"skill name or alias already exists")
        skill.name=new_name
    aliases=values.pop("aliases",None)
    for key,value in values.items(): setattr(skill,key,value)
    try:
        s.flush()
        if aliases is not None:
            _sync_skill_aliases(s,skill,aliases,strict=True)
        else:
            # A canonical rename still needs the legacy rows rechecked.
            _sync_skill_aliases(s,skill,_skill_alias_values(skill,s),strict=True)
        s.commit(); s.refresh(skill)
    except (ValueError, IntegrityError) as exc:
        s.rollback()
        message = str(exc.orig) if isinstance(exc, IntegrityError) and exc.orig else str(exc)
        raise HTTPException(409,message or "skill name or alias already exists") from exc
    return _skill_response(skill,s)
@app.post("/skills/{id}/aliases",response_model=SkillOut)
def add_skill_alias(id:int,x:SkillAliasIn,s:Session=Depends(db)):
    skill=s.get(Skill,id)
    if not skill: raise HTTPException(404,"skill not found")
    aliases=_skill_alias_values(skill,s)
    aliases.append(x.alias)
    try:
        _sync_skill_aliases(s,skill,aliases,strict=True)
        s.commit(); s.refresh(skill)
    except (ValueError, IntegrityError) as exc:
        s.rollback()
        message = str(exc.orig) if isinstance(exc, IntegrityError) and exc.orig else str(exc)
        raise HTTPException(409,message or "skill alias already exists") from exc
    return _skill_response(skill,s)
@app.delete("/skills/{id}/aliases/{alias}",response_model=SkillOut)
def remove_skill_alias(id:int,alias:str,s:Session=Depends(db)):
    skill=s.get(Skill,id)
    if not skill: raise HTTPException(404,"skill not found")
    try:
        key=normalize_skill_name(alias)
    except SkillNormalizationError as exc:
        raise HTTPException(422,str(exc)) from exc
    row=s.query(SkillAlias).filter_by(skill_id=id,normalized_name=key).first()
    legacy_match=False
    for value in skill.aliases or []:
        try:
            if normalize_skill_name(value)==key:
                legacy_match=True
                break
        except SkillNormalizationError:
            continue
    if row is None and not legacy_match:
        raise HTTPException(404,"skill alias not found")
    if row is not None: s.delete(row)
    kept=[]
    for value in skill.aliases or []:
        try:
            matches=normalize_skill_name(value)==key
        except SkillNormalizationError:
            matches=False
        if not matches: kept.append(value)
    skill.aliases=kept
    s.commit(); s.refresh(skill)
    return _skill_response(skill,s)
@app.get("/skills/{id}",response_model=SkillOut)
def get_skill(id:int,s:Session=Depends(db)):
    skill=s.get(Skill,id)
    if not skill: raise HTTPException(404,"skill not found")
    return _skill_response(skill,s)
def _requested_skill(x:ContentItemSkillIn,s:Session) -> Skill:
    if x.skill_id is not None and (x.name is not None or x.skill is not None):
        raise HTTPException(422,"provide either skill_id or skill name, not both")
    if x.skill_id is not None:
        skill=s.get(Skill,x.skill_id)
        if not skill: raise HTTPException(404,"skill not found")
        return skill
    value=x.name if x.name is not None else x.skill
    if value is None or not value.strip():
        raise HTTPException(422,"skill_id or skill name is required")
    skill=_skill_for_name(value,s)
    if skill is None:
        raise HTTPException(404,"skill not found or skill name is ambiguous")
    return skill

@app.post("/content-items/{id}/skills",response_model=ContentItemSkillOut)
def add_content_item_skill(id:int,x:ContentItemSkillIn,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    skill=_requested_skill(x,s)
    source=x.source.strip() if isinstance(x.source,str) else ""
    if not source or len(source)>40: raise HTTPException(422,"skill relationship source must be 1-40 characters")
    link=s.query(ContentItemSkill).filter_by(content_item_id=id,skill_id=skill.id).first()
    if link is None:
        link=ContentItemSkill(content_item_id=id,skill_id=skill.id,source=source)
        s.add(link)
    else:
        link.source=source
    try:
        s.commit(); s.refresh(link)
    except IntegrityError as exc:
        s.rollback(); raise HTTPException(409,"skill relationship already exists") from exc
    return _skill_link_response(link,s)

@app.get("/content-items/{id}/skills",response_model=list[ContentItemSkillOut])
def content_item_skills(id:int,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    links=s.query(ContentItemSkill).filter_by(content_item_id=id).order_by(ContentItemSkill.id).all()
    return [_skill_link_response(link,s) for link in links]

@app.delete("/content-items/{id}/skills/{skill_id}",response_model=MutationOut)
def remove_content_item_skill(id:int,skill_id:int,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    link=s.query(ContentItemSkill).filter_by(content_item_id=id,skill_id=skill_id).first()
    if not link: raise HTTPException(404,"skill relationship not found")
    s.delete(link); s.commit(); return {"deleted":True}

@app.get("/skills/{id}/content-items",response_model=list[ContentItemSkillOut])
def skill_content_items(id:int,s:Session=Depends(db)):
    if not s.get(Skill,id): raise HTTPException(404,"skill not found")
    links=s.query(ContentItemSkill).filter_by(skill_id=id).order_by(ContentItemSkill.id).all()
    return [_skill_link_response(link,s) for link in links]

@app.post("/personal-information",response_model=PersonalInformationOut)
@app.post("/personal-info",response_model=PersonalInformationOut,include_in_schema=False)
def add_personal_information(x:PersonalInformationIn,s:Session=Depends(db)):
    values=x.model_dump()
    if values.get("is_primary"):
        s.query(PersonalInformation).update({PersonalInformation.is_primary:False},synchronize_session=False)
    o=PersonalInformation(**values); s.add(o); s.commit(); s.refresh(o); return o

@app.get("/personal-information",response_model=list[PersonalInformationOut])
@app.get("/personal-info",response_model=list[PersonalInformationOut],include_in_schema=False)
def personal_information(s:Session=Depends(db)):
    return s.query(PersonalInformation).order_by(PersonalInformation.id).all()

@app.get("/personal-information/{id}",response_model=PersonalInformationOut)
@app.get("/personal-info/{id}",response_model=PersonalInformationOut,include_in_schema=False)
def personal_information_record(id:int,s:Session=Depends(db)):
    o=s.get(PersonalInformation,id)
    if not o: raise HTTPException(404,"personal information not found")
    return o

@app.patch("/personal-information/{id}",response_model=PersonalInformationOut)
@app.put("/personal-information/{id}",response_model=PersonalInformationOut,include_in_schema=False)
@app.patch("/personal-info/{id}",response_model=PersonalInformationOut,include_in_schema=False)
@app.put("/personal-info/{id}",response_model=PersonalInformationOut,include_in_schema=False)
def edit_personal_information(id:int,x:PersonalInformationPatch,s:Session=Depends(db)):
    o=s.get(PersonalInformation,id)
    if not o: raise HTTPException(404,"personal information not found")
    values=x.model_dump(exclude_unset=True)
    if values.get("is_primary"):
        s.query(PersonalInformation).filter(PersonalInformation.id != id).update(
            {PersonalInformation.is_primary:False},synchronize_session=False)
    for key,value in values.items(): setattr(o,key,value)
    o.updated_at=now(); s.commit(); s.refresh(o); return o

@app.delete("/personal-information/{id}",response_model=MutationOut)
@app.delete("/personal-info/{id}",response_model=MutationOut,include_in_schema=False)
def delete_personal_information(id:int,s:Session=Depends(db)):
    o=s.get(PersonalInformation,id)
    if not o: raise HTTPException(404,"personal information not found")
    if s.query(BaseResume).filter_by(personal_information_id=id).first():
        raise HTTPException(409,"personal information is linked to a base resume")
    s.delete(o); s.commit(); return {"deleted":True}

def _prepare_resume_personal_information(values:dict[str,Any],s:Session, *, migrate_legacy:bool=True) -> dict[str,Any]:
    personal_id=values.get("personal_information_id")
    if personal_id is not None and not s.get(PersonalInformation,personal_id):
        raise HTTPException(404,"personal information not found")
    if migrate_legacy and personal_id is None and "contact" in (values.get("layout_settings") or {}):
        personal=PersonalInformation(**_contact_to_personal_values(values["layout_settings"].get("contact")))
        s.add(personal); s.flush(); values["personal_information_id"]=personal.id
    return values

@app.post("/base-resumes",response_model=BaseResumeOut)
def add_resume(x:ResumeIn,s:Session=Depends(db)):
    values=_prepare_resume_personal_information(x.model_dump(),s,
        migrate_legacy="personal_information_id" not in x.model_fields_set)
    o=BaseResume(**values); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/base-resumes",response_model=list[BaseResumeOut])
def resumes(s:Session=Depends(db)):
    records=s.query(BaseResume).all()
    return sorted(records,key=lambda record:(not bool((record.layout_settings or {}).get("primary")),record.id))
@app.get("/base-resumes/{id}",response_model=BaseResumeOut)
def resume(id:int,s:Session=Depends(db)):
    o=s.get(BaseResume,id)
    if not o: raise HTTPException(404,"base resume not found")
    return o
@app.patch("/base-resumes/{id}",response_model=BaseResumeOut)
def edit_resume(id:int,x:ResumeIn,s:Session=Depends(db)):
    o=s.get(BaseResume,id)
    if not o: raise HTTPException(404,"base resume not found")
    values=x.model_dump()
    explicit_personal_information="personal_information_id" in x.model_fields_set
    if not explicit_personal_information and o.personal_information_id is not None:
        values["personal_information_id"]=o.personal_information_id
    elif explicit_personal_information and values.get("personal_information_id") is None:
        # A durable unlink must not leave the legacy contact payload around:
        # startup backfill would otherwise interpret it as an un-migrated
        # record and immediately re-link the profile.
        settings=dict(values.get("layout_settings") or {})
        settings.pop("contact",None)
        values["layout_settings"]=settings
    values=_prepare_resume_personal_information(values,s,
        migrate_legacy=not explicit_personal_information)
    for k,v in values.items(): setattr(o,k,v)
    s.commit(); return o
@app.post("/base-resumes/{id}/entries",response_model=BaseEntryOut)
def add_entry(id:int,x:EntryIn,s:Session=Depends(db)):
    if not s.get(BaseResume,id): raise HTTPException(404,"resume or content item not found")
    content_item=s.get(ContentItem,x.content_item_id)
    if not content_item: raise HTTPException(404,"resume or content item not found")
    if content_item.is_archived:
        raise HTTPException(409,"cannot add an archived content item; restore it first")
    _validate_entry_bullets(x.content_item_id,x.selected_bullet_ids,s)
    o=BaseEntry(base_resume_id=id,**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/base-resumes/{id}/entries",response_model=list[BaseEntryOut])
def entries(id:int,s:Session=Depends(db)):
    if not s.get(BaseResume,id): raise HTTPException(404,"base resume not found")
    return s.query(BaseEntry).filter_by(base_resume_id=id).order_by(BaseEntry.entry_order).all()
@app.patch("/base-entries/{id}",response_model=BaseEntryOut)
def edit_entry(id:int,x:EntryIn,s:Session=Depends(db)):
    o=s.get(BaseEntry,id)
    if not o: raise HTTPException(404,"base entry not found")
    content_item=s.get(ContentItem,x.content_item_id)
    if not content_item: raise HTTPException(404,"content item not found")
    # Existing references remain valid after archival, but an edit may not
    # introduce a new reference to an inactive source record.
    if content_item.is_archived and o.content_item_id != x.content_item_id:
        raise HTTPException(409,"cannot reference an archived content item; restore it first")
    _validate_entry_bullets(x.content_item_id,x.selected_bullet_ids,s)
    for k,v in x.model_dump(exclude_unset=True).items(): setattr(o,k,v)
    s.commit(); s.refresh(o); return o
@app.delete("/base-entries/{id}",response_model=MutationOut)
def delete_entry(id:int,s:Session=Depends(db)):
    o=s.get(BaseEntry,id)
    if not o: raise HTTPException(404,"base entry not found")
    s.delete(o); s.commit(); return {"deleted":True}
APPLICATION_STATUSES={"draft","applied","interviewing","offer","rejected","withdrawn"}
APPLICATION_STATUS_TRANSITIONS={
    "draft":{"draft","applied","interviewing","offer","rejected","withdrawn"},
    "applied":{"applied","interviewing","offer","rejected","withdrawn"},
    "interviewing":{"interviewing","offer","rejected","withdrawn"},
    "offer":{"offer","rejected","withdrawn"},
    "rejected":{"rejected"},
    "withdrawn":{"withdrawn"},
}
TERMINAL_APPLICATION_STATUSES={"rejected","withdrawn"}

def _transition_application(application:Application, to_status:str, s:Session, *, reason:str|None=None, initial:bool=False) -> ApplicationStatusHistory|None:
    """Validate and apply a lifecycle transition plus its side effects.

    The caller owns the surrounding transaction, so an application update and
    its history event commit (or roll back) together.
    """
    if to_status not in APPLICATION_STATUSES:
        raise HTTPException(422,"invalid application status")
    if to_status in {"interviewing","offer"} and (application.status == "draft" or not application.applied_at):
        raise HTTPException(409,"application must be applied with applied_at before interviewing or offer status")
    if not initial and to_status not in APPLICATION_STATUS_TRANSITIONS.get(application.status,set()):
        raise HTTPException(409,f"cannot transition application from {application.status} to {to_status}")
    if to_status == "applied" and not application.applied_at:
        application.applied_at=now().isoformat()
    if not initial and application.status == to_status:
        return None
    event_record=ApplicationStatusHistory(application_id=application.id,
        from_status=None if initial else application.status,to_status=to_status,reason=reason)
    application.status=to_status
    s.add(event_record)
    return event_record

def _submittable_revision(application_id:int, revision_id:int, s:Session) -> Revision:
    revision=s.get(Revision,revision_id)
    if not revision:
        raise HTTPException(404,"revision not found")
    if revision.application_id != application_id:
        raise HTTPException(422,"revision does not belong to application")
    # A manually-created draft is intentionally not submit-ready.  Artifact
    # paths and a page count are the durable proof that PDF generation
    # completed successfully; failed/generating rows can never be submitted.
    if revision.status not in {"draft","generated"} or revision.generated_at is None or not revision.latex_path or not revision.pdf_path or revision.page_count is None:
        raise HTTPException(409,"revision must be successfully generated before submission")
    artifact_paths=[Path(revision.latex_path),Path(revision.pdf_path)]
    artifact_paths=[path if path.is_absolute() else ROOT / path for path in artifact_paths]
    if any(not path.is_file() for path in artifact_paths):
        raise HTTPException(409,"revision artifacts are missing; generate the revision again before submission")
    try:
        actual_pages=count_pdf_pages(artifact_paths[1])
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(409,f"revision PDF could not be validated: {exc}")
    if actual_pages != revision.page_count:
        raise HTTPException(409,"revision PDF page count does not match generated revision metadata")
    return revision

def _submit_revision(application:Application, revision:Revision, s:Session, *, submitted_at:datetime|None=None) -> Application:
    if application.status in TERMINAL_APPLICATION_STATUSES:
        raise HTTPException(409,f"cannot submit a revision for {application.status} application")
    # Validate the current lifecycle state as well as the submission side
    # effect.  This rejects legacy rows that reached an advanced status
    # without the required applied timestamp.
    if application.status != "draft":
        _transition_application(application,application.status,s)
    application.submitted_revision_id=revision.id
    application.submitted_at=submitted_at or now()
    if not application.applied_at:
        application.applied_at=application.submitted_at.isoformat()
    if application.status == "draft":
        _transition_application(application,"applied",s,reason="revision submitted")
    return application

@app.post("/applications",response_model=ApplicationOut)
def add_app(x:AppIn,s:Session=Depends(db)):
    if not s.get(BaseResume,x.base_resume_id): raise HTTPException(404,"base resume not found")
    if x.status not in APPLICATION_STATUSES: raise HTTPException(422,"invalid application status")
    values=x.model_dump()
    requested_status=values.pop("status")
    # applied_at is lifecycle-managed; callers may not seed or override it.
    values.pop("applied_at",None)
    o=Application(**values,status="draft"); s.add(o); s.flush()
    _transition_application(o,requested_status,s,reason="application created",initial=True)
    s.commit(); s.refresh(o); return o
@app.get("/applications",response_model=list[ApplicationOut])
def applications(status:str|None=None, q:str|None=None, limit:int=50, offset:int=0, s:Session=Depends(db)):
    if not 1 <= limit <= 100 or offset < 0: raise HTTPException(422,"limit must be 1-100 and offset must be non-negative")
    query=s.query(Application)
    if status: query=query.filter_by(status=status)
    if q:
        term=f"%{q.strip()}%"
        query=query.filter((Application.company.ilike(term)) | (Application.position.ilike(term)))
    return query.order_by(Application.created_at.desc()).offset(offset).limit(limit).all()
@app.get("/applications/{id}",response_model=ApplicationOut)
def application(id:int,s:Session=Depends(db)):
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    return o
@app.patch("/applications/{id}",response_model=ApplicationOut)
def edit_application(id:int,x:AppPatch,s:Session=Depends(db)):
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    changes=x.model_dump(exclude_unset=True)
    status_reason=changes.pop("status_reason",None)
    has_submitted_revision="submitted_revision_id" in changes
    submitted_revision_id=changes.pop("submitted_revision_id",None)
    required={"company","position","job_description"}
    if any(key in changes and not isinstance(changes[key],str) for key in required):
        raise HTTPException(422,"company, position, and job_description cannot be null")
    if any(key in changes and not changes[key].strip() for key in required):
        raise HTTPException(422,"company, position, and job_description cannot be blank")
    if "status" in changes and changes["status"] not in APPLICATION_STATUSES:
        raise HTTPException(422,"invalid application status")
    if "base_resume_id" in changes and not s.get(BaseResume,changes["base_resume_id"]):
        raise HTTPException(404,"base resume not found")
    if "applied_at" in changes:
        raise HTTPException(422,"applied_at is lifecycle-managed and cannot be changed")
    if has_submitted_revision and submitted_revision_id is None:
        raise HTTPException(422,"submitted_revision_id cannot be cleared; submit a replacement revision instead")
    if submitted_revision_id is not None:
        revision_to_submit=_submittable_revision(id,submitted_revision_id,s)
        _submit_revision(o,revision_to_submit,s)
    for key,value in changes.items():
        if key != "status": setattr(o,key,value)
    if "status" in changes:
        _transition_application(o,changes["status"],s,reason=status_reason)
    s.commit(); s.refresh(o); return o

@app.post("/applications/{id}/status",response_model=ApplicationOut)
def change_application_status(id:int,x:StatusChangeIn,s:Session=Depends(db)):
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    _transition_application(o,x.status,s,reason=x.reason)
    s.commit(); s.refresh(o); return o

@app.get("/applications/{id}/status-history",response_model=list[StatusHistoryOut])
def application_status_history(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(ApplicationStatusHistory).filter_by(application_id=id).order_by(ApplicationStatusHistory.created_at,ApplicationStatusHistory.id).all()

@app.get("/applications/{id}/history",response_model=list[StatusHistoryOut])
def application_history_alias(id:int,s:Session=Depends(db)):
    return application_status_history(id,s)

def _validate_entry_bullets(content_item_id:int, bullet_ids:list[int], s:Session) -> None:
    if len(bullet_ids) != len(set(bullet_ids)):
        raise HTTPException(422,"selected bullet ids must be unique")
    owned={b.id for b in s.query(Bullet).filter_by(content_item_id=content_item_id).all()}
    if any(bid not in owned for bid in bullet_ids):
        raise HTTPException(422,"selected bullet does not belong to content item")
def analyze_text(text:str):
    tech_vocab=["python","typescript","javascript","react","sql","docker","aws","git","java","c++","fastapi"]
    lower=text.lower(); technologies=[x for x in tech_vocab if re.search(r"\b"+re.escape(x)+r"\b",lower)]
    sentences=[x.strip() for x in re.split(r"[.!?\n]+",text) if x.strip()]
    keywords=technologies+sorted(set(re.findall(r"\b[a-zA-Z]{5,}\b",lower)))[:20]
    return {"requirements":sentences[:12],"keywords":keywords,"technologies":technologies,"responsibilities":sentences[:8],"preferred_qualifications":[]}
def _provider():
    return CodexProvider()

def _provider_call(provider, operation, payload):
    names = ("analyze",) if operation == "analysis" else ("generate_proposal", "generate_proposals")
    for name in names:
        fn=getattr(provider,name,None)
        if fn: return fn(payload)
    raise CodexProviderError("Codex provider does not implement the requested operation")

def _run_error(exc):
    if isinstance(exc, CodexProviderError): return str(exc)[:2000]
    return "Unexpected Codex provider failure"


def _item_skill_links(item_ids:list[int], s:Session) -> dict[int,list[ContentItemSkill]]:
    """Return source skill relationships grouped without changing query order."""
    if not item_ids:
        return {}
    links=s.query(ContentItemSkill).filter(ContentItemSkill.content_item_id.in_(item_ids)).order_by(ContentItemSkill.id).all()
    grouped:dict[int,list[ContentItemSkill]]={item_id:[] for item_id in item_ids}
    for link in links:
        grouped.setdefault(link.content_item_id,[]).append(link)
    return grouped

def _verified_context(a:Application,s:Session) -> dict[str,Any]:
    base=s.get(BaseResume,a.base_resume_id)
    personal=s.get(PersonalInformation,base.personal_information_id) if base and base.personal_information_id else None
    entries=s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id).order_by(BaseEntry.entry_order).all()
    # Archived records are excluded from the active library, but an existing
    # base resume must continue to resolve its historical references.  This
    # keeps archival reversible and prevents source disappearance from
    # breaking proposal validation or snapshot rendering.
    referenced_item_ids={entry.content_item_id for entry in entries}
    item_query=s.query(ContentItem)
    if referenced_item_ids:
        item_query=item_query.filter(or_(ContentItem.is_archived.is_(False), ContentItem.id.in_(referenced_item_ids)))
    else:
        item_query=item_query.filter_by(is_archived=False)
    all_items=item_query.order_by(ContentItem.id).all()
    item_ids=[item.id for item in all_items]
    all_bullets=s.query(Bullet).filter(Bullet.content_item_id.in_(item_ids)).order_by(Bullet.id).all() if item_ids else []
    skill_links=_item_skill_links(item_ids,s)
    skills_by_id={skill.id:skill for skill in s.query(Skill).order_by(Skill.id).all()}
    verified_skill_ids={skill.id for skill in skills_by_id.values() if skill.verified}
    verified={
        "content_items":[{"id":item.id,"type":item.type,"title":item.title,"organization":item.organization,
            "location":item.location,"start_date":item.start_date,"end_date":item.end_date,
            "summary":item.summary,"tags":item.tags,"is_archived":item.is_archived,
            "skill_ids":[link.skill_id for link in skill_links.get(item.id,[]) if link.skill_id in verified_skill_ids]}
            for item in all_items],
        "bullets":[{"id":bullet.id,"content_item_id":bullet.content_item_id,"text":bullet.text,
            "tags":bullet.tags,"supporting_facts":bullet.supporting_facts,"is_locked":bullet.is_locked,
            "is_preferred":bullet.is_preferred} for bullet in all_bullets],
        "skills":[_skill_response(skill,s) for skill in skills_by_id.values() if skill.verified],
        "source_skills":[
            {"content_item_id":item.id,
             "skill_ids":[link.skill_id for link in skill_links.get(item.id,[]) if link.skill_id in verified_skill_ids],
             "skills":[_skill_response(skills_by_id[link.skill_id],s)
                       for link in skill_links.get(item.id,[])
                       if link.skill_id in verified_skill_ids and link.skill_id in skills_by_id]}
            for item in all_items if any(link.skill_id in verified_skill_ids for link in skill_links.get(item.id,[]))
        ],
    }
    # Keep an explicit relationship-named key for providers that distinguish
    # source relationships from the flat verified skill catalog.
    verified["skill_relationships"] = verified["source_skills"]
    layout_settings=dict(base.layout_settings or {})
    # Once a typed record is linked, legacy contact metadata is only a
    # migration fallback and must not keep proposals stale. Hash only the
    # renderable projection, never private/non-render profile fields.
    legacy_contact=layout_settings.pop("contact",None)
    canonical_contact=_personal_contact(personal) if personal else _personal_contact(None,legacy_contact)
    source={"base_resume":{"id":base.id,"section_order":base.section_order,"layout_settings":layout_settings,
        "personal_information_id":base.personal_information_id,"contact":canonical_contact},
        "base_entries":[{"content_item_id":entry.content_item_id,"bullet_ids":entry.selected_bullet_ids,
            "entry_order":entry.entry_order} for entry in entries],**verified}
    analysis=s.query(JobAnalysis).filter_by(application_id=a.id).order_by(JobAnalysis.created_at.desc()).first()
    requirement_rows=_analysis_requirement_rows(analysis,s,reactivate=False) if analysis else []
    # Providers receive requirement-scoped links with stable source
    # identifiers, types, and any human context attached to the link. The
    # complete local projection is hashed below for freshness as well.
    evidence_by_requirement={
        str(row.id): [{
            "id": link.id,
            "requirement_id": link.requirement_id,
            "source_type": link.source_type,
            "source_id": link.source_id,
            "content_item_id": link.content_item_id,
            "bullet_id": link.bullet_id,
            "skill_id": link.skill_id,
            "excerpt": link.excerpt,
            "note": link.note,
        } for link in s.query(RequirementEvidenceLink).filter_by(
            requirement_id=row.id
        ).order_by(RequirementEvidenceLink.id).all()]
        for row in requirement_rows
    }
    source["requirements"]= [{
        "id": row.id, "requirement_id": row.id, "key": row.normalized_key,
        "requirement_key": row.normalized_key, "stable_id": row.normalized_key, "text": row.text, "category": row.category,
        "requirement_type": row.category, "priority": row.priority,
        "source_text": row.source_text, "is_active": bool(row.is_active),
        "evidence_ids": [link.id for link in s.query(RequirementEvidenceLink).filter_by(requirement_id=row.id).order_by(RequirementEvidenceLink.id).all()],
    } for row in requirement_rows]
    # Keep provider-facing requirement records compact, but hash every durable
    # requirement column so edits to identity, source text, activation state,
    # or audit timestamps invalidate an already-generated proposal.
    source["requirement_projection"] = [{
        "id": row.id,
        "application_id": row.application_id,
        "normalized_key": row.normalized_key,
        "text": row.text,
        "category": row.category,
        "priority": row.priority,
        "source_text": row.source_text,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    } for row in requirement_rows]
    source["requirement_evidence_ids"] = [
        link.id for row in requirement_rows
        for link in s.query(RequirementEvidenceLink).filter_by(requirement_id=row.id).order_by(RequirementEvidenceLink.id).all()
    ]
    source["evidence_by_requirement"] = evidence_by_requirement
    source["requirement_evidence"] = evidence_by_requirement
    source["requirement_evidence_projection"] = {
        str(row.id): [{
            "id": link.id,
            "requirement_id": link.requirement_id,
            "source_type": link.source_type,
            "source_id": link.source_id,
            "content_item_id": link.content_item_id,
            "bullet_id": link.bullet_id,
            "skill_id": link.skill_id,
            "excerpt": link.excerpt,
            "note": link.note,
            "created_at": link.created_at,
        } for link in s.query(RequirementEvidenceLink).filter_by(
            requirement_id=row.id
        ).order_by(RequirementEvidenceLink.id).all()]
        for row in requirement_rows
    }
    # Materialization provenance is part of the verified-source projection.
    # The source records themselves are already represented above, but keeping
    # this typed join in the fingerprint prevents an audit/provenance edit
    # from silently leaving an old proposal fresh.
    source["confirmation_materializations"] = [{
        "id": materialization.id,
        "confirmation_id": materialization.confirmation_id,
        "application_id": materialization.application_id,
        "requirement_id": materialization.requirement_id,
        "source_type": materialization.source_type,
        "content_item_id": materialization.content_item_id,
        "bullet_id": materialization.bullet_id,
        "skill_id": materialization.skill_id,
        "idempotency_key": materialization.idempotency_key,
        "payload_hash": materialization.payload_hash,
        "source_payload": materialization.source_payload,
        "created_at": materialization.created_at,
    } for materialization in s.query(ConfirmationMaterialization).filter(
        ConfirmationMaterialization.application_id == a.id,
        ConfirmationMaterialization.requirement_id.in_([row.id for row in requirement_rows]) if requirement_rows else False,
    ).order_by(ConfirmationMaterialization.id).all()]
    fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    base_snapshot=build_snapshot(a,s); base_snapshot.pop("contact",None)
    return {"base_snapshot":base_snapshot,"verified_library":verified,
        "requirements":source["requirements"],
        "requirement_evidence_ids":source["requirement_evidence_ids"],
        "evidence_by_requirement":evidence_by_requirement,
        "requirement_evidence":evidence_by_requirement,
        "confirmation_materializations":source["confirmation_materializations"],
        "source_fingerprint":fingerprint}

def _validate_proposal_payload(a:Application,payload:dict,s:Session,*,require_fresh:bool=True) -> dict[str,Any]:
    context=_verified_context(a,s)
    grounding={**context["verified_library"],"entries":[]}
    validate_proposal(payload,grounding)
    requirements={str(row["id"]):row for row in context.get("requirements",[])}
    evidence_by_requirement={
        str(requirement_id): {str(link["id"]) for link in links}
        for requirement_id, links in context.get("evidence_by_requirement",{}).items()
    }
    for raw_id in payload.get("requirement_ids",[]):
        if str(raw_id) not in requirements:
            raise ValidationError("proposal references unknown requirement")
    for index, raw_change in enumerate(payload.get("bullet_changes",[])):
        for raw_id in raw_change.get("requirement_ids",[]):
            if str(raw_id) not in requirements:
                raise ValidationError(f"proposal.bullet_changes[{index}] references unknown requirement")
    for index, raw_link in enumerate(payload.get("requirement_evidence",[])):
        raw_id=str(raw_link.get("requirement_id"))
        if raw_id not in requirements:
            raise ValidationError(f"proposal.requirement_evidence[{index}] references unknown requirement")
        for evidence_id in raw_link.get("evidence_ids",[]):
            if str(evidence_id) not in evidence_by_requirement.get(raw_id,set()):
                raise ValidationError(f"proposal.requirement_evidence[{index}] evidence does not belong to requirement")
    owned:dict[int,set[int]]={item["id"]:set() for item in grounding["content_items"]}
    base_item_ids={entry["content_item_id"] for entry in context["base_snapshot"]["entries"]}
    for bullet in grounding["bullets"]: owned[bullet["content_item_id"]].add(bullet["id"])
    selected_ids=[entry.get("content_item_id") for entry in payload.get("selected_entries",[])]
    if len(selected_ids)!=len(set(selected_ids)): raise ValidationError("proposal contains duplicate selected entries")
    for entry in payload.get("selected_entries",[]):
        item_id=entry.get("content_item_id")
        if item_id not in owned: raise ValidationError("proposal selects unknown content item")
        source_item=next(item for item in grounding["content_items"] if item["id"]==item_id)
        if source_item.get("is_archived") and item_id not in base_item_ids:
            raise ValidationError("proposal selects an archived content item; restore it first")
        if any(bid not in owned[item_id] for bid in entry.get("bullet_ids",[])):
            raise ValidationError("selected bullet does not belong to content item")
    if selected_ids:
        types={item["id"]:item["type"].lower() for item in grounding["content_items"]}
        required={entry["content_item_id"] for entry in context["base_snapshot"]["entries"]
            if types.get(entry["content_item_id"]) in {"education","activities"}}
        missing=required-set(selected_ids)
        if missing: raise ValidationError(f"proposal omits required base entry {min(missing)}")
    if require_fresh and payload.get("source_fingerprint") != context["source_fingerprint"]:
        raise ValidationError("proposal source data changed; generate a new proposal")
    return context

@app.post("/applications/{id}/analyze",response_model=JobAnalysisOut)
def analyze(id:int, request:OptimizationRequest|None=None, s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    key=request.idempotency_key if request else None
    if key:
        prior=s.query(OptimizationRun).filter_by(application_id=id,operation="analysis",idempotency_key=key).first()
        if prior and prior.status=="succeeded" and prior.output_payload:
            existing=s.get(JobAnalysis,prior.output_payload.get("id"))
            if existing: return _analysis_response(existing,s)
    run=OptimizationRun(application_id=id,operation="analysis",idempotency_key=key,input_payload={"job_description":a.job_description},model=MODEL,reasoning_effort=REASONING,prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION); s.add(run); s.commit()
    try:
        result=_provider_call(_provider(),"analysis",a.job_description)
        if hasattr(result,"model_dump"): result=result.model_dump()
        if not isinstance(result,dict): raise ValueError("Codex returned invalid analysis")
        fields={k:list(result.get(k,[])) for k in ("requirements","keywords","technologies","responsibilities","preferred_qualifications")}
        # Normalize both legacy v1 string output and v2 requirement objects;
        # categories for preferred/responsibility fields are retained in the
        # durable requirement table rather than inferred later by clients.
        normalized_requirements=[]
        for raw in fields["requirements"]:
            payload=_requirement_payload(raw)
            if payload: normalized_requirements.append(payload)
        for raw in fields["preferred_qualifications"]:
            payload=_requirement_payload(raw,category="preferred")
            if payload: normalized_requirements.append(payload)
        for raw in fields["responsibilities"]:
            payload=_requirement_payload(raw,category="responsibility")
            if payload: normalized_requirements.append(payload)
        # Technologies are first-class requirements when the provider did not
        # already include them in the structured list. This preserves the
        # legacy comparison behavior while giving each term a stable id.
        seen_keys={item["normalized_key"] for item in normalized_requirements}
        for raw in fields["technologies"]:
            payload=_requirement_payload(raw,category="technology")
            if payload and payload["normalized_key"] not in seen_keys:
                normalized_requirements.append(payload); seen_keys.add(payload["normalized_key"])
        o=JobAnalysis(application_id=id,requirements=normalized_requirements,
            keywords=fields["keywords"],technologies=fields["technologies"],
            responsibilities=fields["responsibilities"],preferred_qualifications=fields["preferred_qualifications"],
            schema_version=SCHEMA_VERSION); s.add(o); s.flush()
        s.query(JobRequirement).filter_by(application_id=id).update({"is_active":False}, synchronize_session=False)
        rows=[]
        for payload in normalized_requirements:
            row=_upsert_requirement(s,id,payload); rows.append(row)
        o.requirements=[{"id":row.id,"requirement_id":row.id,"key":row.normalized_key,
            "text":row.text,"category":row.category,"priority":row.priority,
            "source_text":row.source_text} for row in rows]
        # A normalized skill relationship is verified source evidence. Link
        # exact skill/alias mentions automatically, without inventing bullets
        # or promoting unverified skills.
        _sync_requirement_skill_evidence(s, rows, id)
        run.status="succeeded"; run.output_payload={"id":o.id,**fields,
            "requirements":[{"id":row.id,"text":row.text,"category":row.category} for row in rows]}; run.completed_at=now(); s.commit(); s.refresh(o); return _analysis_response(o,s)
    except Exception as exc:
        run.status="failed"; run.error=_run_error(exc); run.completed_at=now(); s.commit(); raise HTTPException(503,"Codex provider unavailable: "+run.error)
@app.get("/applications/{id}/analysis",response_model=JobAnalysisOut)
def get_analysis(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    o=s.query(JobAnalysis).filter_by(application_id=id).order_by(JobAnalysis.created_at.desc()).first()
    if not o: raise HTTPException(404,"analysis not found")
    return _analysis_response(o,s)
@app.post("/applications/{id}/proposals/generate",response_model=ProposalOut)
def generate_proposals(id:int, request:OptimizationRequest|None=None, s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    analysis=s.query(JobAnalysis).filter_by(application_id=id).order_by(JobAnalysis.created_at.desc()).first()
    if not analysis: raise HTTPException(404,"analyze application first")
    key=request.idempotency_key if request else None
    if key:
        prior=s.query(OptimizationRun).filter_by(application_id=id,operation="proposal",idempotency_key=key).first()
        if prior and prior.status=="succeeded" and prior.output_payload:
            existing=s.get(Proposal,prior.output_payload.get("id"))
            if existing: return existing
    context=_verified_context(a,s)
    payload={"analysis":{k:getattr(analysis,k) for k in ("requirements","keywords","technologies","responsibilities","preferred_qualifications")},
        "analysis_schema_version":analysis.schema_version or "1.0",
        **context,"confirmations":[{"requirement_id":x.requirement_id,"requirement":x.requirement,"status":x.status,"context":x.context}
        for x in s.query(MissingConfirmation).filter_by(application_id=id)]}
    run=OptimizationRun(application_id=id,operation="proposal",idempotency_key=key,input_payload=payload,model=MODEL,reasoning_effort=REASONING,prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION); s.add(run); s.commit()
    try:
        out=_provider_call(_provider(),"proposal",payload)
        if hasattr(out,"model_dump"): out=out.model_dump()
        if not isinstance(out,dict): raise ValueError("Codex returned invalid proposal")
        out={**out,"source_fingerprint":context["source_fingerprint"],"prompt_version":PROMPT_VERSION,
            "schema_version":SCHEMA_VERSION}
        _validate_proposal_payload(a,out,s)
        # Reuse the existing proposal validation gates before persistence.
        p=Proposal(application_id=id,payload=out); s.add(p); s.flush()
        run.status="succeeded"; run.output_payload={"id":p.id,**out}; run.completed_at=now(); s.commit(); s.refresh(p); return p
    except ValidationError as exc:
        run.status="failed"; run.error=str(exc)[:2000]; run.completed_at=now(); s.commit(); raise HTTPException(422,str(exc))
    except Exception as exc:
        run.status="failed"; run.error=_run_error(exc); run.completed_at=now(); s.commit(); raise HTTPException(503,"Codex provider unavailable: "+run.error)

@app.get("/applications/{id}/optimization-runs",response_model=list[OptimizationRunOut])
def optimization_runs(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(OptimizationRun).filter_by(application_id=id).order_by(OptimizationRun.created_at.desc()).all()

@app.get("/optimization-runs/{id}",response_model=OptimizationRunOut)
def optimization_run(id:int,s:Session=Depends(db)):
    o=s.get(OptimizationRun,id)
    if not o: raise HTTPException(404,"optimization run not found")
    return o

def _application_requirement(application_id:int, requirement_id:int, s:Session) -> JobRequirement:
    requirement=s.get(JobRequirement,requirement_id)
    if requirement is None or requirement.application_id != application_id:
        raise HTTPException(404,"requirement not found")
    return requirement

def _validated_evidence_link(application_id:int, requirement_id:int, x:EvidenceLinkIn, s:Session) -> RequirementEvidenceLink:
    _application_requirement(application_id,requirement_id,s)
    values=x.model_dump(exclude_none=True)
    content_item_id=values.get("content_item_id")
    bullet_id=values.get("bullet_id")
    skill_id=values.get("skill_id")
    if content_item_id is None and bullet_id is None and skill_id is None:
        raise HTTPException(422,"evidence link requires content_item_id, bullet_id, or skill_id")
    if content_item_id is not None and not s.get(ContentItem,content_item_id):
        raise HTTPException(404,"evidence content item not found")
    bullet=s.get(Bullet,bullet_id) if bullet_id is not None else None
    if bullet_id is not None and bullet is None:
        raise HTTPException(404,"evidence bullet not found")
    if bullet is not None:
        if content_item_id is not None and bullet.content_item_id != content_item_id:
            raise HTTPException(422,"evidence bullet does not belong to content item")
        content_item_id=bullet.content_item_id
    skill=s.get(Skill,skill_id) if skill_id is not None else None
    if skill_id is not None and skill is None:
        raise HTTPException(404,"evidence skill not found")
    if skill is not None and not skill.verified:
        raise HTTPException(422,"evidence skill must be verified")
    source_type=values.get("source_type")
    inferred="bullet" if bullet_id is not None else ("skill" if skill_id is not None else "content_item")
    if source_type is not None and source_type not in {"bullet","content_item","skill"}:
        raise HTTPException(422,"source_type must be bullet, content_item, or skill")
    if source_type is not None and source_type != inferred:
        raise HTTPException(422,"source_type does not match evidence source")
    # A normalized skill attached to a content item is evidence only when the
    # relationship itself exists; this prevents arbitrary skill claims.
    if skill is not None and content_item_id is not None and not s.query(ContentItemSkill.id).filter_by(
        content_item_id=content_item_id, skill_id=skill.id
    ).first():
        raise HTTPException(422,"evidence skill is not linked to content item")
    duplicate=s.query(RequirementEvidenceLink).filter_by(
        requirement_id=requirement_id,content_item_id=content_item_id,
        bullet_id=bullet_id,skill_id=skill_id,
    ).first()
    if duplicate is not None:
        raise HTTPException(409,"evidence link already exists")
    return RequirementEvidenceLink(
        requirement_id=requirement_id,content_item_id=content_item_id,
        bullet_id=bullet_id,skill_id=skill_id,source_type=inferred,
        excerpt=values.get("excerpt"),note=values.get("note"),
    )

@app.get("/applications/{id}/requirements",response_model=list[JobRequirementOut])
def application_requirements(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    rows=s.query(JobRequirement).filter_by(application_id=id,is_active=True).order_by(JobRequirement.id).all()
    return [_requirement_response(row,s) for row in rows]

@app.get("/applications/{id}/requirements/{requirement_id}",response_model=JobRequirementOut)
def application_requirement(id:int,requirement_id:int,s:Session=Depends(db)):
    return _requirement_response(_application_requirement(id,requirement_id,s),s)

@app.get("/requirements/{requirement_id}",response_model=JobRequirementOut)
def requirement(requirement_id:int,s:Session=Depends(db)):
    row=s.get(JobRequirement,requirement_id)
    if row is None: raise HTTPException(404,"requirement not found")
    return _requirement_response(row,s)

@app.post("/applications/{id}/requirements/{requirement_id}/evidence",response_model=EvidenceLinkOut)
def add_requirement_evidence(id:int,requirement_id:int,x:EvidenceLinkIn,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    link=_validated_evidence_link(id,requirement_id,x,s)
    s.add(link); s.commit(); s.refresh(link)
    return _evidence_response(link,s)

@app.get("/applications/{id}/requirements/{requirement_id}/evidence",response_model=list[EvidenceLinkOut])
def requirement_evidence(id:int,requirement_id:int,s:Session=Depends(db)):
    _application_requirement(id,requirement_id,s)
    return [_evidence_response(link,s) for link in s.query(RequirementEvidenceLink).filter_by(requirement_id=requirement_id).order_by(RequirementEvidenceLink.id).all()]

@app.delete("/requirement-evidence/{link_id}",response_model=MutationOut)
def delete_requirement_evidence(link_id:int,s:Session=Depends(db)):
    link=s.get(RequirementEvidenceLink,link_id)
    if link is None: raise HTTPException(404,"evidence link not found")
    s.delete(link); s.commit(); return {"deleted":True}

def _comparison(a:Application,s:Session):
    analysis=s.query(JobAnalysis).filter_by(application_id=a.id).order_by(JobAnalysis.created_at.desc()).first()
    if not analysis: raise HTTPException(404,"analyze application first")
    rows=_analysis_requirement_rows(analysis,s,reactivate=False)
    # Technology terms retain the concise legacy comparison display (for
    # example ``docker``), while prose requirements remain structured rows.
    technology_rows=[row for row in rows if row.category == "technology"]
    prose_rows=[row for row in rows if row.category != "technology"]
    selected_rows=[]; seen_rows=set()
    for row in [*technology_rows,*prose_rows]:
        if row.id not in seen_rows:
            selected_rows.append(row); seen_rows.add(row.id)
    if not selected_rows:
        for term in list(dict.fromkeys([*analysis.technologies,*analysis.keywords])):
            payload=_requirement_payload(term,category="keyword")
            if payload:
                row=_upsert_requirement(s,a.id,payload)
                if row.id not in seen_rows:
                    selected_rows.append(row); seen_rows.add(row.id)
    base_ids={e.content_item_id for e in s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id)}
    item_query=s.query(ContentItem)
    if base_ids:
        item_query=item_query.filter(or_(ContentItem.is_archived.is_(False), ContentItem.id.in_(base_ids)))
    else:
        item_query=item_query.filter_by(is_archived=False)
    all_items=item_query.all()
    item_skill_links=_item_skill_links([item.id for item in all_items],s)
    skill_index=_skill_name_index(s)
    represented=[]; weak=[]; library_only=[]; unsupported=[]; structured=[]
    for requirement in selected_rows:
        term=requirement.text
        matching_skill_ids=set()
        candidates=[term,*re.findall(r"[A-Za-z][A-Za-z0-9+#.-]*",term)]
        for candidate in candidates:
            try:
                matching_skill_ids.update(skill_index.get(normalize_skill_name(candidate),set()))
            except SkillNormalizationError:
                continue
        hits=[i for i in all_items if (
            any(link.skill_id in matching_skill_ids for link in item_skill_links.get(i.id,[]))
            or term.casefold() in ((i.title or '')+' '+(i.summary or '')).casefold()
            or any(term.casefold() in b.text.casefold() for b in i.bullets)
        )]
        link_rows=s.query(RequirementEvidenceLink).filter_by(requirement_id=requirement.id).order_by(RequirementEvidenceLink.id).all()
        evidence_payload=[_evidence_response(link,s) for link in link_rows]
        if not hits and evidence_payload:
            evidence_item_ids={link.content_item_id for link in link_rows if link.content_item_id is not None}
            hits=[item for item in all_items if item.id in evidence_item_ids]
        # A standalone verified skill is still durable library evidence even
        # when the user has not attached it to a particular experience.
        # Treat it as library-only rather than re-reporting the requirement as
        # unsupported after confirmation.
        if not hits and any(link.skill_id is not None for link in link_rows):
            status="library_only"
            library_only.append(term)
            structured.append({**_requirement_response(requirement,s),"status":status})
            continue
        if not hits:
            status="unsupported"
            unsupported.append({"requirement_id":requirement.id,"requirement_key":requirement.normalized_key,"requirement":term,"status":"unresolved","evidence_links":evidence_payload})
        elif any(i.id in base_ids for i in hits):
            status="represented"; represented.append(term)
        else:
            status="library_only"; library_only.append(term)
        structured.append({**_requirement_response(requirement,s),"status":status})
    # Objects keep stable IDs for UI confirmation while preserving the simple
    # category arrays used by older clients.
    return {'well_represented':represented,'weakly_represented':weak,'library_only':library_only,
            'unsupported':unsupported,'requirements':structured}
@app.get("/applications/{id}/comparison",response_model=ComparisonOut)
def comparison(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    return _comparison(a,s)

def _materialization_response(materialization:ConfirmationMaterialization, s:Session) -> dict[str,Any]:
    """Serialize the typed source join without exposing ORM internals."""
    source_id = materialization.bullet_id or materialization.skill_id
    source: dict[str,Any] | None = None
    if materialization.bullet_id is not None:
        bullet = s.get(Bullet, materialization.bullet_id)
        if bullet is not None:
            source = {"type":"bullet", "id":bullet.id, "text":bullet.text,
                "content_item_id":bullet.content_item_id,
                "supporting_facts":list(bullet.supporting_facts or [])}
    elif materialization.skill_id is not None:
        skill = s.get(Skill, materialization.skill_id)
        if skill is not None:
            source = {"type":"skill", "id":skill.id, "name":skill.name,
                "verified":bool(skill.verified)}
    return {
        "id": materialization.id,
        "materialization_id": materialization.id,
        "confirmation_id": materialization.confirmation_id,
        "application_id": materialization.application_id,
        "requirement_id": materialization.requirement_id,
        "source_type": materialization.source_type,
        "source_id": source_id,
        "source_record_id": source_id,
        "content_item_id": materialization.content_item_id,
        "bullet_id": materialization.bullet_id,
        "skill_id": materialization.skill_id,
        "idempotency_key": materialization.idempotency_key,
        "payload_hash": materialization.payload_hash,
        "source_fingerprint": materialization.source_fingerprint,
        "result_fingerprint": materialization.result_fingerprint,
        "source_payload": materialization.source_payload,
        "provenance": {
            "confirmation_id": materialization.confirmation_id,
            "application_id": materialization.application_id,
            "requirement_id": materialization.requirement_id,
            "source_type": materialization.source_type,
            "idempotency_key": materialization.idempotency_key,
            "source_fingerprint": materialization.source_fingerprint,
            "result_fingerprint": materialization.result_fingerprint,
        },
        "source": source,
        "source_record": source,
        "created_at": materialization.created_at,
    }

def _confirmation_response(confirmation:MissingConfirmation, s:Session) -> dict[str,Any]:
    rows=s.query(ConfirmationMaterialization).filter_by(
        confirmation_id=confirmation.id
    ).order_by(ConfirmationMaterialization.id).all()
    materializations=[_materialization_response(row,s) for row in rows]
    return {
        "id":confirmation.id,
        "application_id":confirmation.application_id,
        "requirement":confirmation.requirement,
        "requirement_id":confirmation.requirement_id,
        "status":confirmation.status,
        "context":confirmation.context or {},
        "created_at":confirmation.created_at,
        "materializations":materializations,
        "materialization":materializations[-1] if materializations else None,
        "source_record":materializations[-1].get("source") if materializations else None,
    }

def _guard_materialized_confirmation(
    confirmation:MissingConfirmation,
    s:Session,
    *,
    status:str|None=None,
    requirement_id:int|None=None,
) -> None:
    """Keep provenance immutable once a source record has been materialized."""
    materialized=s.query(ConfirmationMaterialization.id).filter_by(
        confirmation_id=confirmation.id
    ).first()
    if materialized is None:
        return
    if status is not None and status != "confirmed":
        raise HTTPException(409,"materialized confirmation cannot be demoted")
    if requirement_id is not None and requirement_id != confirmation.requirement_id:
        raise HTTPException(409,"materialized confirmation requirement cannot be changed")

def _source_input_from_confirmation(
    *,
    context:dict[str,Any] | None = None,
    source:SourceRecordIn | None = None,
    source_type:str | None = None,
    idempotency_key:str | None = None,
) -> SourceRecordIn:
    """Adapt legacy confirmation context into the strict source DTO.

    Context is intentionally treated as an input adapter only.  Persistence
    always goes through ``SourceRecordIn`` and the materializer below.
    """
    if source is not None:
        raw=source.model_dump(exclude_none=True)
        for key in ("source","record","skill","bullet"):
            nested=raw.get(key)
            if isinstance(nested,dict):
                try:
                    SourceRecordIn.model_validate(nested)
                except PydanticValidationError as exc:
                    raise HTTPException(422,"invalid confirmed source record: "+exc.errors()[0]["msg"]) from exc
                raw={**raw,**nested}
                break
        values={key:value for key,value in raw.items() if key in SourceRecordIn.model_fields and key not in {"source","record","skill","bullet"}}
    else:
        raw=dict(context or {})
        nested=raw.get("source")
        if isinstance(nested,dict):
            raw={**raw,**nested}
        for key in ("skill","bullet","record"):
            nested=raw.get(key)
            if isinstance(nested,dict):
                raw={**raw,**nested}
                break
        values={key:value for key,value in raw.items() if key in SourceRecordIn.model_fields}
    if source_type is not None:
        values["source_type"]=source_type
    if idempotency_key is not None:
        values["idempotency_key"]=idempotency_key
    try:
        return SourceRecordIn.model_validate(values)
    except PydanticValidationError as exc:
        raise HTTPException(422,"invalid confirmed source record: "+exc.errors()[0]["msg"]) from exc

def _materialization_type(source:SourceRecordIn) -> str:
    candidates=[value.strip().casefold() for value in (
        source.source_type, source.type, source.kind, source.record_type
    ) if isinstance(value,str) and value.strip()]
    if not candidates:
        if source.skill_id is not None or source.skill_name is not None or source.name is not None:
            return "skill"
        if source.bullet_id is not None or source.text is not None or source.bullet_text is not None:
            return "bullet"
        raise HTTPException(422,"confirmed source_type must be skill or bullet")
    if any(value not in {"skill","bullet"} for value in candidates):
        raise HTTPException(422,"confirmed source_type must be skill or bullet")
    if len(set(candidates)) != 1:
        raise HTTPException(422,"confirmed source type aliases disagree")
    return candidates[0]

def _clean_string_list(values:Any, field_name:str, *, required:bool=False) -> list[str]:
    if not isinstance(values,list) or any(not isinstance(value,str) or not value.strip() for value in values):
        raise HTTPException(422,f"{field_name} must be a list of non-empty strings")
    cleaned=[]
    seen=set()
    for value in values:
        value=value.strip()
        if value not in seen:
            cleaned.append(value); seen.add(value)
    if required and not cleaned:
        raise HTTPException(422,f"{field_name} must contain at least one evidence fact")
    return cleaned

def _source_payload_for_hash(source:SourceRecordIn, source_type:str) -> dict[str,Any]:
    """Return a deterministic, ID-free request projection for idempotency."""
    if source_type == "skill":
        name=source.skill_name or source.name
        return {
            "source_type":"skill",
            "skill_id":source.skill_id,
            "name":name.strip() if isinstance(name,str) else None,
            "aliases":list(source.aliases or []),
            "category":source.category,
            "notes":source.notes,
            "content_item_id":source.content_item_id,
            "excerpt":source.excerpt,
            "note":source.note,
        }
    return {
        "source_type":"bullet",
        "bullet_id":source.bullet_id,
        "content_item_id":source.content_item_id,
        "content_item":source.content_item,
        "text":(source.text or source.bullet_text).strip() if isinstance(source.text or source.bullet_text,str) else None,
        "supporting_facts":list(source.supporting_facts or source.evidence or []),
        "tags":list(source.tags or []),
        "is_preferred":bool(source.is_preferred),
        "excerpt":source.excerpt,
        "note":source.note,
    }

def _hash_payload(payload:dict[str,Any]) -> str:
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()

def _materialization_identity(
    confirmation:MissingConfirmation,
    source:SourceRecordIn,
) -> tuple[str,str,str]:
    """Return source type, payload hash, and normalized idempotency key."""
    source_type=_materialization_type(source)
    payload_hash=_hash_payload(_source_payload_for_hash(source,source_type))
    requested_key=source.idempotency_key
    if requested_key is not None:
        requested_key=requested_key.strip()
        if not requested_key or len(requested_key)>200:
            raise HTTPException(422,"idempotency_key must be 1-200 characters")
    key=requested_key or f"confirmed:{confirmation.id}:sha256:{payload_hash}"
    return source_type,payload_hash,key

def _materialize_confirmation(
    confirmation:MissingConfirmation,
    source:SourceRecordIn,
    s:Session,
) -> ConfirmationMaterialization:
    """Materialize one confirmed requirement into verified source data.

    This is deliberately deterministic and local: no provider output is
    accepted, source ownership is checked against the application/requirement,
    and the resulting evidence link and provenance row commit together.
    """
    if confirmation.status != "confirmed":
        raise HTTPException(409,"only a confirmed requirement can be materialized")
    if confirmation.requirement_id is None:
        raise HTTPException(422,"confirmed source materialization requires requirement_id")
    application=s.get(Application,confirmation.application_id)
    if application is None:
        raise HTTPException(404,"application not found")
    requirement=_application_requirement(application.id,confirmation.requirement_id,s)
    source_type,payload_hash,idempotency_key=_materialization_identity(confirmation,source)
    request_payload=_source_payload_for_hash(source,source_type)
    existing=s.query(ConfirmationMaterialization).filter_by(
        application_id=application.id,requirement_id=requirement.id,idempotency_key=idempotency_key
    ).first()
    if existing is not None:
        if existing.payload_hash != payload_hash:
            raise HTTPException(409,"idempotency key was already used for a different source record")
        return existing
    before_fingerprint=_verified_context(application,s)["source_fingerprint"]

    content_item_id=None
    bullet_id=None
    skill_id=None
    source_payload=dict(request_payload)
    if source_type == "skill":
        if source.bullet_id is not None or source.text is not None or source.bullet_text is not None:
            raise HTTPException(422,"skill materialization cannot include bullet fields")
        name=source.skill_name or source.name
        if source.skill_id is not None and name is not None:
            skill=s.get(Skill,source.skill_id)
            if skill is None:
                raise HTTPException(404,"source skill not found")
            try:
                if normalize_skill_name(name) != normalize_skill_name(skill.name):
                    raise HTTPException(422,"skill name does not match skill_id")
            except SkillNormalizationError as exc:
                raise HTTPException(422,str(exc)) from exc
        elif source.skill_id is not None:
            skill=s.get(Skill,source.skill_id)
            if skill is None:
                raise HTTPException(404,"source skill not found")
        else:
            if not isinstance(name,str) or not name.strip():
                raise HTTPException(422,"skill materialization requires name")
            try:
                display_name=display_skill_name(name)
            except SkillNormalizationError as exc:
                raise HTTPException(422,str(exc)) from exc
            skill=_skill_for_name(display_name,s)
            if skill is None:
                skill=Skill(name=display_name,category=source.category,notes=source.notes,aliases=[],verified=True)
                s.add(skill); s.flush()
            elif source.category is not None and skill.category is None:
                skill.category=source.category
            if source.notes is not None and skill.notes is None:
                skill.notes=source.notes
        # A confirmation is explicit human verification.  Existing
        # unverified rows may be promoted, but their editable fields are never
        # overwritten by the confirmation payload.
        skill.verified=True
        if source.aliases:
            try:
                _sync_skill_aliases(s,skill,[*_skill_alias_values(skill,s),*source.aliases],strict=True)
            except (ValueError,IntegrityError) as exc:
                s.rollback()
                raise HTTPException(409,str(exc)) from exc
        if source.content_item_id is not None:
            item=s.get(ContentItem,source.content_item_id)
            if item is None: raise HTTPException(404,"source content item not found")
            if item.is_archived: raise HTTPException(409,"cannot attach verified skill to an archived content item")
            content_item_id=item.id
            relationship=s.query(ContentItemSkill).filter_by(content_item_id=item.id,skill_id=skill.id).first()
            if relationship is None:
                s.add(ContentItemSkill(content_item_id=item.id,skill_id=skill.id,source="confirmed"))
        skill_id=skill.id
        source_payload.update({"skill_id":skill.id,"name":skill.name,"verified":True})
    else:
        if source.skill_id is not None or source.skill_name is not None or source.name is not None:
            raise HTTPException(422,"bullet materialization cannot include skill fields")
        if source.bullet_id is not None:
            if source.text is not None or source.bullet_text is not None or source.supporting_facts or source.evidence or source.tags:
                raise HTTPException(422,"an existing bullet cannot be combined with new bullet content")
            bullet=s.get(Bullet,source.bullet_id)
            if bullet is None: raise HTTPException(404,"source bullet not found")
            if source.content_item_id is not None and bullet.content_item_id != source.content_item_id:
                raise HTTPException(422,"source bullet does not belong to content item")
            content_item_id=bullet.content_item_id
            item=s.get(ContentItem,content_item_id)
            if item is None: raise HTTPException(404,"source content item not found")
            if item.is_archived: raise HTTPException(409,"cannot use evidence from an archived content item")
            # Preserve the referenced existing bullet on the materialization
            # row.  Without this assignment the CHECK constraint sees a
            # bullet-shaped request with no bullet_id and the route fails (or
            # loses the source provenance on databases without that CHECK).
            bullet_id=bullet.id
        else:
            text_value=source.text or source.bullet_text
            if not isinstance(text_value,str) or not text_value.strip():
                raise HTTPException(422,"bullet materialization requires text")
            facts=_clean_string_list(source.supporting_facts or source.evidence,"supporting_facts",required=True)
            if source.content_item_id is not None and source.content_item is not None:
                raise HTTPException(422,"provide either content_item_id or content_item")
            if source.content_item_id is not None:
                item=s.get(ContentItem,source.content_item_id)
                if item is None: raise HTTPException(404,"source content item not found")
                if item.is_archived: raise HTTPException(409,"cannot add evidence to an archived content item")
            elif source.content_item is not None:
                raw_item=source.content_item
                allowed={"type","title","organization","location","start_date","end_date","summary","tags"}
                unknown=set(raw_item)-allowed
                if unknown: raise HTTPException(422,"content_item contains unsupported fields")
                item_values={key:raw_item.get(key) for key in allowed if raw_item.get(key) is not None}
                if not isinstance(item_values.get("type"),str) or not item_values["type"].strip():
                    raise HTTPException(422,"content_item.type is required")
                if not isinstance(item_values.get("title"),str) or not item_values["title"].strip():
                    raise HTTPException(422,"content_item.title is required")
                scalar_fields={"organization","location","start_date","end_date","summary"}
                if any(key in item_values and not isinstance(item_values[key],str) for key in scalar_fields):
                    raise HTTPException(422,"content_item text fields must be strings")
                item_values["type"]=item_values["type"].strip(); item_values["title"]=item_values["title"].strip()
                item_values["tags"]=_clean_string_list(item_values.get("tags",[]),"content_item.tags")
                item_values["tags"].append(f"confirmed-requirement:{requirement.id}")
                item=ContentItem(**item_values)
                s.add(item); s.flush()
                _append_content_version(s,item,action="confirmed",changed_fields=list(_CONTENT_VERSION_FIELDS))
            else:
                raise HTTPException(422,"bullet materialization requires content_item_id or content_item")
            content_item_id=item.id
            tags=_clean_string_list(source.tags,"tags")
            provenance_tag=f"confirmed-requirement:{requirement.id}"
            if provenance_tag not in tags: tags.append(provenance_tag)
            bullet=Bullet(content_item_id=item.id,text=text_value.strip(),tags=tags,
                supporting_facts=facts,is_locked=False,is_preferred=bool(source.is_preferred))
            s.add(bullet); s.flush()
            _append_bullet_version(s,bullet,action="confirmed",changed_fields=list(_BULLET_VERSION_FIELDS))
            bullet_id=bullet.id
        if bullet_id is None:
            bullet=s.get(Bullet,bullet_id)
        source_payload.update({"content_item_id":content_item_id,"bullet_id":bullet_id,
            "text":bullet.text if bullet is not None else None,
            "supporting_facts":list(bullet.supporting_facts or []) if bullet is not None else []})

    materialization=ConfirmationMaterialization(
        confirmation_id=confirmation.id,application_id=application.id,requirement_id=requirement.id,
        source_type=source_type,content_item_id=content_item_id,bullet_id=bullet_id,skill_id=skill_id,
        idempotency_key=idempotency_key,payload_hash=payload_hash,source_fingerprint=before_fingerprint,
        source_payload=source_payload,
    )
    s.add(materialization); s.flush()
    # Link the new verified source back to the requirement.  If analysis had
    # already produced the exact link, preserve that row and only add the
    # explicit materialization provenance.
    duplicate=s.query(RequirementEvidenceLink).filter_by(
        requirement_id=requirement.id,content_item_id=content_item_id,
        bullet_id=bullet_id,skill_id=skill_id,
    ).first()
    if duplicate is None:
        excerpt=source.excerpt
        if excerpt is None:
            excerpt=(bullet.text if source_type == "bullet" and bullet is not None else s.get(Skill,skill_id).name)
        s.add(RequirementEvidenceLink(requirement_id=requirement.id,content_item_id=content_item_id,
            bullet_id=bullet_id,skill_id=skill_id,source_type=source_type,excerpt=excerpt,
            note=source.note or f"confirmed requirement {requirement.id}; materialization {materialization.id}"))
    s.flush()
    materialization.result_fingerprint=_verified_context(application,s)["source_fingerprint"]
    return materialization

def _materialize_and_commit(
    confirmation:MissingConfirmation,
    source:SourceRecordIn,
    s:Session,
) -> ConfirmationMaterialization:
    """Commit a materialization and turn uniqueness races into safe retries."""
    try:
        materialization=_materialize_confirmation(confirmation,source,s)
        s.commit()
    except IntegrityError as exc:
        s.rollback()
        # A concurrent request may have committed the same idempotency key
        # while this transaction was creating its source row.  Return that
        # durable result when the payload agrees; otherwise report a clear
        # conflict instead of leaking a 500/SQL error.
        try:
            _,payload_hash,idempotency_key=_materialization_identity(confirmation,source)
            existing=s.query(ConfirmationMaterialization).filter_by(
                application_id=confirmation.application_id,
                requirement_id=confirmation.requirement_id,
                idempotency_key=idempotency_key,
            ).first()
        except HTTPException:
            existing=None
        if existing is not None and existing.payload_hash == payload_hash:
            return existing
        raise HTTPException(409,"source materialization conflicted; retry with the same idempotency key") from exc
    s.refresh(materialization)
    return materialization

@app.post("/applications/{id}/missing-confirmations",response_model=list[ConfirmationOut])
def create_confirmations(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    existing_ids={x.requirement_id for x in s.query(MissingConfirmation).filter_by(application_id=id) if x.requirement_id is not None}
    existing_text={x.requirement for x in s.query(MissingConfirmation).filter_by(application_id=id)}
    out=[]
    for record in _comparison(a,s)['unsupported']:
        req=record['requirement']
        requirement_id=record.get("requirement_id")
        if (requirement_id is not None and requirement_id in existing_ids) or req in existing_text:
            continue
        x=MissingConfirmation(application_id=id,requirement=req,requirement_id=requirement_id); s.add(x); out.append(x)
    s.commit()
    for x in out: s.refresh(x)
    return [_confirmation_response(row,s) for row in s.query(MissingConfirmation).filter_by(application_id=id).order_by(MissingConfirmation.id).all()]
@app.post("/applications/{id}/confirmations",response_model=ConfirmationOut)
def confirm_alias(id:int, payload:ConfirmationDecisionIn, s:Session=Depends(db)):
    """Compatibility contract for the concise confirmation workflow."""
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    body=payload.model_dump()
    comparison_rows=_comparison(a,s)["unsupported"]
    target=None
    if payload.requirement_id is not None:
        target=next((record for record in comparison_rows if record.get("requirement_id")==payload.requirement_id),None)
        # Once a materialization exists the requirement is no longer
        # unsupported, but retrying the same confirmed decision must remain
        # idempotent.  Resolve it through the durable requirement row rather
        # than requiring the comparison to be stale.
        if target is None:
            requirement_row=s.get(JobRequirement,payload.requirement_id)
            existing=s.query(MissingConfirmation).filter_by(
                application_id=id,requirement_id=payload.requirement_id).first()
            if requirement_row is not None and requirement_row.application_id == id and existing is not None:
                target={"requirement_id":requirement_row.id,"requirement":requirement_row.text}
    elif payload.requirement:
        target=next((record for record in comparison_rows if record.get("requirement")==payload.requirement.strip()),None)
        if target is None:
            existing=s.query(MissingConfirmation).filter_by(application_id=id,requirement=payload.requirement.strip()).first()
            if existing is not None and existing.requirement_id is not None:
                requirement_row=s.get(JobRequirement,existing.requirement_id)
                if requirement_row is not None:
                    target={"requirement_id":requirement_row.id,"requirement":requirement_row.text}
    if target is None: raise HTTPException(422,"requirement is not an unsupported application requirement")
    requirement_id=target.get("requirement_id")
    req=target["requirement"]
    decision=str(payload.decision or payload.status or 'unresolved').lower()
    status={'confirm':'confirmed','confirmed':'confirmed','reject':'rejected','rejected':'rejected'}.get(decision,'unresolved')
    o=s.query(MissingConfirmation).filter_by(application_id=id,requirement_id=requirement_id).first() if requirement_id is not None else s.query(MissingConfirmation).filter_by(application_id=id,requirement=req).first()
    if o is not None:
        _guard_materialized_confirmation(o,s,status=status,requirement_id=requirement_id)
    if not o:
        o=MissingConfirmation(application_id=id,requirement=req,requirement_id=requirement_id,status=status,context=body); s.add(o)
    else: o.status=status; o.context=body; o.requirement_id=requirement_id or o.requirement_id
    s.flush()
    if status == "confirmed":
        has_source=payload.source is not None or bool(payload.context) or payload.source_type is not None
        if has_source:
            source=_source_input_from_confirmation(context=payload.context,source=payload.source,
                source_type=payload.source_type,idempotency_key=payload.idempotency_key)
            _materialize_and_commit(o,source,s)
        # A status-only confirmation is retained for clients that collect the
        # decision first and submit the typed source payload through the
        # dedicated materialization route afterward.
    s.commit(); s.refresh(o); return _confirmation_response(o,s)
@app.get("/applications/{id}/missing-confirmations",response_model=list[ConfirmationOut])
def list_confirmations(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return [_confirmation_response(row,s) for row in s.query(MissingConfirmation).filter_by(application_id=id).order_by(MissingConfirmation.id).all()]
@app.patch("/missing-confirmations/{id}",response_model=ConfirmationOut)
def update_confirmation(id:int,x:ConfirmationIn,s:Session=Depends(db)):
    o=s.get(MissingConfirmation,id)
    if not o: raise HTTPException(404,"confirmation not found")
    if x.status not in {'confirmed','rejected','unresolved'}: raise HTTPException(422,"invalid confirmation status")
    _guard_materialized_confirmation(o,s,status=x.status,requirement_id=x.requirement_id)
    if x.requirement_id is not None:
        if not s.get(JobRequirement,x.requirement_id) or s.get(JobRequirement,x.requirement_id).application_id != o.application_id:
            raise HTTPException(422,"requirement does not belong to confirmation application")
        o.requirement_id=x.requirement_id
    o.status=x.status; o.context=x.context
    s.flush()
    if x.status == "confirmed":
        has_source=x.source is not None or bool(x.context) or x.source_type is not None
        if has_source:
            source=_source_input_from_confirmation(context=x.context,source=x.source,
                source_type=x.source_type,idempotency_key=x.idempotency_key)
            _materialize_and_commit(o,source,s)
        # Status-only confirmation remains valid; the dedicated materialize
        # endpoint performs the required typed-source validation later.
    s.commit(); s.refresh(o); return _confirmation_response(o,s)

@app.post("/missing-confirmations/{id}/materialize",response_model=ConfirmationMaterializationOut)
def materialize_confirmation(id:int,x:SourceRecordIn,s:Session=Depends(db)):
    """Materialize a typed source record after a confirmation decision."""
    confirmation=s.get(MissingConfirmation,id)
    if confirmation is None: raise HTTPException(404,"confirmation not found")
    if confirmation.status != "confirmed":
        raise HTTPException(409,"confirm the missing requirement before materializing source data")
    source=_source_input_from_confirmation(source=x)
    materialization=_materialize_and_commit(confirmation,source,s)
    return _materialization_response(materialization,s)

@app.post("/applications/{id}/missing-confirmations/{confirmation_id}/materialize",response_model=ConfirmationMaterializationOut)
def materialize_application_confirmation(id:int,confirmation_id:int,x:SourceRecordIn,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    confirmation=s.get(MissingConfirmation,confirmation_id)
    if confirmation is None or confirmation.application_id != id:
        raise HTTPException(404,"confirmation not found")
    if confirmation.status != "confirmed":
        raise HTTPException(409,"confirm the missing requirement before materializing source data")
    source=_source_input_from_confirmation(source=x)
    materialization=_materialize_and_commit(confirmation,source,s)
    return _materialization_response(materialization,s)

@app.get("/missing-confirmations/{id}/materializations",response_model=list[ConfirmationMaterializationOut])
def confirmation_materializations(id:int,s:Session=Depends(db)):
    confirmation=s.get(MissingConfirmation,id)
    if confirmation is None: raise HTTPException(404,"confirmation not found")
    return [_materialization_response(row,s) for row in s.query(ConfirmationMaterialization).filter_by(
        confirmation_id=id).order_by(ConfirmationMaterialization.id).all()]

@app.post("/applications/{id}/confirmations/{confirmation_id}/materialize",response_model=ConfirmationMaterializationOut,include_in_schema=False)
def materialize_application_confirmation_alias(id:int,confirmation_id:int,x:SourceRecordIn,s:Session=Depends(db)):
    return materialize_application_confirmation(id,confirmation_id,x,s)
@app.post("/applications/{id}/proposals",response_model=ProposalOut)
def proposal(id:int,x:ProposalIn,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    context=_verified_context(a,s)
    p={**x.payload,"source_fingerprint":context["source_fingerprint"],
        "prompt_version":x.payload.get("prompt_version",PROMPT_VERSION),
        "schema_version":x.payload.get("schema_version",SCHEMA_VERSION)}
    try: _validate_proposal_payload(a,p,s)
    except ValidationError as exc: raise HTTPException(422,str(exc))
    o=Proposal(application_id=id,payload=p); s.add(o); s.commit(); s.refresh(o); return o
@app.post("/proposals/{id}/approve",response_model=ProposalOut)
def approve(id:int,s:Session=Depends(db)):
    o=s.get(Proposal,id)
    if not o: raise HTTPException(404,"proposal not found")
    a=s.get(Application,o.application_id)
    try: _validate_proposal_payload(a,o.payload,s)
    except ValidationError as exc:
        status=409 if "source data changed" in str(exc) else 422
        raise HTTPException(status,str(exc))
    try:
        # Approval is the last point a user can rely on a proposal being
        # executable.  Do this before changing its status, not only at PDF
        # generation time.
        build_snapshot(a,s,o.payload,o.id)
    except ValidationError as exc:
        raise HTTPException(422,str(exc))
    o.status="approved"; s.commit(); return o
@app.post("/proposals/{id}/decision",response_model=ProposalOut)
def proposal_decision(id:int, decision:dict, s:Session=Depends(db)):
    o=s.get(Proposal,id)
    if not o: raise HTTPException(404,"proposal not found")
    status=decision.get('status')
    if status not in {'approved','rejected','pending'}: raise HTTPException(422,"invalid proposal status")
    if status=='approved':
        a=s.get(Application,o.application_id)
        try: _validate_proposal_payload(a,o.payload,s)
        except ValidationError as exc:
            code=409 if "source data changed" in str(exc) else 422
            raise HTTPException(code,str(exc))
        try: build_snapshot(a,s,o.payload,o.id)
        except ValidationError as exc: raise HTTPException(422,str(exc))
    o.status=status; o.payload={**o.payload,'decisions':decision.get('decisions',{})}; s.commit(); s.refresh(o); return o
@app.get("/applications/{id}/proposals",response_model=list[ProposalOut])
def proposals(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(Proposal).filter_by(application_id=id).order_by(Proposal.created_at.desc()).all()
def build_snapshot(a:Application,s:Session,proposal_payload:dict|None=None,proposal_id:int|None=None):
    base=s.get(BaseResume,a.base_resume_id)
    base_entries=s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id).order_by(BaseEntry.entry_order).all()
    selected=(proposal_payload or {}).get("selected_entries") or [
        {"content_item_id":entry.content_item_id,"bullet_ids":list(entry.selected_bullet_ids)} for entry in base_entries]
    item_ids=[entry["content_item_id"] for entry in selected]
    items={item.id:item for item in s.query(ContentItem).filter(ContentItem.id.in_(item_ids)).all()} if item_ids else {}
    item_skill_links=_item_skill_links(item_ids,s)
    # Tailored snapshots and their source fingerprints are based only on
    # verified source claims.  An unverified relationship remains available
    # for later review/matching, but cannot silently alter an immutable resume
    # or make an already-generated proposal stale.
    linked_ids={link.skill_id for links in item_skill_links.values() for link in links}
    related_skills={skill.id:skill for skill in s.query(Skill).filter(
        Skill.id.in_(linked_ids), Skill.verified.is_(True)
    ).all()} if linked_ids else {}
    requested_bullets=[bid for entry in selected for bid in entry.get("bullet_ids",[])]
    bullets={bullet.id:bullet for bullet in s.query(Bullet).filter(Bullet.id.in_(requested_bullets)).all()} if requested_bullets else {}
    changes={change["bullet_id"]:change for change in (proposal_payload or {}).get("bullet_changes",[])}
    selected_bullet_ids=set(requested_bullets)
    unused=set(changes)-selected_bullet_ids
    if unused: raise ValidationError(f"rewrite references unselected bullet {min(unused)}")

    grouped:dict[str,list[dict[str,Any]]]={}
    resolved_bullets=[]
    for selection in selected:
        item=items.get(selection["content_item_id"])
        if not item: raise ValidationError("entry references unknown content item")
        entry_bullets=[]
        for bid in selection.get("bullet_ids",[]):
            bullet=bullets.get(bid)
            if not bullet or bullet.content_item_id!=item.id: raise ValidationError("selected bullet does not belong to content item")
            change=changes.get(bid); text=change["proposed_text"] if change else bullet.text
            record={"id":bid,"content_item_id":item.id,"text":text,"source_text":bullet.text,
                "supporting_facts":bullet.supporting_facts,"is_locked":bullet.is_locked,
                "was_rewritten":bool(change)}
            entry_bullets.append(record); resolved_bullets.append(record)
        section=item.type.lower(); summary=item.summary or ""; display_title=item.title
        source_skill_records=[related_skills[link.skill_id] for link in item_skill_links.get(item.id,[])
                              if link.skill_id in related_skills]
        source_skill_ids=[skill.id for skill in source_skill_records]
        source_skill_names=[skill.name for skill in source_skill_records]
        if section=="education" and summary.startswith("GPA:"):
            parts=summary.split("; Coursework:",1); display_title=f"{item.title}; {parts[0]}"
            summary=f"Coursework:{parts[1]}" if len(parts)>1 else ""
        elif section=="experience" and summary.startswith("Advisor:"):
            display_title=f"{item.title} | {summary}"; summary=""
        grouped.setdefault(section,[]).append({"content_item_id":item.id,"title":item.title,
            "display_title":display_title,
            "organization":item.organization or "","location":item.location or "",
            "dates":" -- ".join(x for x in (item.start_date,item.end_date) if x),
            "skills":item.summary or "" if section=="project" else "",
            "skill_ids":source_skill_ids,"skill_names":source_skill_names,
            "summary":summary if section!="project" else "","bullets":entry_bullets})

    configured=[str(section).lower() for section in (base.section_order or [])]
    verified_skills=s.query(Skill).filter_by(verified=True).order_by(Skill.category,Skill.name).all()
    skill_groups=[]
    for skill in verified_skills:
        category=skill.category or "Skills"
        group=next((record for record in skill_groups if record["category"]==category),None)
        if group is None: group={"category":category,"skills":[]}; skill_groups.append(group)
        group["skills"].append(skill.name)
    if skill_groups and "skills" not in configured:
        activity_index=configured.index("activities") if "activities" in configured else len(configured)
        configured.insert(activity_index,"skills")
    order=list(dict.fromkeys([*configured,*grouped.keys(),*( ["skills"] if skill_groups else [] )]))
    sections=[]
    for key in order:
        if key=="skills" and skill_groups:
            sections.append({"key":"skills","title":"Technical Skills","entries":[],"skill_groups":skill_groups})
        elif key in grouped:
            title={"project":"Projects"}.get(key,key.replace("_"," ").title())
            sections.append({"key":key,"title":title,"entries":grouped[key]})
    personal=s.get(PersonalInformation,base.personal_information_id) if base and base.personal_information_id else None
    contact=_personal_contact(personal,(base.layout_settings or {}).get("contact"))
    for field in ("linkedin","github","website"):
        value=contact.get(field,"")
        if value and not contact.get(f"{field}_label"):
            contact[f"{field}_label"]=value.removeprefix("https://").removeprefix("http://").rstrip("/")
        if value and not value.startswith(("http://","https://")):
            contact[f"{field}_label"]=value.rstrip("/")
            contact[field]="https://"+value
    snapshot={"contact":contact,"sections":sections,
        "content_items":[{"id":items[item_id].id,"title":items[item_id].title,
                           "skill_ids":[skill.id for skill in related_skills.values()
                                         if skill.id in {link.skill_id for link in item_skill_links.get(item_id,[])}]}
                         for item_id in item_ids],
        "bullets":resolved_bullets,"entries":[{"content_item_id":entry["content_item_id"],
            "bullet_ids":list(entry.get("bullet_ids",[])),
            "skill_ids":[link.skill_id for link in item_skill_links.get(entry["content_item_id"],[])
                          if link.skill_id in related_skills]}
            for entry in selected]}
    if proposal_payload is not None:
        snapshot["provenance"]={"proposal_id":proposal_id,"source_fingerprint":proposal_payload.get("source_fingerprint"),
            "prompt_version":proposal_payload.get("prompt_version"),"schema_version":proposal_payload.get("schema_version"),
            "bullet_changes":[{"bullet_id":bid,"source_text":bullets[bid].text,
                "proposed_text":changes[bid]["proposed_text"]} for bid in sorted(changes)]}
    validate_resume_snapshot(snapshot)
    return snapshot

def _reserve_revision(application_id:int, snapshot:dict[str,Any], s:Session, *, status:str="draft") -> Revision:
    """Allocate a unique immutable revision number before writing artifacts.

    A unique index serializes competing SQLite writers.  Retrying after a
    collision avoids two requests compiling into the same revision directory.
    """
    for _ in range(3):
        number=(s.query(func.max(Revision.revision_number)).filter_by(application_id=application_id).scalar() or 0)+1
        record=Revision(application_id=application_id,revision_number=number,resume_json=snapshot,status=status)
        s.add(record)
        try:
            s.commit(); s.refresh(record); return record
        except IntegrityError:
            s.rollback()
    raise HTTPException(409,"could not allocate a unique revision number; retry the request")
@app.get("/applications/{id}/snapshot",response_model=SnapshotOut)
def snapshot(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    return build_snapshot(a,s)
@app.post("/applications/{id}/revisions",response_model=RevisionOut)
def revision(id:int,x:RevisionIn,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    required_snapshot_fields={"contact","sections","content_items","bullets","entries"}
    missing=required_snapshot_fields-set(x.resume_json)
    if missing: raise HTTPException(422,"resume snapshot is missing required fields: "+", ".join(sorted(missing)))
    try: validate_resume_snapshot(x.resume_json)
    except ValidationError as exc: raise HTTPException(422,str(exc))
    canonical=build_snapshot(s.get(Application,id),s)
    if x.resume_json != canonical:
        raise HTTPException(422,"manual revisions must match the current canonical snapshot; use an approved proposal to create tailored revisions")
    return _reserve_revision(id,canonical,s)
@app.get("/applications/{id}/revisions",response_model=list[RevisionOut])
def revisions(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(Revision).filter_by(application_id=id).order_by(Revision.revision_number).all()

def _revision_comparison_response(
    application_id: int,
    from_revision_id: int,
    to_revision_id: int,
    s: Session,
) -> dict[str, Any]:
    """Compare two revisions owned by one application without persisting a diff."""

    if not s.get(Application, application_id):
        raise HTTPException(404, "application not found")
    before = s.get(Revision, from_revision_id)
    after = s.get(Revision, to_revision_id)
    # Do not reveal whether a revision exists under another application.  A
    # revision is addressable only through its owning application here.
    if not before or before.application_id != application_id:
        raise HTTPException(404, "from revision not found")
    if not after or after.application_id != application_id:
        raise HTTPException(404, "to revision not found")

    diff = compare_snapshots(before.resume_json, after.resume_json)
    return {
        "application_id": application_id,
        "from_revision": {"id": before.id, "revision_number": before.revision_number},
        "to_revision": {"id": after.id, "revision_number": after.revision_number},
        **diff,
    }


@app.get("/applications/{id}/revisions/compare", response_model=RevisionComparisonOut)
def compare_revisions(
    id: int,
    from_revision_id: int | None = None,
    to_revision_id: int | None = None,
    before_revision_id: int | None = None,
    after_revision_id: int | None = None,
    s: Session = Depends(db),
):
    """Return an ephemeral semantic diff for two revisions of an application.

    ``before_revision_id``/``after_revision_id`` are accepted as descriptive
    aliases for clients that do not use the ``from``/``to`` terminology.  A
    request must provide exactly one complete pair.
    """

    supplied = [
        (from_revision_id, to_revision_id),
        (before_revision_id, after_revision_id),
    ]
    pairs = [pair for pair in supplied if any(value is not None for value in pair)]
    if len(pairs) != 1 or any(value is None for value in pairs[0]):
        raise HTTPException(422, "provide from_revision_id and to_revision_id")
    left, right = pairs[0]
    return _revision_comparison_response(id, left, right, s)


@app.get("/applications/{id}/revisions/{from_revision_id}/compare/{to_revision_id}", response_model=RevisionComparisonOut)
def compare_revision_path(id: int, from_revision_id: int, to_revision_id: int, s: Session = Depends(db)):
    """Path-parameter form of the revision comparison endpoint."""

    return _revision_comparison_response(id, from_revision_id, to_revision_id, s)


@app.get("/revisions/{from_revision_id}/compare/{to_revision_id}", response_model=RevisionComparisonOut)
def compare_revision_ids(from_revision_id: int, to_revision_id: int, s: Session = Depends(db)):
    """Compare two revisions when their shared application is implicit."""

    before = s.get(Revision, from_revision_id)
    after = s.get(Revision, to_revision_id)
    if not before or not after or before.application_id != after.application_id:
        raise HTTPException(404, "revisions not found")
    return _revision_comparison_response(before.application_id, from_revision_id, to_revision_id, s)

@app.get("/revisions/{id}",response_model=RevisionOut)
def revision_detail(id:int,s:Session=Depends(db)):
    o=s.get(Revision,id)
    if not o: raise HTTPException(404,"revision not found")
    return o
@app.post("/applications/{id}/generate",response_model=GenerationOut)
def generate(id:int,x:GenerateIn,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    proposal=s.get(Proposal,x.proposal_id)
    if not proposal: raise HTTPException(404,"proposal not found")
    if proposal.application_id!=id: raise HTTPException(422,"proposal does not belong to application")
    if proposal.status!="approved": raise HTTPException(409,"proposal must be approved before generation")
    try:
        _validate_proposal_payload(a,proposal.payload,s)
        snap=build_snapshot(a,s,proposal.payload,proposal.id)
    except ValidationError as exc:
        code=409 if "source data changed" in str(exc) else 422
        raise HTTPException(code,str(exc))
    o=_reserve_revision(id,snap,s,status="generating")
    out=GENERATED/'applications'/str(id)/f'revision-{o.revision_number:03d}'
    try:
        tex,pdf=ResumeRenderer().compile(snap,out)
    except RuntimeError as exc:
        o.status="failed"; s.commit()
        raise HTTPException(503,str(exc))
    try:
        page_count=count_pdf_pages(pdf)
    except (FileNotFoundError, RuntimeError) as exc:
        o.status="failed"; s.commit()
        raise HTTPException(503,f"PDF was generated but could not be validated: {exc}")
    o.latex_path=str(tex.relative_to(ROOT)); o.pdf_path=str(pdf.relative_to(ROOT)); o.page_count=page_count; o.generated_at=now(); o.status='draft'; s.commit(); s.refresh(o)
    # Keep filesystem paths for local tooling and expose browser-served URLs
    # for the Vite UI. StaticFiles mounts the generated directory at /generated.
    return {**{c.name:getattr(o,c.name) for c in Revision.__table__.columns},
            'proposal_id':proposal.id,
            'latex_url':'/generated/'+str(tex.relative_to(GENERATED)),
            'pdf_url':'/generated/'+str(pdf.relative_to(GENERATED))}

@app.post("/applications/{id}/submit",response_model=ApplicationOut)
def submit_application(id:int,x:SubmitRevisionIn,s:Session=Depends(db)):
    """Record the exact generated revision used for an application submission."""
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    revision_to_submit=_submittable_revision(id,x.revision_id,s)
    _submit_revision(o,revision_to_submit,s,submitted_at=x.submitted_at)
    s.commit(); s.refresh(o); return o

@app.post("/applications/{id}/submissions",response_model=ApplicationOut)
def submit_application_alias(id:int,x:SubmitRevisionIn,s:Session=Depends(db)):
    return submit_application(id,x,s)

@app.post("/applications/{id}/submitted-revision",response_model=ApplicationOut)
def submit_revision_alias(id:int,x:SubmitRevisionIn,s:Session=Depends(db)):
    return submit_application(id,x,s)

@app.get("/applications/{id}/submitted-revision",response_model=RevisionOut)
def submitted_revision(id:int,s:Session=Depends(db)):
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    if o.submitted_revision_id is None: raise HTTPException(404,"application has no submitted revision")
    revision=s.get(Revision,o.submitted_revision_id)
    # A dangling association can only be produced by a legacy external write;
    # do not expose it as a valid submission.
    if not revision or revision.application_id != id: raise HTTPException(404,"submitted revision not found")
    return revision

class RenderIn(BaseModel): snapshot:dict
@app.post("/render",response_model=RenderOut)
def render(x:RenderIn):
    try:
        return {"latex": ResumeRenderer().render_tex(x.snapshot)}
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
