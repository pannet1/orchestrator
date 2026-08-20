import os
import subprocess
from pathlib import Path

from .config import REPO_ROOT


def resolve_change_prompt(rest: str, prompt_content: str, feature_name: str, prefix: str) -> str | None:
    """Resolve the prompt for a prompt-command from inline text or a prompt file.

    Returns None when no usable prompt is available; the caller reports the error.
    """
    if prompt_content:
        return prompt_content
    if not rest:
        return None
    path = Path(rest)
    if path.suffix == ".md":
        resolved = REPO_ROOT / rest
        if not resolved.exists():
            print(f"[Orchestrator] Prompt file not found: {resolved}")
            return None
        return resolved.read_text().strip()
    return rest.strip()


def resolve_prompt_for_implicit(rest: str, prompt_content: str) -> str | None:
    if prompt_content:
        return prompt_content
    if not rest:
        return None
    p = Path(rest)
    if p.suffix == ".md":
        resolved = REPO_ROOT / rest
        if resolved.exists():
            return resolved.read_text().strip()
    return rest.strip()


def resolve_current_file() -> str | None:
    nvim_addr = os.environ.get("NVIM") or os.environ.get("NVIM_LISTEN_ADDRESS") or ""
    if nvim_addr:
        try:
            result = subprocess.run(
                ["nvim", "--headless", "--server", nvim_addr, "--remote-expr", "expand('%:p')"],
                capture_output=True, text=True, timeout=3,
            check=False)
            if result.returncode == 0:
                path = result.stdout.strip().strip('"')
                if path:
                    return path
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    for var in ("OPENCODE_CURRENT_FILE", "VIM_FILEPATH"):
        val = os.environ.get(var)
        if val:
            p = Path(val)
            if p.is_file():
                return str(p.resolve())
    return None
