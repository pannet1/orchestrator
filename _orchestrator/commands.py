import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import rules
from .config import REPO_ROOT
from .feature import (
    FeatureTarget,
    ModifyResolution,
    ProjectFeatures,
    feature_from_branch,
    load_project,
    register_target,
    unregister_feature,
)
from .git_ops import (
    branch_exists,
    check_branch,
    checkout_main,
    current_branch,
    delete_local_branch,
    delete_remote_branch,
    ensure_branch,
    guard_open_branches,
    merge_branch,
    push_branch,
    rename_branch,
    reset_to_main,
    stage_and_commit,
)
from .launcher import run_runner
from .prompts import (
    resolve_change_prompt,
    resolve_current_file,
    resolve_prompt_for_implicit,
)
from .scaffold import init_new_project, scaffold_new_feature
from .specs import amend_spec, rewrite_spec_with_ai


@dataclass
class CommandResult:
    success: bool = True
    next_action: str = ""


_KNOWN_PREFIXES = frozenset({
    "new", "modify", "do", "delete", "move", "merge", "undo", "init", "scan", "qa",
})

_QA_SKIP_PARTS = frozenset({
    ".agents", "__pycache__", ".git", ".venv",
    ".mypy_cache", ".ruff_cache", "__init__.py",
})


def _parse_request(request: str) -> tuple[str, str, str, str]:
    cmd = request.strip().split(None, 1)
    verb = cmd[0] if cmd else ""
    rest = cmd[1] if len(cmd) > 1 else ""
    domain = ""
    prefix = verb.lower()
    action = rest.strip()
    if prefix in _KNOWN_PREFIXES and rest:
        target, _, tail = rest.partition(" ")
        target = target.strip()
        if "/" in target:
            domain, action = target.split("/", 1)
            action = action.strip()
        else:
            action = target
        rest = tail.strip()
    return prefix, domain, action, rest


_HELP_TEXT = """Usage:  ./.agents/orch.py <action> <domain/Feature> [inline prompt]

Prompt commands (expect an inline prompt):
  init     <path>/<project-name>           create new project
  new      <domain/Feature> "prompt"       scaffold new feature
  modify   <domain/Feature> "prompt"       amend existing spec

Branch commands (run from the feature branch):
  do                                     run backend agent
  delete                                 remove feature
  merge                                  merge current branch to main
  undo                                   discard branch, reset to main

Other:
  move     <OldDomain/OldFeature> <NewDomain/NewFeature>
  scan                                   discover existing features
  qa                                     run feature tests + code-standards audit (no LLM)

Examples:
  ./.agents/orch.py new Payments "auction payment wallet flow"
  ./.agents/orch.py modify shared/Payment "share screenshot separately"
  ./.agents/orch.py do Payment
  ./.agents/orch.py qa
"""


def _prompt_required_result(prefix: str, name: str) -> CommandResult:
    print("=" * 60)
    print(f"ERROR: `{prefix} {name}` requires a prompt.")
    print()
    print("Options:")
    print(f'  ./.agents/orch.py {prefix} {name} --prompt path/to/prompt.md')
    print(f'  ./.agents/orch.py {prefix} {name} "describe your change in words"')
    print(f'  ./.agents/orch.py {prefix} {name} path/to/prompt.md')
    print("=" * 60)
    return CommandResult(success=False)


def _cmd_init(domain: str, action: str, rest: str, prompt_content: str) -> CommandResult:
    # The prompt argument is ignored: init only creates the folder + .agents symlink.
    if not action:
        print("[Orchestrator] init requires a project target: init <path>/<project-name>")
        return CommandResult(success=False)
    if domain:
        project_dir = Path(domain) / action
    elif "/" in action:
        project_dir = Path("/") / action  # absolute path — leading "/" was consumed by parsing
    else:
        print("[Orchestrator] init requires a project target: init <path>/<project-name>")
        return CommandResult(success=False)
    if init_new_project(project_dir):
        return CommandResult(next_action=f'cd {project_dir} && ./.agents/orch.py new <domain/Feature> "prompt"')
    return CommandResult(success=False)


def _cmd_scan(project: ProjectFeatures) -> CommandResult:
    features = project.scan()
    if not features:
        print("[Orchestrator] No features discovered.")
        return CommandResult(next_action='new <domain/Feature> "prompt" to start the first one')
    by_domain: dict[str, list[str]] = {}
    for target in features:
        by_domain.setdefault(target.domain, []).append(target.name)
    for domain in sorted(by_domain):
        print(f"{domain}/")
        for name in sorted(by_domain[domain]):
            print(f"  {name}")
    return CommandResult(next_action='new <domain/Feature> "prompt" or modify <domain/Feature> "prompt"')


def _qa_audit_file(path: Path) -> list[str]:
    return [
        f"    {v.path}:{v.line} {v.message}" if v.line else f"    {v.path}: {v.message}"
        for v in rules.check_file(path)
    ]


def _cmd_qa(project: ProjectFeatures) -> CommandResult:
    """Run every feature's Tests.py via pytest + a rules-based standards audit. No LLM."""
    all_passed: list[str] = []
    all_failed: list[str] = []
    all_violations: list[str] = []

    print("=" * 50)
    print(" QA — Full Feature Regression + Code Standards")
    print("=" * 50)
    print()

    for py_file in sorted(REPO_ROOT.glob("*.py")):
        if any(part in _QA_SKIP_PARTS for part in py_file.relative_to(REPO_ROOT).parts):
            continue
        all_violations.extend(_qa_audit_file(py_file))

    n_features = 0
    for name, domain in sorted(project.known_features.items()):
        feat_dir = project.root_for_domain(domain) / domain / name if domain else project.root_for_domain(domain) / name
        test_file = feat_dir / "Tests.py"
        if not test_file.exists():
            continue
        n_features += 1
        print(f"  [{domain}/{name}]")

        if feat_dir.is_dir():
            for py_file in sorted(feat_dir.glob("*.py")):
                all_violations.extend(_qa_audit_file(py_file))

        result = subprocess.run(
            ["uv", "run", "pytest", str(test_file), "-v"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=120,
            check=False,
        )
        for line in result.stdout.splitlines():
            if " PASSED" in line:
                t = line.split("::")[-1].replace(" PASSED", "").strip()
                print(f"    PASS  {t}")
                all_passed.append(f"      {domain}/{name} :: {t}")
            elif " FAILED" in line:
                t = line.split("::")[-1].replace(" FAILED", "").strip()
                print(f"    FAIL  {t}")
                all_failed.append(f"      {domain}/{name} :: {t}")
        print()

    print("=" * 50)
    print(" Code Standards Violations")
    print("=" * 50)
    print()
    if all_violations:
        for v in sorted(set(all_violations)):
            print(v)
    else:
        print("  (none)")
    print()

    print("=" * 50)
    print(f" All Tests ({len(all_passed)} passed, {len(all_failed)} failed)")
    print("=" * 50)
    print()
    print("  Passing:")
    if all_passed:
        for p in all_passed:
            print(p)
    print()
    print("  Failing:")
    if all_failed:
        for f in all_failed:
            print(f)
    else:
        print("    (none)")
    print()

    print("=" * 50)
    print(f" Summary: {len(all_passed)} passed, {len(all_failed)} failed, {n_features} feature slices")
    print("=" * 50)

    return CommandResult(success=not all_failed)


def _cmd_feature(target: FeatureTarget, rest: str, prompt_content: str, no_controller: bool, prefix: str) -> CommandResult:
    description = resolve_change_prompt(rest, prompt_content, target.name, prefix)
    if description is None:
        return _prompt_required_result(prefix, target.name)
    check_branch(target.name, target.domain)
    feature_dir = scaffold_new_feature(target, description, no_controller=no_controller)
    if feature_dir and feature_dir.is_dir():
        return CommandResult(next_action=f"./.agents/orch.py do {target.domain}/{target.name}")
    print(f"[Orchestrator] Failed to scaffold feature '{target.name}'.")
    return CommandResult(success=False)


def _cmd_do(target: FeatureTarget | None, raw: str, max_attempts: int = 4) -> CommandResult:
    if not raw:
        print("[Orchestrator] No feature name given and cannot infer from current branch.")
        return CommandResult(next_action='checkout or create a feature branch first — new <domain/Feature> "prompt"')
    if not target:
        print(f"[Orchestrator] Feature not found: {raw}.")
        return CommandResult(next_action=f'./.agents/orch.py new {raw}')
    if not (target.dir / "spec.md").exists():
        print("[Orchestrator] No spec.md found.")
        return CommandResult(next_action=f'./.agents/orch.py new {raw}')

    display = target.name
    feature_dir = target.dir

    branch = current_branch()
    if branch == "main" or branch.startswith("main"):
        if guard_open_branches():
            return CommandResult(next_action="merge or delete the listed branches first")
        print("[Orchestrator] On main with clean slate.")
        branch = ensure_branch(display, target.domain)

    spec_text = (feature_dir / "spec.md").read_text() if (feature_dir / "spec.md").exists() else ""
    if "## Modification" in spec_text:
        task = f"Modify {display} per the amended spec.md"
    else:
        task = f"Implement {display} per its spec.md"
    commit_type = "feat"

    print(f"[Orchestrator] Generating code for {display}...")
    ok = run_runner("backend", feature_dir, task, max_attempts=max_attempts)
    if ok:
        register_target(target)
        print(f"\n{'='*60}\nALL TESTS PASSED.\n")
        print(f"[Orchestrator] Staging {feature_dir}...")
        msg_body = f"{commit_type}: {display}"
        print(f"[Orchestrator] Committing: {msg_body}")
        ok, detail = stage_and_commit([str(feature_dir)], msg_body)
        if not ok:
            print(f"[Orchestrator] {detail}")
            print("You may need to commit and merge manually.")
            return CommandResult(success=False, next_action="resolve the git error above, then commit and merge manually")
        if detail == "nothing to commit":
            print("[Orchestrator] Nothing to commit — already up to date.")
        elif detail:
            print(detail)
        print(f"[Orchestrator] Pushing {branch} to origin...")
        ok_push, push_err = push_branch(branch)
        if not ok_push:
            print(f"[Orchestrator] git push failed: {push_err}")
            print("You may need to commit and push manually.")
            return CommandResult(success=False, next_action="resolve the git error above, then push and merge manually")
        print(f"[Orchestrator] Done. {display} committed and pushed to '{branch}' (NOT merged to main).")
        return CommandResult(next_action=f'run merge to merge {branch} into main when ready')
    print(f"\n{'='*60}")
    print("IMPLEMENTATION FAILED. The auto-QA loop exhausted its attempts.")
    print("Copy the error output above and tell the AI:")
    print(f'  "The auto-QA loop failed for {display}. Here is the output: ..."')
    print("=" * 60)
    return CommandResult(success=False, next_action="fix the failing tests above, then run do again — or undo to discard this branch")


def _cmd_modify(res: ModifyResolution | None, raw: str, rest: str, prompt_content: str, implicit: bool) -> CommandResult:
    if res is None:
        print("[Orchestrator] No feature name given (modify expects a domain/Feature target, inline prompt, prompt file, or nvim context).")
        return CommandResult(next_action='pass a domain/Feature target with an inline prompt')
    if res.amend is None:
        print(f"[Orchestrator] Feature '{res.name}' not found.")
        return CommandResult(success=False, next_action=f'./.agents/orch.py new {res.name}')
    if implicit:
        change_prompt = resolve_prompt_for_implicit(rest, prompt_content)
        if not change_prompt:
            print("[Orchestrator] No prompt provided.")
            return CommandResult(next_action='provide an inline prompt, a prompt file, or nvim context')
    else:
        change_prompt = resolve_change_prompt(rest, prompt_content, res.name, "modify")
        if change_prompt is None:
            return _prompt_required_result("modify", res.name)
    heading = "Modification Request"
    check_branch(res.name, res.branch_domain)
    if not res.amend.dir.exists():
        scaffold_new_feature(res.amend, res.scaffold_overview, no_controller=True)
    rewrite_spec_with_ai(res.amend.dir, change_prompt, heading)
    amend_spec(
        res.amend.dir,
        heading="CONTRACT AMENDMENT",
        branch_prefix="modify",
        feature_name=res.name,
    )
    return CommandResult(next_action=f'./.agents/orch.py do {res.name} to implement the amended spec')


def _cmd_delete(target: FeatureTarget | None, raw: str) -> CommandResult:
    if not raw:
        print("[Orchestrator] No feature name given and cannot infer from current branch.")
        return CommandResult(next_action='checkout a feature branch first, or pass a feature name')
    target_branches = [f"{target.domain}/{raw}"] if target else [raw]
    branch = current_branch()
    on_target = branch in target_branches
    found_any = False

    if target and target.dir.exists():
        import shutil
        shutil.rmtree(target.dir)
        print(f"[Orchestrator] Deleted feature directory: {target.dir}")
        found_any = True

    unregister_feature(raw, target.dir if target else None, target.config_path if target else None)

    if on_target:
        checkout_main()
        delete_local_branch(branch)
        print(f"[Orchestrator] Deleted branch: {branch}")
        found_any = True
    else:
        for tb in target_branches:
            if branch_exists(tb):
                delete_local_branch(tb)
                print(f"[Orchestrator] Deleted branch: {tb}")
                found_any = True

    if not found_any:
        print(f"[Orchestrator] Nothing to delete: feature '{raw}' not found.")
    return CommandResult(next_action='scan to list remaining features, or new <domain/Feature> "prompt" to start one')


def _cmd_merge(branch: str, name: str, target: FeatureTarget | None, action: str, rest: str) -> CommandResult:
    if branch == "main" or branch.startswith("main"):
        print("[Orchestrator] You are on main. Checkout a feature branch before running merge.")
        return CommandResult(next_action='checkout a feature branch, then run merge')
    if not branch or branch == "(unknown)":
        print("[Orchestrator] Detached HEAD — checkout a feature branch before running merge.")
        return CommandResult(next_action='checkout a feature branch, then run merge')
    if action or rest:
        print("[Orchestrator] merge takes no target — it merges the current branch to main.")
        return CommandResult()
    if target and target.dir.exists():
        commit_type = "feat"
        print(f"[Orchestrator] Staging {target.dir}...")
        msg_body = f"{commit_type}: {name}"
        print(f"[Orchestrator] Committing: {msg_body}")
        ok, detail = stage_and_commit([str(target.dir)], msg_body)
        if not ok:
            print(f"[Orchestrator] {detail}")
            return CommandResult(success=False, next_action="resolve the git error above, then merge manually")
        if detail == "nothing to commit":
            print("[Orchestrator] Nothing to commit — already up to date.")
        elif detail:
            print(detail)
    else:
        print(f"[Orchestrator] No feature dir for '{name}' — merging branch as-is.")
    print(f"[Orchestrator] Pushing and merging {branch} to main...")

    ok_merge, merge_err = merge_branch(branch)
    if not ok_merge:
        print(f"[Orchestrator] {merge_err}")
        print("You may need to resolve and merge manually.")
        return CommandResult(success=False, next_action="resolve the git error above, then merge manually")
    print(f"[Orchestrator] Done. {name} merged to main.")
    return CommandResult(next_action='scan to list features, or new <domain/Feature> "prompt" to start one')


def _cmd_undo(action: str, rest: str) -> CommandResult:
    branch = current_branch()
    if branch == "main" or branch.startswith("main"):
        print("[Orchestrator] You are on main. Checkout a feature branch before running undo.")
        return CommandResult(next_action='checkout a feature branch, then run undo to discard it')
    if not branch or branch == "(unknown)":
        print("[Orchestrator] Detached HEAD — checkout a feature branch before running undo.")
        return CommandResult(next_action='checkout a feature branch, then run undo to discard it')
    if action or rest:
        print("[Orchestrator] undo takes no target — it discards the current branch and resets to main.")
        return CommandResult()
    print(f"[Orchestrator] Undoing {branch}: discarding commits and resetting to main...")
    ok, err = reset_to_main()
    if not ok:
        print(f"[Orchestrator] {err}")
        return CommandResult(success=False)
    delete_local_branch(branch)
    delete_remote_branch(branch)
    print(f"[Orchestrator] Done. {branch} removed; working tree matches main exactly.")
    return CommandResult(next_action='new <domain/Feature> "prompt" to start fresh, or scan to list features')


def _cmd_move(old_target: FeatureTarget | None, new_target: FeatureTarget | None, rest: str) -> CommandResult:
    if not old_target or not new_target:
        print("[Orchestrator] Usage: move <OldDomain/OldFeature> <NewDomain/NewFeature>")
        return CommandResult()
    old_dir = old_target.dir
    new_dir = new_target.dir
    if new_dir.exists():
        print(f"[Orchestrator] Target '{new_target.name}' already exists at {new_dir}")
        return CommandResult(success=False)
    old_name_disk = old_target.name
    print(f"[Orchestrator] Moving {old_name_disk} -> {new_target.name}...")
    new_dir.parent.mkdir(parents=True, exist_ok=True)
    old_dir.rename(new_dir)
    unregister_feature(old_target.name, old_dir, old_target.config_path)
    register_target(new_target)
    print("[Orchestrator] Running tests...")
    result = subprocess.run(
        ["uv", "run", "pytest", "tests/", "--ignore=tests/test_session_lifecycle.py", "--ignore=tests/test_links.py", "-q"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        check=False,
    )
    test_ok = result.returncode == 0
    if test_ok:
        last = [l for l in result.stdout.strip().splitlines() if l][-3:]
        print("\n".join(last))
        print("[Orchestrator] All tests pass.")
    else:
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print("[Orchestrator] Tests failed after rename. Check output above.")
    current = current_branch()
    old_branch = f"{old_target.domain}/{old_name_disk}"
    new_branch = f"{new_target.domain}/{new_target.name}"
    if old_branch == current:
        print(f"[Orchestrator] Renaming branch {old_branch} -> {new_branch}...")
        rename_branch(new_branch)
        ok, detail = stage_and_commit([str(new_dir)], f"move: {old_name_disk} -> {new_target.name}")
        if not ok and detail != "nothing to commit":
            print(f"[Orchestrator] {detail}")
        push_branch(new_branch)
        if not test_ok:
            print(f"[Orchestrator] Tests failed — branch moved to {new_branch}.")
    print(f"[Orchestrator] Moved {old_name_disk} -> {new_target.name} on branch {new_branch} (NOT merged to main).")
    return CommandResult(success=test_ok, next_action=f'run merge to merge {new_branch} into main when ready')


def _split_move_target(target_str: str) -> tuple[str, str]:
    t = target_str.strip().strip('"').strip("'")
    if "/" in t:
        domain, name = t.split("/", 1)
        return domain.strip(), name.strip()
    return "", t.strip()


def _resolve_do(project: ProjectFeatures, action: str, rest: str, app: str) -> tuple[FeatureTarget | None, str]:
    raw = action or rest
    if not raw:
        raw = feature_from_branch(current_branch())
    if not raw:
        return None, ""
    return project.resolve(raw, app=app), raw


def _resolve_delete(project: ProjectFeatures, action: str, rest: str, app: str) -> tuple[FeatureTarget | None, str]:
    raw = action or rest
    if not raw:
        raw = feature_from_branch(current_branch())
    if not raw:
        return None, ""
    return project.resolve(raw, app=app), raw

def dispatch(request: str, prompt_content: str = "", no_controller: bool = False, app: str = "", max_attempts: int = 6) -> CommandResult:

    prefix, domain, action, rest = _parse_request(request)
    project = load_project(REPO_ROOT)

    if domain and not app:
        app = project.app_for_domain(domain)

    if prefix not in _KNOWN_PREFIXES:
        print("[Orchestrator] Unknown command.")
        print()
        print(_HELP_TEXT)
        return CommandResult(success=False)

    if prefix == "init":
        return _cmd_init(domain, action, rest, prompt_content)

    display_prefix = "feature"
    if prefix == "new":
        display_prefix = prefix
        prefix = "feature"

    if prefix == "scan":
        return _cmd_scan(project)

    if prefix == "qa":
        if action or rest:
            print("[Orchestrator] qa takes no target — it audits the whole project.")
            return CommandResult()
        return _cmd_qa(project)

    if prefix == "feature":
        feature_target = project.target_for_new(action, domain, app)
        return _cmd_feature(feature_target, rest, prompt_content, no_controller, display_prefix)

    if prefix == "do":
        target, raw = _resolve_do(project, action, rest, app)
        return _cmd_do(target, raw, max_attempts=max_attempts)

    if prefix == "modify":
        implicit = not action
        raw = action or rest
        if implicit:
            raw = resolve_current_file() or ""
        res = project.resolve_modify(raw, app=app, implicit=implicit) if raw else None
        return _cmd_modify(res, raw, rest, prompt_content, implicit)

    if prefix == "delete":
        target, raw = _resolve_delete(project, action, rest, app)
        return _cmd_delete(target, raw)

    if prefix == "merge":
        branch = current_branch()
        name = feature_from_branch(branch)
        target = project.resolve(name) if name else None
        return _cmd_merge(branch, name, target, action, rest)

    if prefix == "undo":
        return _cmd_undo(action, rest)

    if prefix == "move":
        old_target = project.resolve(action, app=app) if action else None
        new_target = None
        if old_target and rest:
            new_domain, new_name = _split_move_target(rest)
            new_target = project.target_for_new(new_name, new_domain or old_target.domain, app)
        return _cmd_move(old_target, new_target, rest)

    print("[Orchestrator] Unknown request.")
    print()
    print(_HELP_TEXT)
    return CommandResult(success=False)
