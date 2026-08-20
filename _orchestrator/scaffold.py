import os
import sys
from pathlib import Path

from .config import AGENTS_DIR
from .feature import register_target
from .llm import generate_spec_with_ai
from .specs import _qa_spec
from .templates import CODE_TEMPLATES, DEFAULT_OVERVIEW, SPEC_TEMPLATE


def format_spec_overview(overview: str) -> str:
    if overview:
        return overview
    return DEFAULT_OVERVIEW


def scaffold_new_feature(target, overview: str = "", no_controller: bool = False) -> Path:
    slice_dir = target.dir
    slice_dir.mkdir(parents=True, exist_ok=True)

    if overview:
        ai_spec = generate_spec_with_ai(target.domain, target.name, overview)
        if ai_spec:
            (slice_dir / "spec.md").write_text(ai_spec)
            _qa_spec(slice_dir / "spec.md", overview, f"new:{target.name}")
        else:
            overview_text = format_spec_overview(overview)
            spec = SPEC_TEMPLATE.format(
                domain_title=target.domain.title() if target.domain else target.name,
                action=target.name,
                overview=overview_text,
            ).rstrip("\n")
            (slice_dir / "spec.md").write_text(spec)
            print("[Orchestrator] LLM unavailable — using template spec.md", file=sys.stderr)
    else:
        spec = SPEC_TEMPLATE.format(
            domain_title=target.domain.title() if target.domain else target.name,
            action=target.name,
            overview=DEFAULT_OVERVIEW,
        ).rstrip("\n")
        (slice_dir / "spec.md").write_text(spec)

    for fname, template in CODE_TEMPLATES.items():
        if no_controller and fname == "Controller.py":
            continue
        content = template.format(action=target.name).lstrip("\n")
        (slice_dir / fname).write_text(content)

    (slice_dir / "__init__.py").touch()
    register_target(target)

    label = f"{target.domain}/{target.name}"
    note = " (no controller)" if no_controller else ""
    print(f"\nScaffolded new feature: {label}{note}\n")
    return slice_dir


def init_new_project(project_dir: Path) -> bool:
    """Create a new project folder with a .agents symlink, then cd into it."""
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(project_dir)

    dot_link = project_dir / ".agents"
    if not dot_link.is_symlink() and not dot_link.exists():
        rel = os.path.relpath(str(AGENTS_DIR), str(project_dir))
        dot_link.symlink_to(rel)
        print(f"  .agents/ -> {rel}")
    print(f"[Orchestrator] Project ready at {project_dir}")
    return True
