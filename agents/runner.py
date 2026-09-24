"""
Runner Engine — generic sub-agent executor.

Pipes persona + spec.md + existing code to the Zen API,
extracts Python code blocks from the response, writes them to disk,
and runs pytest in an auto-correction loop.

Usage:
    Normally spawned by launcher.py (`orch.py do`). Standalone debugging from agents/:

    PYTHONPATH=. python _orchestrator/runner.py \
        --persona personas/backend_agent.md \
        --target features/your-domain/YourFeature/ \
        --task "Implement the feature per spec.md" \
        --api
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path.cwd()
VERBOSE = False
FEATURE_CANONICAL = {"Schema.py", "Handler.py", "Controller.py", "Tests.py"}

try:
    from agents import rules
    from agents.llm import llm_complete
except ImportError:
    try:
        from . import rules
        from .llm import llm_complete
    except ImportError:
        import rules  # type: ignore[no-redef]
        from llm import llm_complete  # type: ignore[no-redef]


def _complete(prompt: str, persona: str = "", max_attempts: int = 0) -> str | None:
    return llm_complete(prompt, system=persona, max_attempts=max_attempts)


def read_file(path: Path) -> str:
    with open(path) as f:
        return f.read()


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def collect_target_files(target: Path, expected: set[str] | frozenset[str] | list[str] | None = None) -> dict:
    expected_files = expected if expected is not None else FEATURE_CANONICAL
    files = {}
    for fname in ["spec.md", *expected_files]:
        path = target / fname
        if path.exists():
            files[fname] = read_file(path)
    for p in sorted(target.iterdir()):
        if p.is_file() and p.suffix == ".py" and p.name not in files:
            files[p.name] = read_file(p)
    return files


def build_prompt(
    persona: str,
    target: Path,
    target_files: dict,
    task: str,
    error: str,
    expected: set[str] | frozenset[str] | list[str] | None = None,
) -> str:
    req_files = ", ".join(sorted(expected)) if expected else "Schema.py, Handler.py, Controller.py, Tests.py"
    parts: list[str] = []
    parts.append("## Target Directory")
    parts.append(str(target))
    parts.append("")
    parts.append("## How to Work")
    parts.append(
        "Work like a normal interactive coding session, using your tools (read, write, bash). "
        "Do NOT paste file contents into your response — read what you need yourself.\n"
        "- Read spec.md in the target directory first; it is the contract.\n"
        "- Read the existing files in the target directory before editing.\n"
        "- Follow the repo rules in AGENTS.md (read it if needed).\n"
        f"- Write the required files ({req_files}) into the target "
        "directory with the write tool.\n"
        "- Never modify files outside the target directory. Never commit or push.\n"
        "- Run `uv run pytest <target>/Tests.py` to verify before finishing; iterate on failures.\n"
        "- Reply with a short summary of what you changed and the test result."
    )
    if error:
        parts.append("")
        parts.append("## Previous Feedback")
        parts.append(error)
    parts.append("")
    parts.append("## Task")
    parts.append(task)
    return "\n".join(parts)


def build_retry_prompt(target: Path, last_error: str) -> str:
    return (
        f"## Target Directory\n{target}\n\n"
        "## Task\n"
        "Fix the violations below. You may return complete updated files (JSON or markdown) or targeted "
        "SEARCH/REPLACE blocks (<<<<<<< SEARCH ... ======= ... >>>>>>> REPLACE) for only the files "
        "that need changes. Unchanged files already on disk do not need to be resent.\n"
        "Never modify files outside the target directory; never commit or push.\n\n"
        f"## Previous Feedback\n{last_error}"
    )


def call_llm(prompt: str, persona: str = "", max_attempts: int = 0) -> str:
    response = _complete(prompt, persona=persona, max_attempts=max_attempts)
    if response is None:
        print("[Runner] LLM call failed.", file=sys.stderr)
        sys.exit(1)
    return response


def _unescape(text: str) -> str:
    return text.replace("\\n", "\n")


def parse_search_replace_blocks(patch_text: str) -> list[tuple[str, str]]:
    """Parse <<<<<<< SEARCH ... ======= ... >>>>>>> [REPLACE] blocks from patch text."""
    blocks: list[tuple[str, str]] = []
    lines = patch_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if line.startswith("<" * 5) and "SEARCH" in line:
            search_lines: list[str] = []
            replace_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("=" * 5):
                search_lines.append(lines[i])
                i += 1
            if i >= n:
                break
            i += 1  # Skip ======= line
            while i < n and not lines[i].strip().startswith(">" * 5):
                replace_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1  # Skip >>>>>>> line
            blocks.append(("\n".join(search_lines), "\n".join(replace_lines)))
        else:
            i += 1
    return blocks


def apply_search_replace_blocks(original_content: str, patch_text: str) -> tuple[str, bool, list[str]]:
    """Apply SEARCH/REPLACE blocks to original_content.

    Returns:
        (updated_content, was_applied, list_of_errors)
    """
    blocks = parse_search_replace_blocks(patch_text)
    if not blocks:
        return original_content, False, []

    content = original_content
    errors: list[str] = []

    for idx, (search_str, replace_str) in enumerate(blocks, start=1):
        if not search_str:
            if content and not content.endswith("\n"):
                content += "\n"
            content += replace_str
            continue

        if search_str in content:
            if replace_str == "" and search_str + "\n" in content:
                content = content.replace(search_str + "\n", "", 1)
            else:
                content = content.replace(search_str, replace_str, 1)
            continue

        search_lines = [l.rstrip() for l in search_str.splitlines()]
        content_lines = content.splitlines()
        found = False
        k = len(search_lines)
        if k <= len(content_lines):
            for start in range(len(content_lines) - k + 1):
                if [l.rstrip() for l in content_lines[start : start + k]] == search_lines:
                    new_lines = content_lines[:start] + replace_str.splitlines() + content_lines[start + k:]
                    has_trailing_nl = content.endswith("\n")
                    content = "\n".join(new_lines)
                    if has_trailing_nl and not content.endswith("\n"):
                        content += "\n"
                    found = True
                    break

        if found:
            continue

        stripped_search = search_str.strip()
        if stripped_search and stripped_search in content:
            content = content.replace(stripped_search, replace_str.strip(), 1)
            continue

        preview = search_str[:80].replace("\n", "\\n")
        errors.append(f"Block {idx}: search text not found: '{preview}'")

    applied = len(errors) < len(blocks)
    return content, applied, errors


def strip_preamble(text: str) -> str:
    idx = text.find("{")
    if idx < 0:
        idx = text.find("[")
    if idx >= 0 and idx > 0:
        before = text[:idx].strip()
        if before:
            print(f"[Runner] Stripped {len(before)} chars of preamble from response", file=sys.stderr)
        text = text[idx:]
    return text


def extract_code_blocks(text: str) -> dict[str, str]:
    files: dict[str, str] = {}

    text = strip_preamble(text)

    # Primary: try JSON (both bare and fenced)
    files = extract_json_blocks(text)
    if files:
        return {k: _unescape(v) for k, v in files.items()}

    # Fallback: markdown patterns
    # Pattern 1: ### filename\n```python/diff/patch ... ```
    pattern1 = re.compile(
        r'^###\s+(\S+)\s*\n```(?:python|diff|patch)?\n(.*?)```',
        re.MULTILINE | re.DOTALL
    )
    for match in pattern1.finditer(text):
        fname = match.group(1)
        code = match.group(2).strip()
        if fname and code:
            files[fname] = _unescape(code)

    # Pattern 2: ## `path/to/filename` ... ```python/diff/patch ... ```
    pattern2 = re.compile(
        r'^##\s+`[^`]+/(\S+)`\s*\n.*?```(?:python|diff|patch)?\n(.*?)```',
        re.MULTILINE | re.DOTALL
    )
    for match in pattern2.finditer(text):
        fname = match.group(1)
        code = match.group(2).strip()
        if fname and code and fname not in files:
            files[fname] = _unescape(code)

    # Pattern 3: ###/## filename.py followed directly by SEARCH/REPLACE blocks (no markdown fence)
    pattern3 = re.compile(
        r'^#{2,3}\s+(?:`?[^`\n]+/)?(\S+\.py)`?\s*\n\s*(<{5,9}\s*SEARCH.*?>{5,9}(?:[^\r\n]*))',
        re.MULTILINE | re.DOTALL
    )
    for match in pattern3.finditer(text):
        fname = match.group(1)
        code = match.group(2).strip()
        if fname and code and fname not in files:
            files[fname] = _unescape(code)

    # Pattern 4: any ```python/diff/patch ... ``` block preceded by a filename somewhere nearby
    if not files:
        blocks = re.split(r'```(?:python|diff|patch)?\n', text)
        for i in range(1, len(blocks), 2):
            code = blocks[i].strip()
            if code.endswith("```"):
                code = code[:-3].strip()
            if not code:
                continue
            before = blocks[i - 1]
            candidates = re.findall(r'(\w+\.py)', before)
            if candidates:
                files[candidates[-1]] = _unescape(code)

    return files


def write_code_blocks(
    files: dict[str, str],
    target: Path,
    protect: set[str] | None = None,
    allow_auxiliary: bool = True,
    expected: set[str] | None = None,
) -> tuple[list[Path], list[Path]]:
    written: list[Path] = []
    deleted: list[Path] = []
    expected_files = expected if expected is not None else FEATURE_CANONICAL
    produced: set[str] = set()
    protect = protect or set()

    for fname, code in files.items():
        path = target / fname
        blocks = parse_search_replace_blocks(code)
        if blocks:
            if path.exists():
                original = read_file(path)
                patched, applied, errors = apply_search_replace_blocks(original, code)
                if applied and not errors:
                    write_file(path, patched + ("\n" if not patched.endswith("\n") else ""))
                    written.append(path)
                    produced.add(fname)
                    print(f"[Runner] Applied {len(blocks)} patch block(s) to {fname}")
                else:
                    print(f"[Runner] Failed to apply patch blocks to {fname}: {errors}", file=sys.stderr)
            else:
                print(f"[Runner] Cannot apply patch to non-existent file {fname}", file=sys.stderr)
        else:
            write_file(path, code + ("\n" if not code.endswith("\n") else ""))
            written.append(path)
            produced.add(fname)

    if not allow_auxiliary:
        non_canonical = (produced - expected_files) - protect
        for fname in non_canonical:
            path = target / fname
            if path.exists():
                path.unlink()
                deleted.append(path)
                produced.discard(fname)
                print(f"[Runner] Deleted auxiliary file {fname} (--no-auxiliary active)")

    for fname in expected_files:
        if fname not in produced and fname not in protect:
            path = target / fname
            if path.exists():
                path.unlink()
                deleted.append(path)
                print(f"[Runner] Deleted {fname} (absent from AI output)")

    all_on_disk = {p.name for p in target.iterdir() if p.suffix == ".py"}
    unexpected = all_on_disk - produced - {p.name for p in deleted} - protect
    for fname in unexpected:
        path = target / fname
        path.unlink()
        deleted.append(path)
        print(f"[Runner] Deleted unexpected file {fname}")

    return written, deleted


def validate_code_standards(written: list[Path]) -> list[str]:
    """Per-file standards gates, driven by agents/rules/python.json (group 'standards')."""
    violations: list[str] = []
    for p in written:
        if not p.exists() or p.suffix != ".py":
            continue
        for v in rules.check_text(p.read_text(), p.name, groups={"standards"}):
            violations.append(f"{v.path}:{v.line} {v.message}" if v.line else f"{v.path}: {v.message}")
    return violations


def validate_constitution(repo_root: Path, target: Path) -> list[str]:
    """Enforce ALL 11 rules from AGENTS.md Section 3 via script code."""
    issues: list[str] = []

    # 1. Python version file
    py_ver_file = repo_root / ".python-version"
    if not py_ver_file.exists():
        issues.append("Missing .python-version")

    # 2. Package manager: uv only
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text()
        if '[tool.poetry]' in text or '[tool.pdm]' in text:
            issues.append("pyproject.toml uses non-uv tool (poetry/pdm detected)")
    else:
        issues.append("Missing pyproject.toml (uv-managed)")

    # 3. No pip/poetry/conda
    for marker in ("requirements.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "environment.yml", "setup.py", "setup.cfg"):
        if (repo_root / marker).exists():
            issues.append(f"Forbidden package manager file: {marker} (use uv only)")

    # 4. No requirements.txt (duplicate check, explicit)
    if (repo_root / "requirements.txt").exists():
        issues.append("requirements.txt not permitted (use pyproject.toml + uv)")

    # 5. Project time library only — check imports in generated code
    # Detect the project's designated time library from existing code (ignore .venv/caches)
    ignored_parts = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".mypy_cache", ".ruff_cache"}
    existing_files = [f for f in repo_root.rglob("*.py") if not any(p in ignored_parts for p in f.parts)]
    time_libs = {"pendulum", "arrow", "python-dateutil", "delorean", "maya", "udatetime", "pytz"}
    project_time_lib: str = ""
    for f in existing_files:
        if "__pycache__" in f.parts:
            continue
        text = f.read_text()
        for m in re.finditer(r'^import (\w+)|^from (\w+)', text, re.MULTILINE):
            lib = m.group(1) or m.group(2)
            if lib in time_libs:
                project_time_lib = lib
                break
        if project_time_lib:
            break
    if not project_time_lib:
        project_time_lib = "pendulum"  # default fallback
    generated_files = list(target.rglob("*.py")) if target.is_dir() else []
    for f in generated_files:
        if f.name.startswith("__"):
            continue
        text = f.read_text()
        for m in re.finditer(r'^import (\w+)|^from (\w+)', text, re.MULTILINE):
            lib = m.group(1) or m.group(2)
            if lib in time_libs and lib != project_time_lib:
                issues.append(f"{f.name}: use project time library ({project_time_lib}) instead of {lib}")

    # 6. logging.getLogger — checked in validate_code_standards (per-file)
    # 7. Zero comments — checked in validate_code_standards (per-file)
    # 8. No secrets — grep for common secret patterns in generated code
    # Exclude test files (fixture data is legitimate)
    secret_patterns = [
        (r'(?i)(password|secret|token|api_key|api_secret)\s*[=:]\s*["\'][^"\']+["\']', "hardcoded secret"),
        (r'(?i)(access_token|auth_token)\s*=\s*["\'][^"\']{8,}["\']', "hardcoded auth token"),
    ]
    for f in generated_files:
        if f.name.startswith("__") or f.suffix != ".py" or f.name == "Tests.py":
            continue
        text = f.read_text()
        for pat, label in secret_patterns:
            for m in re.finditer(pat, text):
                line_num = text[:m.start()].count("\n") + 1
                issues.append(f"{f.name}:{line_num} potential {label}")

    # 9. No emojis — checked in validate_code_standards (per-line)
    # 10. Unit tests — checked in validate_code_structure (Tests.py)
    # 11. Type annotations — checked in validate_code_standards (return types)

    return issues


def validate_pep8(repo_root: Path, target: Path) -> list[str]:
    issues: list[str] = []
    py_files = list(target.rglob("*.py")) if target.is_dir() else [f for f in [target] if f.suffix == ".py"]
    py_files = [f for f in py_files if not f.name.startswith("__")]
    if not py_files:
        return issues
    cmd = ["uv", "run", "pycodestyle", "--select=E302,E501", "--first"] + [str(f) for f in py_files]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_root), timeout=30, check=False)
    if result.returncode != 0:
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(":", 3)
            if len(parts) >= 4:
                fname = Path(parts[0]).name
                lineno = parts[1]
                msg = parts[3].strip()
                code = msg.split()[0] if msg else ""
                label = "line-too-long" if code == "E501" else "blank-lines"
                issues.append(f"{fname}:{lineno} PEP 8 {label}")
    return issues


def truncated_files(written: list[Path]) -> list[str]:
    truncated: list[str] = []
    for p in written:
        if not p.exists():
            continue
        content = p.read_text().rstrip()
        if not content:
            continue
        last_char = content[-1]
        if last_char in "([{," or content.endswith("Optional["):
            truncated.append(p.name)
            continue
        lines = content.splitlines()
        for line in lines:
            s = line.strip()
            if re.match(r'^\w+\s*=\s*$', s):
                truncated.append(p.name)
                break
    return truncated


def validate_code_structure(code: str, fname: str) -> list[str]:
    """Structural gates (group 'structure') via the shared rules registry."""
    return [v.message for v in rules.check_text(code, fname, groups={"structure"})]


def extract_json_blocks(text: str) -> dict[str, str]:
    """Try JSON parse; fall back to markdown parsing."""
    text = text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
    # Try JSON parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.endswith(".py"):
            result[k] = v
    return result


def run_pytest(test_path: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    feature_dir = str(test_path.parent)
    env["PYTHONPATH"] = feature_dir + os.pathsep + str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    with subprocess.Popen(
        ["uv", "run", "pytest", str(test_path), "--tb=long", "-v"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(REPO_ROOT), env=env,
    ) as proc:
        if proc.stdout is None:
            return False, ""
        output = ""
        for line in proc.stdout:
            print(line, end="", file=sys.stderr)
            output += line
    passed = proc.returncode == 0
    return passed, output


def verify_target_files(
    target: Path,
    repo_root: Path,
    expected: set[str],
) -> tuple[bool, str]:
    """Run static validation and pytest on current files on disk."""
    disk_files = {p.name for p in target.iterdir() if p.is_file() and p.suffix == ".py"}
    missing = expected - disk_files
    if missing:
        return False, f"Missing canonical files: {missing}."

    all_py = [p for p in target.iterdir() if p.is_file() and p.suffix == ".py" and not p.name.startswith("__")]
    bad = truncated_files(all_py)
    if bad:
        return False, f"Files appear truncated: {bad}."

    struct_issues: list[str] = []
    for w in all_py:
        struct_issues.extend(validate_code_structure(w.read_text(), w.name))
    std_violations = validate_code_standards(all_py)
    const_violations = validate_constitution(repo_root, target)
    pep8_violations = validate_pep8(repo_root, target)
    all_violations = struct_issues + std_violations + const_violations + pep8_violations
    if all_violations:
        return False, "Violations:\n  " + "\n  ".join(all_violations)

    test_file = target / "Tests.py"
    if test_file.exists():
        passed, test_output = run_pytest(test_file)
        if not passed:
            failure_summary = test_output[-1500:] if len(test_output) > 1500 else test_output
            return False, f"Pytest verification failed:\n{failure_summary.strip()}"

    return True, ""


def handle_interactive_fallback(
    target: Path,
    last_error: str,
    stdin_fn: Callable[[str], str] | None = None,
) -> tuple[str, str]:
    """Prompt developer for next action when automated attempts exhaust.

    Returns:
        (action, hint)
        action: 'retry', 'verify', or 'quit'
    """
    _input = stdin_fn if stdin_fn is not None else input
    print("\n" + "=" * 60, file=sys.stderr)
    print("[Runner] Automated attempts exhausted.", file=sys.stderr)
    if last_error:
        print(f"[Runner] Last error:\n{last_error.strip()}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Options:", file=sys.stderr)
    print("  [r] Retry with additional developer guidance / hint", file=sys.stderr)
    print("  [v] Re-verify current files on disk (after manual editing)", file=sys.stderr)
    print("  [q] Quit and leave current files in place (default)", file=sys.stderr)
    sys.stderr.flush()
    sys.stdout.flush()

    try:
        choice = _input("Choice [r/v/q]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "quit", ""

    if choice == "r":
        try:
            hint = _input("Enter developer hint / instruction for LLM: ").strip()
        except (EOFError, KeyboardInterrupt):
            hint = ""
        return "retry", hint
    elif choice == "v":
        return "verify", ""
    return "quit", ""


def auto_backend(
    target: Path,
    prompt: str,
    verbose: bool = False,
    persona: str = "",
    spec: str = "",
    max_attempts: int = 0,
    no_controller: bool = False,
    allow_auxiliary: bool = True,
    expected: set[str] | frozenset[str] | list[str] | None = None,
    interactive: bool = True,
    stdin_fn: Callable[[str], str] | None = None,
) -> bool:
    if expected is not None:
        expected_set = set(expected)
    else:
        expected_set = set(FEATURE_CANONICAL)
    if no_controller:
        expected_set.discard("Controller.py")
    expected = expected_set
    pre_existing = {p.name for p in target.iterdir() if p.is_file() and p.suffix == ".py"}
    protected_extra = pre_existing - expected
    known_files = set(pre_existing)
    t_total = time.time()

    last_error: str = ""
    written: list[Path] = []
    attempt = 0
    max_auto_attempts = 3
    while True:
        attempt += 1
        t_attempt = time.time()
        print(f"[Runner] LLM attempt {attempt}...")
        if last_error:
            response = call_llm(build_retry_prompt(target, last_error), persona=persona, max_attempts=max_attempts)
        else:
            response = call_llm(prompt, persona=persona, max_attempts=max_attempts)
        files = extract_code_blocks(response)
        has_failed = False
        if files:
            current_protect = protected_extra | known_files
            new_written, _ = write_code_blocks(
                files,
                target,
                protect=current_protect,
                allow_auxiliary=allow_auxiliary,
                expected=expected,
            )
            written = new_written
            known_files.update(files.keys())
            if not written and any(parse_search_replace_blocks(c) for c in files.values()):
                last_error = "Failed to apply patch blocks to target files. Ensure SEARCH blocks match existing file content exactly or provide complete files."
                print(f"[Runner] {last_error} Retrying...", file=sys.stderr)
                has_failed = True
        else:
            written = [p for p in target.iterdir() if p.suffix == ".py" and p.name in (expected | known_files)]
            files = {p.name: p.read_text() for p in written}
            if not written:
                last_error = "No code blocks found in LLM response and no files written to the target directory."
                print(f"[Runner] {last_error} Retrying...", file=sys.stderr)
                has_failed = True

        if not has_failed:
            for w in written:
                print(f"[Runner] Inspecting {w}")
            bad = truncated_files(written)
            if bad:
                for p in written:
                    if p.name in bad:
                        p.unlink()
                last_error = f"Files appear truncated: {bad}. Regenerate complete code."
                print(f"[Runner] {last_error} Retrying...", file=sys.stderr)
                has_failed = True

        if not has_failed:
            struct_issues: list[str] = []
            for w in written:
                struct_issues.extend(validate_code_structure(w.read_text(), w.name))
            std_violations = validate_code_standards(written)
            const_violations = validate_constitution(REPO_ROOT, target)
            pep8_violations = validate_pep8(REPO_ROOT, target)
            all_violations = struct_issues + std_violations + const_violations + pep8_violations
            t_elapsed = time.time() - t_attempt
            if all_violations:
                last_error = "Violations:\n  " + "\n  ".join(all_violations)
                print(f"[Runner] {last_error}", file=sys.stderr)

            disk_files = {p.name for p in target.iterdir() if p.is_file() and p.suffix == ".py"}
            missing = expected - disk_files
            if missing:
                last_error = f"Missing canonical files: {missing}. Must include ALL {len(expected)} canonical files."
                print(f"[Runner] {last_error} Retrying... ({t_elapsed:.1f}s)", file=sys.stderr)
                has_failed = True
            elif all_violations:
                print(f"[Runner] Retrying after violations ({t_elapsed:.1f}s)...", file=sys.stderr)
                has_failed = True

        if not has_failed:
            test_file = target / "Tests.py"
            if not test_file.exists():
                last_error = ""
                print(f"[Runner] Attempt {attempt} OK (no Tests.py found) ({time.time() - t_attempt:.1f}s)")
                break

            print(f"[Runner] Running tests for {target.name} (attempt {attempt})...")
            passed, test_output = run_pytest(test_file)
            if not passed:
                failure_summary = test_output[-1500:] if len(test_output) > 1500 else test_output
                last_error = f"Pytest verification failed:\n{failure_summary.strip()}"
                print(f"[Runner] Tests failed on attempt {attempt}. Retrying with error feedback...", file=sys.stderr)
                has_failed = True
            else:
                last_error = ""
                print(f"[Runner] Attempt {attempt} OK & All Tests Passed ({time.time() - t_attempt:.1f}s)")
                break

        if attempt >= max_auto_attempts:
            is_interactive = interactive and (sys.stdin.isatty() or stdin_fn is not None)
            if not is_interactive:
                total = time.time() - t_total
                print(f"[Runner] Failed after {attempt} attempts ({total:.1f}s total).", file=sys.stderr)
                return False

            while True:
                action, hint = handle_interactive_fallback(target, last_error, stdin_fn=stdin_fn)
                if action == "retry":
                    last_error = f"{last_error}\n\n## Developer Guidance\n{hint}" if hint else last_error
                    break
                elif action == "verify":
                    print(f"[Runner] Re-verifying {target.name} after manual edits...")
                    v_ok, v_err = verify_target_files(target, REPO_ROOT, expected)
                    if v_ok:
                        print(f"[Runner] Manual verification PASSED ({time.time() - t_total:.1f}s total)")
                        has_failed = False
                        break
                    last_error = v_err
                    print(f"[Runner] {last_error}", file=sys.stderr)
                else:
                    total = time.time() - t_total
                    print(f"[Runner] Aborted by developer after {attempt} attempts ({total:.1f}s total).", file=sys.stderr)
                    return False

            if not has_failed:
                break
            continue

    spec_path = target / "spec.md"
    if spec_path.exists():
        spec_text = spec_path.read_text()
        m = re.search(r"## Modification Request\n(.+?)(?=\n## |\Z)", spec_text, re.DOTALL)
        if m:
            print(f"\n{'='*60}")
            print("EXTRACTION INSTRUCTIONS (from spec.md)")
            print(f"{'='*60}")
            print(m.group(1).strip())
            print(f"{'='*60}\n")
    return True


def run() -> None:
    parser = argparse.ArgumentParser(description="Runner Engine — sub-agent executor")
    parser.add_argument("--persona", required=True, type=Path, help="Path to persona .md file")
    parser.add_argument("--target", required=True, type=Path, help="Path to target feature directory")
    parser.add_argument("--task", default="", help="Task description for the sub-agent")
    parser.add_argument("--error", type=Path, help="Path to error/traceback file (for fix loops)")
    parser.add_argument("--api", action="store_true", help="Auto mode: call opencode, write files, run tests")
    parser.add_argument("--prompt-only", action="store_true", help="Print prompt to stdout only (no API call)")
    parser.add_argument("--max-attempts", type=int, default=0, help="Maximum LLM model-chain attempts; 0 = try every discovered free model (default: 0)")
    parser.add_argument("--no-controller", action="store_true", help="Skip Controller.py requirement")
    parser.add_argument("--no-auxiliary", action="store_true", help="Disallow auxiliary non-canonical .py files")
    parser.add_argument("--no-interactive", action="store_true", help="Disable interactive fallback on attempt exhaustion")
    parser.add_argument("--canonical", help="Comma-separated list of expected canonical files (e.g. Schema.py,Handler.py,Tests.py)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print full prompt and response to stderr")
    args = parser.parse_args()
    if args.verbose:
        global VERBOSE
        VERBOSE = True

    if not args.persona.exists():
        print(f"Error: persona not found: {args.persona}", file=sys.stderr)
        sys.exit(1)
    if not args.target.is_dir():
        print(f"Error: target not found: {args.target}", file=sys.stderr)
        sys.exit(1)

    if args.canonical:
        expected_files: set[str] = {f.strip() for f in args.canonical.split(",") if f.strip()}
    else:
        expected_files = set(FEATURE_CANONICAL)
        try:
            from agents.feature import ProjectFeatures
            proj = ProjectFeatures.load(REPO_ROOT)
            domain = proj.domain_of(args.target) if hasattr(proj, "domain_of") else ""
            expected_files = set(proj.get_canonical_files(domain))
        except Exception:
            pass

    if args.no_controller:
        expected_files.discard("Controller.py")

    persona = read_file(args.persona)
    target_files = collect_target_files(args.target, expected=expected_files)
    task = args.task or f"Work on the feature at {args.target}"
    error = ""
    if args.error and args.error.exists():
        error = read_file(args.error)

    prompt = build_prompt(persona, args.target, target_files, task, error, expected=expected_files)

    if args.prompt_only or not args.api:
        print("=== PERSONA (system message) ===")
        print(persona)
        print("\n=== USER PROMPT ===")
        print(prompt)
        return

    spec = target_files.get("spec.md", "")
    ok = auto_backend(
        args.target,
        prompt,
        verbose=args.verbose,
        persona=persona,
        spec=spec,
        max_attempts=args.max_attempts,
        no_controller=args.no_controller,
        allow_auxiliary=not args.no_auxiliary,
        expected=expected_files,
        interactive=not args.no_interactive,
    )
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    run()
