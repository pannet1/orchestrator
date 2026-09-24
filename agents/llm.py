from __future__ import annotations

import io
import json
import select
import shutil
import subprocess
import sys
import time

from .config import AGENTS_DIR, MODEL_CONFIG, REPO_ROOT

TOOL_CALL_MARKERS = ("<tool_calls>", "<invoke>")


# Path to the optional pin file (editable by the user).
MODEL_CHAIN_FILE = AGENTS_DIR / "model_chain.json"


# Provider preference order. Free models are tried in this provider order;
# the opencode family is exhausted first (its free catalogue rotates often and
# is the primary free provider), then openrouter, with the local llama-swap
# model always the last resort (no network needed).
PROVIDER_PREFERENCE: tuple[str, ...] = (
    "opencode",
    "opencode-go",
    "openrouter",
    "llama-swap",
)

# Local fallback model that should always be available (runs on the machine,
# no API/network). Kept as the guaranteed final attempt.
LOCAL_FALLBACK = "llama-swap/qwen2.5-coder-7b-instruct"

# Safety net used only when live model discovery fails entirely (e.g. `pi`
# missing or offline). Mirrors a last-known-good free chain.
DEFAULT_MODEL_CHAIN: tuple[str, ...] = (
    "openrouter/poolside/laguna-s-2.1:free",
    "openrouter/cohere/north-mini-code:free",
    "opencode/nemotron-3-ultra-free",
    "opencode/deepseek-v4-flash-free",
    "opencode/laguna-s-2.1-free",
    LOCAL_FALLBACK,
)

# Process-lifetime cache of the discovered model ids. Free-tier providers
# (notably opencode) rotate their model catalogue frequently, so the list is
# re-queried from each provider on every run instead of being hard-coded.
_model_cache: list[str] | None = None
_model_cache_time: float = 0.0
MODEL_CACHE_TTL = 300.0  # seconds — re-query providers at most every 5 minutes


def _load_pinned_models() -> list[str]:
    """Load optional pinned model IDs from agents/model_chain.json.

    These are tried FIRST (in file order) before the dynamically discovered
    free models, letting a user force a specific favourite. An empty list (or
    a missing/invalid file) means "use live provider discovery only" — there is
    no need to maintain the full free-model list by hand anymore.
    """
    if not MODEL_CHAIN_FILE.exists():
        return []
    try:
        data = json.loads(MODEL_CHAIN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list) and all(isinstance(x, str) for x in data):
        return data
    return []


def _pi_binary() -> str | None:
    return shutil.which("pi")


def query_provider_models(force: bool = False) -> list[str]:
    """Query `pi --list-models` and return all `provider/model` ids.

    This is the single source of truth for which models exist right now.
    Free-tier providers rotate models often, so we ask the live catalogue
    rather than trusting a stale hard-coded list. Results are cached for
    MODEL_CACHE_TTL seconds to avoid hammering the provider on every attempt.
    Returns [] on any failure (missing binary, network error, parse error).
    """
    global _model_cache, _model_cache_time
    now = time.time()
    if not force and _model_cache is not None and (now - _model_cache_time) < MODEL_CACHE_TTL:
        return _model_cache

    omp = _pi_binary()
    if omp is None:
        return _model_cache or []

    try:
        result = subprocess.run(
            [omp, "--list-models"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30, check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return _model_cache or []

    ids: list[str] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "provider":  # table header
            continue
        provider, model = parts[0], parts[1]
        ids.append(f"{provider}/{model}")
    _model_cache = ids
    _model_cache_time = now
    return ids


def refresh_models() -> None:
    """Drop the model cache so the next call re-queries every provider."""
    global _model_cache, _model_cache_time
    _model_cache = None
    _model_cache_time = 0.0


def _is_free(model_id: str) -> bool:
    """A model is free when its name carries a `-free` or `:free` suffix
    (e.g. `opencode/nemotron-3-ultra-free`, `openrouter/cohere/...:free`),
    or is the openrouter `free` aggregate."""
    name = model_id.split("/", 1)[1] if "/" in model_id else model_id
    return name.endswith("-free") or ":free" in name or name == "free"


def free_model_ids(force: bool = False) -> list[str]:
    """Return the currently-available free models, ordered by PROVIDER_PREFERENCE.

    Discovery is live, so newly rotated-in free models are picked up
    automatically and rotated-out ones are dropped. The local llama-swap model
    is always appended as the final fallback. Falls back to
    DEFAULT_MODEL_CHAIN when discovery yields nothing.
    """
    all_ids = query_provider_models(force=force)
    if not all_ids:
        return list(DEFAULT_MODEL_CHAIN)

    free = [m for m in all_ids if _is_free(m)]

    def sort_key(mid: str) -> tuple[int, int]:
        provider = mid.split("/", 1)[0]
        try:
            pidx = PROVIDER_PREFERENCE.index(provider)
        except ValueError:
            pidx = len(PROVIDER_PREFERENCE)
        return (pidx, all_ids.index(mid))

    free.sort(key=sort_key)

    # Optional manual pinning: try these first, in file order.
    pinned = _load_pinned_models()
    ordered: list[str] = []
    for p in pinned:
        if p not in ordered:
            ordered.append(p)
    for m in free:
        if m not in ordered:
            ordered.append(m)

    # Always keep the local fallback available as the last resort.
    if LOCAL_FALLBACK not in ordered:
        ordered.append(LOCAL_FALLBACK)
    return ordered


def default_model() -> str:
    if not MODEL_CONFIG.exists():
        return ""
    try:
        cfg = json.loads(MODEL_CONFIG.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    return cfg.get("model", "")


def _model_chain(model: str, limit: int = 0) -> list[str]:
    """Attempt order: an explicitly requested `model` (or configured default)
    leads, then every live free model discovered from the providers, deduped.
    `limit` caps the chain (0 / negative = try all discovered free models).
    Free-tier values are NOT absorbed — the requested model always leads,
    matching the interactive session's behavior."""
    chain: list[str] = []
    if model:
        chain.append(model)
    for candidate in free_model_ids():
        if candidate not in chain:
            chain.append(candidate)
        if limit and len(chain) >= limit:
            break
    return chain[:limit] if limit and limit > 0 else chain


def _extract_text(ndjson: str) -> str | None:
    """Pull the final assistant text out of the pi `--mode json` event stream."""
    text: list[str] = []
    for line in ndjson.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "message_end":
            content = ev.get("message", {}).get("content", [])
            text = [c.get("text", "") for c in content if c.get("type") == "text" and c.get("text")]
        elif ev.get("type") == "agent_end":
            messages = ev.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    t = [c.get("text", "") for c in content if c.get("type") == "text" and c.get("text")]
                    if t:
                        text = t
                        break
    if text:
        return "\n".join(text).strip()
    return None


def _stop_reason(ndjson: str) -> str:
    reason = ""
    for line in ndjson.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "message_end":
            r = ev.get("message", {}).get("stopReason")
            if r:
                reason = r
    return f" (stopReason={reason})" if reason else ""


def _detect_error(line: str, is_stderr: bool = False) -> tuple[bool, str]:
    """Check if an output line (from stdout event or stderr) indicates an error or rate limit."""
    line_str = line.strip()
    if not line_str:
        return False, ""

    try:
        ev = json.loads(line_str)
        if isinstance(ev, dict):
            ev_type = ev.get("type", "")
            if ev_type == "auto_retry_start":
                err_msg = ev.get("errorMessage") or "auto retry initiated"
                return True, f"auto_retry ({err_msg})"
            if ev_type == "error":
                err_msg = ev.get("error") or ev.get("message") or "error event"
                return True, f"error event ({err_msg})"
            if ev.get("errorMessage"):
                return True, f"errorMessage ({ev.get('errorMessage')})"
            if ev_type == "message_end":
                stop_reason = ev.get("message", {}).get("stopReason", "")
                if stop_reason in ("error", "abort"):
                    return True, f"stopReason={stop_reason}"
            # Standard pi JSON event without error fields; do not run text heuristics on event payloads
            return False, ""
    except (json.JSONDecodeError, ValueError):
        pass

    if is_stderr:
        lower = line_str.lower()
        if "429" in line_str or "rate limit" in lower or "ratelimit" in lower:
            return True, f"rate limit: {line_str[:120]}"
        if "error from provider" in lower:
            return True, f"provider error: {line_str[:120]}"
        if "insufficient_quota" in lower or "quota exceeded" in lower:
            return True, "quota exceeded"
        if "unauthorized" in lower or "authentication error" in lower:
            return True, "auth error"

    return False, ""


class PiStreamFormatter:
    """Pretty-prints pi JSON events and streaming progress to stderr."""

    def __init__(self, attempt: int, total_attempts: int, model: str):
        self.attempt = attempt
        self.total_attempts = total_attempts
        self.model = model
        self.in_text = False
        self.is_thinking = False
        self.seen_tools: set[str] = set()

    def on_attempt_start(self) -> None:
        print(f"[LLM] Attempt {self.attempt}/{self.total_attempts} (model: {self.model})", file=sys.stderr)
        sys.stderr.flush()

    def format_stdout_event(self, line_str: str) -> None:
        try:
            ev = json.loads(line_str)
        except (json.JSONDecodeError, ValueError):
            if line_str and not line_str.startswith("{"):
                self._ensure_newline()
                print(f"[LLM] {line_str}", file=sys.stderr)
                sys.stderr.flush()
            return

        if not isinstance(ev, dict):
            return

        ev_type = ev.get("type", "")

        if ev_type == "message_update":
            ame = ev.get("assistantMessageEvent", {})
            ame_type = ame.get("type", "")

            if ame_type == "thinking_start":
                if not self.is_thinking:
                    self._ensure_newline()
                    print("[LLM] Thinking...", file=sys.stderr)
                    sys.stderr.flush()
                    self.is_thinking = True

            elif ame_type == "thinking_end":
                self.is_thinking = False

            elif ame_type == "text_start":
                self._ensure_newline()
                sys.stderr.write("[LLM] Output: ")
                sys.stderr.flush()
                self.in_text = True

            elif ame_type == "text_delta":
                delta = ame.get("delta", "")
                if delta:
                    if not self.in_text:
                        self._ensure_newline()
                        sys.stderr.write("[LLM] Output: ")
                        self.in_text = True
                    sys.stderr.write(delta)
                    sys.stderr.flush()

            elif ame_type == "text_end":
                if self.in_text:
                    sys.stderr.write("\n")
                    sys.stderr.flush()
                    self.in_text = False

            elif ame_type in ("tool_call_start", "tool_start"):
                tool_id = ame.get("id") or ame.get("toolCallId") or ""
                tool_name = ame.get("tool") or ame.get("name") or "tool"
                if not tool_id or tool_id not in self.seen_tools:
                    if tool_id:
                        self.seen_tools.add(tool_id)
                    self._ensure_newline()
                    print(f"[LLM] Tool: {tool_name}", file=sys.stderr)
                    sys.stderr.flush()

        elif ev_type in ("tool_start", "tool_execution_start", "tool_execution_update"):
            tool_id = ev.get("toolCallId") or ev.get("id") or ""
            tool = ev.get("toolName") or ev.get("tool") or "tool"
            # Only print tool invocation once per toolCallId
            if not tool_id or tool_id not in self.seen_tools:
                if tool_id:
                    self.seen_tools.add(tool_id)
                self._ensure_newline()
                args = ev.get("args", {})
                args_summary = ""
                if isinstance(args, dict):
                    if "path" in args:
                        args_summary = f"path={args['path']}"
                    elif "command" in args:
                        cmd_str = str(args["command"])
                        args_summary = f"cmd={cmd_str[:60]}"
                    elif "context" in args:
                        ctx = str(args["context"])
                        args_summary = f"{ctx[:60]}..."
                summary_str = f" ({args_summary})" if args_summary else ""
                print(f"[LLM] Tool: {tool}{summary_str}", file=sys.stderr)
                sys.stderr.flush()

        elif ev_type in ("tool_end", "tool_execution_end"):
            tool = ev.get("toolName") or ev.get("tool") or "tool"
            self._ensure_newline()
            print(f"[LLM] Tool done: {tool}", file=sys.stderr)
            sys.stderr.flush()

        elif ev_type == "auto_retry_start":
            self._ensure_newline()
            err = ev.get("errorMessage", "")
            print(f"[LLM] Retry triggered: {err[:120]}", file=sys.stderr)
            sys.stderr.flush()

        elif ev_type == "error":
            self._ensure_newline()
            err = ev.get("error") or ev.get("message") or "error"
            print(f"[LLM] Error: {err}", file=sys.stderr)
            sys.stderr.flush()

    def format_stderr_line(self, line_str: str) -> None:
        if line_str and not line_str.startswith("{"):
            self._ensure_newline()
            print(f"[LLM] (stderr) {line_str}", file=sys.stderr)
            sys.stderr.flush()

    def _ensure_newline(self) -> None:
        if self.in_text:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.in_text = False

    def finish(self) -> None:
        self._ensure_newline()


def llm_complete(prompt: str, system: str = "", model: str = "", timeout: int = 300, max_attempts: int = 0) -> str | None:
    """One-shot completion routed through the pi harness (`pi -p --mode json`).

    Mirrors the interactive TUI session as closely as possible: runs in the
    repo root, tools enabled (auto-approved, non-interactive), repo rules and
    skills loaded. The model fetches its own context with the read tool
    instead of receiving giant pasted prompts, and can write files / run
    tests itself.

    Returns the model's final text, or None when pi is unavailable or fails.
    The per-attempt model walks the live free-model list (discovered from each
    provider via `pi --list-models`, most-capable provider first): each model
    gets exactly one attempt — attempt 1 uses the requested model (or the
    configured default), then every currently-available free model, never
    repeating a model. Because the catalogue is queried live, rotated-in
    free models are tried automatically and rotated-out ones are skipped.

    `max_attempts` caps how many models are tried (0 = try all discovered
    free models). During execution, clean real-time progress is printed to
    stderr. If an error or auto-retry is encountered, execution breaks out of
    the attempt immediately and advances to the next model.
    """
    omp = _pi_binary()
    if omp is None:
        print("[LLM] pi binary not found on PATH — no pi model transport.", file=sys.stderr)
        return None

    # Single --model per invocation (the per-attempt model `m`).
    base_cmd = [omp, "-p", prompt, "--mode", "json"]
    if system:
        base_cmd += ["--system-prompt", system]

    chain = _model_chain(model or default_model(), max_attempts)
    total_models = len(chain)

    for i, m in enumerate(chain, 1):
        cmd = base_cmd + ["--model", m]
        formatter = PiStreamFormatter(attempt=i, total_attempts=total_models, model=m)
        formatter.on_attempt_start()

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(REPO_ROOT),
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        error_encountered = False
        error_reason = ""
        start_time = time.time()

        while proc.poll() is None:
            if time.time() - start_time > timeout + 10:
                error_encountered = True
                error_reason = f"timed out after {timeout}s"
                break

            streams = [s for s in (proc.stdout, proc.stderr) if s is not None]
            if not streams:
                break

            try:
                ready, _, _ = select.select(streams, [], [], 0.5)
            except (io.UnsupportedOperation, ValueError, TypeError, OSError):
                ready = streams

            if not ready:
                continue

            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                line_str = line.strip()
                if not line_str:
                    continue

                if stream == proc.stdout:
                    stdout_lines.append(line_str)
                    formatter.format_stdout_event(line_str)
                else:
                    stderr_lines.append(line_str)
                    formatter.format_stderr_line(line_str)

                is_err, reason = _detect_error(line_str, is_stderr=(stream != proc.stdout))
                if is_err:
                    error_encountered = True
                    error_reason = reason
                    break
            sys.stderr.flush()

            if error_encountered:
                break

        formatter.finish()

        if error_encountered:
            print(f"[LLM] Attempt {i} error detected ({error_reason}) — cancelling and trying next model", file=sys.stderr)
            sys.stderr.flush()
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            continue

        if proc.stdout:
            for line in proc.stdout.read().splitlines():
                line_str = line.strip()
                if line_str:
                    stdout_lines.append(line_str)
                    formatter.format_stdout_event(line_str)
                    is_err, reason = _detect_error(line_str, is_stderr=False)
                    if is_err:
                        error_encountered = True
                        error_reason = reason
        if proc.stderr:
            for line in proc.stderr.read().splitlines():
                line_str = line.strip()
                if line_str:
                    stderr_lines.append(line_str)
                    formatter.format_stderr_line(line_str)
                    is_err, reason = _detect_error(line_str, is_stderr=True)
                    if is_err:
                        error_encountered = True
                        error_reason = reason

        formatter.finish()
        proc.wait()
        sys.stderr.flush()

        if error_encountered:
            print(f"[LLM] Attempt {i} error detected ({error_reason}) — trying next model", file=sys.stderr)
            sys.stderr.flush()
            continue

        if proc.returncode != 0:
            tail = [l for l in stderr_lines if l][-3:]
            print(f"[LLM] Attempt {i} failed with code {proc.returncode} (model: {m}): {' | '.join(tail) or 'no stderr'}", file=sys.stderr)
            sys.stderr.flush()
            continue

        stdout_data = "\n".join(stdout_lines)
        result = _extract_text(stdout_data)
        if not result:
            print(f"[LLM] Attempt {i} returned no text (model: {m}{_stop_reason(stdout_data)}) — retrying", file=sys.stderr)
            sys.stderr.flush()
            continue

        if any(marker in result for marker in TOOL_CALL_MARKERS):
            print(f"[LLM] Attempt {i} response contains raw tool-call markers (model: {m}) — trying next model", file=sys.stderr)
            sys.stderr.flush()
            continue

        return result

    print("[LLM] No model produced usable text — giving up.", file=sys.stderr)
    sys.stderr.flush()
    return None


def generate_spec_with_ai(domain: str, action: str, prompt: str) -> str | None:
    root_spec = REPO_ROOT / "SPEC.md"
    arch_blueprint = root_spec.read_text() if root_spec.exists() else ""

    system_prompt = (
        "You are a spec writer for a software project. "
        "Generate a structured feature specification in markdown.\n\n"
        "Here is the project's architectural blueprint:\n"
        + arch_blueprint +
        "\n\nUse this exact format for the feature spec:\n"
        "  # <Action> — <Domain> Feature\n"
        "  ## Overview\n"
        "  <description>\n"
        "  ## Expected Files\n"
        "  * `Schema.py`: <data models, validation schemas, and types>\n"
        "  * `Handler.py`: <core business logic and workflow execution>\n"
        "  * `Controller.py`: <interface endpoints, routes, or dispatch entrypoints>\n"
        "  * `Tests.py`: <unit and integration test suite covering happy paths and edge cases>\n"
        "  ## Input / Output\n"
        "  | Direction | Format | Description |\n"
        "  |-----------|--------|-------------|\n"
        "  | Input | <...> | <...> |\n"
        "  | Output | <...> | <...> |\n"
        "  ## Business Logic Constraints\n"
        "  * <rules>\n"
        "  ## Error Cases\n"
        "  | Condition | Error | Message |\n"
        "  |-----------|-------|-------------|\n"
        "  | <when> | <type> | <message> |\n"
        "  ## Dependencies\n"
        "  * <libraries, config>\n"
        "  ## Code Standards\n"
        "  All code must use type annotations per PEP 484.\n\n"
        "Output ONLY the markdown spec — no preamble, no explanation."
    )
    return llm_complete(
        f"Feature: {action}\nDomain: {domain or '(none)'}\n\nDescription:\n{prompt}",
        system=system_prompt,
    )
