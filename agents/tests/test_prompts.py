import subprocess
from pathlib import Path
from typing import Any
import pytest

import agents.prompts as prompts


class TestPrompts:
    def test_resolve_change_prompt_from_content(self) -> None:
        result = prompts.resolve_change_prompt("ignored", "my explicit prompt", "Feature", "new")
        assert result == "my explicit prompt"

    def test_resolve_change_prompt_empty(self) -> None:
        result = prompts.resolve_change_prompt("", "", "Feature", "new")
        assert result is None

    def test_resolve_change_prompt_inline_text(self) -> None:
        result = prompts.resolve_change_prompt("implement user signup", "", "Feature", "new")
        assert result == "implement user signup"

    def test_resolve_change_prompt_from_existing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prompts, "REPO_ROOT", tmp_path)
        prompt_file = tmp_path / "spec_prompt.md"
        prompt_file.write_text("detailed spec prompt")

        result = prompts.resolve_change_prompt("spec_prompt.md", "", "Feature", "new")
        assert result == "detailed spec prompt"

    def test_resolve_change_prompt_from_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(prompts, "REPO_ROOT", tmp_path)
        result = prompts.resolve_change_prompt("missing.md", "", "Feature", "new")
        assert result is None
        assert "Prompt file not found" in capsys.readouterr().out

    def test_resolve_prompt_for_implicit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(prompts, "REPO_ROOT", tmp_path)
        prompt_file = tmp_path / "implicit.md"
        prompt_file.write_text("implicit prompt content")

        assert prompts.resolve_prompt_for_implicit("", "content") == "content"
        assert prompts.resolve_prompt_for_implicit("", "") is None
        assert prompts.resolve_prompt_for_implicit("inline text", "") == "inline text"
        assert prompts.resolve_prompt_for_implicit("implicit.md", "") == "implicit prompt content"

    def test_resolve_current_file_from_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        dummy_file = tmp_path / "current.py"
        dummy_file.write_text("x = 1\n")

        monkeypatch.delenv("NVIM", raising=False)
        monkeypatch.delenv("NVIM_LISTEN_ADDRESS", raising=False)
        monkeypatch.setenv("OPENCODE_CURRENT_FILE", str(dummy_file))

        assert prompts.resolve_current_file() == str(dummy_file.resolve())

    def test_resolve_current_file_from_nvim(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        dummy_file = tmp_path / "nvim_file.py"
        dummy_file.write_text("x = 1\n")

        monkeypatch.setenv("NVIM", "/tmp/nvim_sock")

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f'"{dummy_file}"\n')

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert prompts.resolve_current_file() == str(dummy_file)

    def test_resolve_current_file_fallback_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NVIM", raising=False)
        monkeypatch.delenv("NVIM_LISTEN_ADDRESS", raising=False)
        monkeypatch.delenv("OPENCODE_CURRENT_FILE", raising=False)
        monkeypatch.delenv("VIM_FILEPATH", raising=False)

        assert prompts.resolve_current_file() is None
