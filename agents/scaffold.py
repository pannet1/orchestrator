import os
import sys
from pathlib import Path

from .config import AGENTS_DIR
from .feature import register_target
from .llm import generate_spec_with_ai
from .specs import _qa_spec, parse_expected_files
from .templates import CODE_TEMPLATES, DEFAULT_OVERVIEW, SPEC_TEMPLATE, get_spec_template


DEFAULT_FILE_DESCRIPTIONS: dict[str, str] = {
    "Schema.py": "Data validation models, request/response schemas, and types",
    "Handler.py": "Core business logic execution and feature workflow orchestration",
    "Controller.py": "Interface endpoints, routing, or dispatch entry points",
    "Tests.py": "Unit and integration test suite covering positive paths and edge cases",
    "schema.sql": "Slice-local idempotent database DDL table schema and indexes (CREATE TABLE IF NOT EXISTS)",
    "view.html": "Slice-local Jinja2 frontend template with plain JavaScript or Vue 3 client-side logic",
    "Schema.ts": "Data validation models, request/response schemas, and types",
    "Handler.ts": "Core business logic execution and feature workflow orchestration",
    "Controller.ts": "Interface endpoints, routing, or dispatch entry points",
    "Tests.ts": "Unit and integration test suite covering positive paths and edge cases",
    "Schema.js": "Data validation models, request/response schemas, and types",
    "Handler.js": "Core business logic execution and feature workflow orchestration",
    "Controller.js": "Interface endpoints, routing, or dispatch entry points",
    "Tests.js": "Unit and integration test suite covering positive paths and edge cases",
}


def format_spec_overview(overview: str) -> str:
    if overview:
        return overview
    return DEFAULT_OVERVIEW


def scaffold_new_feature(target, overview: str = "", no_controller: bool = False) -> Path:
    slice_dir = target.dir
    slice_dir.mkdir(parents=True, exist_ok=True)

    stack = getattr(target, "stack", "python")
    canonical = getattr(target, "canonical_files", None)
    expected_files = list(canonical) if canonical else list(CODE_TEMPLATES.keys())
    if no_controller:
        expected_files = [f for f in expected_files if not f.startswith("Controller.")]

    file_lines = []
    for fname in sorted(expected_files):
        desc = DEFAULT_FILE_DESCRIPTIONS.get(fname, f"Implementation module for {target.name}")
        file_lines.append(f"* `{fname}`: {desc}")
    expected_files_md = "\n".join(file_lines)

    spec_template = get_spec_template(stack)

    if overview:
        ai_spec = generate_spec_with_ai(target.domain, target.name, overview)
        if ai_spec:
            if not parse_expected_files(ai_spec):
                ai_spec = ai_spec.rstrip() + f"\n\n## Expected Files\n\n{expected_files_md}\n"
            (slice_dir / "spec.md").write_text(ai_spec)
            _qa_spec(slice_dir / "spec.md", overview, f"new:{target.name}")
            current_spec = (slice_dir / "spec.md").read_text()
            if not parse_expected_files(current_spec):
                (slice_dir / "spec.md").write_text(current_spec.rstrip() + f"\n\n## Expected Files\n\n{expected_files_md}\n")
        else:
            overview_text = format_spec_overview(overview)
            spec = spec_template.format(
                domain_title=target.domain.title() if target.domain else target.name,
                action=target.name,
                overview=overview_text,
                expected_files=expected_files_md,
            ).rstrip("\n")
            (slice_dir / "spec.md").write_text(spec)
            print("[Orchestrator] LLM unavailable — using template spec.md", file=sys.stderr)
    else:
        spec = spec_template.format(
            domain_title=target.domain.title() if target.domain else target.name,
            action=target.name,
            overview=DEFAULT_OVERVIEW,
            expected_files=expected_files_md,
        ).rstrip("\n")
        (slice_dir / "spec.md").write_text(spec)

    # Do not pre-scaffold rigid code files (Schema, Handler, Controller, etc.).
    # Human in the loop can modify the expected files in spec.md before running `do`.
    if stack == "python":
        (slice_dir / "__init__.py").touch()
    register_target(target)

    label = f"{target.domain}/{target.name}"
    note = " (no controller)" if no_controller else ""
    print(f"\nScaffolded new feature spec: {label}{note}")
    print(f"Review {slice_dir / 'spec.md'} to inspect or edit expected files before implementation.\n")
    return slice_dir


def init_new_project(project_dir: Path, stack: str = "python", db: str = "", ui: str = "") -> bool:
    """Create a new project folder with a `.agents` symlink to this repo's `agents/`.

    Projects live inside this single git repository, so there is no
    per-project git repo to manage. `init` scaffolds the folder, configuration,
    and links `.agents` -> `agents/` (the tool sources).
    """
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(project_dir)

    dot_link = project_dir / ".agents"
    if dot_link.is_symlink() and not dot_link.exists():
        dot_link.unlink()
    if not dot_link.is_symlink() and not dot_link.exists():
        rel = os.path.relpath(str(AGENTS_DIR), str(project_dir))
        dot_link.symlink_to(rel)
        print(f"  .agents/ -> {rel}  (this repo's agents/)")

    if stack == "python":
        # Pin the Python version for the target project. Constitution rule #1
        # requires .python-version to exist; pin to 3.13 so uv never picks up a
        # newer default (e.g. 3.14).
        py_ver_file = project_dir / ".python-version"
        if not py_ver_file.exists():
            py_ver_file.write_text("3.13\n")
            print("  .python-version -> 3.13")

        # Constitution rule #2 requires a uv-managed pyproject.toml
        pyproject = project_dir / "pyproject.toml"
        if not pyproject.exists():
            pyproject.write_text(
                f'[project]\nname = "{project_dir.name.lower()}"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n\n'
                "[dependency-groups]\ndev = [\n    \"pytest>=8.0.0\",\n]\n\n"
                "[tool.pytest.ini_options]\npythonpath = [\".\"]\n"
            )
            print("  pyproject.toml -> uv-managed (with pytest & pythonpath)")

        # Scaffold minimal shared/logger.py so feature imports succeed out of the box
        shared_dir = project_dir / "shared"
        logger_file = shared_dir / "logger.py"
        if not logger_file.exists():
            shared_dir.mkdir(parents=True, exist_ok=True)
            (shared_dir / "__init__.py").touch()
            logger_file.write_text(
                "import logging\n\n\n"
                "def logging_func(name: str) -> logging.Logger:\n"
                "    return logging.getLogger(name)\n"
            )
            print("  shared/logger.py -> logging_func scaffolded")

        # Scaffold minimal db/manager.py for gateway db access
        db_dir = project_dir / "db"
        db_file = db_dir / "manager.py"
        if not db_file.exists():
            db_dir.mkdir(parents=True, exist_ok=True)
            (db_dir / "__init__.py").touch()
            db_file.write_text(
                "import sqlite3\n"
                "from contextlib import contextmanager\n"
                "from typing import Generator\n\n\n"
                "@contextmanager\n"
                "def get_db(db_path: str = \":memory:\") -> Generator[sqlite3.Connection, None, None]:\n"
                "    conn = sqlite3.connect(db_path)\n"
                "    conn.row_factory = sqlite3.Row\n"
                "    try:\n"
                "        yield conn\n"
                "        conn.commit()\n"
                "    finally:\n"
                "        conn.close()\n"
            )
            print("  db/manager.py -> get_db scaffolded")

        if db or ui:
            import json
            features_cfg = project_dir / ".features.json"
            cfg_data: dict[str, object] = {
                "features_dir": "features",
                "stack": "python",
            }
            if db:
                cfg_data["db"] = db
            if ui:
                cfg_data["ui"] = ui
            features_cfg.write_text(json.dumps(cfg_data, indent=2) + "\n")
            print(f"  .features.json -> configured with db={db} ui={ui}")

    elif stack == "typescript":
        pkg_json = project_dir / "package.json"
        if not pkg_json.exists():
            pkg_json.write_text(
                f'{{\n  "name": "{project_dir.name.lower()}",\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {{\n    "test": "node --test"\n  }}\n}}\n'
            )
            print("  package.json -> scaffolded")

        tsconfig = project_dir / "tsconfig.json"
        if not tsconfig.exists():
            tsconfig.write_text(
                '{\n  "compilerOptions": {\n    "target": "ES2022",\n    "module": "NodeNext",\n    "moduleResolution": "NodeNext",\n    "strict": true,\n    "esModuleInterop": true,\n    "skipLibCheck": true\n  }\n}\n'
            )
            print("  tsconfig.json -> scaffolded")

        shared_dir = project_dir / "shared"
        logger_file = shared_dir / "logger.ts"
        if not logger_file.exists():
            shared_dir.mkdir(parents=True, exist_ok=True)
            logger_file.write_text(
                "export function loggingFunc(name: string) {\n"
                "  return {\n"
                '    info: (msg: string, ...args: unknown[]) => console.info(`[${name}] ${msg}`, ...args),\n'
                '    error: (msg: string, ...args: unknown[]) => console.error(`[${name}] ${msg}`, ...args),\n'
                '    warn: (msg: string, ...args: unknown[]) => console.warn(`[${name}] ${msg}`, ...args),\n'
                "  };\n"
                "}\n"
            )
            print("  shared/logger.ts -> loggingFunc scaffolded")

        features_cfg = project_dir / ".features.json"
        if not features_cfg.exists():
            features_cfg.write_text(
                '{\n  "features_dir": "features",\n  "stack": "typescript",\n  "canonical_files": ["Schema.ts", "Handler.ts", "Controller.ts", "Tests.ts"]\n}\n'
            )
            print("  .features.json -> configured for typescript")

    elif stack == "javascript":
        pkg_json = project_dir / "package.json"
        if not pkg_json.exists():
            pkg_json.write_text(
                f'{{\n  "name": "{project_dir.name.lower()}",\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {{\n    "test": "node --test"\n  }}\n}}\n'
            )
            print("  package.json -> scaffolded")

        shared_dir = project_dir / "shared"
        logger_file = shared_dir / "logger.js"
        if not logger_file.exists():
            shared_dir.mkdir(parents=True, exist_ok=True)
            logger_file.write_text(
                "export function loggingFunc(name) {\n"
                "  return {\n"
                '    info: (msg, ...args) => console.info(`[${name}] ${msg}`, ...args),\n'
                '    error: (msg, ...args) => console.error(`[${name}] ${msg}`, ...args),\n'
                '    warn: (msg, ...args) => console.warn(`[${name}] ${msg}`, ...args),\n'
                "  };\n"
                "}\n"
            )
            print("  shared/logger.js -> loggingFunc scaffolded")

        features_cfg = project_dir / ".features.json"
        if not features_cfg.exists():
            features_cfg.write_text(
                '{\n  "features_dir": "features",\n  "stack": "javascript",\n  "canonical_files": ["Schema.js", "Handler.js", "Controller.js", "Tests.js"]\n}\n'
            )
            print("  .features.json -> configured for javascript")

    print(f"[Orchestrator] Project ready at {project_dir} (stack: {stack})")
    return True
