"""Rule-driven static checks for generated code.

Rules live in `agents/rules/<lang>/core.json` — declarative JSON, one file per
language. This module only *executes* them: classify a file by extension, load
its rule set, apply each check. Adding a rule never touches code; adding a
language means creating `rules/<lang>/core.json`.

Both consumers use this single registry:
  - `orch.py qa`      — audits every `.py` in the repo + feature slices
  - runner.py         — `validate_code_standards` / `validate_code_structure`

Check kinds:
  - `line`           regex per line; `exclude` = substrings that neutralize a hit
  - `text-required`  whole file must contain every string in `contains`
  - `balanced`       delimiter counts (`match` pair or `odd` single token)
  - `py-ast`         named structural checks (Python only), one implementation each

Common filters:
  - `only`    apply only when filename ∈ list
  - `except`  skip when filename ∈ list
  - `skip`    skip when filename ∈ `name` AND text contains every string in `contains`
  - `group`   subset selector (`standards` | `structure`) — runner splits its two gates
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".vue": "vue",
    ".jinja": "jinja",
    ".jinja2": "jinja",
    ".ts": "ts",
    ".js": "ts",
}

_cache: dict[str, list[dict]] = {}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int  # 0 = file-level
    rule: str
    message: str


def load_rules(lang: str) -> list[dict]:
    """Load (and cache) `<RULES_DIR>/<lang>/core.json`; missing file => no rules."""
    if lang not in _cache:
        rule_file = RULES_DIR / lang / "core.json"
        checks: list[dict] = []
        if rule_file.exists():
            try:
                checks = json.loads(rule_file.read_text()).get("checks", [])
            except (json.JSONDecodeError, OSError):
                checks = []
        _cache[lang] = checks
    return _cache[lang]


def lang_for(name: str) -> str:
    return LANG_BY_EXT.get(Path(name).suffix, "")


def _applies(check: dict, name: str, text: str) -> bool:
    if check.get("only") and name not in check["only"]:
        return False
    if check.get("except") and name in check["except"]:
        return False
    skip = check.get("skip")
    if skip and name in skip.get("name", []) and all(s in text for s in skip.get("contains", [])):
        return False
    return True


def _check_line(check: dict, name: str, text: str, out: list[Violation]) -> None:
    pattern = re.compile(check["pattern"])
    excludes = check.get("exclude", [])
    for i, line in enumerate(text.splitlines(), 1):
        if not pattern.search(line):
            continue
        if any(x in line for x in excludes):
            continue
        out.append(Violation(name, i, check["id"], check["message"]))


def _check_text_required(check: dict, name: str, text: str, out: list[Violation]) -> None:
    for content in check.get("contains", []):
        if content not in text:
            out.append(Violation(name, 0, check["id"], check["message"]))
            return


def _check_balanced(check: dict, name: str, text: str, out: list[Violation]) -> None:
    for pair in check.get("pairs", []):
        if pair.get("mode") == "odd":
            token = pair["token"]
            if text.count(token) % 2:
                out.append(Violation(name, 0, check["id"], pair["message"]))
        else:
            op, cl = pair["open"], pair["close"]
            o, c = text.count(op), text.count(cl)
            if o != c:
                out.append(Violation(name, 0, check["id"], pair["message"].format(open=o, close=c)))


# -- py-ast named checks ------------------------------------------------------

def _ast_syntax_error(text: str) -> list[Violation]:
    try:
        ast.parse(text)
    except SyntaxError:
        return [Violation("<file>", 0, "syntax", "syntax error")]
    return []


def _ast_return_annotations(text: str, name: str) -> list[Violation]:
    syntax = _ast_syntax_error(text)
    if syntax:
        return [Violation(name, 0, "syntax", "syntax error")]
    tree = ast.parse(text)
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("test_"):
            continue
        if node.returns is None:
            out.append(Violation(name, node.lineno, "return-types", f"{node.name} missing return type"))
    return out


def _ast_bare_none(text: str, name: str) -> list[Violation]:
    syntax = _ast_syntax_error(text)
    if syntax:
        return [Violation(name, 0, "syntax", "syntax error")]
    tree = ast.parse(text)
    out: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = [None] * (len(node.args.args) - len(node.args.defaults)) + list(node.args.defaults)
        for arg, default in zip(node.args.args, defaults):
            if arg.annotation is None:
                continue
            if not isinstance(default, ast.Constant) or default.value is not None:
                continue
            ann = arg.annotation
            if isinstance(ann, ast.Subscript):
                continue  # Optional[...]
            if isinstance(ann, ast.Name) and ann.id == "Optional":
                continue
            if isinstance(ann, ast.Attribute) and ann.attr == "Optional":
                continue
            label = getattr(ann, "id", getattr(ann, "attr", type(ann).__name__))
            out.append(Violation(name, node.lineno, "bare-none", f"param {arg.arg} has bare {label} = None"))
    return out


def _ast_unused_imports(text: str, name: str) -> list[Violation]:
    imports: list[tuple[str, int, str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.match(r"^from\s+(\S+)\s+import\s+(.+)$", s)
        if m:
            for part in m.group(2).split(","):
                alias = part.strip().split(" as ")[-1].strip()
                if alias and alias != "_" and not alias.startswith("*"):
                    imports.append((alias.split(".")[0], i + 1, f"from {m.group(1)} import {part.strip()}"))
            continue
        m = re.match(r"^import\s+(.+)$", s)
        if m:
            for part in m.group(1).split(","):
                alias = part.strip().split(" as ")[-1].strip()
                top = alias.split(".")[0]
                if top and top != "_":
                    imports.append((top, i + 1, f"import {part.strip()}"))
    out: list[Violation] = []
    for top, ln, full in imports:
        if full.startswith("from __future__"):
            continue
        if "from __future__ import annotations" in text and full.startswith("from typing import"):
            continue
        if top == "__name__":
            continue
        if top == "pytest" and name == "Tests.py":
            continue
        rest = "\n".join(lines[ln:])
        if top not in rest:
            out.append(Violation(name, ln, "unused-imports", f"unused import '{full}'"))
    return out


def _ast_shared_logger(text: str, name: str) -> list[Violation]:
    if "from shared.logger import logging_func" in text:
        return []
    if "logging.getLogger" in text:
        return [Violation(name, 0, "shared-logger",
                          "use `from shared.logger import logging_func` instead of logging.getLogger(__name__)")]
    return [Violation(name, 0, "shared-logger", "missing `from shared.logger import logging_func`")]


_AST_CHECKS: dict[str, object] = {
    "return_annotations": _ast_return_annotations,
    "bare_none": _ast_bare_none,
    "unused_imports": _ast_unused_imports,
    "shared_logger_import": _ast_shared_logger,
}


def check_text(text: str, name: str, groups: set[str] | None = None, lang: str | None = None) -> list[Violation]:
    """Apply the rule set for `name`'s language to `text`. `groups=None` => all rules."""
    lang = lang or lang_for(name)
    out: list[Violation] = []
    for check in load_rules(lang):
        if groups is not None and check.get("group") not in groups:
            continue
        if not _applies(check, name, text):
            continue
        kind = check["kind"]
        if kind == "line":
            _check_line(check, name, text, out)
        elif kind == "text-required":
            _check_text_required(check, name, text, out)
        elif kind == "balanced":
            _check_balanced(check, name, text, out)
        elif kind == "py-ast":
            fn = _AST_CHECKS.get(check.get("check", ""))
            if fn is not None:
                out.extend(fn(text, name))  # type: ignore[operator]
    return out


def check_file(path: Path) -> list[Violation]:
    return check_text(path.read_text(), path.name)