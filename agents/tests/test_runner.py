"""Tests for agents/runner.py — file pruning protection and prompt context."""

from pathlib import Path

import pytest

import agents.runner as rr

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

        monkeypatch.setattr(rr, "call_llm", lambda prompt, persona="", **kwargs: self._llm_output())
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

    def test_pytest_failure_retries_and_recovers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        for name in rr.FEATURE_CANONICAL:
            (target / name).write_text("# old\n")

        llm_prompts: list[str] = []

        def fake_call_llm(prompt: str, persona: str = "", **kwargs: object) -> str:
            llm_prompts.append(prompt)
            return self._llm_output()

        call_count = 0

        def fake_run_pytest(test_path: Path) -> tuple[bool, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return False, "FAILED Tests.py::test_x - AssertionError"
            return True, "PASSED Tests.py::test_x"

        monkeypatch.setattr(rr, "call_llm", fake_call_llm)
        monkeypatch.setattr(rr, "run_pytest", fake_run_pytest)
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        ok = rr.auto_backend(target, "prompt", persona="p")

        assert ok is True
        assert call_count == 2
        assert len(llm_prompts) == 2
        assert "Pytest verification failed" in llm_prompts[1]
        assert "AssertionError" in llm_prompts[1]

    def test_auto_backend_no_controller_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        tests = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert True\\n"
        no_ctrl_output = (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Tests.py": "' + tests + '"}'
        )

        monkeypatch.setattr(rr, "call_llm", lambda prompt, persona="", **kwargs: no_ctrl_output)
        monkeypatch.setattr(rr, "run_pytest", lambda test_path: (True, ""))
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        ok = rr.auto_backend(target, "prompt", persona="p", no_controller=True)

        assert ok is True
        assert (target / "Schema.py").exists()
        assert (target / "Handler.py").exists()
        assert (target / "Tests.py").exists()
        assert not (target / "Controller.py").exists()


# ── Search/Replace Patching ────────────────────────────────────────

class TestSearchReplacePatching:
    def test_parse_search_replace_single_block(self) -> None:
        patch = (
            "<<<<<<< SEARCH\n"
            "def foo() -> int:\n"
            "    return 1\n"
            "=======\n"
            "def foo() -> int:\n"
            "    return 2\n"
            ">>>>>>> REPLACE\n"
        )
        blocks = rr.parse_search_replace_blocks(patch)
        assert len(blocks) == 1
        assert blocks[0][0] == "def foo() -> int:\n    return 1"
        assert blocks[0][1] == "def foo() -> int:\n    return 2"

    def test_parse_search_replace_without_replace_word(self) -> None:
        patch = (
            "<<<<<<< SEARCH\n"
            "x = 1\n"
            "=======\n"
            "x = 2\n"
            ">>>>>>>\n"
        )
        blocks = rr.parse_search_replace_blocks(patch)
        assert len(blocks) == 1
        assert blocks[0] == ("x = 1", "x = 2")

    def test_parse_search_replace_multiple_blocks(self) -> None:
        patch = (
            "Some text before\n"
            "<<<<<<< SEARCH\n"
            "a = 1\n"
            "=======\n"
            "a = 10\n"
            ">>>>>>> REPLACE\n"
            "Some text between\n"
            "<<<<<<< SEARCH\n"
            "b = 2\n"
            "=======\n"
            "b = 20\n"
            ">>>>>>> REPLACE\n"
        )
        blocks = rr.parse_search_replace_blocks(patch)
        assert len(blocks) == 2
        assert blocks[0] == ("a = 1", "a = 10")
        assert blocks[1] == ("b = 2", "b = 20")

    def test_apply_search_replace_exact(self) -> None:
        original = (
            "from shared.logger import logging_func\n"
            "logger = logging_func(__name__)\n\n"
            "def compute() -> int:\n"
            "    return 1\n"
        )
        patch = (
            "<<<<<<< SEARCH\n"
            "def compute() -> int:\n"
            "    return 1\n"
            "=======\n"
            "def compute() -> int:\n"
            "    return 42\n"
            ">>>>>>> REPLACE"
        )
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is True
        assert errors == []
        assert "return 42" in updated
        assert "return 1" not in updated

    def test_apply_search_replace_multiple_sequential(self) -> None:
        original = "x = 1\ny = 2\nz = 3\n"
        patch = (
            "<<<<<<< SEARCH\nx = 1\n=======\nx = 10\n>>>>>>> REPLACE\n"
            "<<<<<<< SEARCH\nz = 3\n=======\nz = 30\n>>>>>>> REPLACE\n"
        )
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is True
        assert errors == []
        assert updated == "x = 10\ny = 2\nz = 30\n"

    def test_apply_search_replace_whitespace_tolerance(self) -> None:
        original = "def run() -> None:   \n    val = 1  \n"
        patch = (
            "<<<<<<< SEARCH\n"
            "def run() -> None:\n"
            "    val = 1\n"
            "=======\n"
            "def run() -> None:\n"
            "    val = 2\n"
            ">>>>>>> REPLACE"
        )
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is True
        assert errors == []
        assert "val = 2" in updated

    def test_apply_search_replace_deletion(self) -> None:
        original = "a = 1\nb = 2\nc = 3\n"
        patch = "<<<<<<< SEARCH\nb = 2\n=======\n>>>>>>> REPLACE"
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is True
        assert errors == []
        assert updated == "a = 1\nc = 3\n"

    def test_apply_search_replace_insertion_at_end(self) -> None:
        original = "a = 1\n"
        patch = "<<<<<<< SEARCH\n=======\nb = 2\n>>>>>>> REPLACE"
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is True
        assert errors == []
        assert updated == "a = 1\nb = 2"

    def test_apply_search_replace_unmatched_block(self) -> None:
        original = "a = 1\n"
        patch = "<<<<<<< SEARCH\nnot_here = 99\n=======\nx = 2\n>>>>>>> REPLACE"
        updated, applied, errors = rr.apply_search_replace_blocks(original, patch)
        assert applied is False
        assert len(errors) == 1
        assert "search text not found" in errors[0]
        assert updated == original

    def test_extract_code_blocks_with_diff_and_unfenced_patch(self) -> None:
        fenced = (
            "### Handler.py\n"
            "```diff\n"
            "<<<<<<< SEARCH\n"
            "return 1\n"
            "=======\n"
            "return 2\n"
            ">>>>>>> REPLACE\n"
            "```\n"
        )
        files = rr.extract_code_blocks(fenced)
        assert "Handler.py" in files
        assert "return 2" in files["Handler.py"]

        unfenced = (
            "### Handler.py\n"
            "<<<<<<< SEARCH\n"
            "return 1\n"
            "=======\n"
            "return 2\n"
            ">>>>>>> REPLACE\n"
        )
        files2 = rr.extract_code_blocks(unfenced)
        assert "Handler.py" in files2
        assert "return 2" in files2["Handler.py"]


# ── Incremental Retry & Auxiliary Files ────────────────────────────

class TestIncrementalRetryAndAuxiliary:
    def test_write_code_blocks_applies_patch(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "Handler.py").write_text("class H:\n    val = 1\n")
        (target / "Schema.py").write_text("class S:\n    pass\n")
        (target / "Controller.py").write_text("router = 1\n")
        (target / "Tests.py").write_text("def test_x() -> None:\n    assert True\n")

        patch_output = {
            "Handler.py": "<<<<<<< SEARCH\n    val = 1\n=======\n    val = 2\n>>>>>>> REPLACE",
        }
        written, deleted = rr.write_code_blocks(
            patch_output,
            target,
            protect={"Schema.py", "Controller.py", "Tests.py"},
        )
        assert (target / "Handler.py").read_text() == "class H:\n    val = 2\n"
        assert len(written) == 1
        assert written[0].name == "Handler.py"
        assert deleted == []

    def test_write_code_blocks_retains_file_on_patch_failure(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        orig = "class H:\n    val = 1\n"
        (target / "Handler.py").write_text(orig)

        bad_patch = {
            "Handler.py": "<<<<<<< SEARCH\n    non_existent = 99\n=======\n    val = 2\n>>>>>>> REPLACE",
        }
        written, _ = rr.write_code_blocks(
            bad_patch,
            target,
            protect={"Handler.py"},
        )
        assert (target / "Handler.py").read_text() == orig
        assert written == []

    def test_write_code_blocks_preserves_auxiliary_file(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        files = {
            "Schema.py": "class S:\n    pass\n",
            "Handler.py": "class H:\n    pass\n",
            "Controller.py": "router = 1\n",
            "Tests.py": "def test_x() -> None:\n    assert True\n",
            "models.py": "class User:\n    id: int\n",
        }
        written, deleted = rr.write_code_blocks(files, target, allow_auxiliary=True)
        assert (target / "models.py").exists()
        assert (target / "models.py").read_text() == "class User:\n    id: int\n"
        assert not any(p.name == "models.py" for p in deleted)

    def test_write_code_blocks_removes_auxiliary_when_disallowed(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        files = {
            "Schema.py": "class S:\n    pass\n",
            "Handler.py": "class H:\n    pass\n",
            "Controller.py": "router = 1\n",
            "Tests.py": "def test_x() -> None:\n    assert True\n",
            "models.py": "class User:\n    id: int\n",
        }
        written, deleted = rr.write_code_blocks(files, target, allow_auxiliary=False)
        assert not (target / "models.py").exists()
        assert any(p.name == "models.py" for p in deleted)

    def test_auto_backend_single_file_retry_patch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        controller = "from fastapi import APIRouter\\n\\nfrom shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\nrouter = APIRouter()\\n"
        tests_bad = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert False\\n"

        attempt1_output = (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Controller.py": "' + controller + '", '
            '"Tests.py": "' + tests_bad + '"}'
        )
        attempt2_output = (
            '{"Tests.py": "<<<<<<< SEARCH\\n    assert False\\n=======\\n    assert True\\n>>>>>>> REPLACE"}'
        )

        attempts = [attempt1_output, attempt2_output]
        call_index = 0

        def fake_call_llm(prompt: str, persona: str = "", **kwargs: object) -> str:
            nonlocal call_index
            resp = attempts[call_index]
            call_index += 1
            return resp

        test_call_count = 0

        def fake_run_pytest(test_path: Path) -> tuple[bool, str]:
            nonlocal test_call_count
            test_call_count += 1
            content = test_path.read_text()
            if "assert False" in content:
                return False, "FAILED Tests.py::test_x - AssertionError: assert False"
            return True, "PASSED Tests.py::test_x"

        monkeypatch.setattr(rr, "call_llm", fake_call_llm)
        monkeypatch.setattr(rr, "run_pytest", fake_run_pytest)
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        ok = rr.auto_backend(target, "prompt", persona="p")

        assert ok is True
        assert call_index == 2
        assert test_call_count == 2
        for name in rr.FEATURE_CANONICAL:
            assert (target / name).exists()
        assert "assert True" in (target / "Tests.py").read_text()

    def test_auto_backend_preserves_auxiliary_file_across_retries(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        controller = "from fastapi import APIRouter\\n\\nfrom shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\nrouter = APIRouter()\\n"
        tests_bad = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert False\\n"
        models = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass User:\\n    id: int\\n"

        attempt1_output = (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Controller.py": "' + controller + '", '
            '"Tests.py": "' + tests_bad + '", '
            '"models.py": "' + models + '"}'
        )
        attempt2_output = (
            '{"Tests.py": "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert True\\n"}'
        )

        attempts = [attempt1_output, attempt2_output]
        call_index = 0

        def fake_call_llm(prompt: str, persona: str = "", **kwargs: object) -> str:
            nonlocal call_index
            resp = attempts[call_index]
            call_index += 1
            return resp

        test_call_count = 0

        def fake_run_pytest(test_path: Path) -> tuple[bool, str]:
            nonlocal test_call_count
            test_call_count += 1
            if test_call_count == 1:
                return False, "FAILED"
            return True, "PASSED"

        monkeypatch.setattr(rr, "call_llm", fake_call_llm)
        monkeypatch.setattr(rr, "run_pytest", fake_run_pytest)
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        ok = rr.auto_backend(target, "prompt", persona="p")

        assert ok is True
        assert (target / "models.py").exists()

    def test_auto_backend_with_custom_canonical_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        worker = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass W:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        tests = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert True\\n"

        output = (
            '{"Schema.py": "' + schema + '", '
            '"Worker.py": "' + worker + '", '
            '"Tests.py": "' + tests + '"}'
        )

        monkeypatch.setattr(rr, "call_llm", lambda prompt, persona="", **kwargs: output)
        monkeypatch.setattr(rr, "run_pytest", lambda test_path: (True, ""))
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)
        (tmp_path / ".python-version").write_text("3.13\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        custom_manifest = {"Schema.py", "Worker.py", "Tests.py"}
        ok = rr.auto_backend(target, "prompt", persona="p", expected=custom_manifest)

        assert ok is True
        assert (target / "Schema.py").exists()
        assert (target / "Worker.py").exists()
        assert (target / "Tests.py").exists()
        assert not (target / "Controller.py").exists()
        assert not (target / "Handler.py").exists()

    def test_build_prompt_with_custom_canonical(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        target_files = {"spec.md": "# Spec"}
        prompt = rr.build_prompt("persona", target, target_files, "task", "", expected={"Schema.py", "Worker.py", "Tests.py"})
        assert "Write the required files (Schema.py, Tests.py, Worker.py)" in prompt


# ── Interactive Sub-Agent Fallback & Verification ───────────────────

class TestInteractiveFallback:
    def test_handle_interactive_fallback_retry_with_hint(self, tmp_path: Path) -> None:
        inputs = iter(["r", "use pendulum for timestamps"])
        action, hint = rr.handle_interactive_fallback(tmp_path, "Pytest failed", stdin_fn=lambda _: next(inputs))
        assert action == "retry"
        assert hint == "use pendulum for timestamps"

    def test_handle_interactive_fallback_retry_without_hint(self, tmp_path: Path) -> None:
        inputs = iter(["r", ""])
        action, hint = rr.handle_interactive_fallback(tmp_path, "Error", stdin_fn=lambda _: next(inputs))
        assert action == "retry"
        assert hint == ""

    def test_handle_interactive_fallback_verify(self, tmp_path: Path) -> None:
        inputs = iter(["v"])
        action, hint = rr.handle_interactive_fallback(tmp_path, "Error", stdin_fn=lambda _: next(inputs))
        assert action == "verify"
        assert hint == ""

    def test_handle_interactive_fallback_quit(self, tmp_path: Path) -> None:
        inputs = iter(["q"])
        action, hint = rr.handle_interactive_fallback(tmp_path, "Error", stdin_fn=lambda _: next(inputs))
        assert action == "quit"
        assert hint == ""

    def test_handle_interactive_fallback_eof(self, tmp_path: Path) -> None:
        def raise_eof(_: str) -> str:
            raise EOFError
        action, hint = rr.handle_interactive_fallback(tmp_path, "Error", stdin_fn=raise_eof)
        assert action == "quit"
        assert hint == ""


class TestVerifyTargetFiles:
    def test_verify_target_files_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        (target / "Schema.py").write_text("from pydantic import BaseModel\n\n\nclass S(BaseModel):\n    pass\n")
        (target / "Handler.py").write_text("from shared.logger import logging_func\n\nlogger = logging_func(__name__)\n\n\nclass H:\n    def run(self) -> str:\n        return 'ok'\n")
        (target / "Controller.py").write_text("from fastapi import APIRouter\nfrom shared.logger import logging_func\n\nlogger = logging_func(__name__)\nrouter = APIRouter()\n")
        (target / "Tests.py").write_text("from shared.logger import logging_func\n\nlogger = logging_func(__name__)\n\n\ndef test_pass() -> None:\n    assert True\n")

        monkeypatch.setattr(rr, "run_pytest", lambda path: (True, "PASSED"))
        expected = {"Schema.py", "Handler.py", "Controller.py", "Tests.py"}
        ok, err = rr.verify_target_files(target, tmp_path, expected)
        assert ok is True
        assert err == ""

    def test_verify_target_files_missing_canonical(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "Schema.py").write_text("class S:\n    pass\n")
        expected = {"Schema.py", "Handler.py"}
        ok, err = rr.verify_target_files(target, tmp_path, expected)
        assert ok is False
        assert "Missing canonical files" in err

    def test_verify_target_files_constitution_violation(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        # Comment in code violates constitution rule 6
        (target / "Schema.py").write_text("# A comment\nclass S:\n    pass\n")
        expected = {"Schema.py"}
        ok, err = rr.verify_target_files(target, tmp_path, expected)
        assert ok is False
        assert "Violations:" in err

    def test_verify_target_files_pytest_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        (target / "Schema.py").write_text("from pydantic import BaseModel\n\n\nclass S(BaseModel):\n    pass\n")
        (target / "Tests.py").write_text("from shared.logger import logging_func\n\nlogger = logging_func(__name__)\n\n\ndef test_fail() -> None:\n    assert False\n")

        monkeypatch.setattr(rr, "run_pytest", lambda path: (False, "FAILED Tests.py::test_fail"))
        expected = {"Schema.py", "Tests.py"}
        ok, err = rr.verify_target_files(target, tmp_path, expected)
        assert ok is False
        assert "Pytest verification failed" in err


class TestAutoBackendInteractiveExhaustion:
    def test_auto_backend_non_interactive_aborts_on_exhaustion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        monkeypatch.setattr(rr, "call_llm", lambda *a, **k: '{"Schema.py": "class S:\\n    pass\\n"}')
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)

        ok = rr.auto_backend(target, "prompt", interactive=False)
        assert ok is False

    def test_auto_backend_interactive_quit_aborts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        monkeypatch.setattr(rr, "call_llm", lambda *a, **k: '{"Schema.py": "class S:\\n    pass\\n"}')
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)

        inputs = iter(["q"])
        ok = rr.auto_backend(target, "prompt", interactive=True, stdin_fn=lambda _: next(inputs))
        assert ok is False

    def test_auto_backend_interactive_retry_succeeds_on_attempt_4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        controller = "from fastapi import APIRouter\\n\\nfrom shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\nrouter = APIRouter()\\n"
        tests_fail = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert False\\n"
        tests_pass = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert True\\n"

        failing_output = (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Controller.py": "' + controller + '", '
            '"Tests.py": "' + tests_fail + '"}'
        )
        passing_output = (
            '{"Tests.py": "' + tests_pass + '"}'
        )

        call_count = 0
        received_prompts: list[str] = []

        def fake_call_llm(prompt: str, persona: str = "", **kwargs: object) -> str:
            nonlocal call_count
            call_count += 1
            received_prompts.append(prompt)
            if call_count <= 3:
                return failing_output
            return passing_output

        def fake_run_pytest(test_path: Path) -> tuple[bool, str]:
            if "assert False" in test_path.read_text():
                return False, "FAILED Tests.py::test_x"
            return True, "PASSED"

        monkeypatch.setattr(rr, "call_llm", fake_call_llm)
        monkeypatch.setattr(rr, "run_pytest", fake_run_pytest)
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)

        inputs = iter(["r", "make the assertion True"])
        ok = rr.auto_backend(target, "prompt", interactive=True, stdin_fn=lambda _: next(inputs))

        assert ok is True
        assert call_count == 4
        assert "Developer Guidance" in received_prompts[-1]
        assert "make the assertion True" in received_prompts[-1]

    def test_auto_backend_interactive_verify_reprompt_and_succeed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (tmp_path / ".python-version").write_text("3.12\n")
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\nversion = '0.1.0'\n")

        schema = "from pydantic import BaseModel\\n\\n\\nclass S(BaseModel):\\n    pass\\n"
        handler = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\nclass H:\\n    def run(self) -> str:\\n        return \\\"ok\\\"\\n"
        controller = "from fastapi import APIRouter\\n\\nfrom shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\nrouter = APIRouter()\\n"
        tests_fail = "from shared.logger import logging_func\\n\\nlogger = logging_func(__name__)\\n\\n\\ndef test_x() -> None:\\n    assert False\\n"

        failing_output = (
            '{"Schema.py": "' + schema + '", '
            '"Handler.py": "' + handler + '", '
            '"Controller.py": "' + controller + '", '
            '"Tests.py": "' + tests_fail + '"}'
        )

        monkeypatch.setattr(rr, "call_llm", lambda *a, **k: failing_output)
        monkeypatch.setattr(rr, "REPO_ROOT", tmp_path)

        def fake_run_pytest(test_path: Path) -> tuple[bool, str]:
            if "assert False" in test_path.read_text():
                return False, "FAILED Tests.py::test_x"
            return True, "PASSED"

        monkeypatch.setattr(rr, "run_pytest", fake_run_pytest)

        first_attempt = True

        def interactive_input(_: str) -> str:
            nonlocal first_attempt
            if first_attempt:
                first_attempt = False
                return "v"
            (target / "Tests.py").write_text(
                "from shared.logger import logging_func\n\nlogger = logging_func(__name__)\n\n\ndef test_x() -> None:\n    assert True\n"
            )
            return "v"

        ok = rr.auto_backend(target, "prompt", interactive=True, stdin_fn=interactive_input)
        assert ok is True
        assert "assert True" in (target / "Tests.py").read_text()