# Orchestrator (`orch.py`)

The orchestrator is a single CLI entry point, `agents/orch.py`. Projects get a
`.agents` symlink to this directory, so the tool is invoked as:

```
./.agents/orch.py <action> <domain/Feature> [inline prompt] [options]
```

Every command routes through `_orchestrator/commands.py::dispatch()`. There are
no other entry points — `runner.py` is an internal subprocess (never run by
hand), and the old standalone `scaffolder.py` / `scaffold_project.py` scripts
have been deleted.

## Options

| Flag | Effect |
|---|---|
| `--prompt` / `-p <path-or-text>` | Prompt file (`.md` read from disk) or inline string. Positional text after the target is also accepted. |
| `--model` / `-m <id>` | Persist `{"model": <id>}` to `agents/model_config.json`, then continue the run with that model. Paid models (e.g. `claude-sonnet-4-5`) lead the fallback chain; free models are absorbed into the chain order. |
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
| `init <path>/<project-name>` | Create project folder + `.agents` symlink, chdir into it. Does **not** create `.features.json`. Prompt arg ignored. | `new` |
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
  (keyword → [domain, action]), optional per-`apps` override configs.
- Domains are subdirectories of `features_dir`; a feature is
  `features/<domain>/<Feature>/`.
- Branches are named `<domain>/<Feature>` (or `modify/<Feature>`), mirroring
  the feature path. `check_branch` auto-creates them from `main` when the tree
  is clean; `guard_open_branches` refuses to start work while other open
  branches exist.
- Owned by `_orchestrator/feature.py` (`ProjectFeatures`, `register_target`,
  `unregister_feature`, `load_project`). Single implementation — nothing else
  reads/writes `.features.json`.

## Rules engine

`agents/rules/<lang>/core.json` holds declarative checks per language
(currently `python/`). The engine in `_orchestrator/rules.py` classifies
files by extension, loads the matching rule set, and executes checks
(`line`, `text-required`, `balanced`, `py-ast` kinds). Both consumers use
this single registry:

- `orch.py qa` — audits every `.py` in the repo + feature slices.
- `runner.py` — `validate_code_standards` (group `standards`) and `validate_code_structure` (group `structure`).

To add a check, edit `agents/rules/python/core.json` (or create a new
language dir). No code changes required.

## Code-generation pipeline (`do`)

`launcher.run_runner("backend", feature_dir, task)` spawns
`runner.py` as a subprocess with the `backend_agent.md` persona:

1. Read spec.md + task; collect existing files in the feature dir.
2. Few-shot prompt: built from working features in the same domain
   (`FEW_SHOT_COUNT = 2`).
3. LLM (via `_orchestrator/llm.py`) returns code; extracted and written,
   protected files preserved.
4. QA gates: code standards, unused imports, AGENTS.md constitution (11
   rules), root-file checks, `.features.json` sync, PEP8, truncation,
   structure; then `pytest` on the feature's tests.
5. On failure, loop re-runs with the error output (`auto_backend`), exhausting
   attempts before returning failure. `do` then reports and tells the user to
   paste the output back to the AI.

Only when all gates pass does `do` commit and push.

## Model selection

`_orchestrator/llm.py::llm_complete(prompt, system, model, timeout=300,
max_attempts=4)`. One attempt per model — no repeats:

- Free fallback chain (most capable first):
  `nemotron-3-ultra-free` → `deepseek-v4-flash-free` →
  `nemotron-3.5-lightning-free`.
- Explicit paid `--model` leads the chain ahead of the free tier; a `-free`
  config/override value is absorbed into the chain order.
- With a `system` prompt, the reply must begin with a per-call `[VERIFY_…]`
  token; failure advances to the next model. All free models exhausted →
  `None`.

## Supporting files

| File | Role |
|---|---|
| `orch.py` | CLI: arg parsing, `--model` persistence, dispatch. |
| `_orchestrator/commands.py` | All command handlers + parsing (`domain/Feature`, known prefixes). |
| `_orchestrator/feature.py` | Feature resolution: `.features.json`, domains, targets, branch-name inference. |
| `_orchestrator/scaffold.py` | `scaffold_new_feature` (spec + 4 templates), `init_new_project` (folder + symlink). |
| `_orchestrator/specs.py` | Spec generation/QA (`rewrite_spec_with_ai`, `amend_spec`, `_qa_spec`, `_validate_spec`). |
| `_orchestrator/git_ops.py` | Every git operation — commands.py never shells out to git itself. |
| `_orchestrator/launcher.py` | Spawns `runner.py` with a persona. |
| `_orchestrator/llm.py` | Zen-model completions with fallback chain (sole owner of model selection). |
| `_orchestrator/prompts.py` | Prompt resolution incl. current-file detection via `nvim --headless`. |
| `_orchestrator/templates.py` | Code + spec templates, default overview. |
| `_orchestrator/config.py` | Paths: `REPO_ROOT`, `AGENTS_DIR`, `PERSONAS_DIR`, `MODEL_CONFIG`; `load_persona(name)`. |
| `_orchestrator/rules.py` | Rules engine — executes `agents/rules/<lang>/core.json` checks. |
| `_orchestrator/runner.py` | Backend subprocess engine (never run by hand). |
| `agents/rules/python/core.json` | Declarative Python checks (line/text/ast/balanced kinds). |
| `personas/*.md` | `backend_agent.md` (used by `do`), `spec_qa_agent.md` (loaded via `load_persona`). |

## Tests

```
cd agents
.venv/bin/python -m pytest _orchestrator/tests -q   # single suite: commands, feature, git, llm, templates, runner, rules (182)
```