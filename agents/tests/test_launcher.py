import subprocess
from pathlib import Path
from typing import Any
import pytest

import agents.launcher as launcher


class TestLauncher:
    def test_run_runner_missing_persona(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ok = launcher.run_runner("non_existent_key", tmp_path, "do work")
        assert ok is False
        assert "Persona not found" in capsys.readouterr().err

    def test_run_runner_launches_subprocess_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "feat"
        target.mkdir()
        error_file = tmp_path / "err.txt"
        error_file.write_text("error details")

        executed_cmd: list[str] = []
        executed_env: dict[str, str] = {}

        def fake_run(cmd: list[str], env: dict[str, str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal executed_cmd, executed_env
            executed_cmd = cmd
            executed_env = env
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)

        ok = launcher.run_runner(
            "backend",
            target,
            "task desc",
            error_path=error_file,
            max_attempts=3,
            no_controller=True,
            canonical_files={"Schema.py", "Worker.py", "Tests.py"},
        )

        assert ok is True
        assert "--persona" in executed_cmd
        assert "--target" in executed_cmd
        assert str(target) in executed_cmd
        assert "--task" in executed_cmd
        assert "task desc" in executed_cmd
        assert "--api" in executed_cmd
        assert "--max-attempts" in executed_cmd
        assert "3" in executed_cmd
        assert "--no-controller" in executed_cmd
        assert "--canonical" in executed_cmd
        assert "Schema.py,Tests.py,Worker.py" in executed_cmd
        assert "--error" in executed_cmd
        assert str(error_file) in executed_cmd
        assert executed_env["PYTHONUNBUFFERED"] == "1"
        assert "PYTHONPATH" in executed_env

    def test_run_runner_launches_subprocess_pipe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "feat"
        target.mkdir()

        class FakePopen:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.returncode = 0
                self.stdout = iter(["line 1\n", "line 2\n"])

            def __enter__(self) -> "FakePopen":
                return self

            def __exit__(self, *args: Any) -> None:
                pass

        monkeypatch.setattr(subprocess, "Popen", FakePopen)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        ok = launcher.run_runner("backend", target, "task")
        assert ok is True
