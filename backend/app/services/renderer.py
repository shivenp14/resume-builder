"""Deterministic structured resume rendering and PDF compilation."""
from __future__ import annotations
import shutil, subprocess
from pathlib import Path
from typing import Any
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from .validation import validate_resume_snapshot

def latex_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(char, char) for char in text)

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
        result = subprocess.run([latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-outdir=" + str(out), str(tex_path)], text=True, capture_output=True)
        if result.returncode or not pdf_path.exists():
            raise RuntimeError("LaTeX compilation failed:\n" + result.stdout[-4000:] + result.stderr[-2000:])
        return tex_path, pdf_path
