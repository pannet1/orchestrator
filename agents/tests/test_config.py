from pathlib import Path
import pytest

from agents.config import (
    AGENTS_DIR,
    FEATURES_CONFIG,
    MODEL_CONFIG,
    PERSONAS_DIR,
    REPO_ROOT,
    RUNNER,
    load_persona,
)


class TestConfig:
    def test_paths_defined(self) -> None:
        assert isinstance(REPO_ROOT, Path)
        assert isinstance(AGENTS_DIR, Path)
        assert AGENTS_DIR.name == "agents"
        assert FEATURES_CONFIG == REPO_ROOT / ".features.json"
        assert RUNNER == AGENTS_DIR / "runner.py"
        assert PERSONAS_DIR == AGENTS_DIR / "personas"
        assert MODEL_CONFIG == AGENTS_DIR / "model_config.json"

    def test_load_persona_existing(self) -> None:
        content = load_persona("backend")
        assert len(content) > 0
        assert "backend" in content.lower() or "python" in content.lower()

    def test_load_persona_missing(self) -> None:
        content = load_persona("non_existent_persona_xyz")
        assert content == ""
