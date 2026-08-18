"""The code harness for GeminiVertex — deterministic, mocked, answers "does
the plumbing work," never "is the model's output good." See PLAN.md's
Phase 2.5/3 recheck for why that split matters and where the eval/load
harnesses (a different kind of tool) live instead.
"""

from unittest.mock import patch

import pytest
from google.genai import types

from llm import GeminiVertex, LLM
from tests.conftest import make_fake_client, make_response


# --- response mapping -------------------------------------------------

async def test_happy_path_maps_all_fields():
    """The full mapping, using the exact 6/2/21/29 shape from the live smoke
    test recorded in PLAN.md — not an arbitrary made-up example."""
    client = make_fake_client(make_response(
        text="Hi.",
        prompt_tokens=6,
        candidates_tokens=2,
        thoughts_tokens=21,
        total_tokens=29,
        finish_reason=types.FinishReason.STOP,
        model_version="gemini-2.5-flash-002",
    ))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.answer == "Hi."
    assert response.input_tokens == 6
    assert response.finish_reason == "STOP"
    assert response.model == "gemini-2.5-flash-002"
    assert response.latency_ms is not None and response.latency_ms >= 0


async def test_thinking_tokens_fold_into_output_tokens():
    """The regression test PLAN.md calls for: thinking tokens must be
    counted, not silently dropped, or cost accounting undercounts on every
    single call. 2 (visible) + 21 (thinking) = 23, not 2."""
    client = make_fake_client(make_response(candidates_tokens=2, thoughts_tokens=21))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.output_tokens == 23


async def test_thinking_tokens_also_exposed_as_a_breakdown():
    """The eval/load harnesses need visible-vs-thinking separated for
    analysis (PLAN.md's JSONL schema) — output_tokens stays the honest
    total, thinking_tokens is the breakdown on top, never a second source
    of truth that could drift out of sync."""
    client = make_fake_client(make_response(candidates_tokens=2, thoughts_tokens=21))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.thinking_tokens == 21
    assert response.output_tokens - response.thinking_tokens == 2  # visible portion


async def test_visible_output_tokens_property_subtracts_thinking():
    """SimpleResponse.visible_output_tokens is the length-of-`answer`
    convenience — callers who want "how long was the visible answer"
    shouldn't have to know to subtract thinking_tokens from output_tokens
    themselves. See llm.py's SimpleResponse docstring for why output_tokens
    itself can't mean this for a reasoning model."""
    client = make_fake_client(make_response(candidates_tokens=2, thoughts_tokens=21))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.visible_output_tokens == 2


def test_visible_output_tokens_equals_output_tokens_with_no_thinking():
    """For a provider/response with no thinking concept (thinking_tokens is
    None), visible_output_tokens must equal output_tokens exactly — this is
    what keeps Together's existing meaning of output_tokens unchanged."""
    response = LLM.SimpleResponse(answer="hi", input_tokens=5, output_tokens=10)
    assert response.visible_output_tokens == 10


@pytest.mark.parametrize(
    "sdk_reason,expected",
    [
        (types.FinishReason.STOP, "STOP"),
        (types.FinishReason.SAFETY, "SAFETY"),
        (types.FinishReason.RECITATION, "RECITATION"),
        (types.FinishReason.MAX_TOKENS, "MAX_TOKENS"),
    ],
)
async def test_finish_reason_mapped_distinctly(sdk_reason, expected):
    """finish_reason must pass through as a distinct, checkable value per
    PLAN.md's eval-harness plan — SAFETY/RECITATION are dropped-sample
    signals, not just "some error happened.\""""
    client = make_fake_client(make_response(finish_reason=sdk_reason))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.finish_reason == expected


async def test_safety_block_with_no_visible_text_does_not_crash():
    """A prompt-level safety block can come back with no candidates at all,
    or a candidate with no text — this is the "dropped sample" case from
    PLAN.md's eval harness. Mapping it must not raise; answer should be an
    empty string, not None, matching SimpleResponse's `answer: str` contract."""
    client = make_fake_client(make_response(text="", finish_reason=types.FinishReason.SAFETY))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.answer == ""
    assert response.finish_reason == "SAFETY"


async def test_no_candidates_at_all_does_not_crash():
    """The more extreme case: candidates is None entirely (blocked before
    generation started)."""
    client = make_fake_client(make_response(include_candidate=False))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.answer == ""
    assert response.finish_reason is None


async def test_missing_usage_metadata_does_not_crash():
    """usage_metadata is Optional on the real type — defend against it being
    absent rather than assuming it's always present."""
    client = make_fake_client(make_response(include_usage=False))
    provider = GeminiVertex(client=client)

    response = await provider.ask_generic_question("system", "question", 0.5)

    assert response.input_tokens == 0
    assert response.output_tokens == 0


# --- thinking_budget / max_output_tokens (constructor config, not per-call) --

async def test_thinking_budget_omitted_by_default():
    """Unset means "let the model use its own default" — must not send a
    literal 0 or some other asserted value we didn't intend."""
    client = make_fake_client()
    provider = GeminiVertex(client=client)

    await provider.ask_generic_question("system", "question", 0.5)

    _, kwargs = client.aio.models.generate_content.call_args
    assert kwargs["config"].thinking_config is None


async def test_thinking_budget_threaded_through_when_set():
    client = make_fake_client()
    provider = GeminiVertex(client=client, thinking_budget=0)  # 0 = disabled

    await provider.ask_generic_question("system", "question", 0.5)

    _, kwargs = client.aio.models.generate_content.call_args
    assert kwargs["config"].thinking_config.thinking_budget == 0


async def test_max_output_tokens_threaded_through_when_set():
    client = make_fake_client()
    provider = GeminiVertex(client=client, max_output_tokens=16)

    await provider.ask_generic_question("system", "question", 0.5)

    _, kwargs = client.aio.models.generate_content.call_args
    assert kwargs["config"].max_output_tokens == 16


def test_thinking_budget_and_max_output_tokens_exposed_for_logging():
    """The eval harness's JSONL needs these per PLAN.md's raw-data schema
    (thinking_budget_setting, max_output_tokens_setting) — must be readable
    without reaching into private state."""
    provider = GeminiVertex(client=make_fake_client(), thinking_budget=5, max_output_tokens=200)
    assert provider.thinking_budget == 5
    assert provider.max_output_tokens == 200


def test_thinking_budget_and_max_output_tokens_default_to_none():
    provider = GeminiVertex(client=make_fake_client())
    assert provider.thinking_budget is None
    assert provider.max_output_tokens is None


# --- config / fail-fast -------------------------------------------------

def test_missing_project_raises_at_construction(monkeypatch):
    """Fail fast at construction, not three calls later with a confusing
    auth error — matches the fail-fast decision in PLAN.md's Phase 2.5."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(ValueError, match="project"):
        GeminiVertex()


def test_model_defaults_to_gemini_2_5_flash(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "irrelevant")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    with patch("llm.gemini_vertex.genai.Client"):
        provider = GeminiVertex()
    assert provider.model == "gemini-2.5-flash"


def test_model_is_configurable_not_hardcoded(monkeypatch):
    """The requirement from this session: robust for testing against
    additional models without touching provider code."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "irrelevant")
    with patch("llm.gemini_vertex.genai.Client"):
        provider = GeminiVertex(model="gemini-2.5-pro")
    assert provider.model == "gemini-2.5-pro"


def test_parallelism_returns_configured_concurrency(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "irrelevant")
    with patch("llm.gemini_vertex.genai.Client"):
        provider = GeminiVertex(concurrency=42)
    assert provider.parallelism() == 42


def test_parallelism_has_a_conservative_default(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "irrelevant")
    monkeypatch.delenv("GEMINI_CONCURRENCY", raising=False)
    with patch("llm.gemini_vertex.genai.Client"):
        provider = GeminiVertex()
    assert provider.parallelism() > 0


def test_concurrency_zero_is_respected_not_treated_as_unset(monkeypatch):
    """Regression test: `concurrency or int(os.getenv(...))` would silently
    discard an explicit concurrency=0 because 0 is falsy in Python. Must use
    an `is not None` check instead, same as the other config knobs."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "irrelevant")
    monkeypatch.setenv("GEMINI_CONCURRENCY", "99")  # would win under `or`
    with patch("llm.gemini_vertex.genai.Client"):
        provider = GeminiVertex(concurrency=0)
    assert provider.parallelism() == 0


def test_thinking_budget_env_var_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_BUDGET", "256")
    provider = GeminiVertex(client=make_fake_client())
    assert provider.thinking_budget == 256


def test_thinking_budget_explicit_arg_overrides_env_var(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_BUDGET", "256")
    provider = GeminiVertex(client=make_fake_client(), thinking_budget=0)
    assert provider.thinking_budget == 0


# --- retry configuration -------------------------------------------------

def test_retry_options_configured_on_client(monkeypatch):
    """Not testing tenacity's internals — that's the SDK's job, verified
    against its source in PLAN.md. This only checks that GeminiVertex
    actually wires HttpRetryOptions through, since the SDK defaults to zero
    retries otherwise (also verified in PLAN.md's Phase 1)."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    with patch("llm.gemini_vertex.genai.Client") as mock_client_cls:
        GeminiVertex(retry_attempts=7)

    _, kwargs = mock_client_cls.call_args
    assert kwargs["vertexai"] is True
    assert kwargs["project"] == "evertune-tests"
    assert kwargs["location"] == "us-central1"
    assert kwargs["http_options"].retry_options.attempts == 7


def test_retry_attempts_env_var_fallback(monkeypatch):
    """Matches the GEMINI_MODEL/GEMINI_CONCURRENCY convention — retry_attempts
    shouldn't be the one config knob with no env-var path."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "3")

    with patch("llm.gemini_vertex.genai.Client") as mock_client_cls:
        GeminiVertex()

    _, kwargs = mock_client_cls.call_args
    assert kwargs["http_options"].retry_options.attempts == 3


def test_retry_attempts_explicit_arg_overrides_env_var(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "evertune-tests")
    monkeypatch.setenv("GEMINI_RETRY_ATTEMPTS", "3")

    with patch("llm.gemini_vertex.genai.Client") as mock_client_cls:
        GeminiVertex(retry_attempts=9)

    _, kwargs = mock_client_cls.call_args
    assert kwargs["http_options"].retry_options.attempts == 9


def test_injected_client_skips_project_requirement(monkeypatch):
    """The client= injection point exists for exactly this: tests (and the
    load harness, which may share one client across provider instances)
    shouldn't need a real GCP project to construct a GeminiVertex."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    client = make_fake_client()

    provider = GeminiVertex(client=client)  # must not raise

    assert provider.parallelism() > 0
