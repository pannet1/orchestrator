"""Tests for the shared rules registry (_orchestrator/rules.py + agents/rules/)."""

from pathlib import Path

from _orchestrator import rules


def _check(text: str, name: str = "Handler.py", groups: set[str] | None = None) -> list[rules.Violation]:
    return rules.check_text(text, name, groups=groups)


def _msgs(vs: list[rules.Violation], rule: str | None = None) -> list[str]:
    return [v.message for v in vs if rule is None or v.rule == rule]


class TestLineChecks:

    def test_print_without_logger(self) -> None:
        assert _msgs(_check("def f() -> None:\n    print('hi')\n"), "no-print") == ["use shared logger, not print()"]

    def test_print_with_logger_line_allowed(self) -> None:
        assert _msgs(_check("logger.info('x')\nprint('skip', logger)\n"), "no-print") == []

    def test_comment_blocked_noqa_allowed(self) -> None:
        assert _msgs(_check("# keep out\n"), "comment") == ["comments not allowed"]
        assert _msgs(_check("# noqa: E501\n"), "comment") == []

    def test_emoji_and_flask(self) -> None:
        vs = _check("import flask\nprint('🦄')\n")
        assert any("forbidden import" in v.message for v in vs)
        assert any("emoji" in v.message for v in vs)


class TestAstChecks:

    def test_missing_return_type(self) -> None:
        vs = _check("def compute(x: int):\n    return x\n")
        assert any("missing return type" in v.message for v in vs)

    def test_test_functions_exempt_from_return_types(self) -> None:
        assert _msgs(_check("def test_x():\n    assert True\n"), "return-types") == []

    def test_bare_none(self) -> None:
        vs = _check("def f(x: str = None) -> None:\n    pass\n")
        assert any("bare str = None" in v.message for v in vs)

    def test_optional_none_allowed(self) -> None:
        assert _msgs(_check("def f(x: Optional[str] = None) -> None:\n    pass\n"), "bare-none") == []

    def test_unused_import(self) -> None:
        vs = _check("import math\n\ndef f() -> None:\n    pass\n")
        assert any("unused import 'import math'" in v.message for v in vs)

    def test_used_import_and_typing_exemption(self) -> None:
        code = ("from __future__ import annotations\nfrom typing import Optional\nimport re\n"
                "from shared.logger import logging_func\n\n"
                "logger = logging_func(__name__)\n\n\n"
                "def f(x: Optional[str] = None) -> None:\n    return re.sub('a', 'b', x)\n")
        assert _check(code) == []

    def test_pytest_import_exempt_in_tests(self) -> None:
        code = "import pytest\n\ndef test_x() -> None:\n    assert True\n"
        assert _check(code, name="Tests.py") == []

    def test_shared_logger_required_outside_exempts(self) -> None:
        vs = _check("def f() -> None:\n    pass\n", name="bios_cleaner.py")
        assert any("missing `from shared.logger import logging_func`" in v.message for v in vs)

    def test_shared_logger_exempt_in_tests_and_init(self) -> None:
        assert _check("def test_x() -> None:\n    assert True\n", name="Tests.py") == []
        assert _check("", name="__init__.py") == []

    def test_shared_logger_skip_for_schema_with_basemodel(self) -> None:
        code = "from pydantic import BaseModel\n\nclass S(BaseModel):\n    pass\n"
        assert not any(v.rule == "shared-logger" for v in _check(code, name="Schema.py"))


class TestStructureGroup:

    def test_handler_requires_logger_exact_message(self) -> None:
        vs = _check("def compute(x: int) -> int:\n    return x + 1\n", groups={"structure"})
        assert _msgs(vs, "handler-logger") == ["Handler.py must have a module-level logger"]

    def test_valid_handler_clean(self) -> None:
        code = ("from shared.logger import logging_func\n\n"
                "logger = logging_func(__name__)\n\n\n"
                "def compute(x: int) -> int:\n    return x + 1\n")
        assert _check(code, groups={"structure"}) == []

    def test_schema_requires_basemodel(self) -> None:
        vs = _check("class S:\n    pass\n", name="Schema.py", groups={"structure"})
        assert "Schema.py must import and use pydantic.BaseModel" in _msgs(vs, "schema-basemodel")

    def test_tests_require_test_functions(self) -> None:
        vs = _check("x = 1\n", name="Tests.py", groups={"structure"})
        assert "Tests.py must contain test functions" in _msgs(vs, "tests-presence")

    def test_balanced_parens(self) -> None:
        vs = _check("def f(:\n    pass\n", groups={"structure"})
        assert any("Unbalanced parentheses" in v.message for v in vs)

    def test_groups_filter_isolates_standard_vs_structure(self) -> None:
        code = "def f(:\n    print('x')\n"
        assert any(v.rule == "no-print" for v in _check(code, groups={"standards"}))
        assert not any(v.rule == "balanced" for v in _check(code, groups={"standards"}))
        assert any(v.rule == "balanced" for v in _check(code, groups={"structure"}))


class TestLanguageDispatch:

    def test_vue_has_no_rules_yet(self) -> None:
        assert _check("<template><div>hi</div></template>\n", name="App.vue") == []

    def test_unknown_extension_no_rules(self) -> None:
        assert _check("random", name="notes.txt") == []

    def test_lang_for(self) -> None:
        assert rules.lang_for("a.py") == "python"
        assert rules.lang_for("a.vue") == "vue"
        assert rules.lang_for("a.jinja") == "jinja"
        assert rules.lang_for("a.ts") == "ts"
        assert rules.lang_for("a.txt") == ""

    def test_check_file_reads_disk(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("print('no')\n")
        vs = rules.check_file(f)
        assert any(v.rule == "no-print" for v in vs)
        assert vs[0].path == "x.py"


class TestBadCode:

    def test_syntax_error_reported(self) -> None:
        vs = _check("def f(:\n")
        assert any(v.rule == "syntax" for v in vs)