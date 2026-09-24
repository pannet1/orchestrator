# Orchestrator (`orch.py`)

The orchestrator is a single CLI entry point, `agents/orch.py` (this repo's
`agents/` dir). Projects get a `.agents` symlink to this repo's `agents/` dir,
so the tool is invoked from inside a target project as:

```
./.agents/orch.py <action> <domain/Feature> [inline prompt] [options]
```

> `REPO_ROOT = Path.cwd()` — the orchestrator operates on **whatever directory
> you run it from** (the target project), not on this repo. This repo is the
> tool. `model_config.json`, `model_chain.json`, `personas/`, and `rules/` live
> in `agents/` (i.e. `.agents/...` from a target project).

Every command routes through `agents/commands.py::dispatch()`. There are
no other entry points — `runner.py` is an internal subprocess (never run by
hand), and the old standalone `scaffolder.py` / `scaffold_project.py` scripts
have been deleted.

## Options

| Flag | Effect |
|---|---|
| `--prompt` / `-p <path-or-text>` | Prompt file (`.md` read from disk) or inline string. Positional text after the target is also accepted. |
| `--model` / `-m <id>` | Persist `{"model": <id>}` to `model_config.json` (this repo root), then continue the run with that model leading the chain. |
| `--no-controller` | Skip `Controller.py` generation (background workers). |
| `--app` / `-a <app>` | App context, e.g. `-a private` resolves against `features/` instead of `web/features/`. Auto-selected from the domain when the project defines `apps`. |

## Target syntax

`<domain/Feature>` — split on the first `/`. Without a domain, it is inferred
from `.features.json` `known_features`, else `nodomain`. Commands with no
target (`do`, `delete`, `merge`, `undo`, `qa`) infer the feature from the current
git branch name.

## Commands

| Command | What it does | Next |
|---|---|---|
| `init <path>/<project-name>` | Create project folder + `.agents` symlink → `agents/`. Does **not** create `.features.json`. Prompt arg ignored. | `new` |
| `new <domain/Feature> "prompt"` | Create feature branch, write spec.md (LLM-generated + spec-QA'd, template fallback), scaffold 4 files (`Schema.py`, `Handler.py`, `Controller.py`, `Tests.py`) + `__init__.py`, register in `.features.json`. | `do` |
| `modify <domain/Feature> "prompt"` | Amend the feature's spec.md via LLM + append a `CONTRACT AMENDMENT` section; branch `modify/<Feature>`. Implicit mode (no target): uses the file currently open in nvim. Creates the feature dir if missing (no controller). | `do` |
| `do [Feature]` | Run the backend agent: LLM implements spec.md, QA gates validate, pytest must pass; then stage + commit (`feat: <Name>`) + push the branch (**not merged**). On `main` with clean slate, auto-creates the feature branch. | `merge` |
| `delete [Feature]` | `rm -rf` the feature dir, unregister from `.features.json`, delete local branch(es). Remote branch untouched. | `scan` / `new` |
| `merge` | On a feature branch: commit staged work, merge branch into `main`, delete local + remote branch. | `scan` / `new` |
| `undo` | On a feature branch: `fetch` + `checkout main` + `reset --hard` + `clean -fd`, delete local + remote branch. | `new` / `scan` |
| `move <OldDomain/OldFeature> <NewDomain/NewFeature>` | Rename dir, re-register in `.features.json`, run `uv run pytest tests/`, rename branch if checked out, commit `move: ...`, push (**not merged**). | `merge` |
| `scan` | Discover feature slices under `features_dir` by structure, list grouped by domain. | `new` / `modify` |
| `qa` | Run every feature's Tests.py via pytest + rules-based code standards audit. **No LLM**. | `scan` / `new` |

Flow per feature: `new` → `do` → `merge`. `modify` slots in before `do`.
`delete` / `undo` discard work. `qa` is a standalone audit — run anytime.

## Feature model

- `.features.json` (repo root, or discovered nested): `features_dir` (default
  `features`), `known_features` (name → domain), `domain_keywords`
  (keyword → [domain, action]), optional `canonical_files` (custom slice
  manifest, e.g. `["Schema.py", "Worker.py", "Tests.py"]`), optional
  per-domain overrides under `domains.<domain>.canonical_files`, and
  optional per-`apps` override configs.
- Domains are subdirectories of `features_dir`; a feature is
  `features/<domain>/<Feature>/`.
- Branches are named `<domain>/<Feature>` (or `modify/<Feature>`), mirroring
  the feature path. `check_branch` auto-creates them from `main` when the tree
  is clean; `guard_open_branches` refuses to start work while other open
  branches exist.
- Owned by `agents/feature.py` (`ProjectFeatures`, `register_target`,
  `unregister_feature`, `load_project`). Single implementation — nothing else
  reads/writes `.features.json`.

## Rules engine

`agents/rules/<lang>.json` (this repo root, i.e. `.agents/rules/...` from a target project) holds declarative checks per language
(currently `python.json`). The engine in `agents/rules.py` classifies
files by extension, loads the matching rule set, and executes checks
(`line`, `text-required`, `balanced`, `py-ast` kinds). Both consumers use
this single registry:

- `orch.py qa` — audits every `.py` in the repo + feature slices.
- `runner.py` — `validate_code_standards` (group `standards`) and `validate_code_structure` (group `structure`).

To add a check, edit `agents/rules/python.json` (or create a new
language file). No code changes required.

## Code-generation pipeline (`do`)

`launcher.run_runner("backend", feature_dir, task, no_controller=...)` spawns
`runner.py` as a subprocess with the `backend_agent.md` persona:

1. Read spec.md + task; collect existing files in the feature dir.
2. Few-shot prompt: built from working features in the same domain.
3. LLM (via `agents/llm.py`) returns code; extracted and written (supports
   both full-file rewrites and targeted `SEARCH/REPLACE` patch blocks; auxiliary
   modules and pre-existing files preserved).
4. QA gates: code standards, unused imports, AGENTS.md constitution (11
   rules), root-file checks, PEP8, truncation, structure, canonical files
   (`Schema.py`, `Handler.py`, `Controller.py` unless `--no-controller`, `Tests.py`);
   then `pytest` on the feature's tests inside the retry loop.
5. On failure of any gate or test, loop re-runs with the error output
   (`auto_backend`), enabling targeted single-file or patch repairs before
   exhausting attempts. If 3 automated attempts exhaust, an interactive fallback
   allows the developer to supply guidance hints (`r`), re-verify manual edits (`v`),
   or quit (`q`).

Only when all gates and tests pass does `do` commit (`feat: <Name>`, staging both
the feature directory and `.features.json`) and push.

## Model selection

`agents/llm.py::llm_complete(prompt, system, model, timeout=300,
max_attempts=0)` shells out to the **`pi` CLI** (`pi -p --mode json --model <m>`)
and extracts the final assistant text from the JSONL event stream. Attempt
order = `[requested model]` (or `default_model()` from `model_config.json`)
**then** dynamically discovered live free models, deduplicated, capped at
`max_attempts`. Each model gets exactly one attempt — no repeats; on error /
empty response / raw tool-call markers it falls through to the next model.

`model_chain.json` (inside `agents/`) is an optional pin list. By default, live
provider discovery is the source of truth.

## Supporting files

| File | Role |
|---|---|
| `agents/orch.py` | CLI: arg parsing, `--model` persistence, dispatch. |
| `agents/commands.py` | All command handlers + parsing (`domain/Feature`, known prefixes). |
| `agents/feature.py` | Feature resolution: `.features.json`, domains, targets, branch-name inference. |
| `agents/scaffold.py` | `scaffold_new_feature` (spec + 4 templates), `init_new_project` (folder + symlink + baseline config). |
| `agents/specs.py` | Spec generation/QA (`rewrite_spec_with_ai`, `amend_spec`, `_qa_spec`, `_validate_spec`). |
| `agents/git_ops.py` | Every git operation — commands.py never shells out to git itself. |
| `agents/launcher.py` | Spawns `runner.py` with a persona. |
| `agents/llm.py` | `pi`-based completions with the model chain (sole owner of model selection). |
| `agents/prompts.py` | Prompt resolution incl. current-file detection via `nvim --headless`. |
| `agents/templates.py` | Code + spec templates, default overview. |
| `agents/config.py` | Paths: `REPO_ROOT`, `AGENTS_DIR`, `PERSONAS_DIR`, `MODEL_CONFIG`; `load_persona(name)`. |
| `agents/rules.py` | Rules engine — executes `agents/rules/python.json` checks. |
| `agents/runner.py` | Backend subprocess engine (never run by hand). |
| `agents/rules/python.json` | Declarative Python checks (line/text/ast/balanced kinds). |
| `agents/personas/*.md` | `backend_agent.md` (used by `do`), `spec_qa_agent.md` (loaded via `load_persona`). |

## Tests

The orchestrator is managed with **uv** (no `.venv`, no pip). From this repo root:

```bash
uv run pytest agents/tests -q   # single suite: commands, feature, git, llm, templates, runner, rules
```