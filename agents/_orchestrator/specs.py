from pathlib import Path

from .config import load_persona
from .llm import llm_complete

MAX_SPEC_QA_ATTEMPTS = 3

SPEC_QA_PERSONA = load_persona("spec_qa")


def _validate_spec(spec: str, original_prompt: str) -> tuple[bool | None, str]:
    """Validate spec against the original prompt, returning (is_valid, corrected_spec).
    is_valid is True if valid, False if issues found and corrected, None if LLM validation unavailable.
    """
    result = llm_complete(
        f"## Original Prompt\n\n{original_prompt}\n\n## Generated Spec\n\n{spec}",
        system=SPEC_QA_PERSONA,
    )
    if result is None:
        return None, spec
    result = result.strip()
    if result.startswith("VALID"):
        return True, spec
    lines = result.split("\n")
    corrected = []
    in_spec = False
    for line in lines:
        if line.startswith("#") and not line.startswith("-") and not in_spec:
            in_spec = True
        if in_spec:
            corrected.append(line)
    if corrected:
        new_spec = "\n".join(corrected).strip()
        return False, new_spec
    return False, spec


def _qa_spec(spec_path: Path, original_prompt: str, label: str) -> None:
    """Run quality assurance loop on a spec file against the original prompt."""
    for attempt in range(1, MAX_SPEC_QA_ATTEMPTS + 1):
        spec = spec_path.read_text()
        is_valid, corrected = _validate_spec(spec, original_prompt)
        if is_valid is None:
            print(f"[Orchestrator] Spec QA skipped — LLM validation unavailable ({label})")
            return
        if is_valid:
            print(f"[Orchestrator] Spec QA passed ({label})")
            return
        print(f"[Orchestrator] Spec QA issue found ({label}), attempt {attempt}/{MAX_SPEC_QA_ATTEMPTS}")
        spec_path.write_text(corrected)
    print(f"[Orchestrator] Spec QA exhausted {MAX_SPEC_QA_ATTEMPTS} attempts — spec may still have issues ({label})")


def rewrite_spec_with_ai(feature_dir: Path, change_prompt: str, section: str) -> bool:
    spec_path = feature_dir / "spec.md"
    existing = spec_path.read_text() if spec_path.exists() else ""
    heading = section.replace(" Request", "").replace(" Resolution", "")

    amendment = (
        f"\n## {heading}\n\n"
        f"{change_prompt}\n\n"
        "### Constraints\n"
        "* <!-- added by modification -->\n"
    )
    if existing:
        spec_path.write_text(existing + amendment)
    else:
        spec_path.write_text(amendment)
    print(f"[Orchestrator] spec.md amended with structured '{heading}' section")

    _qa_spec(spec_path, change_prompt, f"amend:{heading}")

    return True


def amend_spec(feature_dir: Path, heading: str, branch_prefix: str, feature_name: str = "") -> None:
    display = feature_name or feature_dir.name
    print(f"\n{'='*60}\n{heading} for {display}")
    print("Spec amended. Run do when ready:\n")
    print(f"  ./.agents/orch.py do {display}")
    print("=" * 60)
