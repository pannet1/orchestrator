#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

_agents_dir = Path(__file__).resolve().parent
_repo_dir = _agents_dir.parent
for _p in (str(_repo_dir), str(_agents_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from agents.commands import dispatch
    from agents.config import MODEL_CONFIG
except ImportError:
    from commands import dispatch  # type: ignore[no-redef]
    from config import MODEL_CONFIG  # type: ignore[no-redef]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Orchestrator Agent -- decompose and dispatch feature work.",
        usage="%(prog)s <action> <domain/Feature> [inline prompt] [flags]",
        add_help=False,
    )
    parser.add_argument(
        "--help", "-h", action="store_true",
        help="show this help message and exit",
    )
    parser.add_argument(
        "command",
        nargs="*",
        help="e.g. new Payments / modify shared/Payment / do Payment",
    )
    parser.add_argument(
        "--prompt", "-p",
        help="Path to a prompt file with multi-sentence feature logic (relative to repo root)",
    )
    parser.add_argument(
        "--model", "-m",
        help="Override Zen API model for this run (e.g. --model claude-sonnet-4-5)",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=0,
        help="Cap LLM attempts across the discovered free-model list (0 = try all available free models; default: 0)",
    )
    parser.add_argument(
        "--no-controller", action="store_true",
        help="Skip Controller.py generation (for background workers)",
    )
    parser.add_argument(
        "--app", "-a", default="",
        help="App context: 'private' for admin features (uses features/ dir instead of web/features/)",
    )
    parser.add_argument(
        "--stack", "-s", default="",
        choices=["python", "typescript", "javascript", ""],
        help="Project stack: python, typescript, or javascript",
    )
    parser.add_argument(
        "--db", default="",
        help="Database engine (e.g. sqlite, duckdb, postgres) for vertical slice schema.sql",
    )
    parser.add_argument(
        "--ui", default="",
        help="Frontend UI template type: 'vue' (Jinja2+Vue3) or 'plain' (Jinja2+Plain JS)",
    )
    parser.add_argument(
        "--no-tester", action="store_true",
        help="Skip adversarial Tester subagent audit of Tests.py",
    )
    args = parser.parse_args()
    if args.model:
        MODEL_CONFIG.write_text(json.dumps({"model": args.model}) + "\n")
        print(f"[Orchestrator] Model set to: {args.model}\n")
    if getattr(args, "help", False) or not args.command:
        parser.print_help()
        print()
        from agents.commands import _HELP_TEXT
        print(_HELP_TEXT)
        sys.exit(0 if getattr(args, "help", False) else 1)
    return args


if __name__ == "__main__":
    args = parse_args()
    request = " ".join(args.command)
    prompt_content = ""
    if args.prompt:
        path = Path(args.prompt)
        if path.suffix == ".md" and path.exists():
            prompt_content = path.read_text().strip()
        else:
            prompt_content = args.prompt.strip()
    result = dispatch(
        request,
        prompt_content,
        no_controller=args.no_controller,
        no_tester=args.no_tester,
        app=args.app,
        max_attempts=args.max_attempts,
        stack=args.stack,
        db=args.db,
        ui=args.ui,
    )
    if result.next_action:
        print()
        print(f"Next: {result.next_action}")
    sys.exit(0 if result.success else 1)
