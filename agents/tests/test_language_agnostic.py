"""Tests for language-agnostic / multi-language support (TypeScript, JavaScript, etc.)."""

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from agents.feature import FeatureTarget, ProjectFeatures
import agents.rules as rules
import agents.runner as runner
import agents.scaffold as scaffold
from agents.templates import (
    CODE_TEMPLATES_JS,
    CODE_TEMPLATES_TS,
    get_code_templates,
    get_spec_template,
)
from agents.commands import dispatch


class TestLanguageAgnosticTemplates:

    def test_get_spec_template_stacks(self) -> None:
        py_spec = get_spec_template("python")
        assert "PEP 484" in py_spec
        assert "from shared.logger import logging_func" in py_spec

        ts_spec = get_spec_template("typescript")
        assert "strict TypeScript type annotations" in ts_spec
        assert "shared/logger" in ts_spec

        js_spec = get_spec_template("javascript")
        assert "clean ES module syntax" in js_spec
        assert "shared/logger" in js_spec

    def test_get_code_templates_stacks(self) -> None:
        ts_templates = get_code_templates("typescript")
        assert set(ts_templates.keys()) == {"Schema.ts", "Handler.ts", "Controller.ts", "Tests.ts"}
        assert "zod" in ts_templates["Schema.ts"]
        assert "node:test" in ts_templates["Tests.ts"]

        js_templates = get_code_templates("javascript")
        assert set(js_templates.keys()) == {"Schema.js", "Handler.js", "Controller.js", "Tests.js"}
        assert "node:test" in js_templates["Tests.js"]

        py_templates = get_code_templates("python")
        assert set(py_templates.keys()) == {"Schema.py", "Handler.py", "Controller.py", "Tests.py"}


class TestLanguageAgnosticRules:

    def test_ts_comment_blocked_ts_ignore_allowed(self) -> None:
        vs = rules.check_text("// a comment\n", "Handler.ts")
        assert any("comments not allowed" in v.message for v in vs)

        vs_ignore = rules.check_text("// @ts-ignore\nconst x = 1;\n", "Handler.ts")
        assert not any("comments not allowed" in v.message for v in vs_ignore)

        vs_expect = rules.check_text("// @ts-expect-error\nconst x = 1;\n", "Handler.ts")
        assert not any("comments not allowed" in v.message for v in vs_expect)

    def test_ts_block_comment_blocked(self) -> None:
        vs = rules.check_text("/* block comment */\n", "Handler.ts")
        assert any("comments not allowed" in v.message for v in vs)

    def test_ts_console_log_blocked(self) -> None:
        vs = rules.check_text("console.log('debug');\n", "Handler.ts")
        assert any("use shared logger, not console.log()" in v.message for v in vs)

    def test_ts_emoji_blocked(self) -> None:
        vs = rules.check_text("const msg = '🎉';\n", "Handler.ts")
        assert any("emoji found" in v.message for v in vs)

    def test_ts_handler_requires_logger(self) -> None:
        vs = rules.check_text("export class H {}\n", "Handler.ts", groups={"structure"})
        assert any("Handler must have a module-level logger" in v.message for v in vs)

    def test_ts_tests_requires_test_token(self) -> None:
        vs = rules.check_text("const x = 1;\n", "Tests.ts", groups={"structure"})
        assert any("Tests must contain test assertions" in v.message for v in vs)

    def test_ts_balanced_delimiters(self) -> None:
        vs = rules.check_text("function foo() {\n", "Handler.ts", groups={"structure"})
        assert any("Unbalanced braces" in v.message for v in vs)


class TestLanguageAgnosticScaffolding:

    def test_init_typescript_project(self, tmp_path: Path) -> None:
        proj = tmp_path / "ts_project"
        ok = scaffold.init_new_project(proj, stack="typescript")
        assert ok is True
        assert (proj / ".agents").is_symlink()
        assert (proj / "package.json").exists()
        assert (proj / "tsconfig.json").exists()
        assert (proj / "shared" / "logger.ts").exists()
        assert (proj / ".features.json").exists()

        cfg = json.loads((proj / ".features.json").read_text())
        assert cfg["stack"] == "typescript"
        assert "Schema.ts" in cfg["canonical_files"]
        assert not (proj / ".python-version").exists()
        assert not (proj / "pyproject.toml").exists()

    def test_init_javascript_project(self, tmp_path: Path) -> None:
        proj = tmp_path / "js_project"
        ok = scaffold.init_new_project(proj, stack="javascript")
        assert ok is True
        assert (proj / ".agents").is_symlink()
        assert (proj / "package.json").exists()
        assert (proj / "shared" / "logger.js").exists()
        assert (proj / ".features.json").exists()

        cfg = json.loads((proj / ".features.json").read_text())
        assert cfg["stack"] == "javascript"
        assert "Schema.js" in cfg["canonical_files"]

    def test_scaffold_new_feature_typescript(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        slice_dir = tmp_path / "features" / "auth" / "Login"
        target = FeatureTarget(
            name="Login",
            domain="auth",
            dir=slice_dir,
            root=tmp_path / "features",
            config_path=tmp_path / ".features.json",
            stack="typescript",
            canonical_files=frozenset({"Schema.ts", "Handler.ts", "Controller.ts", "Tests.ts"}),
        )
        monkeypatch.setattr(scaffold, "generate_spec_with_ai", lambda *a, **k: None)

        scaffold.scaffold_new_feature(target, "User authentication via JWT")

        assert (slice_dir / "spec.md").exists()
        assert not (slice_dir / "__init__.py").exists()
        spec_text = (slice_dir / "spec.md").read_text()
        assert "strict TypeScript type annotations" in spec_text
        assert "* `Schema.ts`" in spec_text
        assert "* `Handler.ts`" in spec_text
        assert "* `Controller.ts`" in spec_text
        assert "* `Tests.ts`" in spec_text


class TestLanguageAgnosticFeatureResolution:

    def test_load_project_detects_typescript_from_tsconfig(self, tmp_path: Path) -> None:
        (tmp_path / "tsconfig.json").write_text("{}")
        (tmp_path / ".features.json").write_text("{}\n")
        proj = ProjectFeatures.load(tmp_path)
        assert proj.stack == "typescript"
        assert "Schema.ts" in proj.canonical_files

    def test_load_project_detects_javascript_from_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / ".features.json").write_text("{}\n")
        proj = ProjectFeatures.load(tmp_path)
        assert proj.stack == "javascript"
        assert "Schema.js" in proj.canonical_files

    def test_load_project_explicit_stack_and_test_command(self, tmp_path: Path) -> None:
        cfg = {
            "stack": "typescript",
            "test_command": "pnpm vitest run {test_path}",
            "canonical_files": ["Schema.ts", "Handler.ts", "Tests.ts"],
        }
        (tmp_path / ".features.json").write_text(json.dumps(cfg) + "\n")
        proj = ProjectFeatures.load(tmp_path)
        assert proj.stack == "typescript"
        assert proj.test_command == "pnpm vitest run {test_path}"
        assert proj.canonical_files == ("Schema.ts", "Handler.ts", "Tests.ts")

        target = proj.target_for_new("Profile", "users")
        assert target.stack == "typescript"
        assert target.test_command == "pnpm vitest run {test_path}"
        assert "Schema.ts" in target.canonical_files

    def test_scan_detects_typescript_slices(self, tmp_path: Path) -> None:
        features_dir = tmp_path / "features"
        slice_dir = features_dir / "billing" / "Invoice"
        slice_dir.mkdir(parents=True)
        (slice_dir / "Handler.ts").write_text("export class InvoiceHandler {}\n")

        (tmp_path / ".features.json").write_text(json.dumps({"stack": "typescript"}) + "\n")
        proj = ProjectFeatures.load(tmp_path)
        scanned = proj.scan()
        assert len(scanned) == 1
        assert scanned[0].name == "Invoice"
        assert scanned[0].domain == "billing"
        assert scanned[0].stack == "typescript"


class TestLanguageAgnosticRunner:

    def test_constitution_typescript_requires_package_json(self, tmp_path: Path) -> None:
        target = tmp_path / "features" / "auth" / "Login"
        target.mkdir(parents=True)
        issues = runner.validate_constitution(tmp_path, target, stack="typescript")
        assert "Missing package.json" in issues

        (tmp_path / "package.json").write_text("{}")
        issues_ok = runner.validate_constitution(tmp_path, target, stack="typescript")
        assert not any("package.json" in i for i in issues_ok)

    def test_constitution_typescript_catches_secrets(self, tmp_path: Path) -> None:
        target = tmp_path / "features" / "auth" / "Login"
        target.mkdir(parents=True)
        (tmp_path / "package.json").write_text("{}")
        (target / "Handler.ts").write_text('const secret = "super-secret-token-value";\n')

        issues = runner.validate_constitution(tmp_path, target, stack="typescript")
        assert any("potential hardcoded secret" in i for i in issues)

    def test_collect_target_files_ts_and_js(self, tmp_path: Path) -> None:
        target = tmp_path / "Feature"
        target.mkdir()
        (target / "spec.md").write_text("# spec\n")
        (target / "Schema.ts").write_text("export const S = {};\n")
        (target / "Handler.ts").write_text("export class H {}\n")
        (target / "helper.js").write_text("module.exports = {};\n")
        (target / "notes.txt").write_text("not code\n")

        files = runner.collect_target_files(target, expected={"Schema.ts", "Handler.ts"})
        assert "spec.md" in files
        assert "Schema.ts" in files
        assert "Handler.ts" in files
        assert "helper.js" in files
        assert "notes.txt" not in files

    def test_extract_code_blocks_typescript_json_and_markdown(self) -> None:
        json_resp = json.dumps({
            "Schema.ts": "export const Schema = {};",
            "Handler.ts": "export class Handler {}",
            "Controller.ts": "export class Controller {}",
            "Tests.ts": "test('ok', () => {});",
        })
        extracted = runner.extract_code_blocks(json_resp)
        assert set(extracted.keys()) == {"Schema.ts", "Handler.ts", "Controller.ts", "Tests.ts"}

        md_resp = (
            "### Schema.ts\n```typescript\nexport const Schema = {};\n```\n\n"
            "### Handler.ts\n```ts\nexport class Handler {}\n```\n"
        )
        extracted_md = runner.extract_code_blocks(md_resp)
        assert "Schema.ts" in extracted_md
        assert "Handler.ts" in extracted_md

    def test_run_tests_custom_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        test_file = tmp_path / "Tests.ts"
        test_file.touch()

        executed_cmd: list[str] = []

        class FakeProc:
            def __init__(self, cmd: list[str], *args: object, **kwargs: object) -> None:
                nonlocal executed_cmd
                executed_cmd = cmd
                self.stdout = iter(["vitest passed\n"])
                self.returncode = 0

            def __enter__(self) -> "FakeProc":
                return self

            def __exit__(self, *args: object) -> None:
                pass

        monkeypatch.setattr(runner.subprocess, "Popen", FakeProc)

        passed, output = runner.run_tests(test_file, test_command="pnpm vitest run {test_path}", stack="typescript")
        assert passed is True
        assert executed_cmd == ["pnpm", "vitest", "run", str(test_file)]


class TestLanguageAgnosticCliDispatch:

    def test_init_dispatch_with_stack_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        dispatch("init mypath/NodeApp --stack typescript")

        proj = tmp_path / "mypath" / "NodeApp"
        assert proj.is_dir()
        assert (proj / ".agents").is_symlink()
        assert (proj / "package.json").exists()
        assert (proj / "tsconfig.json").exists()
        assert (proj / "shared" / "logger.ts").exists()
        assert (proj / ".features.json").exists()
