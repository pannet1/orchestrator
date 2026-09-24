from pathlib import Path
from typing import Any
import pytest

import agents.specs as specs


class TestSpecs:
    def test_validate_spec_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(specs, "llm_complete", lambda prompt, system=None: "VALID\nEverything matches")
        is_valid, spec = specs._validate_spec("# Spec\n\nContent", "original prompt")
        assert is_valid is True
        assert spec == "# Spec\n\nContent"

    def test_validate_spec_llm_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(specs, "llm_complete", lambda prompt, system=None: None)
        is_valid, spec = specs._validate_spec("# Spec\n\nContent", "original prompt")
        assert is_valid is None
        assert spec == "# Spec\n\nContent"

    def test_validate_spec_issues_corrected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        correction = "ISSUES\n- Missing requirement\n# Corrected Spec\n## Requirements\n- Must pass"
        monkeypatch.setattr(specs, "llm_complete", lambda prompt, system=None: correction)
        is_valid, spec = specs._validate_spec("# Bad Spec", "original prompt")
        assert is_valid is False
        assert "Corrected Spec" in spec
        assert "ISSUES" not in spec

    def test_qa_spec_passes_first_attempt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\nContent")
        monkeypatch.setattr(specs, "_validate_spec", lambda s, p: (True, s))

        specs._qa_spec(spec_file, "prompt", "test:Spec")
        assert "Spec QA passed (test:Spec)" in capsys.readouterr().out

    def test_qa_spec_skipped_when_llm_unavailable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec\nContent")
        monkeypatch.setattr(specs, "_validate_spec", lambda s, p: (None, s))

        specs._qa_spec(spec_file, "prompt", "test:Spec")
        assert "Spec QA skipped — LLM validation unavailable" in capsys.readouterr().out

    def test_qa_spec_retries_and_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        spec_file = tmp_path / "spec.md"
        spec_file.write_text("# Spec 1")

        attempts = [(False, "# Spec 2"), (True, "# Spec 2")]
        idx = 0

        def fake_validate(s: str, p: str) -> tuple[bool, str]:
            nonlocal idx
            res = attempts[idx]
            idx += 1
            return res

        monkeypatch.setattr(specs, "_validate_spec", fake_validate)
        specs._qa_spec(spec_file, "prompt", "test:Spec")

        out = capsys.readouterr().out
        assert "Spec QA issue found (test:Spec), attempt 1/3" in out
        assert "Spec QA passed (test:Spec)" in out
        assert spec_file.read_text() == "# Spec 2"

    def test_rewrite_spec_with_ai(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        feat_dir = tmp_path / "Feature"
        feat_dir.mkdir()
        spec_file = feat_dir / "spec.md"
        spec_file.write_text("# Initial Spec\n\nContent")

        qa_called = False

        def fake_qa_spec(path: Path, prompt: str, label: str) -> None:
            nonlocal qa_called
            qa_called = True

        monkeypatch.setattr(specs, "_qa_spec", fake_qa_spec)

        ok = specs.rewrite_spec_with_ai(feat_dir, "change requirement", "Modification Request")
        assert ok is True
        assert qa_called is True
        content = spec_file.read_text()
        assert "# Initial Spec" in content
        assert "## Modification" in content
        assert "change requirement" in content

    def test_amend_spec_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        feat_dir = tmp_path / "Feature"
        feat_dir.mkdir()
        specs.amend_spec(feat_dir, "CONTRACT AMENDMENT", "modify", "Payment")
        out = capsys.readouterr().out
        assert "CONTRACT AMENDMENT for Payment" in out
        assert "./.agents/orch.py do Payment" in out

    def test_parse_expected_files_standard_list(self) -> None:
        spec_text = (
            "# Feature\n\n"
            "## Expected Files\n"
            "* `Schema.py`\n"
            "* `Handler.py`\n"
            "* `Controller.py`\n"
            "* `Tests.py`\n"
        )
        files = specs.parse_expected_files(spec_text)
        assert files == ["Schema.py", "Handler.py", "Controller.py", "Tests.py"]

    def test_parse_expected_files_custom_auth_manifest(self) -> None:
        spec_text = (
            "# Login Feature\n\n"
            "## Expected Files\n"
            "* `Schema.py`\n"
            "* `Security.py`\n"
            "* `Service.py`\n"
            "* `Controller.py`\n"
            "* `Tests.py`\n"
        )
        files = specs.parse_expected_files(spec_text)
        assert files == ["Schema.py", "Security.py", "Service.py", "Controller.py", "Tests.py"]

    def test_parse_expected_files_with_descriptions_and_headings(self) -> None:
        spec_text = (
            "# Worker Feature\n\n"
            "## Module Architecture\n"
            "- `Schema.py`: Pydantic models\n"
            "- `Worker.py`: Background job runner\n"
            "- `Tests.py`: Unit tests\n"
        )
        files = specs.parse_expected_files(spec_text)
        assert files == ["Schema.py", "Worker.py", "Tests.py"]

    def test_parse_expected_files_ensures_tests_py(self) -> None:
        spec_text = (
            "# Feature\n\n"
            "## Expected Files\n"
            "- `Schema.py`\n"
            "- `Service.py`\n"
        )
        files = specs.parse_expected_files(spec_text)
        assert files == ["Schema.py", "Service.py", "Tests.py"]

    def test_parse_expected_files_empty_when_missing_section(self) -> None:
        spec_text = "# Feature\n\n## Overview\nSome overview."
        files = specs.parse_expected_files(spec_text)
        assert files == []


