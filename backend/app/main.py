"""Local-first Resume Builder API.

The API deliberately keeps AI output as proposals: source records and revisions
are never silently changed by analysis or optimization.
"""
from datetime import datetime, timezone
import json, re
from pathlib import Path
from typing import Any
import uuid
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Text, Integer, DateTime, ForeignKey, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker, Session
from .services.renderer import ResumeRenderer
from .services.validation import ValidationError, validate_resume_snapshot, validate_proposal
from .services.codex_provider import CodexProvider, CodexProviderError, MODEL, REASONING
from .services.llm_prompts import PROMPT_VERSION
from .services.llm_schemas import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
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
    id: Mapped[int]=mapped_column(primary_key=True); base_resume_id: Mapped[int]=mapped_column(ForeignKey("base_resumes.id")); content_item_id: Mapped[int]=mapped_column(ForeignKey("content_items.id")); selected_bullet_ids: Mapped[list]=mapped_column(JSON,default=list); entry_order: Mapped[int]=mapped_column(Integer,default=0)
class Application(Base):
    __tablename__="applications"
    id: Mapped[int]=mapped_column(primary_key=True); company: Mapped[str]=mapped_column(String(200)); position: Mapped[str]=mapped_column(String(200)); job_url: Mapped[str|None]=mapped_column(String(500)); job_description: Mapped[str]=mapped_column(Text); notes: Mapped[str|None]=mapped_column(Text); status: Mapped[str]=mapped_column(String(30),default="draft"); base_resume_id: Mapped[int]=mapped_column(ForeignKey("base_resumes.id")); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); updated_at: Mapped[datetime]=mapped_column(DateTime,default=now,onupdate=now)
class JobAnalysis(Base):
    __tablename__="job_analyses"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirements: Mapped[list]=mapped_column(JSON); keywords: Mapped[list]=mapped_column(JSON); technologies: Mapped[list]=mapped_column(JSON); responsibilities: Mapped[list]=mapped_column(JSON); preferred_qualifications: Mapped[list]=mapped_column(JSON); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Proposal(Base):
    __tablename__="proposals"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); payload: Mapped[dict]=mapped_column(JSON); status: Mapped[str]=mapped_column(String(20),default="pending"); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class Revision(Base):
    __tablename__="revisions"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); revision_number: Mapped[int]; resume_json: Mapped[dict]=mapped_column(JSON); latex_path: Mapped[str|None]=mapped_column(String(500)); pdf_path: Mapped[str|None]=mapped_column(String(500)); page_count: Mapped[int|None]; status: Mapped[str]=mapped_column(String(20),default="draft"); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class MissingConfirmation(Base):
    __tablename__="missing_confirmations"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); requirement: Mapped[str]=mapped_column(Text); status: Mapped[str]=mapped_column(String(20),default="unresolved"); context: Mapped[dict]=mapped_column(JSON,default=dict); created_at: Mapped[datetime]=mapped_column(DateTime,default=now)
class OptimizationRun(Base):
    __tablename__="optimization_runs"
    id: Mapped[int]=mapped_column(primary_key=True); application_id: Mapped[int]=mapped_column(ForeignKey("applications.id")); operation: Mapped[str]=mapped_column(String(40)); status: Mapped[str]=mapped_column(String(20),default="running"); model: Mapped[str]=mapped_column(String(100),default="gpt-5.6-luna"); reasoning_effort: Mapped[str]=mapped_column(String(20),default="low"); prompt_version: Mapped[str]=mapped_column(String(40),default="v1"); schema_version: Mapped[str]=mapped_column(String(40),default="v1"); idempotency_key: Mapped[str|None]=mapped_column(String(200)); input_payload: Mapped[dict]=mapped_column(JSON,default=dict); output_payload: Mapped[dict|None]=mapped_column(JSON); error: Mapped[str|None]=mapped_column(Text); created_at: Mapped[datetime]=mapped_column(DateTime,default=now); completed_at: Mapped[datetime|None]=mapped_column(DateTime)
Base.metadata.create_all(engine)
def db():
    s=SessionLocal()
    try: yield s
    finally: s.close()

class ItemIn(BaseModel): type:str; title:str; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]=[]
class ItemPatch(BaseModel): type:str|None=None; title:str|None=None; organization:str|None=None; location:str|None=None; start_date:str|None=None; end_date:str|None=None; summary:str|None=None; tags:list[str]|None=None
class BulletIn(BaseModel): text:str; tags:list[str]=[]; supporting_facts:list[str]=[]; is_locked:bool=False; is_preferred:bool=False
class SkillIn(BaseModel): name:str; category:str|None=None; aliases:list[str]=[]; notes:str|None=None; verified:bool=False
class ResumeIn(BaseModel): name:str; template_id:str="default"; section_order:list[str]=[]; layout_settings:dict={}
class EntryIn(BaseModel): content_item_id:int; selected_bullet_ids:list[int]=[]; entry_order:int=0
class AppIn(BaseModel): company:str; position:str; job_description:str; base_resume_id:int; job_url:str|None=None; notes:str|None=None
class ProposalIn(BaseModel): payload:dict
class RevisionIn(BaseModel): resume_json:dict; latex_path:str|None=None; pdf_path:str|None=None; page_count:int|None=None; status:str="draft"
class ConfirmationIn(BaseModel): status:str; context:dict={}
class OptimizationRequest(BaseModel): idempotency_key:str|None=None

app=FastAPI(title="Resume Builder API",version="0.1.0")
GENERATED = ROOT / "generated"
GENERATED.mkdir(exist_ok=True)
app.mount("/generated", StaticFiles(directory=GENERATED), name="generated")
app.mount("/checkpoints", StaticFiles(directory=ROOT / "checkpoints"), name="checkpoints")
@app.get("/health")
def health(): return {"status":"ok"}
@app.post("/content-items")
def create_item(x:ItemIn,s:Session=Depends(db)):
    o=ContentItem(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/content-items")
def items(s:Session=Depends(db)): return s.query(ContentItem).filter_by(is_archived=False).all()
@app.get("/content-items/{id}")
def item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    return o
@app.patch("/content-items/{id}")
def edit_item(id:int,x:ItemPatch,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    for k,v in x.model_dump(exclude_unset=True).items(): setattr(o,k,v)
    s.commit(); return o
@app.delete("/content-items/{id}")
def archive_item(id:int,s:Session=Depends(db)):
    o=s.get(ContentItem,id)
    if not o: raise HTTPException(404,"content item not found")
    o.is_archived=True; s.commit(); return {"archived":True}
@app.post("/content-items/{id}/bullets")
def add_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    o=Bullet(content_item_id=id,**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/content-items/{id}/bullets")
def get_bullets(id:int,s:Session=Depends(db)):
    if not s.get(ContentItem,id): raise HTTPException(404,"content item not found")
    return s.query(Bullet).filter_by(content_item_id=id).all()
@app.patch("/bullets/{id}")
def edit_bullet(id:int,x:BulletIn,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    for k,v in x.model_dump().items(): setattr(o,k,v)
    s.commit(); return o
@app.delete("/bullets/{id}")
def delete_bullet(id:int,s:Session=Depends(db)):
    o=s.get(Bullet,id)
    if not o: raise HTTPException(404,"bullet not found")
    s.delete(o); s.commit(); return {"deleted":True}
@app.post("/skills")
def add_skill(x:SkillIn,s:Session=Depends(db)):
    if s.query(Skill).filter_by(name=x.name).first(): raise HTTPException(409,"skill exists")
    o=Skill(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/skills")
def skills(s:Session=Depends(db)): return s.query(Skill).all()
@app.post("/base-resumes")
def add_resume(x:ResumeIn,s:Session=Depends(db)):
    o=BaseResume(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/base-resumes")
def resumes(s:Session=Depends(db)):
    records=s.query(BaseResume).all()
    return sorted(records,key=lambda record:(not bool((record.layout_settings or {}).get("primary")),record.id))
@app.get("/base-resumes/{id}")
def resume(id:int,s:Session=Depends(db)):
    o=s.get(BaseResume,id)
    if not o: raise HTTPException(404,"base resume not found")
    return o
@app.patch("/base-resumes/{id}")
def edit_resume(id:int,x:ResumeIn,s:Session=Depends(db)):
    o=s.get(BaseResume,id)
    if not o: raise HTTPException(404,"base resume not found")
    for k,v in x.model_dump().items(): setattr(o,k,v)
    s.commit(); return o
@app.post("/base-resumes/{id}/entries")
def add_entry(id:int,x:EntryIn,s:Session=Depends(db)):
    if not s.get(BaseResume,id) or not s.get(ContentItem,x.content_item_id): raise HTTPException(404,"resume or content item not found")
    if x.selected_bullet_ids:
        owned={b.id for b in s.query(Bullet).filter_by(content_item_id=x.content_item_id).all()}
        if any(bid not in owned for bid in x.selected_bullet_ids): raise HTTPException(422,"selected bullet does not belong to content item")
    o=BaseEntry(base_resume_id=id,**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/base-resumes/{id}/entries")
def entries(id:int,s:Session=Depends(db)):
    if not s.get(BaseResume,id): raise HTTPException(404,"base resume not found")
    return s.query(BaseEntry).filter_by(base_resume_id=id).order_by(BaseEntry.entry_order).all()
@app.patch("/base-entries/{id}")
def edit_entry(id:int,x:EntryIn,s:Session=Depends(db)):
    o=s.get(BaseEntry,id)
    if not o: raise HTTPException(404,"base entry not found")
    for k,v in x.model_dump(exclude_unset=True).items(): setattr(o,k,v)
    s.commit(); s.refresh(o); return o
@app.delete("/base-entries/{id}")
def delete_entry(id:int,s:Session=Depends(db)):
    o=s.get(BaseEntry,id)
    if not o: raise HTTPException(404,"base entry not found")
    s.delete(o); s.commit(); return {"deleted":True}
@app.post("/applications")
def add_app(x:AppIn,s:Session=Depends(db)):
    if not s.get(BaseResume,x.base_resume_id): raise HTTPException(404,"base resume not found")
    o=Application(**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/applications")
def applications(s:Session=Depends(db)): return s.query(Application).order_by(Application.created_at.desc()).all()
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

@app.post("/applications/{id}/analyze")
def analyze(id:int, request:OptimizationRequest|None=None, s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    key=request.idempotency_key if request else None
    if key:
        prior=s.query(OptimizationRun).filter_by(application_id=id,operation="analysis",idempotency_key=key).first()
        if prior and prior.status=="succeeded": return prior.output_payload
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
@app.get("/applications/{id}/analysis")
def get_analysis(id:int,s:Session=Depends(db)):
    o=s.query(JobAnalysis).filter_by(application_id=id).order_by(JobAnalysis.created_at.desc()).first()
    if not o: raise HTTPException(404,"analysis not found")
    return o
@app.post("/applications/{id}/proposals/generate")
def generate_proposals(id:int, request:OptimizationRequest|None=None, s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    analysis=s.query(JobAnalysis).filter_by(application_id=id).order_by(JobAnalysis.created_at.desc()).first()
    if not analysis: raise HTTPException(404,"analyze application first")
    key=request.idempotency_key if request else None
    if key:
        prior=s.query(OptimizationRun).filter_by(application_id=id,operation="proposal",idempotency_key=key).first()
        if prior and prior.status=="succeeded": return prior.output_payload
    payload={"analysis":{k:getattr(analysis,k) for k in ("requirements","keywords","technologies","responsibilities","preferred_qualifications")},"snapshot":build_snapshot(a,s),"confirmations":[{"requirement":x.requirement,"status":x.status,"context":x.context} for x in s.query(MissingConfirmation).filter_by(application_id=id)]}
    all_items=s.query(ContentItem).filter_by(is_archived=False).all()
    all_bullets=s.query(Bullet).filter(Bullet.content_item_id.in_([item.id for item in all_items])).all() if all_items else []
    verified={"content_items":[{"id":item.id,"type":item.type,"title":item.title,"organization":item.organization,"summary":item.summary,"tags":item.tags} for item in all_items],
              "bullets":[{"id":bullet.id,"content_item_id":bullet.content_item_id,"text":bullet.text,"tags":bullet.tags,"supporting_facts":bullet.supporting_facts,"is_locked":bullet.is_locked,"is_preferred":bullet.is_preferred} for bullet in all_bullets]}
    payload={**payload,"base_snapshot":payload.pop("snapshot"),"verified_library":verified}
    grounding={**verified,"entries":[]}
    run=OptimizationRun(application_id=id,operation="proposal",idempotency_key=key,input_payload=payload,model=MODEL,reasoning_effort=REASONING,prompt_version=PROMPT_VERSION,schema_version=SCHEMA_VERSION); s.add(run); s.commit()
    try:
        out=_provider_call(_provider(),"proposal",payload)
        if hasattr(out,"model_dump"): out=out.model_dump()
        if not isinstance(out,dict): raise ValueError("Codex returned invalid proposal")
        validate_proposal(out, grounding)
        owned={item.id:{bullet.id for bullet in item.bullets} for item in all_items}
        for entry in out.get("selected_entries",[]):
            item_id=entry.get("content_item_id")
            if item_id not in owned: raise ValidationError("proposal selects unknown content item")
            if any(bid not in owned[item_id] for bid in entry.get("bullet_ids",[])): raise ValidationError("selected bullet does not belong to content item")
        # Reuse the existing proposal validation gates before persistence.
        p=Proposal(application_id=id,payload=out); s.add(p); s.flush()
        run.status="succeeded"; run.output_payload={"id":p.id,**out}; run.completed_at=now(); s.commit(); s.refresh(p); return p
    except ValidationError as exc:
        run.status="failed"; run.error=str(exc)[:2000]; run.completed_at=now(); s.commit(); raise HTTPException(422,str(exc))
    except Exception as exc:
        run.status="failed"; run.error=_run_error(exc); run.completed_at=now(); s.commit(); raise HTTPException(503,"Codex provider unavailable: "+run.error)

@app.get("/applications/{id}/optimization-runs")
def optimization_runs(id:int,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    return s.query(OptimizationRun).filter_by(application_id=id).order_by(OptimizationRun.created_at.desc()).all()

@app.get("/optimization-runs/{id}")
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
@app.get("/applications/{id}/comparison")
def comparison(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    return _comparison(a,s)
@app.post("/applications/{id}/missing-confirmations")
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
@app.post("/applications/{id}/confirmations")
def confirm_alias(id:int, payload:dict, s:Session=Depends(db)):
    """Compatibility contract for the concise confirmation workflow."""
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    req=str(payload.get('requirement','')).strip()
    decision=str(payload.get('decision',payload.get('status','unresolved'))).lower()
    status={'confirm':'confirmed','confirmed':'confirmed','reject':'rejected','rejected':'rejected'}.get(decision,'unresolved')
    o=s.query(MissingConfirmation).filter_by(application_id=id,requirement=req).first()
    if not o:
        o=MissingConfirmation(application_id=id,requirement=req,status=status,context=payload); s.add(o)
    else: o.status=status; o.context=payload
    s.commit(); s.refresh(o); return o
@app.get("/applications/{id}/missing-confirmations")
def list_confirmations(id:int,s:Session=Depends(db)): return s.query(MissingConfirmation).filter_by(application_id=id).all()
@app.patch("/missing-confirmations/{id}")
def update_confirmation(id:int,x:ConfirmationIn,s:Session=Depends(db)):
    o=s.get(MissingConfirmation,id)
    if not o: raise HTTPException(404,"confirmation not found")
    if x.status not in {'confirmed','rejected','unresolved'}: raise HTTPException(422,"invalid confirmation status")
    o.status=x.status; o.context=x.context; s.commit(); s.refresh(o); return o
@app.post("/applications/{id}/proposals")
def proposal(id:int,x:ProposalIn,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    p=x.payload
    if p.get('snapshot'):
        try: validate_proposal(p, p['snapshot'])
        except ValidationError as exc: raise HTTPException(422,str(exc))
    # Stable IDs are mandatory; unknown IDs and edits to locked bullets are rejected.
    for c in p.get("selected_entries",[]):
        item=s.get(ContentItem,c.get("content_item_id"))
        if not item: raise HTTPException(422,"unknown content item")
        owned={b.id for b in s.query(Bullet).filter_by(content_item_id=item.id).all()}
        if any(int(bid) not in owned for bid in c.get("bullet_ids",[])): raise HTTPException(422,"selected bullet does not belong to content item")
    for change in p.get("bullet_changes",[]):
        b=s.get(Bullet,change.get("bullet_id"))
        if not b: raise HTTPException(422,"unknown bullet")
        if b.is_locked and change.get("proposed_text",b.text)!=b.text: raise HTTPException(422,"locked bullet cannot be rewritten")
        if change.get("proposed_text") and not any(f.lower() in change["proposed_text"].lower() for f in b.supporting_facts) and re.search(r"\\d",change["proposed_text"]) and not re.search(r"\\d",b.text): raise HTTPException(422,"unsupported numeric claim")
    o=Proposal(application_id=id,payload=p); s.add(o); s.commit(); s.refresh(o); return o
@app.post("/proposals/{id}/approve")
def approve(id:int,s:Session=Depends(db)):
    o=s.get(Proposal,id)
    if not o: raise HTTPException(404,"proposal not found")
    o.status="approved"; s.commit(); return o
@app.post("/proposals/{id}/decision")
def proposal_decision(id:int, decision:dict, s:Session=Depends(db)):
    o=s.get(Proposal,id)
    if not o: raise HTTPException(404,"proposal not found")
    status=decision.get('status')
    if status not in {'approved','rejected','pending'}: raise HTTPException(422,"invalid proposal status")
    o.status=status; o.payload={**o.payload,'decisions':decision.get('decisions',{})}; s.commit(); s.refresh(o); return o
@app.get("/applications/{id}/proposals")
def proposals(id:int,s:Session=Depends(db)): return s.query(Proposal).filter_by(application_id=id).order_by(Proposal.created_at.desc()).all()
def build_snapshot(a:Application,s:Session):
    entries=s.query(BaseEntry).filter_by(base_resume_id=a.base_resume_id).order_by(BaseEntry.entry_order).all()
    items={i.id:i for i in s.query(ContentItem).filter(ContentItem.id.in_([e.content_item_id for e in entries])).all()} if entries else {}
    bullets={b.id:b for b in s.query(Bullet).filter(Bullet.content_item_id.in_(list(items))).all()} if items else {}
    sections={}
    for e in entries:
        item=items[e.content_item_id]; title=item.type.title()
        sections.setdefault(title,[]).append({'title':item.title,'organization':item.organization or '',
            'location':item.location or '',
            'dates':' — '.join(x for x in (item.start_date,item.end_date) if x),
            # Project technology strings are stored in the content item's
            # summary and are rendered beside the project title.
            'skills':item.summary or '' if item.type == 'project' else '',
            'bullets':[{'id':bid,'text':bullets[bid].text} for bid in e.selected_bullet_ids if bid in bullets]})
    return {'contact':{'name':'','location':'','email':'','phone':''},'sections':[{'title':k,'entries':v} for k,v in sections.items()],'content_items':[{'id':i.id,'title':i.title} for i in items.values()],'bullets':[{'id':b.id,'content_item_id':b.content_item_id,'text':b.text,'supporting_facts':b.supporting_facts,'is_locked':b.is_locked} for b in bullets.values()],'entries':[{'content_item_id':e.content_item_id,'bullet_ids':e.selected_bullet_ids} for e in entries]}
@app.get("/applications/{id}/snapshot")
def snapshot(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    return build_snapshot(a,s)
@app.post("/applications/{id}/revisions")
def revision(id:int,x:RevisionIn,s:Session=Depends(db)):
    if not s.get(Application,id): raise HTTPException(404,"application not found")
    if any(k in x.resume_json for k in ('content_items','bullets','entries')):
        try: validate_resume_snapshot(x.resume_json)
        except ValidationError as exc: raise HTTPException(422,str(exc))
    n=(s.query(Revision).filter_by(application_id=id).count()+1)
    o=Revision(application_id=id,revision_number=n,**x.model_dump()); s.add(o); s.commit(); s.refresh(o); return o
@app.get("/applications/{id}/revisions")
def revisions(id:int,s:Session=Depends(db)): return s.query(Revision).filter_by(application_id=id).order_by(Revision.revision_number).all()
@app.get("/revisions/{id}")
def revision_detail(id:int,s:Session=Depends(db)):
    o=s.get(Revision,id)
    if not o: raise HTTPException(404,"revision not found")
    return o
@app.post("/applications/{id}/generate")
def generate(id:int,s:Session=Depends(db)):
    a=s.get(Application,id)
    if not a: raise HTTPException(404,"application not found")
    snap=build_snapshot(a,s); n=s.query(Revision).filter_by(application_id=id).count()+1
    out=GENERATED/'applications'/str(id)/f'revision-{n:03d}'
    try:
        tex,pdf=ResumeRenderer().compile(snap,out)
    except RuntimeError as exc: raise HTTPException(503,str(exc))
    page_count=None
    try:
        import subprocess
        page_count=int(subprocess.check_output(['pdfinfo',str(pdf)],text=True).split('Pages:')[1].splitlines()[0].strip())
    except Exception: pass
    o=Revision(application_id=id,revision_number=n,resume_json=snap,latex_path=str(tex.relative_to(ROOT)),pdf_path=str(pdf.relative_to(ROOT)),page_count=page_count,status='draft'); s.add(o); s.commit(); s.refresh(o)
    # Keep filesystem paths for local tooling and expose browser-served URLs
    # for the Vite UI. StaticFiles mounts the generated directory at /generated.
    return {**{c.name:getattr(o,c.name) for c in Revision.__table__.columns},
            'latex_url':'/generated/'+str(tex.relative_to(GENERATED)),
            'pdf_url':'/generated/'+str(pdf.relative_to(GENERATED))}

class RenderIn(BaseModel): snapshot:dict
@app.post("/render")
def render(x:RenderIn):
    try:
        return {"latex": ResumeRenderer().render_tex(x.snapshot)}
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
