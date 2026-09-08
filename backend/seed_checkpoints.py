"""Replace demo records with the real resumes stored in ``checkpoints``."""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from backend.app.main import (ROOT, Application, BaseEntry, BaseResume, Bullet,
    BulletVersion, ContentItem, ContentItemVersion, JobAnalysis,
    MissingConfirmation, Proposal, Revision, SessionLocal, Skill, analyze_text,
    _append_bullet_version, _append_content_version, _BULLET_VERSION_FIELDS,
    _CONTENT_VERSION_FIELDS)

CHECKPOINTS = ROOT / "checkpoints"
PRIMARY = "baseline-2026-07-27"
DISPLAY_NAMES = {
    "baseline-2026-06-16": "Baseline · June 16, 2026",
    "baseline-2026-07-27": "Baseline · July 27, 2026",
    "it-resume-2026-06-16": "IT resume · June 16, 2026",
    "cold-stone-crew-member-2026-07-24": "Cold Stone · Crew Member",
    "appian-2026-07-30": "Appian · Software Engineering Intern",
    "vishwa-daycare-resume-2026-08-07": "Vishwa Pandya · Daycare",
}

def braced(text: str, start: int) -> tuple[str, int]:
    start = text.find("{", start)
    if start < 0: return "", len(text)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{" and (index == 0 or text[index - 1] != "\\"): depth += 1
        elif text[index] == "}" and (index == 0 or text[index - 1] != "\\"):
            depth -= 1
            if depth == 0: return text[start + 1:index], index + 1
    return text[start + 1:], len(text)

def args_after(text: str, start: int, count: int) -> tuple[list[str], int]:
    values, cursor = [], start
    for _ in range(count):
        value, cursor = braced(text, cursor); values.append(value)
    return values, cursor

def clean(value: str) -> str:
    value = re.sub(r"%.*", "", value)
    value = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", value)
    for command in ("textbf", "textit", "emph", "underline", "small", "scshape"):
        value = re.sub(rf"\\{command}\s*\{{([^{{}}]*)\}}", r"\1", value)
    value = value.replace(r"\textasciitilde{}", "~").replace(r"\&", "&").replace(r"\%", "%").replace(r"\_", "_")
    value = value.replace("$|$", "|").replace("--", "–")
    value = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?", "", value)
    value = value.replace("{", "").replace("}", "").replace("$", "")
    return re.sub(r"\s+", " ", value).strip(" \\:")

def parse_contact(path: Path) -> dict[str,str]:
    text=path.read_text(errors="replace")
    center=re.search(r"\\begin\{center\}(.*?)\\end\{center\}",text,re.S)
    block=center.group(1) if center else text
    name_match=re.search(r"\\textbf\{\\Huge(?:\s+\\scshape)?\s+([^}]+)\}",block)
    email_match=re.search(r"mailto:([^}]+)",block)
    linkedin_match=re.search(r"https?://(?:www\.)?linkedin\.com/[^}\s]+",block)
    phone_match=re.search(r"(?:\(\d{3}\)\s*\d{3}-\d{4}|\d{3}-\d{3}-\d{4})",block)
    location_match=re.search(r"\\small\s+([^$\\\n]+?)\s*\$\|\$",block)
    contact={"name":clean(name_match.group(1)) if name_match else "",
        "location":clean(location_match.group(1)) if location_match else "",
        "email":email_match.group(1) if email_match else "","phone":phone_match.group(0) if phone_match else ""}
    if linkedin_match:
        contact["linkedin"]=linkedin_match.group(0)
        contact["linkedin_label"]=contact["linkedin"].removeprefix("https://").removeprefix("http://").rstrip("/")
    return contact

def parse_resume(path: Path) -> list[dict]:
    text = path.read_text(errors="replace")
    sections = list(re.finditer(r"\\section\{([^}]+)\}", text)); records = []
    for section_index, section in enumerate(sections):
        section_name = clean(section.group(1)).lower().replace(" & ", "_")
        section_name = section_name.replace("technical skills", "skills").replace("relevant skills", "skills")
        end = sections[section_index + 1].start() if section_index + 1 < len(sections) else len(text)
        body = text[section.end():end]
        markers = list(re.finditer(r"\\resume(Subheading|ProjectHeading)\b", body))
        if not markers:
            summary = clean(body)
            if summary: records.append({"type": section_name, "title": clean(section.group(1)), "summary": summary, "bullets": []})
            continue
        for marker_index, marker in enumerate(markers):
            count = 4 if marker.group(1) == "Subheading" else 2
            values, content_start = args_after(body, marker.end(), count)
            content_end = markers[marker_index + 1].start() if marker_index + 1 < len(markers) else len(body)
            chunk, bullet_values, cursor = body[content_start:content_end], [], 0
            while True:
                found = chunk.find(r"\resumeItem", cursor)
                if found < 0: break
                bullet, cursor = braced(chunk, found + len(r"\resumeItem")); bullet = clean(bullet)
                if bullet: bullet_values.append(bullet)
            if count == 4: organization, dates, title, location = map(clean, values)
            else: title, dates = map(clean, values); organization, location = "Project", ""
            records.append({"type": "project" if count == 2 else section_name, "title": title,
                "organization": organization, "location": location, "start_date": dates, "bullets": bullet_values})
    return records

def _clear_source_history(session) -> None:
    """Clear histories as part of the intentional destructive seed reset.

    ``seed`` replaces every source row.  Clearing the append-only tables in
    the same transaction prevents SQLite from reusing a primary key and
    accidentally associating a new source with the previous source's history.
    Fresh imported rows receive a new baseline version below.
    """
    session.query(BulletVersion).delete(synchronize_session=False)
    session.query(ContentItemVersion).delete(synchronize_session=False)

def seed() -> None:
    folders = sorted(path for path in CHECKPOINTS.iterdir() if (path / "resume.tex").exists())
    with SessionLocal() as session:
        _clear_source_history(session)
        for model in (Revision, Proposal, MissingConfirmation, JobAnalysis, Application, BaseEntry, Bullet, ContentItem, Skill, BaseResume):
            session.query(model).delete()
        bases = {}
        for folder in folders:
            slug, is_primary = folder.name, folder.name == PRIMARY
            pdf_file = next(iter(sorted(folder.glob("*.pdf"))))
            owner = "Vishwa Pandya" if slug.startswith("vishwa-") else "Shiven Pandya"
            base = BaseResume(name=DISPLAY_NAMES.get(slug, slug.replace("-", " ").title()), template_id="latex-checkpoint",
                section_order=[], layout_settings={"primary": is_primary, "owner":owner,
                "contact":parse_contact(folder/"resume.tex"),"checkpoint": slug,
                "pdf_path": pdf_file.relative_to(ROOT).as_posix()})
            session.add(base); session.flush(); bases[slug] = base; section_order = []
            for order, record in enumerate(parse_resume(folder / "resume.tex")):
                item_type = record.get("type", "other")
                if item_type not in section_order: section_order.append(item_type)
                bullets = record.pop("bullets", [])
                item = ContentItem(**record, tags=[f"checkpoint:{slug}"], is_archived=False)
                session.add(item); session.flush()
                _append_content_version(session, item, action="created", changed_fields=list(_CONTENT_VERSION_FIELDS))
                bullet_ids = []
                for text in bullets:
                    verified=owner=="Shiven Pandya"
                    bullet = Bullet(content_item_id=item.id, text=text, tags=[f"checkpoint:{slug}"],
                        supporting_facts=[text] if verified else [],is_locked=not verified,is_preferred=is_primary)
                    session.add(bullet); session.flush()
                    _append_bullet_version(session, bullet, action="created", changed_fields=list(_BULLET_VERSION_FIELDS))
                    bullet_ids.append(bullet.id)
                session.add(BaseEntry(base_resume_id=base.id, content_item_id=item.id, selected_bullet_ids=bullet_ids, entry_order=order))
            base.section_order = section_order
        details = (CHECKPOINTS / "appian-2026-07-30" / "job-details.txt").read_text()
        app = Application(company="Appian", position="Software Engineering Intern · Summer 2027", job_description=details,
            job_url="https://job-boards.greenhouse.io/appian/jobs/8041237/confirmation?gh_src=Simplify",
            notes="Imported from checkpoints/appian-2026-07-30/job-details.txt", status="applied",
            base_resume_id=bases["appian-2026-07-30"].id, created_at=datetime(2026, 7, 30, tzinfo=timezone.utc))
        session.add(app); session.flush(); session.add(JobAnalysis(application_id=app.id, **analyze_text(details)))
        session.commit(); print(f"Imported {len(folders)} resumes; primary is {DISPLAY_NAMES[PRIMARY]}.")

def migrate_verified() -> None:
    """Backfill checkpoint metadata without replacing existing database records."""
    updated=0
    # ``begin`` makes the migration all-or-nothing.  In particular, a bad
    # checkpoint must not leave contact metadata committed while the evidence
    # backfill is only partially complete.
    with SessionLocal.begin() as session:
        for base in session.query(BaseResume).all():
            settings=dict(base.layout_settings or {})
            slug=settings.get("checkpoint")
            if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
                continue
            folder=CHECKPOINTS / slug
            # The checkpoint marker and an on-disk resume are both required;
            # do not accept arbitrary paths from layout_settings.
            if not folder.is_dir() or not (folder / "resume.tex").is_file():
                continue
            # Presence, rather than truthiness, is the migration marker.  An
            # intentionally empty parsed contact must not be reprocessed on
            # every invocation.
            if "contact" not in settings:
                settings["contact"]=parse_contact(folder / "resume.tex")
                base.layout_settings=settings
                updated+=1

            # Ownership is an explicit importer fact.  A contact name is user
            # editable data and must never be used to infer source ownership.
            if settings.get("owner") != "Shiven Pandya":
                continue
            checkpoint_tag=f"checkpoint:{slug}"
            item_ids={entry.content_item_id for entry in session.query(BaseEntry).filter_by(base_resume_id=base.id)}
            items=session.query(ContentItem).filter(ContentItem.id.in_(item_ids)).all() if item_ids else []
            for item in items:
                # Only records positively tied to this checkpoint may be
                # rewritten.  This protects hand-created/library content that
                # happens to be selected by a checkpoint base resume.
                if checkpoint_tag not in (item.tags or []):
                    continue
                for bullet in item.bullets:
                    if checkpoint_tag not in (bullet.tags or []):
                        continue
                    evidence=list(bullet.supporting_facts or [])
                    if not evidence:
                        evidence=[bullet.text]
                    changed_fields=[]
                    if evidence != list(bullet.supporting_facts or []):
                        bullet.supporting_facts=evidence
                        changed_fields.append("supporting_facts")
                    if bullet.is_locked:
                        bullet.is_locked=False
                        changed_fields.append("is_locked")
                    if changed_fields:
                        _append_bullet_version(session, bullet, action="updated", changed_fields=changed_fields)
                        updated+=1
    print(f"Migrated {updated} verified checkpoint records.")

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--migrate-verified",action="store_true")
    args=parser.parse_args()
    migrate_verified() if args.migrate_verified else seed()
