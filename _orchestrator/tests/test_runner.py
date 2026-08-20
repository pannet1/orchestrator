"""Tests for _orchestrator/runner.py — file pruning protection and prompt context."""

from pathlib import Path

import pytest

import _orchestrator.runner as rr

# ── collect_target_files ───────────────────────────────────────────

class TestCollectTargetFiles:
    def test_collects_spec_and_canonical(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "spec.md").write_text("# spec\n")
        (target / "Schema.py").write_text("from pydantic import BaseModel\n")
        (target / "Handler.py").write_text("class H:\n    pass\n")
        (target / "Controller.py").write_text("from fastapi import APIRouter\n")
        (target / "Tests.py").write_text("def test_x() -> None:\n    pass\n")
        files = rr.collect_target_files(target)
        assert set(files) == {"spec.md", "Schema.py", "Handler.py", "Controller.py", "Tests.py"}

    def test_collects_extra_py_files(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "spec.md").write_text("# spec\n")
        (target / "Schema.py").write_text("from pydantic import BaseModel\n")
        (target / "bios_cleaner.py").write_text("def classify() -> None:\n    pass\n")
        (target / "test_Feature.py").write_text("def test_x() -> None:\n    pass\n")
        files = rr.collect_target_files(target)
        assert "bios_cleaner.py" in files
        assert "test_Feature.py" in files

    def test_ignores_non_py_extra(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "spec.md").write_text("# spec\n")
        (target / "Schema.py").write_text("from pydantic import BaseModel\n")
        (target / "notes.txt").write_text("not code\n")
        files = rr.collect_target_files(target)
        assert "notes.txt" not in files


# ── write_code_blocks pruning protection ──────────────────────────

class TestWriteCodeBlocksProtection:
    def _full_output(self) -> dict[str, str]:
        return {
            "Schema.py": "from pydantic import BaseModel\n\nclass S(BaseModel):\n    pass\n",
            "Handler.py": "class H:\n    pass\n",
            "Controller.py": "from fastapi import APIRouter\n\nrouter = APIRouter()\n",
            "Tests.py": "def test_x() -> None:\n    assert True\n",
        }

    def test_preserves_protected_noncanonical(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "bios_cleaner.py").write_text("def classify() -> None:\n    pass\n")
        (target / "test_Feature.py").write_text("def test_x() -> None:\n    pass\n")
        for name in rr.FEATURE_CANONICAL:
            (target / name).write_text("# old\n")

        written, deleted = rr.write_code_blocks(
            self._full_output(),
            target,
            protect={"bios_cleaner.py", "test_Feature.py"},
        )

        assert (target / "bios_cleaner.py").exists()
        assert (target / "test_Feature.py").exists()
        expected = {p.name for p in written}
        assert expected >= rr.FEATURE_CANONICAL
        assert not any(p.name in {"bios_cleaner.py", "test_Feature.py"} for p in deleted)

    def test_deletes_unprotected_unexpected(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "stray.py").write_text("x = 1\n")
        for name in rr.FEATURE_CANONICAL:
            (target / name).write_text("# old\n")

        _, deleted = rr.write_code_blocks(self._full_output(), target, protect=set())

        assert (target / "stray.py").exists() is False
        assert any(p.name == "stray.py" for p in deleted)

    def test_protected_absent_from_output_survives_theory(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "bios_cleaner.py").write_text("def classify() -> None:\n    pass\n")
        for name in rr.FEATURE_CANONICAL:
            (target / name).write_text("# old\n")

        # LLM output omits bios_cleaner.py entirely
        _, deleted = rr.write_code_blocks(
            self._full_output(),
            target,
            protect={"bios_cleaner.py"},
        )

        assert (target / "bios_cleaner.py").exists()
        assert not any(p.name == "bios_cleaner.py" for p in deleted)


# ── build_prompt context ───────────────────────────────────────────

class TestBuildPrompt:
    def test_includes_tool_driven_work_instructions(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "Schema.py").write_text("from pydantic import BaseModel\n")
        (target / "bios_cleaner.py").write_text("def classify() -> None:\n    pass\n")
        (target / "spec.md").write_text("# spec\n")
        target_files = rr.collect_target_files(target)
        prompt = rr.build_prompt("persona", target, target_files, "task", "")
        assert "## Target Directory" in prompt
        assert str(target) in prompt
        assert "## How to Work" in prompt
        assert "read, write, bash" in prompt
        assert "spec.md in the target directory" in prompt
        assert "## Task" in prompt
        assert "task" in prompt
        assert "## Existing:" not in prompt


# ── validate_code_structure ───────────────────────────────────────

class TestValidateCodeStructure:
    def test_function_based_handler_allowed(self) -> None:
        code = (
            "from shared.logger import logging_func\n\n"
            "logger = logging_func(__name__)\n\n\n"
            "def compute(x: int) -> int:\n"
            "    return x + 1\n"
        )
        issues = rr.validate_code_structure(code, "Handler.py")
        assert issues == []

    def test_handler_requires_logger(self) -> None:
        code = "def compute(x: int) -> int:\n    return x + 1\n"
        issues = rr.validate_code_structure(code, "Handler.py")
        assert "Handler.py must have a module-level logger" in issues

    def test_class_based_handler_still_allowed(self) -> None:
        code = (
            "from shared.logger import logging_func\n\n"
            "logger = logging_func(__name__)\n\n\n"
            "class H:\n"
            "    def run(self) -> str:\n"
            "        return \"ok\"\n"
        )
        issues = rr.validate_code_structure(code, "Handler.py")
        assert issues == []


# ── auto_backend end-to-end ────────────────────────────────────────

class TestAutoBackendProtection:
    def _llm_output(self) -> str:
        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        controller = "from fastapi import APIRouter\\n\\nfrom shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\nrouter = APIRouter()\\n"
        tests = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert True\\n"
        return (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Controller.py": "' + controller + '", '
            '"Tests.py": "' + tests + '"}'
        )

    def test_extra_files_survive_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "bios_cleaner.py").write_text("def classify() -> None:\n    pass\n")
        (target / "test_Feature.py").write_text("def test_x() -> None:\n    assert True\n")
        for name in rr.FEATURE_CANONICAL:
            (target / name).write_text("# old\n")

        monkeypatch.setattr(rr, "call_llm", lambda prompt, persona="": self._llm_output())
        monkeypatch.setattr(rr, "run_pytest", lambda test_path: (True, ""))
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        ok = rr.auto_backend(target, "prompt", persona="p")

        assert ok is True
        assert (target / "bios_cleaner.py").exists()
        assert (target / "test_Feature.py").exists()
        for name in rr.FEATURE_CANONICAL:
            assert (target / name).exists()