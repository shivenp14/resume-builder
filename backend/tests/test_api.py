from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_health():
    assert client.get('/health').json()['status'] == 'ok'

def test_content_and_locked_bullet_safeguard():
    item = client.post('/content-items', json={'type':'experience','title':'Acme'}).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={
        'text':'Built a reliable API', 'is_locked':True
    }).json()
    resume = client.post('/base-resumes', json={'name':'Base'}).json()
    client.post(f"/base-resumes/{resume['id']}/entries", json={
        'content_item_id':item['id'], 'selected_bullet_ids':[bullet['id']]
    })
    application = client.post('/applications', json={
        'company':'Acme','position':'Engineer','job_description':'Python developer','base_resume_id':resume['id']
    }).json()
    response = client.post(f"/applications/{application['id']}/proposals", json={'payload':{
        'bullet_changes':[{'bullet_id':bullet['id'],'proposed_text':'Invented 99% metric'}]
    }})
    assert response.status_code == 422

def test_analysis_and_immutable_revision_numbers():
    resume = client.post('/base-resumes', json={'name':'History'}).json()
    application = client.post('/applications', json={
        'company':'Test','position':'Role','job_description':'Need Python and Docker. Build services.','base_resume_id':resume['id']
    }).json()
    analysis = client.post(f"/applications/{application['id']}/analyze").json()
    assert set(analysis['technologies']) == {'python','docker'}
    snapshot = {'content_items': [], 'bullets': [], 'entries': [], 'sections': [], 'contact': {}}
    first = client.post(f"/applications/{application['id']}/revisions", json={'resume_json':snapshot}).json()
    second = client.post(f"/applications/{application['id']}/revisions", json={'resume_json':snapshot}).json()
    assert (first['revision_number'], second['revision_number']) == (1,2)
    assert client.get(f"/applications/{application['id']}/revisions").json()[0]['resume_json'] == snapshot

def test_reference_integrity_and_revision_shape():
    first = client.post('/content-items', json={'type':'experience','title':'First'}).json()
    second = client.post('/content-items', json={'type':'experience','title':'Second'}).json()
    bullet = client.post(f"/content-items/{first['id']}/bullets", json={'text':'Verified work'}).json()
    resume = client.post('/base-resumes', json={'name':'Base'}).json()
    entry = client.post(f"/base-resumes/{resume['id']}/entries", json={
        'content_item_id':first['id'], 'selected_bullet_ids':[bullet['id']]
    }).json()
    assert client.patch(f"/base-entries/{entry['id']}", json={
        'content_item_id':second['id'], 'selected_bullet_ids':[bullet['id']], 'entry_order':0
    }).status_code == 422
    assert client.delete(f"/bullets/{bullet['id']}").status_code == 409
    application = client.post('/applications', json={
        'company':'Test','position':'Role','job_description':'Python','base_resume_id':resume['id']
    }).json()
    assert client.post(f"/applications/{application['id']}/revisions", json={'resume_json':{'x':1}}).status_code == 422

def test_openapi_contracts_and_missing_application_lists():
    schema = client.get('/openapi.json').json()
    assert schema['paths']['/applications']['get']['responses']['200']['content']['application/json']['schema']['type'] == 'array'
    for path, method in [('/applications/{id}/comparison', 'get'), ('/applications/{id}/snapshot', 'get'), ('/applications/{id}/generate', 'post'), ('/render', 'post')]:
        assert 'content' in schema['paths'][path][method]['responses']['200']
    assert client.get('/applications/99999/proposals').status_code == 404
    assert client.get('/applications/99999/revisions').status_code == 404
    assert client.get('/applications/99999/missing-confirmations').status_code == 404

def test_application_patch_rejects_invalid_required_fields_and_status():
    resume = client.post('/base-resumes', json={'name':'Base'}).json()
    application = client.post('/applications', json={
        'company':'Test','position':'Role','job_description':'Python','base_resume_id':resume['id']
    }).json()
    assert client.patch(f"/applications/{application['id']}", json={'company':None}).status_code == 422
    assert client.patch(f"/applications/{application['id']}", json={'status':'made-up'}).status_code == 422

def test_render_endpoint_escapes_latex_and_accepts_structured_snapshot():
    response = client.post('/render', json={'snapshot': {
        'contact': {'name': 'A&B', 'location': 'NJ', 'email': 'a@example.com', 'phone': '555'},
        'sections': [{'title': 'Experience', 'entries': [{
            'title': 'R&D', 'organization': 'Acme_Inc', 'dates': '2025 -- Present',
            'bullets': [{'text': 'Improved API reliability by 20%'}]
        }]}]
    }})
    assert response.status_code == 200
    latex = response.json()['latex']
    assert r'A\&B' in latex
    assert r'Acme\_Inc' in latex
    assert r'20\%' in latex
