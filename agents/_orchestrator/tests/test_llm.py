from __future__ import annotations

import io
import json
import os

import pytest
from unittest.mock import patch

from _orchestrator.llm import (
    LOCAL_FALLBACK,
    PROVIDER_PREFERENCE,
    _is_free,
    _model_chain,
    default_model,
    free_model_ids,
    llm_complete,
    query_provider_models,
)

# A representative live catalogue (mirrors `pi --list-models` output).
FAKE_LIST_MODELS = "\n".join([
    "provider     model                                               context  max-out  thinking  images",
    "llama-swap   qwen2.5-coder-7b-instruct                           8.2K     8.2K     no        no",
    "opencode     nemotron-3-ultra-free                               1M       128K     yes       no",
    "opencode     claude-opus-4-5                                     200K     64K      yes       yes",
    "opencode     laguna-s-2.1-free                                   1M       32K      yes       no",
    "openrouter   cohere/north-mini-code:free                         256K     64K      yes       no",
    "openrouter   poolside/laguna-s-2.1:free                         262.1K   32.8K    yes       no",
    "openrouter   gpt-5.2                                            400K     128K     yes       yes",
])

FAKE_FREE = [
    "openrouter/poolside/laguna-s-2.1:free",
    "openrouter/cohere/north-mini-code:free",
    "opencode/nemotron-3-ultra-free",
    "opencode/laguna-s-2.1-free",
    LOCAL_FALLBACK,
]


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
        # Read the per-attempt model (the LAST --model on the command line).
        last = len(cmd) - 1 - cmd[::-1].index("--model")
        model = cmd[last + 1]
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


class TestLiveDiscovery:

    def test_query_provider_models_parses_table(self) -> None:
        fake_result = type("R", (), {"stdout": FAKE_LIST_MODELS, "returncode": 0})()
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("subprocess.run", return_value=fake_result):
            ids = query_provider_models(force=True)
        assert "opencode/nemotron-3-ultra-free" in ids
        # openrouter model ids include the nested slug
        assert "openrouter/cohere/north-mini-code:free" in ids
        assert "llama-swap/qwen2.5-coder-7b-instruct" in ids
        # non-free models are present too (filtering happens later)
        assert "opencode/claude-opus-4-5" in ids

    def test_is_free_classification(self) -> None:
        assert _is_free("opencode/nemotron-3-ultra-free")
        assert _is_free("openrouter/cohere/north-mini-code:free")
        assert _is_free("openrouter/free")
        assert not _is_free("opencode/claude-opus-4-5")
        assert not _is_free("openrouter/gpt-5.2")
        assert not _is_free(LOCAL_FALLBACK)

    def test_free_model_ids_orders_by_provider_and_appends_local(self) -> None:
        fake_result = type("R", (), {"stdout": FAKE_LIST_MODELS, "returncode": 0})()
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("subprocess.run", return_value=fake_result):
            free = free_model_ids(force=True)
        # opencode (preferred) before openrouter
        assert free.index("opencode/nemotron-3-ultra-free") < free.index("openrouter/cohere/north-mini-code:free")
        # local fallback always last
        assert free[-1] == LOCAL_FALLBACK
        # non-free models excluded
        assert "opencode/claude-opus-4-5" not in free

    def test_free_model_ids_falls_back_when_discovery_empty(self) -> None:
        fake_result = type("R", (), {"stdout": "", "returncode": 0})()
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("subprocess.run", return_value=fake_result):
            free = free_model_ids(force=True)
        assert free[-1] == LOCAL_FALLBACK
        assert len(free) >= 1


class TestModelChain:

    @pytest.fixture(autouse=True)
    def _patch_free(self):
        with patch("_orchestrator.llm.free_model_ids", return_value=list(FAKE_FREE)):
            yield

    def test_explicit_free_model_leads_chain(self) -> None:
        chain = _model_chain("opencode/nemotron-3-ultra-free", 3)
        assert chain[0] == "opencode/nemotron-3-ultra-free"
        assert len(chain) == 3

    def test_non_free_model_leads_chain(self) -> None:
        chain = _model_chain("claude-sonnet-4-5", 3)
        assert chain[0] == "claude-sonnet-4-5"
        assert len(chain) == 3

    def test_chain_capped_at_limit(self) -> None:
        assert len(_model_chain("", 2)) == 2
        assert len(_model_chain("", 5)) == 5

    def test_zero_limit_tries_all_discovered(self) -> None:
        # max_attempts=0 means "try every discovered free model"
        assert len(_model_chain("", 0)) == len(FAKE_FREE)

    def test_llama_swap_is_last_in_chain(self) -> None:
        """llama-swap coding model must be the last fallback — local model last."""
        chain = _model_chain("", 0)
        assert chain[-1] == LOCAL_FALLBACK

    def test_all_models_have_provider_prefix(self) -> None:
        """Every entry in the chain must use the provider/model format."""
        chain = _model_chain("", 0)
        for entry in chain:
            assert "/" in entry, f"Model '{entry}' missing provider prefix"

    def test_provider_preference_reflected(self) -> None:
        assert PROVIDER_PREFERENCE[0] == "opencode"
        assert PROVIDER_PREFERENCE[-1] == "llama-swap"


class TestLlmCompleteModelFallback:

    @pytest.fixture(autouse=True)
    def _patch_free(self):
        with patch("_orchestrator.llm.free_model_ids", return_value=list(FAKE_FREE)):
            yield

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
            last = len(cmd) - 1 - cmd[::-1].index("--model")
            model = cmd[last + 1]
            if model == "opencode/nemotron-3-ultra-free":
                return FakePopen(stderr_text="429 Rate limit exceeded\n", returncode=1)
            return FakePopen(stdout_text=_ndjson("success after rate limit"), returncode=0)

        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen_rl):
            result = llm_complete("prompt", system="sys", model="opencode/nemotron-3-ultra-free", max_attempts=3)
        assert result == "success after rate limit"

    def test_all_models_retried_once(self) -> None:
        """Every model in the chain gets exactly one attempt, never repeated."""
        fake_popen, calls = _fake_popen({LOCAL_FALLBACK: "local fallback wins"})
        with patch("_orchestrator.llm._pi_binary", return_value="pi"), \
                patch("_orchestrator.llm.subprocess.Popen", fake_popen):
            result = llm_complete("prompt", system="sys", max_attempts=0)
        assert result == "local fallback wins"
        # No duplicates — each model tried exactly once
        assert len(calls) == len(set(calls)), f"Duplicate models in call sequence: {calls}"
        # Full discovered chain (includes default_model if set)
        assert calls == _model_chain(default_model(), 0)
        # llama-swap is always the last resort
        assert calls[-1] == LOCAL_FALLBACK


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_MODEL_TESTS"),
    reason="set RUN_LIVE_MODEL_TESTS=1 to probe a model via the live pi transport",
)
class TestModelWorks:
    """Live smoke test: confirm a single model actually returns text through
    `pi`. Default target is the first discovered free model; override with
    MODEL_UNDER_TEST. Needs `pi` on PATH + network/credits."""

    def test_model_returns_text(self) -> None:
        model = os.environ.get("MODEL_UNDER_TEST") or free_model_ids(force=True)[0]
        out = llm_complete(
            "Reply with the single word: OK",
            model=model,
            timeout=90,
            max_attempts=1,
        )
        assert out and "OK" in out, f"model not working: {model} -> {out!r}"
