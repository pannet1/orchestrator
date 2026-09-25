import os
import subprocess
import sys
from pathlib import Path

from .config import AGENTS_DIR, PERSONAS_DIR, RUNNER


def run_runner(
    persona_key: str,
    target: Path,
    task: str,
    error_path: Path | None = None,
    max_attempts: int = 0,
    no_controller: bool = False,
    no_tester: bool = False,
    canonical_files: set[str] | frozenset[str] | list[str] | tuple[str, ...] | None = None,
    stack: str = "python",
    test_command: str = "",
) -> bool:
    persona_path = PERSONAS_DIR / f"{persona_key}_agent.md"
    if persona_key == "backend":
        if stack == "typescript" and (PERSONAS_DIR / "backend_ts_agent.md").exists():
            persona_path = PERSONAS_DIR / "backend_ts_agent.md"
        elif stack == "javascript" and (PERSONAS_DIR / "backend_js_agent.md").exists():
            persona_path = PERSONAS_DIR / "backend_js_agent.md"

    if not persona_path.exists():
        print(f"[Orchestrator] Persona not found: {persona_path}", file=sys.stderr)
        return False

    cmd = [
        sys.executable, str(RUNNER),
        "--persona", str(persona_path),
        "--target", str(target),
        "--task", task,
        "--api",
        "--max-attempts", str(max_attempts),
        "--stack", stack,
    ]
    if test_command:
        cmd += ["--test-command", test_command]
    if no_controller:
        cmd.append("--no-controller")
    if no_tester:
        cmd.append("--no-tester")
    if canonical_files:
        cmd += ["--canonical", ",".join(sorted(canonical_files))]
    if error_path:
        cmd += ["--error", str(error_path)]

    env = dict(os.environ)
    env["PYTHONPATH"] = str(AGENTS_DIR.parent) + os.pathsep + str(AGENTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"

    if sys.stdin.isatty():
        result = subprocess.run(cmd, env=env)
        return result.returncode == 0

    with subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
        if proc.stdout is None:
            return False
        for line in proc.stdout:
            print(line, end="", flush=True)
    return proc.returncode == 0
