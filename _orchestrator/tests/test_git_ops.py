import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from _orchestrator.git_ops import (
    branch_exists,
    current_branch,
    ensure_branch,
    merge_branch,
    open_branches,
    push_branch,
    read_prompt_file,
    reset_to_main,
    stage_and_commit,
)


class TestReadPromptFile:

    def test_reads_prompt_file(self, tmp_path: Path) -> None:
        prompt = tmp_path / "my_prompt.md"
        prompt.write_text("implement this feature")
        with patch("_orchestrator.git_ops.REPO_ROOT", tmp_path):
            result = read_prompt_file("my_prompt.md")
        assert result == "implement this feature"

    def test_reads_strips_whitespace(self, tmp_path: Path) -> None:
        prompt = tmp_path / "my_prompt.md"
        prompt.write_text("  implement this feature  \n")
        with patch("_orchestrator.git_ops.REPO_ROOT", tmp_path):
            result = read_prompt_file("my_prompt.md")
        assert result == "implement this feature"

    def test_exits_on_missing_file(self, tmp_path: Path) -> None:
        with patch("_orchestrator.git_ops.REPO_ROOT", tmp_path), pytest.raises(SystemExit):
            read_prompt_file("nonexistent.md")


class TestCurrentBranch:

    def test_returns_branch_name(self) -> None:
        branch = current_branch()
        assert isinstance(branch, str)
        assert len(branch) > 0

    def test_returns_unknown_on_error(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("git not found")
            result = current_branch()
        assert result == "(unknown)"


class TestBranchExists:

    def test_main_exists(self) -> None:
        assert branch_exists("main") is True

    def test_nonexistent_returns_false(self) -> None:
        assert branch_exists("nonexistent_branch_xyzzy_123") is False


class TestOpenBranches:

    def test_lists_all_non_main_branches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            return type("R", (), {"returncode": 0, "stdout": "main\nauction/SubmitBid\nmodify/Users\n", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        assert open_branches() == ["auction/SubmitBid", "modify/Users"]

    def test_empty_when_only_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            return type("R", (), {"returncode": 0, "stdout": "main\n", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        assert open_branches() == []

    def test_returns_empty_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            raise subprocess.SubprocessError("git not found")

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        assert open_branches() == []


class TestEnsureBranch:

    def _run(self, monkeypatch: pytest.MonkeyPatch, branch: str, exists: bool) -> list[list[str]]:
        calls: list[list[str]] = []
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: branch)
        monkeypatch.setattr("_orchestrator.git_ops.branch_exists", lambda name: exists)

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        return calls

    def test_creates_branch_from_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, "main", False)
        assert ensure_branch("Payment", "shared") == "shared/Payment"
        assert ["git", "checkout", "-b", "shared/Payment"] in calls

    def test_switches_when_branch_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, "main", True)
        assert ensure_branch("Payment", "shared") == "shared/Payment"
        assert ["git", "checkout", "shared/Payment"] in calls

    def test_bare_feature_without_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, "main", False)
        assert ensure_branch("Payments") == "Payments"
        assert ["git", "checkout", "-b", "Payments"] in calls

    def test_keeps_existing_feature_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._run(monkeypatch, "shared/Payment", False)
        assert ensure_branch("Payment", "shared") == "shared/Payment"
        assert calls == []


class TestStageAndCommit:

    def _fake_run(self, monkeypatch: pytest.MonkeyPatch, results: list[tuple[int, str, str]]) -> list[list[str]]:
        calls: list[list[str]] = []
        it = iter(results)

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            rc, out, err = next(it)
            return type("R", (), {"returncode": rc, "stdout": out, "stderr": err})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        return calls

    def test_stages_and_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_run(monkeypatch, [(0, "", ""), (0, "[main abc1234] feat: Payment\n", "")])
        ok, detail = stage_and_commit(["features/shared/Payment"], "feat: Payment")
        assert ok is True
        assert detail == "[main abc1234] feat: Payment"
        assert calls == [
            ["git", "add", "features/shared/Payment"],
            ["git", "commit", "-m", "feat: Payment"],
        ]

    def test_nothing_to_commit_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_run(monkeypatch, [(0, "", ""), (1, "", "nothing to commit, working tree clean")])
        ok, detail = stage_and_commit(["x"], "feat: x")
        assert ok is True
        assert detail == "nothing to commit"

    def test_add_failure_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._fake_run(monkeypatch, [(1, "", "pathspec is outside repository")])
        ok, detail = stage_and_commit(["x"], "feat: x")
        assert ok is False
        assert "git add failed" in detail


class TestPushBranch:

    def test_push_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        ok, err = push_branch("shared/Payment")
        assert ok is True
        assert err == ""

    def test_push_failure_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            return type("R", (), {"returncode": 128, "stdout": "", "stderr": "remote rejected"})

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        ok, err = push_branch("shared/Payment")
        assert ok is False
        assert err == "remote rejected"


class TestResetToMain:

    def test_resets_and_cleans(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        ok, err = reset_to_main()
        assert ok is True
        assert err == ""
        assert calls == [
            ["git", "fetch", "origin"],
            ["git", "checkout", "main"],
            ["git", "reset", "--hard", "origin/main"],
            ["git", "clean", "-fd"],
        ]

    def test_checkout_failure_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: object) -> object:
            rc = 1 if cmd[:2] == ["git", "checkout"] else 0
            return type("R", (), {"returncode": rc, "stdout": "", "stderr": "checkout failed"})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        ok, err = reset_to_main()
        assert ok is False
        assert "git checkout main failed" in err


class TestMergeBranch:

    def _fake_run(self, monkeypatch: pytest.MonkeyPatch, failures: set[int] | None = None) -> list[list[str]]:
        failures = failures or set()
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            if len(calls) - 1 in failures:
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": f"boom at step {len(calls) - 1}"})()
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        return calls

    def test_runs_full_pipeline_and_deletes_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_run(monkeypatch)
        ok, err = merge_branch("shared/Payment")
        assert ok is True
        assert err == ""
        assert calls == [
            ["git", "push", "origin", "shared/Payment"],
            ["git", "checkout", "main"],
            ["git", "merge", "shared/Payment"],
            ["git", "push", "origin", "main"],
            ["git", "push", "origin", "--delete", "shared/Payment"],
            ["git", "branch", "-D", "shared/Payment"],
        ]

    def test_short_circuits_on_push_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_run(monkeypatch, failures={0})
        ok, err = merge_branch("shared/Payment")
        assert ok is False
        assert "git push failed" in err
        assert len(calls) == 1

    def test_short_circuits_on_merge_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_run(monkeypatch, failures={2})
        ok, err = merge_branch("shared/Payment")
        assert ok is False
        assert "git merge failed" in err
        assert [c[:2] for c in calls] == [["git", "push"], ["git", "checkout"], ["git", "merge"]]

    def test_still_deletes_branch_when_merge_succeeds_after_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._fake_run(monkeypatch, failures={3})
        ok, err = merge_branch("shared/Payment")
        assert ok is False
        assert "git push main failed" in err
        assert calls[-1] == ["git", "push", "origin", "main"]
