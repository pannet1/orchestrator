SPEC_TEMPLATE = """\
# {action} — {domain_title} Feature

## Overview

{overview}

## Input / Output

| Direction | Format | Description |
|-----------|--------|-------------|
| Input | `dict` with fields... | <!-- list fields --> |
| Output | `dict` with status key | `{{"status": "ok", ...}}` |

## Business Logic Constraints

* <!-- list invariants, rules, edge cases -->

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
