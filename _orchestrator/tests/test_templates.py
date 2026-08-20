from _orchestrator.templates import CODE_TEMPLATES, DEFAULT_OVERVIEW, SPEC_TEMPLATE


class TestSpecTemplate:

    def test_contains_format_vars(self) -> None:
        assert "{action}" in SPEC_TEMPLATE
        assert "{domain_title}" in SPEC_TEMPLATE
        assert "{overview}" in SPEC_TEMPLATE

    def test_mentions_type_annotations(self) -> None:
        assert "type annotations" in SPEC_TEMPLATE.lower()
        assert "PEP 484" in SPEC_TEMPLATE

    def test_mentions_logging_pattern(self) -> None:
        assert "shared.logger" in SPEC_TEMPLATE
        assert "logging_func" in SPEC_TEMPLATE

    def test_has_all_sections(self) -> None:
        sections = [
            "## Overview",
            "## Input / Output",
            "## Business Logic Constraints",
            "## Error Cases",
            "## Dependencies",
            "## Code Standards",
        ]
        for s in sections:
            assert s in SPEC_TEMPLATE, f"Missing section: {s}"


class TestDefaultOverview:

    def test_is_comment(self) -> None:
        assert DEFAULT_OVERVIEW.startswith("<!--")
        assert DEFAULT_OVERVIEW.endswith("-->")


class TestCodeTemplates:

    def test_has_all_expected_files(self) -> None:
        expected = {"Schema.py", "Handler.py", "Controller.py", "Tests.py"}
        assert set(CODE_TEMPLATES.keys()) == expected

    def test_schema_has_base_model(self) -> None:
        assert "BaseModel" in CODE_TEMPLATES["Schema.py"]

    def test_handler_has_logger_import(self) -> None:
        assert "from shared.logger import logging_func" in CODE_TEMPLATES["Handler.py"]

    def test_controller_imports_handler(self) -> None:
        assert "from .Handler import" in CODE_TEMPLATES["Controller.py"]

    def test_tests_import_pytest(self) -> None:
        assert "import pytest" in CODE_TEMPLATES["Tests.py"]
        assert "class Test" in CODE_TEMPLATES["Tests.py"]
        assert "def test_" in CODE_TEMPLATES["Tests.py"]

    def test_all_contain_action_format_var(self) -> None:
        for name, template in CODE_TEMPLATES.items():
            if name == "Schema.py":
                assert "{action}Schema" in template
            elif name in ("Handler.py", "Controller.py") or name == "Tests.py":
                assert "{action}" in template
