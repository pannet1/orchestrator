import subprocess
import sys

from .config import REPO_ROOT


def current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        check=False)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "(unknown)"


def open_branches() -> list[str]:
    """Local branches other than main (merged or not)."""
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            check=False)
        return [l.strip() for l in result.stdout.split("\n") if l.strip() and l.strip() != "main"]
    except (OSError, subprocess.SubprocessError):
        return []


def guard_open_branches() -> list[str]:
    """Return open (non-main) branches, printing a block banner if any exist."""
    pending = open_branches()
    if pending:
        print("=" * 60)
        print("BLOCKED: Other branches are open. Merge or delete them first:")
        for b in pending:
            print(f"  {b}")
        print("=" * 60)
    return pending


def branch_exists(name: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", name],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    check=False)
    return result.returncode == 0


def merge_branch(branch: str) -> tuple[bool, str]:
    """Push branch, merge into main, push main, delete branch locally and remotely.

    Returns (ok, error_message); error_message is empty on success.
    """
    steps = (
        ("push", ["git", "push", "origin", branch]),
        ("checkout main", ["git", "checkout", "main"]),
        ("merge", ["git", "merge", branch]),
        ("push main", ["git", "push", "origin", "main"]),
    )
    for label, cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
        if result.returncode != 0:
            return False, f"git {label} failed: {result.stderr.strip()}"
        if result.stdout.strip():
            print(result.stdout.strip())
    delete_remote_branch(branch)
    delete_local_branch(branch)
    return True, ""


def ensure_branch(action: str, domain: str = "") -> str:
    """Return a `domain/action` feature branch, creating it from main when
    missing. When already on a non-main branch, returns it unchanged; the
    caller decides how to handle open branches or detached state (no
    sys.exit here)."""
    branch = current_branch()
    if branch == "main" or branch.startswith("main"):
        target = f"{domain}/{action}" if domain else action
        if branch_exists(target):
            print(f"[Orchestrator] Branch '{target}' exists. Switching to it.")
            subprocess.run(["git", "checkout", target], cwd=str(REPO_ROOT), check=False)
        else:
            print(f"[Orchestrator] Creating branch: {target}")
            subprocess.run(["git", "checkout", "-b", target], cwd=str(REPO_ROOT), check=True)
        return target
    return branch


def check_branch(action: str, domain: str = "") -> str:
    branch = current_branch()

    if branch and branch != "main" and not branch.startswith("main"):
        print("=" * 60)
        print(f"You are already on branch '{branch}'.")
        print("Complete and merge this branch first, then try again.")
        print("=" * 60)
        sys.exit(1)

    if branch == "main" or branch.startswith("main"):
        if guard_open_branches():
            sys.exit(1)
    return ensure_branch(action, domain)


def stage_and_commit(paths: list[str], message: str) -> tuple[bool, str]:
    """Stage `paths` and commit. Returns (ok, detail); `detail` is the commit
    output on success, 'nothing to commit' when there was nothing, or the
    failing git error text."""
    add = subprocess.run(
        ["git", "add", *paths], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()}"
    commit = subprocess.run(
        ["git", "commit", "-m", message], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    if commit.returncode != 0:
        combined = commit.stdout + commit.stderr
        if "nothing to commit" in combined:
            return True, "nothing to commit"
        return False, f"git commit failed: {commit.stderr.strip()}"
    return True, commit.stdout.strip()


def push_branch(branch: str) -> tuple[bool, str]:
    """Push `branch` to origin with upstream tracking. Returns (ok, error)."""
    result = subprocess.run(
        ["git", "push", "-u", "origin", branch], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    if result.returncode != 0:
        return False, result.stderr.strip()
    return True, ""


def checkout_main() -> None:
    subprocess.run(["git", "checkout", "main"], cwd=str(REPO_ROOT), check=False)


def delete_local_branch(branch: str) -> None:
    subprocess.run(["git", "branch", "-D", branch], cwd=str(REPO_ROOT), check=False)


def delete_remote_branch(branch: str) -> None:
    subprocess.run(["git", "push", "origin", "--delete", branch],
                   capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)


def reset_to_main() -> tuple[bool, str]:
    """Discard the working tree and hard-reset to origin/main.
    Returns (ok, error)."""
    subprocess.run(["git", "fetch", "origin"], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    checkout = subprocess.run(["git", "checkout", "main"], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    if checkout.returncode != 0:
        return False, f"git checkout main failed: {checkout.stderr.strip()}"
    reset = subprocess.run(["git", "reset", "--hard", "origin/main"],
                           capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    if reset.returncode != 0:
        return False, f"git reset failed: {reset.stderr.strip()}"
    subprocess.run(["git", "clean", "-fd"], capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    return True, ""


def rename_branch(new_name: str) -> None:
    subprocess.run(["git", "branch", "-m", new_name], cwd=str(REPO_ROOT), check=False)


def read_prompt_file(prompt_path: str) -> str:
    path = REPO_ROOT / prompt_path
    if not path.exists():
        print(f"[Orchestrator] Prompt file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path.read_text().strip()
