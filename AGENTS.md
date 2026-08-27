# Orchestrator — `orch.py`

> This is the **documentation an AI agent should read first**. It explains what
> the tool is, how it is wired into a project, every command, and the rules it
> enforces. The human-facing overview lives in `README.md`; this file is the
> authoritative, agent-oriented reference and is loaded automatically by pi/agents.

## What this is

A single-file CLI agent, `orch.py`, that decomposes feature work into a
spec + code, then implements it with an LLM sub-agent and a git
**branch-per-feature** workflow.

- Entry point: `agents/orch.py` (this repo's `agents/` dir).
- All command logic lives in `agents/_orchestrator/commands.py::dispatch()`.
- LLM work is done by shelling out to the **`pi` binary** (`pi -p --mode json`)
  — not a direct API call. The model chain/config is in `agents/model_chain.json` /
  `agents/model_config.json`.
- Git is **never** shelled out by `commands.py` directly; every git operation
  goes through `agents/_orchestrator/git_ops.py`.

## Mental model (read this before doing anything)

```
target project/
├── .agents -> agents/      <- symlink created by `init` (this repo's agents/)
├── .features.json                         <- feature registry (created by `new`)
└── features/                              <- default features_dir
    └── <domain>/<Feature>/
        ├── spec.md        (the contract)
        ├── Schema.py
        ├── Handler.py
        ├── Controller.py  (skipped with --no-controller)
        ├── Tests.py
        └── __init__.py
```

- A **feature** is `features/<domain>/<Feature>/`. A **branch** mirrors that
  path: `<domain>/<Feature>` (or `modify/<Feature>`).
- `REPO_ROOT = Path.cwd()`. The orchestrator operates on **whatever directory
  it is run from** (the target project), not on this repo. This repo is the
  *tool*; the project it orchestrates is the *target*.
- `AGENTS_DIR` = this repo's `agents/` dir (the parent of `_orchestrator/`).
  `model_config.json`, `model_chain.json`, `personas/`, and `rules/` all live
  here and are shared by every target project.
- There is **no `.features.json` in this repo** — it is created inside each
  target project by `new`.

## Creating a new project (the `init` step)

`init` **creates a folder + a `.agents` symlink** (→ this repo's `agents/`)
and `cd`s into it. It does **not** create `.features.json` and **ignores any
prompt**. There is no per-project git repo — the new project lives inside
this single git repository, so there is nothing to manage.

```bash
# from anywhere
python /path/to/orchestrator/orch.py init <path>/<project-name>

# or, inside an existing target project that already has .agents/:
./.agents/orch.py init <path>/<project-name>
```

- Target parsing: `domain/action`. A leading `/` is treated as absolute;
  otherwise the folder is created relative to cwd.
- It symlinks `<project>/.agents` → this orchestrator root, so the tool is then
  reachable as `./.agents/orch.py` from inside the new project.
- Next step it prints: `cd <project> && ./.agents/orch.py new <domain/Feature> "prompt"`

### Canonical new-project flow

```bash
./.agents/orch.py init ~/projects/MyApp
cd ~/projects/MyApp
./.agents/orch.py new Payments "auction payment wallet flow"   # scaffold
./.agents/orch.py do Payments                                   # implement
./.agents/orch.py merge                                         # merge to main
```

## Commands

| Command | Target | What it does | Next |
|---|---|---|---|
| `init <path>/<name>` | required | Create folder + `.agents` symlink → `agents/`. Ignores prompt. No `.features.json` yet. | `new` |
| `new <domain/Feature> "prompt"` | required | Create feature branch, write `spec.md` (LLM-generated, template fallback), scaffold the 4 code files + `__init__.py`, register in `.features.json`. | `do` |
| `modify <domain/Feature> "prompt"` | optional | Amend an existing spec: rewrite spec via LLM, append a `CONTRACT AMENDMENT` section, branch `modify/<Feature>`. Implicit mode (no target) uses the file open in `nvim`. | `do` |
| `do [Feature]` | optional | Run the backend sub-agent: implement `spec.md`, pass QA gates + `pytest`, then `feat:` commit + push the branch (**not merged**). Inferred from current branch if no name. | `merge` |
| `delete [Feature]` | optional | `rm -rf` feature dir, unregister from `.features.json`, delete local + remote branch(es). | `scan`/`new` |
| `merge` | none (uses current branch) | Commit, push branch, merge into `main`, push `main`, delete branch locally + remotely. | `scan`/`new` |
| `undo` | none (uses current branch) | `fetch` + `checkout main` + `reset --hard origin/main` + `clean -fd`, then delete local + remote branch. | `new`/`scan` |
| `move <OldDomain/OldFeature> <NewDomain/NewFeature>` | required x2 | Rename dir, re-register, run `uv run pytest tests/`, rename branch if checked out, `move:` commit + push (not merged). | `merge` |
| `scan` | none | Discover feature slices by directory structure, list grouped by domain. | `new`/`modify` |
| `qa` | none | Run every feature's `Tests.py` via pytest + rules-based standards audit. **No LLM.** | `scan`/`new` |

Global flags (parsed in `orch.py`):

| Flag | Effect |
|---|---|
| `--prompt` / `-p <path-or-text>` | Prompt file (`.md` read from disk) or inline string. Positional text after the target also works. |
| `--model` / `-m <id>` | Persist `{"model": <id>}` to `model_config.json`, then run with that model leading the chain. |
| `--no-controller` | Skip `Controller.py` generation (background workers). |
| `--app` / `-a <app>` | App context (e.g. `-a private` resolves against `features/` instead of `web/features/`). Auto-selected from the domain when the project defines `apps`. |
| `--max-attempts <n>` | Max LLM attempts across the model chain before giving up (default `6`). |

**Flow per feature:** `new` → `do` → `merge`. `modify` slots in before `do`.
`delete`/`undo` discard work. `qa` is a standalone audit.

## The `do` code-generation pipeline

`launcher.run_runner("backend", feature_dir, task)` spawns `runner.py` as a
subprocess with the `backend_agent.md` persona. Inside the loop:

1. Read `spec.md` + task; collect existing files in the feature dir.
2. LLM (`_orchestrator/llm.py`) returns code; extracted (prefers JSON, falls
   back to markdown `### file` fences) and written, with pre-existing
   non-canonical files protected.
3. QA gates (all must pass): structure, code standards (from
   `rules/python/core.json`), the 11-rule **constitution**, PEP8
   (E302/E501), no truncation, and **all 4 files present**.
4. `pytest` on the feature's `Tests.py` must pass.
5. On failure, the loop re-runs with the error output, up to 3 internal
   attempts; if still failing, `run_runner` returns failure and `do` tells the
   user to paste the output back to the AI.

Only when all gates pass does `do` commit (`feat: <Name>`) and push the branch
— **it never merges**.

## Model selection

- `_orchestrator/llm.py::llm_complete(prompt, system, model, timeout=300,
  max_attempts=6)` runs `pi -p --mode json --model <m>` and extracts the
  final assistant text from the JSONL event stream.
- Attempt order = `[requested model]` (or `default_model()` from
  `model_config.json`) **then** the chain in `model_chain.json`, deduplicated,
  capped at `max_attempts`.
- **Each model gets exactly one attempt** — no repeats. On error / empty
  response / raw tool-call markers, it falls through to the next model.
- `model_chain.json` (this repo root) is the editable, most-capable-first
  free-tier chain. Edit it to reorder without touching code. Fallback is the
  hardcoded `DEFAULT_MODEL_CHAIN`.
- `--model` (orch.py) writes `model_config.json` so a paid model leads the
  chain for subsequent runs.

Current `model_chain.json`:

```json
[
  "openrouter/poolside/laguna-s-2.1:free",
  "openrouter/cohere/north-mini-code:free",
  "opencode/nemotron-3-ultra-free",
  "opencode/deepseek-v4-flash-free",
  "opencode/laguna-s-2.1-free",
  "llama-swap/qwen2.5-coder-7b-instruct"
]
```

## Rules engine & Constitution (QA gates)

`rules.py` classifies files by extension and runs declarative checks from
`rules/python/core.json` (groups: `standards`, `structure`). Used by both
`orch.py qa` and `runner.py`. To add a check, edit that JSON — no code change.

`runner.validate_constitution` enforces these 11 rules (the project
"constitution") on generated code:

1. `.python-version` must exist.
2. `pyproject.toml` must not use poetry/pdm.
3. No forbidden package-manager files (`requirements.txt`, `Pipfile*`,
   `poetry.lock`, `environment.yml`, `setup.py`, `setup.cfg`).
4. Use the project's designated time library (detected from existing code;
   default `pendulum`); no `arrow`/`python-dateutil`/`delorean`/`maya`/
   `udatetime`/`pytz` instead.
5. Use `logging.getLogger` (per-file).
6. **Zero comments** in code (per-file).
7. No hardcoded secrets (`password`/`secret`/`token`/`api_key`/`access_token`
   with string values; test fixtures exempt).
8. No emojis (per-line).
9. Type annotations on return values (per-file).
10. A `Tests.py` with unit tests must exist (structure group).
11. All 4 canonical files (`Schema.py`, `Handler.py`, `Controller.py`,
    `Tests.py`) present.

## Running this repo's own tests

The orchestrator is managed with **uv** (no `.venv`, no pip). From this repo
root:

```bash
uv run pytest _orchestrator/tests -q
```

This runs one suite: commands, feature, git, llm, templates, runner, rules.

## Gotchas for agents

- Run `orch.py` **from inside the target project** (or pass a resolved path);
  `REPO_ROOT = cwd` determines where `.features.json` and `features/` live.
- After `init`, the new project has a `.agents` symlink but **no**
  `.features.json` — that appears only after the first `new`.
- `merge`/`undo` take **no target**; they act on the current git branch. Don't
  pass one or the command aborts.
- `do` commits + pushes but **does not merge**. Merging is a separate `merge`
  step (and `merge` deletes the branch).
- `guard_open_branches` blocks starting new work while other non-`main`
  branches exist — merge or `undo` them first.
- `init` ignores its prompt. Don't try to seed a feature through `init`.
- The LLM transport is the **`pi` CLI**, not a direct API. It needs `pi` on
  PATH and a configured provider; `llm_complete` returns `None` if `pi` is
  missing.

## File reference

| Path | Role |
|---|---|
| `orch.py` | CLI: arg parsing, `--model` persistence, `dispatch()`. |
| `_orchestrator/commands.py` | All command handlers + `domain/Feature` parsing. |
| `_orchestrator/feature.py` | Feature resolution: `.features.json`, domains, targets, branch inference. Single owner of the registry. |
| `_orchestrator/scaffold.py` | `scaffold_new_feature` (spec + 4 templates), `init_new_project` (folder + symlink). |
| `_orchestrator/specs.py` | Spec generation/QA (`rewrite_spec_with_ai`, `amend_spec`, `_qa_spec`). |
| `_orchestrator/git_ops.py` | **Every** git operation. `commands.py` never shells out to git itself. |
| `_orchestrator/launcher.py` | Spawns `runner.py` with a persona. |
| `_orchestrator/llm.py` | `pi`-based completions with the model chain. Sole owner of model selection. |
| `_orchestrator/prompts.py` | Prompt resolution incl. current-file detection via `nvim --headless`. |
| `_orchestrator/templates.py` | Code + spec templates, default overview. |
| `_orchestrator/config.py` | Paths: `REPO_ROOT`, `AGENTS_DIR`, `PERSONAS_DIR`, `MODEL_CONFIG`; `load_persona()`. |
| `_orchestrator/rules.py` | Rules engine — executes `rules/python/core.json` checks. |
| `_orchestrator/runner.py` | Backend subprocess engine (never run by hand). |
| `model_chain.json` | Editable free-tier model fallback chain (most capable first). |
| `model_config.json` | `{"model": "..."}` persisted by `--model`. |
| `personas/*.md` | `backend_agent.md` (used by `do`), `spec_qa_agent.md` (loaded via `load_persona`). |
| `rules/python/core.json` | Declarative Python checks (line/text/ast/balanced kinds). |
