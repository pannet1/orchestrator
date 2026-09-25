SPEC_TEMPLATE = """\
# {action} — {domain_title} Feature

## Overview

{overview}

## Expected Files

{expected_files}

## Input / Output

| Direction | Format | Description |
|-----------|--------|-------------|
| Input | `dict` with fields... | <!-- list fields --> |
| Output | `dict` with status key | `{{"status": "ok", ...}}` |

## Business Logic Constraints

* <!-- list invariants, rules, edge cases -->

## Database / Schema (if schema.sql present)

* Idempotent DDL: use `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`.
* Access via Gateway `from db.manager import get_db`. In tests, run in-memory.

## Frontend / View (if view.html present)

* Jinja2 template rendered by Controller via `Jinja2Templates(directory=str(Path(__file__).parent))`.
* Client logic: Plain JavaScript fetch() or Vue 3 with `delimiters: ['[[', ']]']` to avoid Jinja `{{ }}` collisions.

## Error Cases

| Condition | Error | Message |
|-----------|-------|---------|
| <!-- when --> | <!-- exception type --> | <!-- message --> |

## Dependencies

* <!-- libraries, other features, config files -->

## Code Standards

* All code must use type annotations per PEP 484 (function signatures + module-level variables).
* All `.py` files must use `from shared.logger import logging_func; logger = logging_func(__name__)` for logging (never bare `logging.getLogger(__name__)`).
"""

DEFAULT_OVERVIEW = "<!-- Orchestrator: describe what this feature does, why it exists. -->"

CODE_TEMPLATES = {
    "Schema.py": """\
from pydantic import BaseModel


class {action}Schema(BaseModel):
    pass
""",
    "Handler.py": """\
from typing import Any, Dict

from shared.logger import logging_func
logger = logging_func(__name__)


class {action}Handler:

    def execute(self, **kwargs: Any) -> Dict[str, Any]:
        logger.info("{action}.execute called")
        return {{"status": "ok"}}
""",
    "Controller.py": """\
from typing import Any, Dict

from .Handler import {action}Handler
from shared.logger import logging_func
logger = logging_func(__name__)


class {action}Controller:

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        handler = {action}Handler()
        return handler.execute(**request)
""",
    "Tests.py": """\
import pytest

from .Handler import {action}Handler


class Test{action}Handler:

    def test_execute_returns_ok(self) -> None:
        handler = {action}Handler()
        result = handler.execute()
        assert result["status"] == "ok"
""",
}


SPEC_TEMPLATE_TS = """\
# {action} — {domain_title} Feature

## Overview

{overview}

## Expected Files

{expected_files}

## Input / Output

| Direction | Format | Description |
|-----------|--------|-------------|
| Input | Object with fields... | <!-- list fields --> |
| Output | Object with status key | `{{"status": "ok", ...}}` |

## Business Logic Constraints

* <!-- list invariants, rules, edge cases -->

## Error Cases

| Condition | Error | Message |
|-----------|-------|---------|
| <!-- when --> | <!-- error class --> | <!-- message --> |

## Dependencies

* <!-- libraries, other features, config files -->

## Code Standards

* All code must use strict TypeScript type annotations.
* All `.ts` files must import logger via `import {{ loggingFunc }} from "shared/logger"` (or relative path).
"""

SPEC_TEMPLATE_JS = """\
# {action} — {domain_title} Feature

## Overview

{overview}

## Expected Files

{expected_files}

## Input / Output

| Direction | Format | Description |
|-----------|--------|-------------|
| Input | Object with fields... | <!-- list fields --> |
| Output | Object with status key | `{{"status": "ok", ...}}` |

## Business Logic Constraints

* <!-- list invariants, rules, edge cases -->

## Error Cases

| Condition | Error | Message |
|-----------|-------|---------|
| <!-- when --> | <!-- error class --> | <!-- message --> |

## Dependencies

* <!-- libraries, other features, config files -->

## Code Standards

* All code must use clean ES module syntax.
* All `.js` files must import logger via `import {{ loggingFunc }} from "shared/logger.js"` (or relative path).
"""

CODE_TEMPLATES_TS = {
    "Schema.ts": """\
import { z } from "zod";

export const {action}Schema = z.object({});
export type {action}Input = z.infer<typeof {action}Schema>;
""",
    "Handler.ts": """\
import { loggingFunc } from "../../shared/logger";
const logger = loggingFunc("{action}Handler");

export class {action}Handler {
  execute(params: Record<string, unknown> = {}): Record<string, unknown> {
    logger.info("{action}.execute called");
    return { status: "ok" };
  }
}
""",
    "Controller.ts": """\
import { {action}Handler } from "./Handler";
import { loggingFunc } from "../../shared/logger";
const logger = loggingFunc("{action}Controller");

export class {action}Controller {
  handle(request: Record<string, unknown>): Record<string, unknown> {
    const handler = new {action}Handler();
    return handler.execute(request);
  }
}
""",
    "Tests.ts": """\
import test from "node:test";
import assert from "node:assert/strict";
import { {action}Handler } from "./Handler";

test("{action}Handler execute returns ok", () => {
  const handler = new {action}Handler();
  const result = handler.execute();
  assert.strictEqual(result.status, "ok");
});
""",
}

CODE_TEMPLATES_JS = {
    "Schema.js": """\
export function validate{action}Input(input) {
  return input && typeof input === "object";
}
""",
    "Handler.js": """\
import { loggingFunc } from "../../shared/logger.js";
const logger = loggingFunc("{action}Handler");

export class {action}Handler {
  execute(params = {}) {
    logger.info("{action}.execute called");
    return { status: "ok" };
  }
}
""",
    "Controller.js": """\
import { {action}Handler } from "./Handler.js";
import { loggingFunc } from "../../shared/logger.js";
const logger = loggingFunc("{action}Controller");

export class {action}Controller {
  handle(request) {
    const handler = new {action}Handler();
    return handler.execute(request);
  }
}
""",
    "Tests.js": """\
import test from "node:test";
import assert from "node:assert/strict";
import { {action}Handler } from "./Handler.js";

test("{action}Handler execute returns ok", () => {
  const handler = new {action}Handler();
  const result = handler.execute();
  assert.strictEqual(result.status, "ok");
});
""",
}


def get_spec_template(stack: str = "python") -> str:
    if stack == "typescript":
        return SPEC_TEMPLATE_TS
    if stack == "javascript":
        return SPEC_TEMPLATE_JS
    return SPEC_TEMPLATE


def get_code_templates(stack: str = "python") -> dict[str, str]:
    if stack == "typescript":
        return CODE_TEMPLATES_TS
    if stack == "javascript":
        return CODE_TEMPLATES_JS
    return CODE_TEMPLATES


SCHEMA_SQL_TEMPLATE = """\
CREATE TABLE IF NOT EXISTS {table_name} (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""

HTML_TEMPLATE_PLAIN = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{action}</title>
</head>
<body>
    <div id="app">
        <h1>{action}</h1>
        <p>{{ message }}</p>
    </div>
    <script>
        document.addEventListener("DOMContentLoaded", () => {
            console.log("{action} loaded");
        });
    </script>
</body>
</html>
"""

HTML_TEMPLATE_VUE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{action}</title>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
</head>
<body>
    <div id="app">
        <h1>{action}</h1>
        <p>[[ message ]]</p>
    </div>
    <script>
        const { createApp } = Vue;
        createApp({
            delimiters: ['[[', ']]'],
            data() {
                return {
                    message: '{action} ready'
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

FULLSTACK_TEMPLATES: dict[str, str] = {
    "schema.sql": SCHEMA_SQL_TEMPLATE,
    "view.html": HTML_TEMPLATE_VUE,
}


def get_fullstack_template(fname: str, action: str = "", ui: str = "vue") -> str:
    if fname == "schema.sql":
        table_name = (action or "item").lower() + "s"
        return SCHEMA_SQL_TEMPLATE.replace("{table_name}", table_name)
    if fname == "view.html":
        if ui in ("plain", "jinja-plain"):
            return HTML_TEMPLATE_PLAIN.replace("{action}", action)
        return HTML_TEMPLATE_VUE.replace("{action}", action)
    return ""
