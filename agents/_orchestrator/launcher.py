import os
import subprocess
import sys
from pathlib import Path

from .config import AGENTS_DIR, PERSONAS_DIR, RUNNER


def run_runner(persona_key: str, target: Path, task: str, error_path: Path | None = None, max_attempts: int = 4) -> bool:
    persona_path = PERSONAS_DIR / f"{persona_key}_agent.md"
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
    ]
    if error_path:
        cmd += ["--error", str(error_path)]

    # runner.py is a package member now; make the package importable for the subprocess.
    env = dict(os.environ)
    env["PYTHONPATH"] = str(AGENTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    with subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1) as proc:
        if proc.stdout is None:
            return False
        for line in proc.stdout:
            print(line, end="", flush=True)
    return proc.returncode == 0
