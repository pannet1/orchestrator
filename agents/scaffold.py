import os
import sys
from pathlib import Path

from .config import AGENTS_DIR
from .feature import register_target
from .llm import generate_spec_with_ai
from .specs import _qa_spec, parse_expected_files
from .templates import CODE_TEMPLATES, DEFAULT_OVERVIEW, SPEC_TEMPLATE


def format_spec_overview(overview: str) -> str:
    if overview:
        return overview
    return DEFAULT_OVERVIEW


def scaffold_new_feature(target, overview: str = "", no_controller: bool = False) -> Path:
    slice_dir = target.dir
    slice_dir.mkdir(parents=True, exist_ok=True)

    canonical = getattr(target, "canonical_files", None)
    expected_files = list(canonical) if canonical else list(CODE_TEMPLATES.keys())
    if no_controller:
        expected_files = [f for f in expected_files if f != "Controller.py"]

    expected_files_md = "\n".join(f"* `{fname}`" for fname in sorted(expected_files))

    if overview:
        ai_spec = generate_spec_with_ai(target.domain, target.name, overview)
        if ai_spec:
            if not parse_expected_files(ai_spec):
                ai_spec = ai_spec.rstrip() + f"\n\n## Expected Files\n\n{expected_files_md}\n"
            (slice_dir / "spec.md").write_text(ai_spec)
            _qa_spec(slice_dir / "spec.md", overview, f"new:{target.name}")
            current_spec = (slice_dir / "spec.md").read_text()
            if not parse_expected_files(current_spec):
                (slice_dir / "spec.md").write_text(current_spec.rstrip() + f"\n\n## Expected Files\n\n{expected_files_md}\n")
        else:
            overview_text = format_spec_overview(overview)
            spec = SPEC_TEMPLATE.format(
                domain_title=target.domain.title() if target.domain else target.name,
                action=target.name,
                overview=overview_text,
                expected_files=expected_files_md,
            ).rstrip("\n")
            (slice_dir / "spec.md").write_text(spec)
            print("[Orchestrator] LLM unavailable — using template spec.md", file=sys.stderr)
    else:
        spec = SPEC_TEMPLATE.format(
            domain_title=target.domain.title() if target.domain else target.name,
            action=target.name,
            overview=DEFAULT_OVERVIEW,
            expected_files=expected_files_md,
        ).rstrip("\n")
        (slice_dir / "spec.md").write_text(spec)

    # Do not pre-scaffold rigid code files (Schema.py, Handler.py, Controller.py, etc.).
    # Human in the loop can modify the expected files in spec.md before running `do`.
    (slice_dir / "__init__.py").touch()
    register_target(target)

    label = f"{target.domain}/{target.name}"
    note = " (no controller)" if no_controller else ""
    print(f"\nScaffolded new feature spec: {label}{note}")
    print(f"Review {slice_dir / 'spec.md'} to inspect or edit expected files before implementation.\n")
    return slice_dir


def init_new_project(project_dir: Path) -> bool:
    """Create a new project folder with a `.agents` symlink to this repo's `agents/`.

    Projects live inside this single git repository, so there is no
    per-project git repo to manage. `init` only scaffolds the folder and
    links `.agents` -> `agents/` (the tool sources).
    """
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(project_dir)

    dot_link = project_dir / ".agents"
    if dot_link.is_symlink() and not dot_link.exists():
        dot_link.unlink()
    if not dot_link.is_symlink() and not dot_link.exists():
        rel = os.path.relpath(str(AGENTS_DIR), str(project_dir))
        dot_link.symlink_to(rel)
        print(f"  .agents/ -> {rel}  (this repo's agents/)")

    # Pin the Python version for the target project. Constitution rule #1
    # requires .python-version to exist; pin to 3.13 so uv never picks up a
    # newer default (e.g. 3.14).
    py_ver_file = project_dir / ".python-version"
    if not py_ver_file.exists():
        py_ver_file.write_text("3.13\n")
        print("  .python-version -> 3.13")

    # Constitution rule #2 requires a uv-managed pyproject.toml
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(
            f'[project]\nname = "{project_dir.name.lower()}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n\n'
            "[dependency-groups]\ndev = [\n    \"pytest>=8.0.0\",\n]\n\n"
            "[tool.pytest.ini_options]\npythonpath = [\".\"]\n"
        )
        print("  pyproject.toml -> uv-managed (with pytest & pythonpath)")

    # Scaffold minimal shared/logger.py so feature imports succeed out of the box
    shared_dir = project_dir / "shared"
    logger_file = shared_dir / "logger.py"
    if not logger_file.exists():
        shared_dir.mkdir(parents=True, exist_ok=True)
        (shared_dir / "__init__.py").touch()
        logger_file.write_text(
            "import logging\n\n\n"
            "def logging_func(name: str) -> logging.Logger:\n"
            "    return logging.getLogger(name)\n"
        )
        print("  shared/logger.py -> logging_func scaffolded")

    print(f"[Orchestrator] Project ready at {project_dir}")
    return True
