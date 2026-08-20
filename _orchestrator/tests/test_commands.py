import json
from pathlib import Path

import pytest

from _orchestrator.commands import _KNOWN_PREFIXES, _cmd_do, _cmd_qa, _parse_request, dispatch
from _orchestrator.feature import ProjectFeatures, feature_from_branch
from _orchestrator.git_ops import check_branch
from _orchestrator.scaffold import scaffold_new_feature


class FakeProject:
    """Stands in for ProjectFeatures when a test wants resolution to fail."""

    def resolve(self, raw: str, app: str = "") -> object:
        return None

    def app_for_domain(self, domain: str) -> str:
        return ""


class TestParseRequest:

    def test_bare_action_domain_feature(self) -> None:
        assert _parse_request("modify shared/Payment") == ("modify", "shared", "Payment", "")

    def test_bare_action_domain_feature_with_prompt(self) -> None:
        assert _parse_request("modify shared/Payment make it seamless") == (
            "modify", "shared", "Payment", "make it seamless")

    def test_new_accepts_domain_feature_and_prompt(self) -> None:
        assert _parse_request("new shared/TestFeature test prompt") == (
            "new", "shared", "TestFeature", "test prompt")

    def test_new_bare_feature_with_prompt(self) -> None:
        assert _parse_request("new Payments auction payment flow") == (
            "new", "", "Payments", "auction payment flow")

    def test_do_domain_feature(self) -> None:
        assert _parse_request("do shared/Payment") == ("do", "shared", "Payment", "")

    def test_delete_bare_feature(self) -> None:
        assert _parse_request("delete Payment") == ("delete", "", "Payment", "")

    def test_move_two_tokens(self) -> None:
        assert _parse_request("move OldName NewName") == ("move", "", "OldName", "NewName")

    def test_move_across_domains(self) -> None:
        assert _parse_request("move shared/Payment vps/Payments") == ("move", "shared", "Payment", "vps/Payments")

    def test_bare_merge(self) -> None:
        assert _parse_request("merge") == ("merge", "", "", "")

    def test_merge_with_target(self) -> None:
        assert _parse_request("merge shared/Payment") == ("merge", "shared", "Payment", "")

    def test_scan(self) -> None:
        assert _parse_request("scan") == ("scan", "", "", "")

    def test_qa(self) -> None:
        assert _parse_request("qa") == ("qa", "", "", "")

    def test_bare_init(self) -> None:
        assert _parse_request("init") == ("init", "", "", "")

    def test_init_with_target(self) -> None:
        assert _parse_request("init Payments") == ("init", "", "Payments", "")

    def test_legacy_slash_verb_not_parsed(self) -> None:
        assert _parse_request("modify/shared/Payment") == ("modify/shared/payment", "", "", "")

    def test_legacy_domain_slash_action_not_parsed(self) -> None:
        assert _parse_request("vps/modify/Subscription") == ("vps/modify/subscription", "", "", "")

    def test_empty_request(self) -> None:
        assert _parse_request("") == ("", "", "", "")


class TestFeatureFromBranch:

    def test_feature_prefix(self) -> None:
        assert feature_from_branch("feature/Payment") == "Payment"

    def test_modify_prefix(self) -> None:
        assert feature_from_branch("modify/Payment") == "Payment"

    def test_domain_slash_feature_branch(self) -> None:
        assert feature_from_branch("shared/Payment") == "Payment"

    def test_main_returns_empty(self) -> None:
        assert feature_from_branch("main") == ""

    def test_plain_branch_returns_branch_name(self) -> None:
        assert feature_from_branch("dev") == "dev"


class TestDoDeleteInferFromBranch:

    def test_do_without_target_on_feature_branch(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "feature/Payment")
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        dispatch("do")
        assert "Feature not found: Payment" in capsys.readouterr().out

    def test_do_without_target_on_main(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "main")
        dispatch("do")
        assert "cannot infer from current branch" in capsys.readouterr().out

    def test_delete_without_target_on_modify_branch(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "shared/Payment")
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        monkeypatch.setattr("_orchestrator.commands.branch_exists", lambda name: False)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        dispatch("delete")
        out = capsys.readouterr().out
        assert "Nothing to delete: feature 'Payment' not found." in out
        assert not any("stash" in c for c in calls)


class TestMoveHandler:

    def _patch_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, known: dict[str, str]) -> None:
        (tmp_path / "features" / "shared" / "Payment").mkdir(parents=True)
        (tmp_path / "features" / "shared" / "Payment" / "spec.md").touch()
        cfg = tmp_path / ".features.json"
        cfg.write_text(json.dumps({"known_features": known}))
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: ProjectFeatures.load(tmp_path))

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.commands.subprocess.run", fake_run)
        # handle the pytest run (uv) in commands and all git calls in git_ops
        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "main")

    def test_move_within_domain(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env(tmp_path, monkeypatch, {"Payment": "shared"})
        dispatch("move Payment Payments")
        out = capsys.readouterr().out
        assert "Moving Payment -> Payments" in out
        assert (tmp_path / "features" / "shared" / "Payments" / "spec.md").exists()
        assert not (tmp_path / "features" / "shared" / "Payment").exists()
        data = json.loads((tmp_path / ".features.json").read_text())
        assert data["known_features"] == {"Payments": "shared"}

    def test_move_across_domains(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_env(tmp_path, monkeypatch, {"Payment": "shared"})
        dispatch("move Payment vps/Payments")
        out = capsys.readouterr().out
        assert "Moving Payment -> Payments" in out
        assert (tmp_path / "features" / "vps" / "Payments" / "spec.md").exists()
        assert not (tmp_path / "features" / "shared" / "Payment").exists()
        data = json.loads((tmp_path / ".features.json").read_text())
        assert data["known_features"] == {"Payments": "vps"}

    def test_move_missing_target_usage(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        dispatch("move Payment")
        assert "Usage: move <OldDomain/OldFeature> <NewDomain/NewFeature>" in capsys.readouterr().out


class TestScaffoldBareFeature:

    def test_bare_feature_lands_in_nodomain(self, tmp_path: Path) -> None:
        target = ProjectFeatures.load(tmp_path).target_for_new("Payments", "")
        result = scaffold_new_feature(target, "")
        assert result == tmp_path / "features" / "nodomain" / "Payments"
        assert (tmp_path / "features" / "nodomain" / "Payments" / "spec.md").exists()
        assert not (tmp_path / "features" / "Payments").exists()


class TestDomainOf:

    def test_domain_from_feature_dir(self, tmp_path: Path) -> None:
        project = ProjectFeatures.load(tmp_path)
        assert project.domain_of(tmp_path / "features" / "shared" / "Payment") == "shared"

    def test_root_level_feature_assumes_nodomain(self, tmp_path: Path) -> None:
        project = ProjectFeatures.load(tmp_path)
        assert project.domain_of(tmp_path / "features" / "Payment") == "nodomain"

    def test_none_returns_empty(self, tmp_path: Path) -> None:
        project = ProjectFeatures.load(tmp_path)
        assert project.domain_of(None) == ""


class TestCheckBranchNaming:

    def _fake_git(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        return calls

    def test_branch_uses_domain_slash_feature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "main")
        monkeypatch.setattr("_orchestrator.git_ops.open_branches", list)
        monkeypatch.setattr("_orchestrator.git_ops.branch_exists", lambda name: False)
        calls = self._fake_git(monkeypatch)
        check_branch("Payment", "shared")
        assert ["git", "checkout", "-b", "shared/Payment"] in calls

    def test_branch_without_domain_is_bare_feature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "main")
        monkeypatch.setattr("_orchestrator.git_ops.open_branches", list)
        monkeypatch.setattr("_orchestrator.git_ops.branch_exists", lambda name: False)
        calls = self._fake_git(monkeypatch)
        check_branch("Payments", "")
        assert ["git", "checkout", "-b", "Payments"] in calls

    def test_branch_for_bare_feature_uses_nodomain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "main")
        monkeypatch.setattr("_orchestrator.git_ops.open_branches", list)
        monkeypatch.setattr("_orchestrator.git_ops.branch_exists", lambda name: False)
        calls = self._fake_git(monkeypatch)
        check_branch("Payments", "nodomain")
        assert ["git", "checkout", "-b", "nodomain/Payments"] in calls

    def test_no_operation_prefix_in_branch_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "main")
        monkeypatch.setattr("_orchestrator.git_ops.open_branches", list)
        monkeypatch.setattr("_orchestrator.git_ops.branch_exists", lambda name: False)
        calls = self._fake_git(monkeypatch)
        check_branch("Payment", "shared")
        created = [c for c in calls if c[:3] == ["git", "checkout", "-b"]]
        assert created
        for c in created:
            for name in c[3:]:
                assert not name.startswith(("feature/", "modify/"))

    def test_already_on_any_branch_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "shared/Payment")
        with pytest.raises(SystemExit):
            check_branch("Other", "vps")

    def test_any_open_branch_blocks_even_if_merged(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr("_orchestrator.git_ops.current_branch", lambda: "main")
        monkeypatch.setattr("_orchestrator.git_ops.open_branches", lambda: ["modify/Users"])
        with pytest.raises(SystemExit):
            check_branch("Payment", "shared")
        assert "Other branches are open" in capsys.readouterr().out


class TestDoPushesWithoutMerge:

    def test_do_pushes_branch_but_never_merges_to_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = ProjectFeatures.load(tmp_path)
        target = project.target_for_new("SubmitBid", "auction")
        scaffold_new_feature(target, "")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "auction/SubmitBid")
        monkeypatch.setattr("_orchestrator.commands.run_runner", lambda *a, **k: True)
        monkeypatch.setattr("_orchestrator.commands.register_target", lambda *a, **k: None)

        result = _cmd_do(target, "SubmitBid")

        assert ["git", "push", "-u", "origin", "auction/SubmitBid"] in calls
        assert ["git", "commit", "-m", "feat: SubmitBid"] in calls
        assert not any(c[:3] == ["git", "checkout", "main"] for c in calls)
        assert not any(c[:2] == ["git", "merge"] for c in calls)
        assert "run merge" in result.next_action
        assert result.success


class TestMergeGuard:

    def test_merge_on_main_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "main")
        dispatch("merge")
        out = capsys.readouterr().out
        assert "Checkout a feature branch before running merge" in out

    def test_merge_main_variant_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "main*")
        dispatch("merge")
        assert "Checkout a feature branch before running merge" in capsys.readouterr().out

    def test_merge_detached_head_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "")
        dispatch("merge")
        assert "Detached HEAD" in capsys.readouterr().out

    def test_merge_with_target_on_feature_branch_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "modify/Payment")
        dispatch("merge shared/Payment")
        assert "merge takes no target" in capsys.readouterr().out


class TestUndoHandler:

    def test_undo_on_main_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "main")
        dispatch("undo")
        assert "Checkout a feature branch before running undo" in capsys.readouterr().out

    def test_undo_detached_head_aborts(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "")
        dispatch("undo")
        assert "Detached HEAD" in capsys.readouterr().out

    def test_undo_with_target_rejected(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "shared/Payment")
        dispatch("undo shared/Payment")
        assert "undo takes no target" in capsys.readouterr().out

    def test_undo_resets_branch_to_main(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.current_branch", lambda: "shared/Payment")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> object:
            calls.append(cmd)
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        monkeypatch.setattr("_orchestrator.git_ops.subprocess.run", fake_run)
        dispatch("undo")
        out = capsys.readouterr().out
        assert ["git", "fetch", "origin"] in calls
        assert ["git", "checkout", "main"] in calls
        assert ["git", "reset", "--hard", "origin/main"] in calls
        assert ["git", "clean", "-fd"] in calls
        assert ["git", "branch", "-D", "shared/Payment"] in calls
        assert ["git", "push", "origin", "--delete", "shared/Payment"] in calls
        assert "matches main exactly" in out


class TestInitHandler:

    def test_bare_init_fails(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        dispatch("init")
        assert "init requires a project target" in capsys.readouterr().out

    def test_init_without_path_fails(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        dispatch('init MyProject "python fastapi"')
        assert "init requires a project target" in capsys.readouterr().out

    def test_init_creates_folder_and_agents_symlink(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        monkeypatch.chdir(tmp_path)
        dispatch("init mypath/MyProject")
        proj = tmp_path / "mypath" / "MyProject"
        assert proj.is_dir()
        assert (proj / ".agents").is_symlink()
        assert not (proj / ".features.json").exists()
        assert not (proj / "features").is_dir()
        assert not (proj / "SPEC.md").exists()
        assert Path.cwd() == proj

    def test_init_ignores_prompt_argument(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        monkeypatch.chdir(tmp_path)
        dispatch('init mypath/MyProject "Python 3.13, FastAPI, Vue 3"')
        proj = tmp_path / "mypath" / "MyProject"
        assert proj.is_dir()
        assert (proj / ".agents").is_symlink()
        assert not (proj / ".features.json").exists()
        assert not (proj / "features").is_dir()
        assert Path.cwd() == proj

    def test_init_absolute_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("_orchestrator.commands.load_project", lambda repo: FakeProject())
        target = tmp_path / "abs" / "Proj"
        dispatch(f"init {target}")
        assert target.is_dir()
        assert (target / ".agents").is_symlink()
        assert Path.cwd() == target


class FakeQaProject:
    """Stands in for ProjectFeatures in `qa` tests: known features + features root."""

    def __init__(self, known: dict[str, str], root: Path) -> None:
        self.known_features = known
        self._root = root

    def root_for_domain(self, domain: str) -> Path:
        return self._root


class TestQaHandler:

    def _run_qa(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        known: dict[str, str],
        pytest_stdout: str = "",
        pytest_rc: int = 0,
    ) -> str:
        features_root = tmp_path / "features"
        project = FakeQaProject(known, features_root)
        monkeypatch.setattr("_orchestrator.commands.REPO_ROOT", tmp_path)

        class FakeResult:
            def __init__(self, stdout: str, returncode: int) -> None:
                self.stdout = stdout
                self.returncode = returncode

        monkeypatch.setattr(
            "_orchestrator.commands.subprocess.run",
            lambda *a, **k: FakeResult(pytest_stdout, pytest_rc),
        )
        result = _cmd_qa(project)
        assert result.success == (pytest_rc == 0)
        return capsys.readouterr().out

    def test_qa_with_no_features_is_clean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        out = self._run_qa(tmp_path, capsys, monkeypatch, known={})
        assert "0 feature slices" in out

    def test_qa_runs_pytest_per_feature(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        feat_dir = tmp_path / "features" / "auction" / "SubmitBid"
        feat_dir.mkdir(parents=True)
        (feat_dir / "Tests.py").write_text("def test_bid():\n    assert True\n")
        out = self._run_qa(
            tmp_path, capsys, monkeypatch,
            known={"SubmitBid": "auction"},
            pytest_stdout="tests/SubmitBid/Tests.py::test_bid PASSED\n",
        )
        assert "[auction/SubmitBid]" in out
        assert "PASS  test_bid" in out
        assert "1 passed, 0 failed" in out

    def test_qa_fails_when_feature_tests_fail(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        feat_dir = tmp_path / "features" / "auction" / "SubmitBid"
        feat_dir.mkdir(parents=True)
        (feat_dir / "Tests.py").write_text("def test_bid():\n    assert False\n")
        out = self._run_qa(
            tmp_path, capsys, monkeypatch,
            known={"SubmitBid": "auction"},
            pytest_stdout="tests/SubmitBid/Tests.py::test_bid FAILED\n",
            pytest_rc=1,
        )
        assert "FAIL  test_bid" in out
        assert "Summary: 0 passed, 1 failed, 1 feature slices" in out

    def test_qa_skips_features_without_tests(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        feat_dir = tmp_path / "features" / "auction" / "SubmitBid"
        feat_dir.mkdir(parents=True)
        out = self._run_qa(tmp_path, capsys, monkeypatch, known={"SubmitBid": "auction"})
        assert "[auction/SubmitBid]" not in out
        assert "0 feature slices" in out


class TestKnownPrefixes:

    def test_includes_all_commands(self) -> None:
        expected = {"new", "do", "modify", "delete", "move", "merge", "undo", "init", "scan", "qa"}
        assert _KNOWN_PREFIXES == expected
