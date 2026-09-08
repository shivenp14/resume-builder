"""Local-first Resume Builder API.

The API deliberately keeps AI output as proposals: source records and revisions
are never silently changed by analysis or optimization.
"""
from datetime import datetime, timezone
import hashlib, json, re
from pathlib import Path
from typing import Any, Literal
import uuid
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, String, Text, Integer, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint, event, func, text, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from .services.renderer import ResumeRenderer, count_pdf_pages
from .services.validation import ValidationError, validate_resume_snapshot, validate_proposal
from .services.codex_provider import CodexProvider, CodexProviderError, MODEL, REASONING
from .services.llm_prompts import PROMPT_VERSION
from .services.llm_schemas import SCHEMA_VERSION

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
class Bullet(Base):
    __tablename__="bullets"
    id: Mapped[int]=mapped_column(primary_key=True); content_item_id: Mapped[int]=mapped_column(ForeignKey("content_items.id")); text: Mapped[str]=mapped_column(Text); tags: Mapped[list]=mapped_column(JSON,default=list); supporting_facts: Mapped[list]=mapped_column(JSON,default=list); is_locked: Mapped[bool]=mapped_column(Boolean,default=False); is_preferred: Mapped[bool]=mapped_column(Boolean,default=False)
class Skill(Base):
    __tablename__="skills"
    id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(120),unique=True); category: Mapped[str|None]=mapped_column(String(80)); aliases: Mapped[list]=mapped_column(JSON,default=list); notes: Mapped[str|None]=mapped_column(Text); verified: Mapped[bool]=mapped_column(Boolean,default=False)
class BaseResume(Base):
    __tablename__="base_resumes"
    id: Mapped[int]=mapped_column(primary_key=True); name: Mapped[str]=mapped_column(String(120)); template_id: Mapped[str]=mapped_column(String(80),default="default"); section_order: Mapped[list]=mapped_column(JSON,default=list); layout_settings: Mapped[dict]=mapped_column(JSON,default=dict)
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
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirements: Mapped[list]=mapped_column(JSON); keywords: Mapped[list]=mapped_column(JSON); technologies: Mapped[list]=mapped_column(JSON); responsibilities: Mapped[list]=mapped_column(JSON); preferred_qualifications: Mapped[list]=mapped_column(JSON); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Proposal(Base):
    __tablename__="proposals"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); payload: Mapped[dict]=mapped_column(JSON); status: Mapped[str]=mapped_column(String(20),default="pending"); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Revision(Base):
    __tablename__="revisions"
    __table_args__=(UniqueConstraint("application_id", "revision_number", name="uq_revision_application_number"),)
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); revision_number: Mapped[int]; resume_json: Mapped[dict]=mapped_column(JSON); latex_path: Mapped[str|None]=mapped_column(String(500)); pdf_path: Mapped[str|None]=mapped_column(String(500)); page_count: Mapped[int|None]; status: Mapped[str]=mapped_column(String(20),default="draft"); generated_at: Mapped[datetime|None]=mapped_column(DateTime,nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class MissingConfirmation(Base):
    __tablename__="missing_confirmations"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirement: Mapped[str]=mapped_column(Text); status: Mapped[str]=mapped_column(String(20),default="unresolved"); context: Mapped[dict]=mapped_column(JSON,default=dict); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class OptimizationRun(Base):
    __tablename__="optimization_runs"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); operation: Mapped[str]=mapped_column(String(40)); status: Mapped[str]=mapped_column(String(20),default="running"); model: Mapped[str]=mapped_column(String(100),default="gpt-5.6-luna"); reasoning_effort: Mapped[str]=mapped_column(String(20),default="low"); prompt_version: Mapped[str]=mapped_column(String(40),default="v1"); schema_version: Mapped[str]=mapped_column(String(40),default="v1"); idempotency_key: Mapped[str|None]=mapped_column(String(200)); input_payload: Mapped[dict]=mapped_column(JSON,default=dict); output_payload: Mapped[dict|None]=mapped_column(JSON); error: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); completed_at: Mapped[datetime|None]=mapped_column(DateTime)
Base.metadata.create_all(engine)

def _apply_sqlite_integrity_migrations() -> None:
    """Apply additive SQLite schema changes and indexes.

    SQLAlchemy's ``create_all`` never alters an existing SQLite table.  These
    migrations therefore add lifecycle columns one at a time and create the
    status-history table/indexes without rebuilding or replacing user data.
    Every operation is idempotent so startup can safely run repeatedly.
    """
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
        revision_columns = {column["name"] for column in inspect(connection).get_columns("revisions")}
        if "generated_at" not in revision_columns:
            connection.execute(text("ALTER TABLE revisions ADD COLUMN generated_at DATETIME"))
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
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_application_status_history_application_id ON application_status_history (application_id, created_at)"))

_apply_sqlite_integrity_migrations()
def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()

class ItemIn(BaseModel): type:str; title:str; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]=Field(default_factory=list)
class ItemPatch(BaseModel): type:str|None=None; title:str|None=None; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]|None=None
class BulletIn(BaseModel): text:str; tags:list[str]=Field(default_factory=list); supporting_facts:list[str]=Field(default_factory=list); is_locked:bool=False; is_preferred:bool=False
class SkillIn(BaseModel): name:str; category:str|None=None; aliases:list[str]=Field(default_factory=list); notes:str|None=None; verified:bool=False
class ResumeIn(BaseModel): name:str; template_id:str="default"; section_order:list[str]=Field(default_factory=list); layout_settings:dict=Field(default_factory=dict)
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
class ProposalIn(BaseModel): payload:dict[str,Any]
class RevisionIn(BaseModel): resume_json:dict[str,Any]
class ConfirmationIn(BaseModel): status:str; context:dict[str,Any]=Field(default_factory=dict)
class ConfirmationDecisionIn(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)
    decision: str|None=None
    status: str|None=None
    context: dict[str,Any]=Field(default_factory=dict)
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
class SkillOut(APIOut): id:int; name:str; category:str|None; aliases:list[str]; notes:str|None; verified:bool
class BaseResumeOut(APIOut): id:int; name:str; template_id:str; section_order:list[str]; layout_settings:dict[str,Any]
class BaseEntryOut(APIOut): id:int; base_resume_id:int; content_item_id:int; selected_bullet_ids:list[int]; entry_order:int
class ApplicationOut(APIOut):
    id:int; company:str; position:str; job_url:str|None; job_description:str; notes:str|None; status:str; base_resume_id:int; source:str|None; location:str|None; employment_type:str|None; salary_range:str|None; contact_name:str|None; contact_email:str|None; application_deadline:str|None; applied_at:str|None; follow_up_at:str|None; submitted_revision_id:int|None; submitted_at:datetime|None; created_at:datetime; updated_at:datetime
class StatusHistoryOut(APIOut):
    id:int; application_id:int; from_status:str|None; to_status:str; status:str; reason:str|None; created_at:datetime; changed_at:datetime
class JobAnalysisOut(APIOut):
    id:int; application_id:int; requirements:list[str]; keywords:list[str]; technologies:list[str]; responsibilities:list[str]; preferred_qualifications:list[str]; created_at:datetime
class ProposalOut(APIOut): id:int; application_id:int; payload:dict[str,Any]; status:str; created_at:datetime
class RevisionOut(APIOut):
    id:int; application_id:int; revision_number:int; resume_json:dict[str,Any]; latex_path:str|None; pdf_path:str|None; page_count:int|None; status:str; generated_at:datetime|None; created_at:datetime
class ConfirmationOut(APIOut): id:int; application_id:int; requirement:str; status:str; context:dict[str,Any]; created_at:datetime
class OptimizationRunOut(APIOut):
    id:int; application_id:int; operation:str; status:str; model:str; reasoning_effort:str; prompt_version:str; schema_version:str; idempotency_key:str|None; input_payload:dict[str,Any]; output_payload:dict[str,Any]|None; error:str|None; created_at:datetime; completed_at:datetime|None
class ComparisonOut(APIOut):
    well_represented:list[str]; weakly_represented:list[str]; library_only:list[str]; unsupported:list[dict[str,str]]
class SnapshotOut(APIOut):
    contact:dict[str,Any]; sections:list[dict[str,Any]]; content_items:list[dict[str,Any]]; bullets:list[dict[str,Any]]; entries:list[dict[str,Any]]; provenance:dict[str,Any]|None=None
class GenerationOut(RevisionOut): proposal_id:int; latex_url:str; pdf_url:str
class RenderOut(APIOut): latex:str

app=FastAPI(title="Resume Builder API",version="0.1.0")
GENERATED = ROOT / "generated"
GENERATED.mkdir(exist_ok=True)
app.mount("/generated", StaticFiles(directory=GENERATED), name="generated")
app.mount("/checkpoints", StaticFiles(directory=ROOT / "checkpoints"), name="checkpoints")
@app.get("/health",response_model=HealthOut)
def health(): return {"status":"ok"}
@app.post("/content-items",response_model=ContentItemOut)
def create_item(x:ItemIn,s:Session=Depends(db)):
    o=ContentItem(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/content-items",response_model=list[ContentItemOut])
def items(s:Session=Depends(db)): return s.query(ContentItem).filter_by(is_archived=False).all()
@app.get("/content-items/{id}",response_model=ContentItemOut)
def item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    return o
@app.patch("/content-items/{id}",response_model=ContentItemOut)
def edit_item(id:int,x:ItemPatch,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    for k,v in x.model_dump(exclude_unset=True).items(): setattr(o,k,v)
    s.commit(); return o
@app.delete("/content-items/{id}",response_model=MutationOut)
def archive_item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    o.is_archived=True; s.commit(); return {"archived":True}
@app.post("/content-items/{id}/bullets",response_model=BulletOut)
def add_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    o=Bullet(content_item_id=id,**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/content-items/{id}/bullets",response_model=list[BulletOut])
def get_bullets(id:int,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    return s.query(Bullet).filter_by(content_item_id=id).all()
@app.patch("/bullets/{id}",response_model=BulletOut)
def edit_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    for k,v in x.model_dump().items(): setattr(o,k,v)
    s.commit(); return o
@app.delete("/bullets/{id}",response_model=MutationOut)
def delete_bullet(id:int,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    references=[entry.id for entry in s.query(BaseEntry).all() if id in (entry.selected_bullet_ids or [])]
    if references:
        raise HTTPException(409,"bullet is selected by one or more base resume entries; remove it from those entries first")
    s.delete(o); s.commit(); return {"deleted":True}
@app.post("/skills",response_model=SkillOut)
def add_skill(x:SkillIn,s:Session=Depends(db)):
    if s.query(Skill).filter_by(name=x.name).first(): raise HTTPException(409,"skill exists")
    o=Skill(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/skills",response_model=list[SkillOut])
def skills(s:Session=Depends(db)): return s.query(Skill).all()
@app.post("/base-resumes",response_model=BaseResumeOut)
def add_resume(x:ResumeIn,s:Session=Depends(db)):
    o=BaseResume(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
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
    for k,v in x.model_dump().items(): setattr(o,k,v)
    s.commit(); return o
@app.post("/base-resumes/{id}/entries",response_model=BaseEntryOut)
def add_entry(id:int,x:EntryIn,s:Session=Depends(db)):
    if not s.get(BaseResume,id) or not s.get(ContentItem,x.content_item_id): raise HTTPException(404,"resume or content item not found")
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
    if not s.get(ContentItem,x.content_item_id): raise HTTPException(404,"content item not found")
    _validate_entry_bullets(x.content_item_id,x.selected_bullet_ids,s)
    for k,v in x.model_dump(exclude_unset=True).items(): setattr(o,k,v)
    s.commit(); s.refresh(o); return o
@app.delete("/base-entries/{id}",response_model=MutationOut)
def delete_entry(id:int,s:Session=Depends(db)):
    o=s.get(BaseEntry,id)
    if not o: raise HTTPException(404,"base entry not found")
    s.delete(o); s.commit(); return {"deleted":True}
APPLICATION_STATUSES={"draft","applied","interviewing","offer","rejected","withdrawn"}

def _record_status_change(application:Application, to_status:str, s:Session, *, reason:str|None=None) -> ApplicationStatusHistory|None:
    """Append a status event when the status actually changes.

    The caller owns the surrounding transaction, so an application update and
    its history event commit (or roll back) together.
    """
    if application.status == to_status:
        return None
    event_record=ApplicationStatusHistory(application_id=application.id,
        from_status=application.status,to_status=to_status,reason=reason)
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
    if revision.status not in {"draft","generated"} or not revision.latex_path or not revision.pdf_path or revision.page_count is None:
        raise HTTPException(409,"revision must be successfully generated before submission")
    return revision

def _submit_revision(application:Application, revision:Revision, s:Session, *, submitted_at:datetime|None=None) -> Application:
    application.submitted_revision_id=revision.id
    application.submitted_at=submitted_at or now()
    if not application.applied_at:
        application.applied_at=application.submitted_at.isoformat()
    if application.status == "draft":
        _record_status_change(application,"applied",s,reason="revision submitted")
    return application

@app.post("/applications",response_model=ApplicationOut)
def add_app(x:AppIn,s:Session=Depends(db)):
    if not s.get(BaseResume,x.base_resume_id): raise HTTPException(404,"base resume not found")
    if x.status not in APPLICATION_STATUSES: raise HTTPException(422,"invalid application status")
    o=Application(**x.model_dump()); s.add(o); s.flush()
    s.add(ApplicationStatusHistory(application_id=o.id,from_status=None,to_status=o.status,reason="application created"))
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
    if submitted_revision_id is not None:
        revision_to_submit=_submittable_revision(id,submitted_revision_id,s)
        _submit_revision(o,revision_to_submit,s)
    for key,value in changes.items():
        if key != "status": setattr(o,key,value)
    if "status" in changes:
        _record_status_change(o,changes["status"],s,reason=status_reason)
    s.commit(); s.refresh(o); return o

@app.post("/applications/{id}/status",response_model=ApplicationOut)
def change_application_status(id:int,x:StatusChangeIn,s:Session=Depends(db)):
    o=s.get(Application,id)
    if not o: raise HTTPException(404,"application not found")
    if x.status not in APPLICATION_STATUSES: raise HTTPException(422,"invalid application status")
    _record_status_change(o,x.status,s,reason=x.reason)
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

def _verified_context(a:Application,s:Session) -> dict[str,Any]:
    base=s.get(BaseResume,a.base_resume_id)
    entries=s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id).order_by(BaseEntry.entry_order).all()
    all_items=s.query(ContentItem).filter_by(is_archived=False).order_by(ContentItem.id).all()
    item_ids=[item.id for item in all_items]
    all_bullets=s.query(Bullet).filter(Bullet.content_item_id.in_(item_ids)).order_by(Bullet.id).all() if item_ids else []
    verified={
        "content_items":[{"id":item.id,"type":item.type,"title":item.title,"organization":item.organization,
            "location":item.location,"start_date":item.start_date,"end_date":item.end_date,
            "summary":item.summary,"tags":item.tags} for item in all_items],
        "bullets":[{"id":bullet.id,"content_item_id":bullet.content_item_id,"text":bullet.text,
            "tags":bullet.tags,"supporting_facts":bullet.supporting_facts,"is_locked":bullet.is_locked,
            "is_preferred":bullet.is_preferred} for bullet in all_bullets],
        "skills":[{"id":skill.id,"name":skill.name,"category":skill.category,"aliases":skill.aliases,
            "notes":skill.notes,"verified":skill.verified}
            for skill in s.query(Skill).filter_by(verified=True).order_by(Skill.id)],
    }
    source={"base_resume":{"id":base.id,"section_order":base.section_order,"layout_settings":base.layout_settings},
        "base_entries":[{"content_item_id":entry.content_item_id,"bullet_ids":entry.selected_bullet_ids,
            "entry_order":entry.entry_order} for entry in entries],**verified}
    fingerprint=hashlib.sha256(json.dumps(source,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    base_snapshot=build_snapshot(a,s); base_snapshot.pop("contact",None)
    return {"base_snapshot":base_snapshot,"verified_library":verified,"source_fingerprint":fingerprint}

def _validate_proposal_payload(a:Application,payload:dict,s:Session,*,require_fresh:bool=True) -> dict[str,Any]:
    context=_verified_context(a,s)
    grounding={**context["verified_library"],"entries":[]}
    validate_proposal(payload,grounding)
    owned:dict[int,set[int]]={item["id"]:set() for item in grounding["content_items"]}
    for bullet in grounding["bullets"]: owned[bullet["content_item_id"]].add(bullet["id"])
    selected_ids=[entry.get("content_item_id") for entry in payload.get("selected_entries",[])]
    if len(selected_ids)!=len(set(selected_ids)): raise ValidationError("proposal contains duplicate selected entries")
    for entry in payload.get("selected_entries",[]):
        item_id=entry.get("content_item_id")
        if item_id not in owned: raise ValidationError("proposal selects unknown content item")
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
            if existing: return existing
    run=OptimizationRun(application_id=id,operation="analysis",idempotency_key=key,input_payload={"job_description":a.job_description},model=MODEL,reasoning_effort=REASONING,prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION); s.add(run); s.commit()
    try:
        result=_provider_call(_provider(),"analysis",a.job_description)
        if hasattr(result,"model_dump"): result=result.model_dump()
        if not isinstance(result,dict): raise ValueError("Codex returned invalid analysis")
        fields={k:list(result.get(k,[])) for k in ("requirements","keywords","technologies","responsibilities","preferred_qualifications")}
        o=JobAnalysis(application_id=id,**fields); s.add(o); s.flush()
        run.status="succeeded"; run.output_payload={"id":o.id,**fields}; run.completed_at=now(); s.commit(); s.refresh(o); return o
    except Exception as exc:
        run.status="failed"; run.error=_run_error(exc); run.completed_at=now(); s.commit(); raise HTTPException(503,"Codex provider unavailable: "+run.error)
@app.get("/applications/{id}/analysis",response_model=JobAnalysisOut)
def get_analysis(id:int,s:Session=Depends(db)):
    o=s.query(JobAnalysis).filter_by(application_id=id).order_by(JobAnalysis.created_at.desc()).first()
    if not o: raise HTTPException(404,"analysis not found")
    return o
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
        **context,"confirmations":[{"requirement":x.requirement,"status":x.status,"context":x.context}
        for x in s.query(MissingConfirmation).filter_by(application_id=id)]}
    run=OptimizationRun(application_id=id,operation="proposal",idempotency_key=key,input_payload=payload,model=MODEL,reasoning_effort=REASONING,prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION); s.add(run); s.commit()
    try:
        out=_provider_call(_provider(),"proposal",payload)
        if hasattr(out,"model_dump"): out=out.model_dump()
        if not isinstance(out,dict): raise ValueError("Codex returned invalid proposal")
        out={**out,"source_fingerprint":context["source_fingerprint"],"prompt_version":PROMPT_VERSION,
            "schema_version":out.get("schema_version",SCHEMA_VERSION)}
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
def _comparison(a:Application,s:Session):
    analysis=s.query(JobAnalysis).filter_by(application_id=a.id).order_by(JobAnalysis.created_at.desc()).first()
    if not analysis: raise HTTPException(404,"analyze application first")
    terms=list(dict.fromkeys([*analysis.technologies,*analysis.keywords]))
    all_items=s.query(ContentItem).filter_by(is_archived=False).all(); base_ids={e.content_item_id for e in s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id)}
    represented=[]; weak=[]; library_only=[]; unsupported=[]
    for term in terms:
        hits=[i for i in all_items if term.lower() in ((i.title or '')+' '+(i.summary or '')).lower() or any(term.lower() in b.text.lower() for b in i.bullets)]
        if not hits: unsupported.append(term)
        elif any(i.id in base_ids for i in hits): represented.append(term)
        else: library_only.append(term)
    # Objects keep a stable field for UI confirmation while preserving the
    # simple category arrays used by older clients.
    return {'well_represented':represented,'weakly_represented':weak,'library_only':library_only,
            'unsupported':[{'requirement':x,'status':'unresolved'} for x in unsupported]}
@app.get("/applications/{id}/comparison",response_model=ComparisonOut)
def comparison(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    return _comparison(a,s)
@app.post("/applications/{id}/missing-confirmations",response_model=list[ConfirmationOut])
def create_confirmations(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    existing={x.requirement for x in s.query(MissingConfirmation).filter_by(application_id=id)}
    out=[]
    for record in _comparison(a,s)['unsupported']:
        req=record['requirement']
        if req not in existing:
            x=MissingConfirmation(application_id=id,requirement=req); s.add(x); out.append(x)
    s.commit()
    for x in out: s.refresh(x)
    return s.query(MissingConfirmation).filter_by(application_id=id).all()
@app.post("/applications/{id}/confirmations",response_model=ConfirmationOut)
def confirm_alias(id:int, payload:ConfirmationDecisionIn, s:Session=Depends(db)):
    """Compatibility contract for the concise confirmation workflow."""
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    body=payload.model_dump()
    req=payload.requirement.strip()
    if not req: raise HTTPException(422,"requirement is required")
    decision=str(payload.decision or payload.status or 'unresolved').lower()
    status={'confirm':'confirmed','confirmed':'confirmed','reject':'rejected','rejected':'rejected'}.get(decision,'unresolved')
    known={record["requirement"] for record in _comparison(a,s)["unsupported"]}
    if req not in known: raise HTTPException(422,"requirement is not an unsupported application requirement")
    o=s.query(MissingConfirmation).filter_by(application_id=id,requirement=req).first()
    if not o:
        o=MissingConfirmation(application_id=id,requirement=req,status=status,context=body); s.add(o)
    else: o.status=status; o.context=body
    s.commit(); s.refresh(o); return o
@app.get("/applications/{id}/missing-confirmations",response_model=list[ConfirmationOut])
def list_confirmations(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(MissingConfirmation).filter_by(application_id=id).all()
@app.patch("/missing-confirmations/{id}",response_model=ConfirmationOut)
def update_confirmation(id:int,x:ConfirmationIn,s:Session=Depends(db)):
    o=s.get(MissingConfirmation,id)
    if not o: raise HTTPException(404,"confirmation not found")
    if x.status not in {'confirmed','rejected','unresolved'}: raise HTTPException(422,"invalid confirmation status")
    o.status=x.status; o.context=x.context; s.commit(); s.refresh(o); return o
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
    contact=dict((base.layout_settings or {}).get("contact") or {})
    linkedin=contact.get("linkedin","")
    if linkedin and not linkedin.startswith(("http://","https://")):
        contact["linkedin_label"]=linkedin.rstrip("/"); contact["linkedin"]="https://"+linkedin
    snapshot={"contact":contact,"sections":sections,
        "content_items":[{"id":items[item_id].id,"title":items[item_id].title} for item_id in item_ids],
        "bullets":resolved_bullets,"entries":[{"content_item_id":entry["content_item_id"],
            "bullet_ids":list(entry.get("bullet_ids",[]))} for entry in selected]}
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
