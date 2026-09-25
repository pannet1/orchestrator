import json
import pytest
from pathlib import Path

from agents import rules
from agents.feature import FeatureTarget, ProjectFeatures
from agents.orch import parse_args
from agents.runner import (
    collect_target_files,
    extract_code_blocks,
    extract_json_blocks,
    verify_target_files,
    write_code_blocks,
)
from agents.scaffold import DEFAULT_FILE_DESCRIPTIONS, init_new_project, scaffold_new_feature
from agents.specs import parse_expected_files
from agents.templates import (
    FULLSTACK_TEMPLATES,
    HTML_TEMPLATE_PLAIN,
    HTML_TEMPLATE_VUE,
    SCHEMA_SQL_TEMPLATE,
    SPEC_TEMPLATE,
    get_fullstack_template,
)


class TestFullstackTemplates:
    def test_spec_template_contains_db_and_frontend_sections(self) -> None:
        assert "## Database / Schema (if schema.sql present)" in SPEC_TEMPLATE
        assert "## Frontend / View (if view.html present)" in SPEC_TEMPLATE
        assert "CREATE TABLE IF NOT EXISTS" in SPEC_TEMPLATE
        assert "delimiters: ['[[', ']]']" in SPEC_TEMPLATE

    def test_fullstack_templates_defined(self) -> None:
        assert "schema.sql" in FULLSTACK_TEMPLATES
        assert "view.html" in FULLSTACK_TEMPLATES

    def test_get_fullstack_template_schema_sql(self) -> None:
        tmpl = get_fullstack_template("schema.sql", action="Cart")
        assert "CREATE TABLE IF NOT EXISTS carts" in tmpl
        assert "id TEXT PRIMARY KEY" in tmpl

    def test_get_fullstack_template_view_html_vue(self) -> None:
        tmpl = get_fullstack_template("view.html", action="Cart", ui="vue")
        assert "<!DOCTYPE html>" in tmpl
        assert "vue" in tmpl.lower()
        assert "delimiters: ['[[', ']]']" in tmpl
        assert "Cart ready" in tmpl

    def test_get_fullstack_template_view_html_plain(self) -> None:
        tmpl = get_fullstack_template("view.html", action="Cart", ui="plain")
        assert "<!DOCTYPE html>" in tmpl
        assert "vue" not in tmpl.lower()
        assert "{{ message }}" in tmpl


class TestParseExpectedFilesFullstack:
    def test_extracts_schema_sql_and_view_html(self) -> None:
        spec = """# Cart Feature
## Expected Files

* `Schema.py`: schemas
* `Handler.py`: logic
* `Controller.py`: router
* `Tests.py`: tests
* `schema.sql`: database DDL
* `view.html`: jinja vue template

## Input / Output
"""
        files = parse_expected_files(spec)
        assert "Schema.py" in files
        assert "Handler.py" in files
        assert "Controller.py" in files
        assert "Tests.py" in files
        assert "schema.sql" in files
        assert "view.html" in files


class TestFeatureTargetFullstack:
    def test_project_features_load_db_and_ui(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".features.json"
        cfg.write_text(json.dumps({
            "features_dir": "features",
            "stack": "python",
            "db": "sqlite",
            "ui": "jinja-vue",
        }))
        pf = ProjectFeatures.load(tmp_path)
        assert pf.db == "sqlite"
        assert pf.ui == "jinja-vue"

        target = pf.target_for_new("Cart", "shop")
        assert "schema.sql" in target.canonical_files
        assert "view.html" in target.canonical_files
        assert target.db == "sqlite"
        assert target.ui == "jinja-vue"

    def test_target_for_new_cli_overrides(self, tmp_path: Path) -> None:
        pf = ProjectFeatures.load(tmp_path)
        assert pf.db == ""
        assert pf.ui == ""

        # Default: 4 files
        t_default = pf.target_for_new("Payment", "billing")
        assert "schema.sql" not in t_default.canonical_files
        assert "view.html" not in t_default.canonical_files

        # CLI overrides with db=duckdb, ui=plain
        t_override = pf.target_for_new("Payment", "billing", db="duckdb", ui="plain")
        assert "schema.sql" in t_override.canonical_files
        assert "view.html" in t_override.canonical_files
        assert t_override.db == "duckdb"
        assert t_override.ui == "plain"


class TestScaffoldFullstack:
    def test_scaffold_includes_schema_sql_and_view_html_in_spec(self, tmp_path: Path) -> None:
        target = FeatureTarget(
            name="Cart",
            domain="shop",
            dir=tmp_path / "features" / "shop" / "Cart",
            root=tmp_path / "features",
            config_path=tmp_path / ".features.json",
            canonical_files=frozenset({
                "Schema.py", "Handler.py", "Controller.py", "Tests.py", "schema.sql", "view.html",
            }),
            db="sqlite",
            ui="vue",
        )
        scaffold_new_feature(target)
        spec = (target.dir / "spec.md").read_text()
        assert "`schema.sql`" in spec
        assert "`view.html`" in spec
        assert (target.dir / "__init__.py").exists()

    def test_init_new_project_scaffolds_db_manager(self, tmp_path: Path) -> None:
        proj = tmp_path / "my_app"
        ok = init_new_project(proj, stack="python", db="sqlite", ui="vue")
        assert ok
        assert (proj / "db" / "manager.py").exists()
        assert "get_db" in (proj / "db" / "manager.py").read_text()
        assert (proj / ".features.json").exists()
        cfg = json.loads((proj / ".features.json").read_text())
        assert cfg["db"] == "sqlite"
        assert cfg["ui"] == "vue"


class TestFullstackRules:
    def test_sql_rules_idempotent_ddl_pass(self) -> None:
        valid_sql = "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY);\n"
        violations = rules.check_text(valid_sql, "schema.sql")
        assert len(violations) == 0

    def test_sql_rules_non_idempotent_fails(self) -> None:
        invalid_sql = "CREATE TABLE items (id TEXT PRIMARY KEY);\n"
        violations = rules.check_text(invalid_sql, "schema.sql")
        assert any(v.rule == "idempotent-ddl" for v in violations)

    def test_sql_rules_unbalanced_parentheses(self) -> None:
        invalid_sql = "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY;\n"
        violations = rules.check_text(invalid_sql, "schema.sql")
        assert any(v.rule == "balanced-parentheses" for v in violations)

    def test_jinja_rules_valid_view_html(self) -> None:
        valid_html = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
  <h1>{{ title }}</h1>
  {% if show %}
    <p>Visible</p>
  {% endif %}
</body>
</html>"""
        violations = rules.check_text(valid_html, "view.html")
        assert len(violations) == 0

    def test_jinja_rules_unbalanced_expression(self) -> None:
        invalid_html = """<!DOCTYPE html>
<html>
<body>
  <h1>{{ title }</h1>
</body>
</html>"""
        violations = rules.check_text(invalid_html, "view.html")
        assert any(v.rule == "balanced-jinja-expr" for v in violations)

    def test_jinja_rules_unbalanced_block(self) -> None:
        invalid_html = """<!DOCTYPE html>
<html>
<body>
  {% if show
    <p>test</p>
</body>
</html>"""
        violations = rules.check_text(invalid_html, "view.html")
        assert any(v.rule == "balanced-jinja-block" for v in violations)

    def test_jinja_rules_missing_doctype(self) -> None:
        invalid_html = """<html><body>Hello</body></html>"""
        violations = rules.check_text(invalid_html, "view.html")
        assert any(v.rule == "view-html-doctype" for v in violations)

    def test_vue_rules_valid(self) -> None:
        valid_vue = """<template><div>Hello</div></template>\n<script>export default {}</script>"""
        violations = rules.check_text(valid_vue, "App.vue")
        assert len(violations) == 0

    def test_vue_rules_unbalanced_template(self) -> None:
        invalid_vue = """<template><div>Hello</div>\n<script>export default {}</script>"""
        violations = rules.check_text(invalid_vue, "App.vue")
        assert any(v.rule == "balanced-template" for v in violations)


class TestRunnerFullstack:
    def test_extract_json_blocks_with_html_and_sql(self) -> None:
        payload = json.dumps({
            "Schema.py": "class S: pass\n",
            "schema.sql": "CREATE TABLE IF NOT EXISTS t (id INT);\n",
            "view.html": "<!DOCTYPE html><html><body>ok</body></html>\n",
        })
        blocks = extract_json_blocks(payload)
        assert "Schema.py" in blocks
        assert "schema.sql" in blocks
        assert "view.html" in blocks

    def test_extract_code_blocks_markdown_with_html_and_sql(self) -> None:
        md = """### schema.sql
```sql
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY);
```

### view.html
```html
<!DOCTYPE html>
<html><body><h1>Hi</h1></body></html>
```
"""
        blocks = extract_code_blocks(md)
        assert "schema.sql" in blocks
        assert "CREATE TABLE IF NOT EXISTS users" in blocks["schema.sql"]
        assert "view.html" in blocks
        assert "<!DOCTYPE html>" in blocks["view.html"]

    def test_write_and_collect_target_files_fullstack(self, tmp_path: Path) -> None:
        feat_dir = tmp_path / "features" / "shop" / "Cart"
        feat_dir.mkdir(parents=True)
        (feat_dir / "spec.md").write_text("# Cart\n")

        files = {
            "Schema.py": "class CartSchema: pass\n",
            "Handler.py": "class CartHandler: pass\n",
            "Controller.py": "class CartController: pass\n",
            "Tests.py": "def test_ok(): assert True\n",
            "schema.sql": "CREATE TABLE IF NOT EXISTS carts (id TEXT);\n",
            "view.html": "<!DOCTYPE html><html><body>ok</body></html>\n",
        }
        written, _ = write_code_blocks(files, feat_dir, expected=set(files.keys()))
        assert len(written) == 6

        collected = collect_target_files(feat_dir, expected=set(files.keys()))
        assert "schema.sql" in collected
        assert "view.html" in collected
        assert "spec.md" in collected
        assert "Schema.py" in collected

    def test_verify_target_files_fullstack(self, tmp_path: Path) -> None:
        repo_root = tmp_path
        (repo_root / ".python-version").write_text("3.13\n")
        (repo_root / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "0.1.0"\n')
        shared_dir = repo_root / "shared"
        shared_dir.mkdir(parents=True)
        (shared_dir / "logger.py").write_text("def logging_func(n): pass\n")

        feat_dir = repo_root / "features" / "shop" / "Cart"
        feat_dir.mkdir(parents=True)

        files = {
            "Schema.py": "from pydantic import BaseModel\n\nclass CartSchema(BaseModel):\n    id: str\n",
            "Handler.py": "from shared.logger import logging_func\nlogger = logging_func(__name__)\n\ndef get_cart() -> dict:\n    return {}\n",
            "Controller.py": "from shared.logger import logging_func\nlogger = logging_func(__name__)\n\ndef handle() -> dict:\n    return {}\n",
            "Tests.py": "def test_cart() -> None:\n    assert True\n",
            "schema.sql": "CREATE TABLE IF NOT EXISTS carts (id TEXT PRIMARY KEY);\n",
            "view.html": "<!DOCTYPE html>\n<html><head><title>Cart</title></head><body><h1>Cart</h1></body></html>\n",
        }
        for fname, content in files.items():
            (feat_dir / fname).write_text(content)

        expected = set(files.keys())
        # mock test_command to echo pass
        ok, msg = verify_target_files(feat_dir, repo_root, expected=expected, stack="python", test_command="true")
        assert ok, f"verify_target_files failed: {msg}"


class TestOrchCliFullstack:
    def test_parse_args_db_and_ui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        monkeypatch.setattr(sys, "argv", ["orch.py", "new", "Cart", "prompt", "--db", "sqlite", "--ui", "vue"])
        args = parse_args()
        assert args.db == "sqlite"
        assert args.ui == "vue"

    def test_dispatch_passes_db_and_ui(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from agents.commands import dispatch
        import agents.commands

        monkeypatch.setattr(agents.commands, "REPO_ROOT", tmp_path)
        monkeypatch.setattr("agents.feature.REPO_ROOT", tmp_path)
        monkeypatch.setattr("agents.commands.check_branch", lambda *a, **kw: None)
        monkeypatch.setattr("agents.scaffold.generate_spec_with_ai", lambda *a, **kw: None)

        res = dispatch("new Cart inline prompt", db="duckdb", ui="plain")
        assert res.next_action == "./.agents/orch.py do nodomain/Cart"

        spec_file = tmp_path / "features" / "nodomain" / "Cart" / "spec.md"
        assert spec_file.exists()
        spec_text = spec_file.read_text()
        assert "`schema.sql`" in spec_text
        assert "`view.html`" in spec_text
