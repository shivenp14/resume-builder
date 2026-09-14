"""Deterministic structured resume rendering and PDF compilation."""
from __future__ import annotations
import math
import os
import re
import signal
import shutil, subprocess
from pathlib import Path
from typing import Any
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from .validation import validate_resume_snapshot


DEFAULT_LATEX_TIMEOUT_SECONDS = 120.0
LATEX_TERMINATION_GRACE_SECONDS = 5.0
LATEX_DIAGNOSTIC_STDOUT_LIMIT = 4000
LATEX_DIAGNOSTIC_STDERR_LIMIT = 2000

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


def _resolve_latex_timeout(timeout_seconds: float | None) -> float:
    configured = (
        timeout_seconds
        if timeout_seconds is not None
        else os.getenv("LATEX_TIMEOUT_SECONDS", str(DEFAULT_LATEX_TIMEOUT_SECONDS))
    )
    try:
        timeout = float(configured)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("LATEX_TIMEOUT_SECONDS must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError("LATEX_TIMEOUT_SECONDS must be a positive number")
    return timeout


def _text_output(value: Any) -> str:
    """Normalize Popen output, including bytes returned by TimeoutExpired."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _diagnostic_tail(value: Any, limit: int) -> str:
    return _text_output(value)[-limit:]


def _compile_diagnostics(
    command: list[str],
    status: str,
    stdout: Any,
    stderr: Any,
) -> str:
    return (
        f"LaTeX compilation failed ({status}).\n"
        f"Command: {' '.join(command)}\n"
        f"stdout:\n{_diagnostic_tail(stdout, LATEX_DIAGNOSTIC_STDOUT_LIMIT)}\n"
        f"stderr:\n{_diagnostic_tail(stderr, LATEX_DIAGNOSTIC_STDERR_LIMIT)}"
    )


def _cleanup_latex_artifacts(output_dir: Path, tex_path: Path) -> None:
    """Remove files owned by this compilation after an unsuccessful run."""
    # latexmk creates several sidecars (aux, fdb_latexmk, fls, log, synctex.gz,
    # etc.). They all share the input stem, so remove the complete generated
    # set rather than leaving a partial artifact behind after a failed run.
    for artifact in output_dir.glob(f"{tex_path.stem}.*"):
        try:
            if artifact.is_file() or artifact.is_symlink():
                artifact.unlink()
        except OSError:
            # The primary failure is more useful to the caller than a best-
            # effort cleanup error; the generation caller also removes its
            # staging directory as a final safety net.
            continue


def _terminate_process_group(process: subprocess.Popen[str], *, force: bool = False) -> None:
    """Signal a compiler process group without consuming its pipe output."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
        return

    # Windows does not provide killpg. CREATE_NEW_PROCESS_GROUP is used when
    # starting the process; this still bounds the direct compiler process.
    if force:
        process.kill()
    else:
        process.terminate()


def _popen_process_group_kwargs() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": creation_flags} if creation_flags else {}

class ResumeRenderer:
    def __init__(self, template_dir: str | Path | None = None):
        directory = Path(template_dir or Path(__file__).parent / "templates")
        self.env = Environment(loader=FileSystemLoader(directory), undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)
        self.env.filters["latex"] = latex_escape

    def render_tex(self, snapshot: dict[str, Any]) -> str:
        validate_resume_snapshot(snapshot)
        return self.env.get_template("resume.tex.j2").render(resume=snapshot)

    def compile(
        self,
        snapshot: dict[str, Any],
        output_dir: str | Path,
        *,
        latexmk: str = "latexmk",
        timeout_seconds: float | None = None,
    ) -> tuple[Path, Path]:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        tex_path = out / "resume.tex"
        pdf_path = out / "resume.pdf"
        timeout = _resolve_latex_timeout(timeout_seconds)
        if shutil.which(latexmk) is None:
            _cleanup_latex_artifacts(out, tex_path)
            raise RuntimeError("latexmk is not installed")
        # Clear stale output from a previous attempt before writing the new
        # source. This prevents an old PDF from being mistaken for a success.
        _cleanup_latex_artifacts(out, tex_path)
        tex_path.write_text(self.render_tex(snapshot), encoding="utf-8")
        # TinyTeX may need to generate bitmap fonts while compiling. Keep its
        # writable cache beside this revision so local builds do not depend on
        # permissions for the user's global TEXMFVAR directory.
        texmf_var = out / "texmf-var"
        texmf_var.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["TEXMFVAR"] = str(texmf_var)
        command = [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-outdir=" + str(out), str(tex_path)]
        try:
            process = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                **_popen_process_group_kwargs(),
            )
        except OSError as exc:
            _cleanup_latex_artifacts(out, tex_path)
            raise RuntimeError(f"Unable to start LaTeX compiler: {exc}") from exc

        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # communicate() may have buffered output before timing out. Kill
            # the whole process group before collecting the final diagnostics.
            _terminate_process_group(process)
            try:
                final_stdout, final_stderr = process.communicate(
                    timeout=LATEX_TERMINATION_GRACE_SECONDS
                )
            except subprocess.TimeoutExpired as cleanup_exc:
                _terminate_process_group(process, force=True)
                try:
                    final_stdout, final_stderr = process.communicate(
                        timeout=LATEX_TERMINATION_GRACE_SECONDS
                    )
                except subprocess.TimeoutExpired as force_exc:
                    final_stdout = force_exc.stdout
                    final_stderr = force_exc.stderr
            # ``communicate`` is permitted to return only the output collected
            # after the timeout.  Preserve both buffers so an early compiler
            # diagnostic is never lost during process-group cleanup.
            stdout = _text_output(exc.stdout) + _text_output(final_stdout)
            stderr = _text_output(exc.stderr) + _text_output(final_stderr)
            _cleanup_latex_artifacts(out, tex_path)
            raise RuntimeError(
                _compile_diagnostics(
                    command,
                    f"timed out after {timeout:g} seconds",
                    stdout,
                    stderr,
                )
            ) from exc

        if process.returncode or not pdf_path.exists():
            status = (
                f"exit code {process.returncode}"
                if process.returncode
                else "no PDF artifact was produced"
            )
            _cleanup_latex_artifacts(out, tex_path)
            raise RuntimeError(_compile_diagnostics(command, status, stdout, stderr))
        return tex_path, pdf_path
