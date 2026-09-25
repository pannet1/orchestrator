import sys
from pathlib import Path
import pytest

from agents import runner as rr
from agents.commands import prompt_capture_orchestrator_rule
from agents import commands

def test_audit_and_enrich_tests_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "Feature"
    target.mkdir()
    (target / "spec.md").write_text("# spec\n## Business Logic Constraints\n- Should do X\n")
    (target / "Tests.py").write_text("def test_x() -> None:\n    assert True\n")
    
    # Mock llm to return enriched test code
    enriched = "def test_x() -> None:\n    assert True\n\ndef test_adversarial() -> None:\n    assert False\n"
    monkeypatch.setattr(rr, "_complete", lambda prompt, persona=None, max_attempts=0, task="": f"```python\n{enriched}\n```")
    
    # Run the audit
    ok = rr.audit_and_enrich_tests(target, tester_persona="You are a tester")
    assert ok is True
    
    # Verify file was updated
    assert (target / "Tests.py").read_text() == enriched

def test_audit_and_enrich_tests_returns_false_if_no_test_file(tmp_path: Path) -> None:
    target = tmp_path / "Feature"
    target.mkdir()
    (target / "spec.md").write_text("# spec")
    ok = rr.audit_and_enrich_tests(target)
    assert ok is False

def test_audit_and_enrich_tests_restores_on_standards_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "Feature"
    target.mkdir()
    (target / "spec.md").write_text("# spec")
    orig_code = "def test_x() -> None:\n    assert True\n"
    (target / "Tests.py").write_text(orig_code)
    
    # Provide code with a violation (e.g. comment)
    bad_code = "def test_x() -> None:\n    assert True\n# invalid comment\ndef test_y() -> None:\n    pass\n"
    monkeypatch.setattr(rr, "_complete", lambda prompt, persona=None, max_attempts=0, task="": f"```python\n{bad_code}\n```")
    
    ok = rr.audit_and_enrich_tests(target, tester_persona="You are a tester")
    assert ok is False
    
    # Verify file was restored
    assert (target / "Tests.py").read_text() == orig_code

def test_prompt_capture_orchestrator_rule_appends_to_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_dir = tmp_path / "agents"
    personas_dir = agents_dir / "personas"
    personas_dir.mkdir(parents=True)
    
    backend_agent = personas_dir / "backend_agent.md"
    backend_agent.write_text("# Backend Agent\n")
    
    inputs = iter(["y", "1", "Always validate input"])
    def mock_input(prompt: str) -> str:
        return next(inputs)
    
    ok = commands.record_orchestrator_rule("1", "Always validate input", agents_dir=agents_dir, _input=mock_input)
    assert ok is True
    assert "## Learned Rules & Project Constraints" in backend_agent.read_text()
    assert "Always validate input" in backend_agent.read_text()

def test_prompt_capture_orchestrator_rule_creates_json_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents_dir = tmp_path / "agents"
    rules_dir = agents_dir / "rules"
    rules_dir.mkdir(parents=True)
    
    py_rules = rules_dir / "python.json"
    py_rules.write_text('{"checks": []}')
    
    inputs = iter(["python", "print\\(.*\\)"])
    def mock_input(prompt: str) -> str:
        return next(inputs)
    
    ok = commands.record_orchestrator_rule("3", "Do not use print", agents_dir=agents_dir, _input=mock_input)
    assert ok is True
    
    import json
    data = json.loads(py_rules.read_text())
    assert len(data["checks"]) == 1
    assert data["checks"][0]["pattern"] == "print\\(.*\\)"
    assert data["checks"][0]["message"] == "Do not use print"
