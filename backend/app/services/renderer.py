"""Deterministic structured resume rendering and PDF compilation."""
from __future__ import annotations
import os
import re
import shutil, subprocess
from pathlib import Path
from typing import Any
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from .validation import validate_resume_snapshot

def latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(char, char) for char in text)


def _parse_pdfinfo_page_count(output: str) -> int:
    """Extract the page count from pdfinfo output.

    pdfinfo is human-readable rather than a formal machine interface, so
    keep parsing strict and fail with a useful error when its output changes.
    """
    match = re.search(r"(?m)^\s*Pages:\s*(\d+)\s*$", output)
    if not match:
        raise ValueError("pdfinfo output did not contain a Pages field")
    return int(match.group(1))


def count_pdf_pages(pdf_path: str | Path, *, pdfinfo: str = "pdfinfo") -> int:
    """Return a PDF's page count using pdfinfo with a Python fallback.

    The Python fallback is intentionally preferred only after pdfinfo has
    been attempted, preserving compatibility with existing local installs
    while making page counts work in minimal environments and CI. pypdf
    is a runtime dependency so this does not require shelling out to a second
    external PDF utility.
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF artifact does not exist: {path}")

    diagnostics: list[str] = []
    if shutil.which(pdfinfo):
        try:
            result = subprocess.run(
                [pdfinfo, str(path)],
                check=True,
                text=True,
                capture_output=True,
            )
            return _parse_pdfinfo_page_count(result.stdout)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            detail = getattr(exc, "stderr", None) or str(exc)
            diagnostics.append(f"pdfinfo: {detail.strip()}")
    else:
        diagnostics.append(f"pdfinfo executable not found: {pdfinfo}")

    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised in minimal installs
        diagnostics.append(f"pypdf unavailable: {exc}")
    else:
        try:
            return len(PdfReader(str(path)).pages)
        except Exception as exc:  # pypdf exposes several parser exception types
            diagnostics.append(f"pypdf: {exc}")

    raise RuntimeError(
        f"Unable to determine page count for {path}. "
        + " | ".join(diagnostics)
    )

class ResumeRenderer:
    def __init__(self, template_dir: str | Path | None = None):
        directory = Path(template_dir or Path(__file__).parent / "templates")
        self.env = Environment(loader=FileSystemLoader(directory), undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)
        self.env.filters["latex"] = latex_escape

    def render_tex(self, snapshot: dict[str, Any]) -> str:
        validate_resume_snapshot(snapshot)
        return self.env.get_template("resume.tex.j2").render(resume=snapshot)

    def compile(self, snapshot: dict[str, Any], output_dir: str | Path, *, latexmk: str = "latexmk") -> tuple[Path, Path]:
        out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
        tex_path = out / "resume.tex"; pdf_path = out / "resume.pdf"
        tex_path.write_text(self.render_tex(snapshot), encoding="utf-8")
        if shutil.which(latexmk) is None:
            raise RuntimeError("latexmk is not installed")
        # TinyTeX may need to generate bitmap fonts while compiling. Keep its
        # writable cache beside this revision so local builds do not depend on
        # permissions for the user's global TEXMFVAR directory.
        texmf_var = out / "texmf-var"
        texmf_var.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["TEXMFVAR"] = str(texmf_var)
        command = [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-outdir=" + str(out), str(tex_path)]
        result = subprocess.run(command, text=True, capture_output=True, env=environment)
        if result.returncode or not pdf_path.exists():
            status = f"exit code {result.returncode}" if result.returncode else "no PDF artifact was produced"
            raise RuntimeError(
                f"LaTeX compilation failed ({status}).\n"
                f"Command: {' '.join(command)}\n"
                f"stdout:\n{result.stdout[-4000:]}\n"
                f"stderr:\n{result.stderr[-2000:]}"
            )
        return tex_path, pdf_path
