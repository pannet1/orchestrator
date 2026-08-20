from __future__ import annotations

import io
import json
import os

import pytest
from unittest.mock import patch

from _orchestrator.llm import FREE_MODEL_CHAIN, _model_chain, default_model, llm_complete


def _ndjson(text: str) -> str:
    ev = {"type": "message_end", "message": {"content": [{"type": "text", "text": text}]}}
    return json.dumps(ev) + "\n"


class FakePopen:
    def __init__(self, stdout_text: str = "", stderr_text: str = "", returncode: int = 0):
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self.returncode = returncode
        self._polled = False
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        if self._polled:
            return self.returncode
        self._polled = True
        return None

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _fake_popen(by_model: dict[str, str], error_models: set[str] | None = None):
    calls: list[str] = []
    error_models = error_models or set()

    def fake_popen(cmd: list[str], **kwargs: object) -> FakePopen:
        model = cmd[cmd.index("--model") + 1]
        calls.append(model)
        if model in error_models:
            err_ev = {
                "type": "auto_retry_start",
                "attempt": 1,
                "maxAttempts": 10,
                "delayMs": 30000,
                "errorMessage": "429 Error from provider: Rate limit exceeded",
            }
            return FakePopen(stdout_text=json.dumps(err_ev) + "\n", returncode=0)
        text = by_model.get(model, "")
        return FakePopen(stdout_text=_ndjson(text), returncode=0)

    return fake_popen, calls


class TestModelChain:

    def test_explicit_free_model_leads_chain(self) -> None:
        chain = _model_chain("opencode/deepseek-v4-flash-free", 3)
        assert chain[0] == "opencode/deepseek-v4-flash-free"
        assert len(chain) == 3

    def test_non_free_model_leads_chain(self) -> None:
        chain = _model_chain("claude-sonnet-4-5", 3)
        assert chain[0] == "claude-sonnet-4-5"
        assert len(chain) == 3

    def test_chain_capped_at_limit(self) -> None:
        assert len(_model_chain("", 2)) == 2
        assert len(_model_chain("", 5)) == 5
        assert len(_model_chain("", 10)) == 6  # full chain of 6

    def test_llama_swap_is_last_in_chain(self) -> None:
        """llama-swap coding model must be the last fallback — local model last."""
        chain = _model_chain("", 10)
        assert chain[-1] == "llama-swap/qwen2.5-coder-7b-instruct"
        assert len(chain) == 6

    def test_all_models_have_provider_prefix(self) -> None:
        """Every entry in the chain must use the provider/model format."""
        chain = _model_chain("", 10)
        for entry in chain:
            assert "/" in entry, f"Model '{entry}' missing provider prefix"


class TestLlmCompleteModelFallback:

    def test_failure_advances_to_next_model(self) -> None:
        fake_popen, calls = _fake_popen({"openrouter/poolside/laguna-s-2.1:free": "actual content"})
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen):
            result = llm_complete("prompt", system="sys", model="opencode/nemotron-3-ultra-free", max_attempts=3)
        assert result == "actual content"
        assert calls[0] == "opencode/nemotron-3-ultra-free"
        assert calls[1] == "openrouter/poolside/laguna-s-2.1:free"

    def test_empty_model_not_retried(self) -> None:
        fake_popen, calls = _fake_popen({})
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen):
            result = llm_complete("prompt", system="sys", model="opencode/nemotron-3-ultra-free", max_attempts=3)
        assert result is None
        assert calls == ["opencode/nemotron-3-ultra-free", "openrouter/poolside/laguna-s-2.1:free", "openrouter/cohere/north-mini-code:free"]

    def test_auto_retry_breaks_inner_loop_and_advances(self) -> None:
        fake_popen, calls = _fake_popen(
            {"openrouter/poolside/laguna-s-2.1:free": "recovered output"},
            error_models={"opencode/nemotron-3-ultra-free"},
        )
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen):
            result = llm_complete("prompt", system="sys", model="opencode/nemotron-3-ultra-free", max_attempts=3)
        assert result == "recovered output"
        assert calls[0] == "opencode/nemotron-3-ultra-free"
        assert calls[1] == "openrouter/poolside/laguna-s-2.1:free"

    def test_rate_limit_error_advances_to_next_model(self) -> None:
        def fake_popen_rl(cmd: list[str], **kwargs: object) -> FakePopen:
            model = cmd[cmd.index("--model") + 1]
            if model == "opencode/nemotron-3-ultra-free":
                return FakePopen(stderr_text="429 Rate limit exceeded\n", returncode=1)
            return FakePopen(stdout_text=_ndjson("success after rate limit"), returncode=0)

        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen_rl):
            result = llm_complete("prompt", system="sys", model="opencode/nemotron-3-ultra-free", max_attempts=3)
        assert result == "success after rate limit"

    def test_all_models_retried_once(self) -> None:
        """Every model in the chain gets exactly one attempt, never repeated."""
        fake_popen, calls = _fake_popen({"llama-swap/qwen2.5-coder-7b-instruct": "local fallback wins"})
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen):
            result = llm_complete("prompt", system="sys", max_attempts=6)
        assert result == "local fallback wins"
        # No duplicates — each model tried exactly once
        assert len(calls) == len(set(calls)), f"Duplicate models in call sequence: {calls}"
        # Full chain order (includes default_model if set)
        assert calls == _model_chain(default_model(), 6)
        # llama-swap is always the last resort
        assert calls[-1] == "llama-swap/qwen2.5-coder-7b-instruct"


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_MODEL_TESTS"),
    reason="set RUN_LIVE_MODEL_TESTS=1 to probe a model via the live pi transport",
)
class TestModelWorks:
    """Live smoke test: confirm a single model actually returns text through
    `pi`. Default target is the lead OpenRouter model; override with
    MODEL_UNDER_TEST. Needs `pi` on PATH + network/credits."""

    def test_model_returns_text(self) -> None:
        model = os.environ.get("MODEL_UNDER_TEST", FREE_MODEL_CHAIN[0])
        out = llm_complete(
            "Reply with the single word: OK",
            model=model,
            timeout=90,
            max_attempts=1,
        )
        assert out and "OK" in out, f"model not working: {model} -> {out!r}"
