"""Contract tests for applying approved proposals to immutable revisions."""
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import app

client=TestClient(app)

def setup_application():
    item=client.post("/content-items",json={"type":"experience","title":"Engineer",
        "organization":"Acme","location":"Remote","start_date":"2025","end_date":"Present"}).json()
    bullet=client.post(f"/content-items/{item['id']}/bullets",json={"text":"Built a Python API used by 4 teams",
        "supporting_facts":["Built a Python API used by 4 teams"],"is_locked":False}).json()
    resume=client.post("/base-resumes",json={"name":"Primary","section_order":["experience"],
        "layout_settings":{"contact":{"name":"Test Person","location":"Hoboken, NJ",
        "email":"test@example.com","phone":"555-0100","linkedin":"https://linkedin.com/in/test"}}}).json()
    client.post(f"/base-resumes/{resume['id']}/entries",json={"content_item_id":item["id"],
        "selected_bullet_ids":[bullet["id"]],"entry_order":0})
    application=client.post("/applications",json={"company":"Example","position":"Software Engineer",
        "job_description":"Build Python services","base_resume_id":resume["id"]}).json()
    return item,bullet,application

def create_proposal(application_id,item_id,bullet_id,*,selected=True,rewrite=True):
    payload={"selected_entries":[{"content_item_id":item_id,"bullet_ids":[bullet_id]}] if selected else [],
        "bullet_changes":[{"bullet_id":bullet_id,"proposed_text":"Built a Python API supporting 4 teams",
        "rationale":"Tighter wording","evidence":["Built a Python API used by 4 teams"]}] if rewrite else [],
        "warnings":[],"rationale":"Tailored for the role"}
    return client.post(f"/applications/{application_id}/proposals",json={"payload":payload}).json()

def test_approved_proposal_is_materialized_without_mutating_source():
    item,bullet,application=setup_application()
    proposal=create_proposal(application["id"],item["id"],bullet["id"])
    assert client.post(f"/proposals/{proposal['id']}/approve").status_code==200
    generated=client.post(f"/applications/{application['id']}/generate",json={"proposal_id":proposal["id"]})
    assert generated.status_code==200,generated.text
    revision=generated.json()
    assert revision["proposal_id"]==proposal["id"]
    resolved=revision["resume_json"]["bullets"][0]
    assert resolved["text"]=="Built a Python API supporting 4 teams"
    assert resolved["source_text"]==bullet["text"]
    assert revision["resume_json"]["provenance"]["proposal_id"]==proposal["id"]
    assert client.get(f"/content-items/{item['id']}/bullets").json()[0]["text"]==bullet["text"]
    assert Path(revision["latex_path"]).read_text().find("supporting 4 teams")>=0

def test_generation_requires_approved_matching_fresh_proposal():
    item,bullet,application=setup_application()
    pending=create_proposal(application["id"],item["id"],bullet["id"],rewrite=False)
    assert client.post(f"/applications/{application['id']}/generate",json={"proposal_id":pending["id"]}).status_code==409
    assert client.post(f"/proposals/{pending['id']}/approve").status_code==200
    other_item,other_bullet,other_application=setup_application()
    other=create_proposal(other_application["id"],other_item["id"],other_bullet["id"],rewrite=False)
    assert client.post(f"/proposals/{other['id']}/approve").status_code==200
    assert client.post(f"/applications/{application['id']}/generate",json={"proposal_id":other["id"]}).status_code==422
    client.patch(f"/bullets/{bullet['id']}",json={"text":"Source changed","supporting_facts":["Source changed"],
        "tags":[],"is_locked":False,"is_preferred":False})
    stale=client.post(f"/applications/{application['id']}/generate",json={"proposal_id":pending["id"]})
    assert stale.status_code==409

def test_empty_selection_preserves_base_entries_and_unselected_rewrites_fail():
    item,bullet,application=setup_application()
    fallback=create_proposal(application["id"],item["id"],bullet["id"],selected=False,rewrite=False)
    assert client.post(f"/proposals/{fallback['id']}/approve").status_code==200
    generated=client.post(f"/applications/{application['id']}/generate",json={"proposal_id":fallback["id"]})
    assert generated.status_code==200,generated.text
    assert generated.json()["resume_json"]["entries"]==[{"content_item_id":item["id"],"bullet_ids":[bullet["id"]]}]
    unselected=client.post(f"/applications/{application['id']}/proposals",json={"payload":{
        "selected_entries":[{"content_item_id":item["id"],"bullet_ids":[]}],
        "bullet_changes":[{"bullet_id":bullet["id"],"proposed_text":"Built a Python API supporting 4 teams",
            "rationale":"Tighter wording","evidence":[bullet["text"]]}],"warnings":[],"rationale":""}}).json()
    # Approval rejects a proposal that could not be materialized safely.
    assert client.post(f"/proposals/{unselected['id']}/approve").status_code==422

def test_nonempty_selection_must_preserve_foundational_base_entries():
    item,bullet,application=setup_application()
    education=client.post("/content-items",json={"type":"education","title":"B.S. Computer Science",
        "organization":"Stevens Institute of Technology"}).json()
    client.post(f"/base-resumes/{application['base_resume_id']}/entries",json={"content_item_id":education["id"],
        "selected_bullet_ids":[],"entry_order":0})
    response=client.post(f"/applications/{application['id']}/proposals",json={"payload":{
        "selected_entries":[{"content_item_id":item["id"],"bullet_ids":[bullet["id"]]}],
        "bullet_changes":[],"warnings":[],"rationale":""}})
    assert response.status_code==422
    assert "omits required base entry" in response.json()["detail"]

def test_failed_render_marks_reserved_revision_failed(monkeypatch):
    item, bullet, application = setup_application()
    proposal = create_proposal(application["id"], item["id"], bullet["id"], rewrite=False)
    assert client.post(f"/proposals/{proposal['id']}/approve").status_code == 200
    from backend.app import main
    monkeypatch.setattr(main.ResumeRenderer, "compile", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("latex unavailable")))
    response = client.post(f"/applications/{application['id']}/generate", json={"proposal_id":proposal["id"]})
    assert response.status_code == 503
    assert client.get(f"/applications/{application['id']}/revisions").json()[0]["status"] == "failed"
