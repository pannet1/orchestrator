from pathlib import Path

REPO_ROOT = Path.cwd()
AGENTS_DIR = Path(__file__).resolve().parent.parent

FEATURES_CONFIG = REPO_ROOT / ".features.json"
RUNNER = Path(__file__).resolve().parent / "runner.py"
PERSONAS_DIR = AGENTS_DIR / "personas"
MODEL_CONFIG = AGENTS_DIR / "model_config.json"


def load_persona(name: str) -> str:
    """Read `<PERSONAS_DIR>/<name>_agent.md`, or ``""`` when missing."""
    path = PERSONAS_DIR / f"{name}_agent.md"
    return path.read_text() if path.exists() else ""
