"""Code harness for the shared runner in loadtest/runner.py — same
deterministic/mocked discipline as tests/test_gemini_vertex.py. This does
NOT run real experiments; it just proves the runner's own plumbing (record
shape, error classification, concurrency bound, JSONL round-trip) is
correct before it's trusted with real quota.
"""

import asyncio
import json

from google.genai import errors as genai_errors

from llm import GeminiVertex
from loadtest.runner import classify_error, run_batch, run_one, write_jsonl
from loadtest.workload import WorkloadItem
from tests.conftest import make_fake_client, make_response


ITEM = WorkloadItem(category="test category", prompt="test prompt?", candidate_brands=("Acme",))


async def test_run_one_happy_path_produces_a_complete_record():
    client = make_fake_client(make_response(
        text="Acme is great.", prompt_tokens=6, candidates_tokens=2, thoughts_tokens=21,
    ))
    provider = GeminiVertex(client=client, thinking_budget=5, max_output_tokens=100)

    record = await run_one(
        provider, "test category", "test prompt?", temperature=0.7,
        concurrency_level=3, environment="container", attempt_number=1,
    )

    assert record.error_class is None
    assert record.answer == "Acme is great."
    assert record.input_tokens == 6
    assert record.thinking_tokens == 21
    assert record.visible_output_tokens == 2
    assert record.total_tokens == 6 + 23  # input + (visible + thinking)
    assert record.concurrency_level == 3
    assert record.environment == "container"
    assert record.thinking_budget_setting == 5
    assert record.max_output_tokens_setting == 100
    assert record.temperature == 0.7


async def test_run_one_never_raises_on_provider_error():
    """A batch's whole point is observing failures — one bad request must
    not crash the run."""
    async def boom(*args, **kwargs):
        raise genai_errors.ClientError(code=429, response_json={}, response=None)

    client = make_fake_client(side_effect=boom)
    provider = GeminiVertex(client=client)

    record = await run_one(
        provider, "test category", "test prompt?", temperature=0.5,
        concurrency_level=1, environment="container",
    )

    assert record.error_class == "rate_limited"
    assert record.answer == ""


def test_classify_error_rate_limited_vs_server_error():
    rate_limited = genai_errors.ClientError(code=429, response_json={}, response=None)
    server_error = genai_errors.ServerError(code=503, response_json={}, response=None)

    assert classify_error(rate_limited) == "rate_limited"
    assert classify_error(server_error) == "server_error_503"


def test_classify_error_falls_back_for_unknown_exceptions():
    assert classify_error(ValueError("weird")) == "unclassified_ValueError"


async def test_run_batch_respects_repeats_and_produces_one_record_each():
    client = make_fake_client(make_response())
    provider = GeminiVertex(client=client)

    records = await run_batch(
        provider, [ITEM], temperature=0.5, concurrency_level=2,
        environment="container", repeats=5,
    )

    assert len(records) == 5
    assert all(r.error_class is None for r in records)


async def test_run_batch_bounds_concurrency():
    """Every request must observe at most `concurrency_level` in flight —
    verified by an in-flight counter inside the fake call, not by timing
    (timing-based concurrency assertions are flaky)."""
    in_flight = 0
    max_observed = 0
    lock = asyncio.Lock()

    async def tracked_call(*args, **kwargs):
        nonlocal in_flight, max_observed
        async with lock:
            in_flight += 1
            max_observed = max(max_observed, in_flight)
        await asyncio.sleep(0.01)
        async with lock:
            in_flight -= 1
        return make_response()

    client = make_fake_client(side_effect=tracked_call)
    provider = GeminiVertex(client=client)

    items = [ITEM] * 10
    await run_batch(provider, items, temperature=0.5, concurrency_level=3, environment="container")

    assert max_observed <= 3


async def test_write_jsonl_round_trips(tmp_path):
    provider = GeminiVertex(client=make_fake_client(make_response()))
    record = await run_one(provider, "cat", "prompt?", 0.5, 1, "container")

    path = tmp_path / "results.jsonl"
    write_jsonl([record], path, append=False)

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["category"] == "cat"
    assert parsed["error_class"] is None


def test_write_jsonl_appends_by_default(tmp_path):
    path = tmp_path / "results.jsonl"
    record_kwargs = dict(
        timestamp="t", environment="container", concurrency_level=1,
        latency_ms=1.0, input_tokens=1, visible_output_tokens=1,
        thinking_tokens=0, total_tokens=2, finish_reason="STOP",
        error_class=None, attempt_number=1, thinking_budget_setting=None,
        max_output_tokens_setting=None, retry_attempts_setting=5,
        temperature=0.5, category="c", prompt="p", answer="a",
    )
    from loadtest.runner import RequestRecord

    write_jsonl([RequestRecord(**record_kwargs)], path, append=False)
    write_jsonl([RequestRecord(**record_kwargs)], path, append=True)

    assert len(path.read_text().strip().splitlines()) == 2
