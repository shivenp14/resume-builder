import shutil
import base64
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter

from backend.app import main
from backend.tests.test_api import client


@pytest.mark.skipif(shutil.which("latexmk") is None, reason="latexmk is required for PDF compilation")
def test_base_resume_preview_compiles_a_readable_pdf():
    profile = client.post("/personal-information", json={"name": "Preview User", "email": "preview@example.test"}).json()
    resume = client.post("/base-resumes", json={
        "name": "Compiled preview", "personal_information_id": profile["id"],
    }).json()

    response = client.get(f"/base-resumes/{resume['id']}/preview.pdf")

    assert response.status_code == 200, response.text
    assert PdfReader(BytesIO(response.content)).pages


def test_base_resume_preview_renders_current_snapshot_without_revision(monkeypatch, tmp_path):
    item = client.post("/content-items", json={
        "type": "experience", "title": "Preview engineer", "organization": "Preview Co",
    }).json()
    bullet = client.post(f"/content-items/{item['id']}/bullets", json={
        "text": "Built the current resume preview", "supporting_facts": ["preview"],
    }).json()
    resume = client.post("/base-resumes", json={"name": "Preview base"}).json()
    client.post(f"/base-resumes/{resume['id']}/entries", json={
        "content_item_id": item["id"], "selected_bullet_ids": [bullet["id"]], "entry_order": 1,
    })
    application = client.post("/applications", json={
        "company": "Preview Co", "position": "Engineer", "base_resume_id": resume["id"],
        "job_description": "Build useful software.",
    }).json()
    compiled = {}

    def fake_compile(snapshot, output_dir):
        compiled["snapshot"] = snapshot
        tex_path = tmp_path / "resume.tex"
        pdf_path = tmp_path / "resume.pdf"
        tex_path.write_text("preview")
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with pdf_path.open("wb") as stream:
            writer.write(stream)
        return tex_path, pdf_path

    monkeypatch.setattr(main.ResumeRenderer, "compile", lambda _self, snapshot, output_dir: fake_compile(snapshot, output_dir))
    response = client.get(f"/base-resumes/{resume['id']}/preview.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.content.startswith(b"%PDF-")
    assert compiled["snapshot"]["sections"][0]["entries"][0]["bullets"][0]["text"] == "Built the current resume preview"
    assert client.get(f"/applications/{application['id']}/revisions").json() == []


def test_base_resume_preview_pages_returns_read_only_pngs_for_every_page(monkeypatch, tmp_path):
    resume = client.post("/base-resumes", json={"name": "Page image preview"}).json()
    application = client.post("/applications", json={
        "company": "Preview Co", "position": "Engineer", "base_resume_id": resume["id"],
        "job_description": "Build useful software.",
    }).json()

    def fake_compile(_snapshot, output_dir):
        tex_path = tmp_path / "pages.tex"
        pdf_path = tmp_path / "pages.pdf"
        tex_path.write_text("preview")
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        with pdf_path.open("wb") as stream:
            writer.write(stream)
        return tex_path, pdf_path

    monkeypatch.setattr(main.ResumeRenderer, "compile", lambda _self, snapshot, output_dir: fake_compile(snapshot, output_dir))
    response = client.get(f"/base-resumes/{resume['id']}/preview-pages")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    data = response.json()
    assert data["page_count"] == 2
    assert len(data["pages"]) == 2
    for index, page in enumerate(data["pages"], start=1):
        assert page["page"] == index
        assert page["width"] >= 1600
        assert page["data_url"].startswith("data:image/png;base64,")
        png = base64.b64decode(page["data_url"].split(",", 1)[1])
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/applications/{application['id']}/revisions").json() == []


def test_base_resume_preview_pdf_can_be_downloaded(monkeypatch, tmp_path):
    resume = client.post("/base-resumes", json={"name": "Download preview"}).json()

    def fake_compile(_snapshot, output_dir):
        tex_path = tmp_path / "download.tex"
        pdf_path = tmp_path / "download.pdf"
        tex_path.write_text("preview")
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with pdf_path.open("wb") as stream:
            writer.write(stream)
        return tex_path, pdf_path

    # The endpoint itself keeps the inline PDF behavior while exposing an
    # attachment disposition for the app's explicit Download PDF action.
    monkeypatch.setattr(main.ResumeRenderer, "compile", lambda _self, snapshot, output_dir: fake_compile(snapshot, output_dir))
    response = client.get(f"/base-resumes/{resume['id']}/preview.pdf?download=true")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="base-resume-{resume["id"]}.pdf"'


def test_base_resume_preview_reports_renderer_failure(monkeypatch):
    resume = client.post("/base-resumes", json={"name": "Unavailable preview"}).json()
    monkeypatch.setattr(main.ResumeRenderer, "compile", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("latexmk is not installed")))

    response = client.get(f"/base-resumes/{resume['id']}/preview.pdf")

    assert response.status_code == 503
    assert "Resume preview could not be generated" in response.json()["detail"]
