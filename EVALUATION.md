# Competitive Evaluation — `orchestrator`

*Audit date: 2026-11-08. Method: local code review (~8.3k LOC incl. tests) compared against live public projects via web research.*

## What it is

Single-CLI coding-agent harness (`agents/orch.py`, mutable via `.agents` symlink into target projects) that decomposes work into branch-per-feature vertical slices: LLM-written `spec.md` with its own QA pass, then a generate → static-gate → pytest retry loop before commit/push. Model chaining via live free-model discovery through a `pi` CLI subprocess, declarative JSON rules engine, 11-rule "constitution". Docs are unusually good (README + AGENTS.md + file map).

## Competitive landscape (as of Nov 2026)

| Competitor | Scale | Idea |
|---|---|---|
| [github/spec-kit](https://github.com/spec-kit) | ~148k★ | Spec-driven development toolkit — the de-facto SDD standard |
| [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec) | ~74k★ | Lightweight SDD for any assistant; closest rival to the scaffolding |
| [ruvnet/claude-flow](https://github.com/ruvnet/claude-flow) | ~81k★ | Multi-agent swarm orchestration on Claude Code, persistent memory, MCP |
| [bmad-code-org/BMAD-method](https://github.com/bmad-code-org/BMAD-method) | ~55k★ | Agile multi-agent role pipeline with story-file gates |
| [SuperClaude-Org/SuperClaude_Framework](https://github.com/SuperClaude-Org/SuperClaude_Framework) | ~24k★ | Personas + command framework on Claude Code |

## Where we win

- **Free-model chain with live discovery + one-attempt-per-model failover** — genuinely practical for zero-budget use; no listed rival has this.
- **`.features.json` / branch-mirrors-path slice registry** with `merge/undo/move/delete` lifecycle is a cleaner vertical-slice model than any rival's.
- **Non-LLM `qa` gate** — cheap, deterministic, honest quality bar before pytest.

## Where we lose

- Spec-kit / OpenSpec / BMAD have community, MCP hooks, multi-agent and editor integrations.
- claude-flow has real swarms; this repo has none (no parallelism, single in-process agent).
- It re-implements a coder agent in-process, which is the weaker bet in 2026 — the market now orchestrates Claude Code / Codex / Gemini CLI as workers.
- The 11-rule constitution encodes *pannet1's* conventions (pendulum, zero comments, uv-only), not a general tool.

## Verdict

**Keep private.** Publishing as-is invites "why not spec-kit?" — the core is drowned by 100k★ tooling. The publishable fragments are the model-chain and the feature/branch registry, e.g. as a spec-kit plugin or OpenSpec companion layer.

## Top improvements

1. Run **Claude Code / Codex / Gemini CLI as the worker** (`pi`-style subprocess) instead of reimplementing codegen — the gates + registry + retry loop then become a thin, uniquely valuable wrapper.
2. Make conventions **project-local**: move the constitution into the target project's config, and expose the rules engine as an MCP tool so any harness can call it.
