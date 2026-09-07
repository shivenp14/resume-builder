from pathlib import Path

from pypdf import PdfWriter

from backend.app.services import renderer


def test_activity_entries_render_summary_and_bullets():
    latex = renderer.ResumeRenderer().render_tex(
        {
            "contact": {},
            "sections": [
                {
                    "key": "activities",
                    "title": "Activities",
                    "entries": [
                        {
                            "title": "Computer Science Club",
                            "summary": "Member and workshop organizer",
                            "bullets": [{"text": "Led weekly coding sessions"}],
                        },
                        {
                            "title": "Robotics Club",
                            "summary": "",
                            "bullets": [{"text": "Built an autonomous rover"}],
                        },
                    ],
                }
            ],
        }
    )

    assert r"\section{ Activities }" in latex
    assert r"\textbf{ Computer Science Club }: Member and workshop organizer" in latex
    assert r"\item\small{ Led weekly coding sessions }" in latex
    assert r"\item\small{ Robotics Club }" in latex
    assert r"\item\small{ Built an autonomous rover }" in latex
    assert "Computer Science Club" in latex
    assert "Robotics Club" in latex


def test_contact_separators_have_no_leading_or_trailing_pipe():
    render = renderer.ResumeRenderer().render_tex

    linkedin_only = render(
        {
            "contact": {"linkedin": "https://linkedin.com/in/example"},
            "sections": [],
        }
    )
    assert r"$|$ \href" not in linkedin_only
    assert r"\href{ https://linkedin.com/in/example }" in linkedin_only

    location_and_email = render(
        {
            "contact": {"location": "Hoboken, NJ", "email": "a@example.com"},
            "sections": [],
        }
    )
    assert "Hoboken, NJ $|$" in location_and_email
    assert "a@example.com" in location_and_email


def test_count_pdf_pages_uses_python_fallback_when_pdfinfo_is_missing(
    tmp_path: Path, monkeypatch
):
    pdf = tmp_path / "resume.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    with pdf.open("wb") as stream:
        writer.write(stream)

    monkeypatch.setattr(renderer.shutil, "which", lambda _name: None)
    assert renderer.count_pdf_pages(pdf) == 2


def test_count_pdf_pages_reports_missing_or_invalid_artifact(tmp_path: Path):
    missing = tmp_path / "missing.pdf"
    try:
        renderer.count_pdf_pages(missing)
    except FileNotFoundError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("missing PDF should fail loudly")
