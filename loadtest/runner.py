"""The shared request-runner + JSONL writer used by both the eval and load
harnesses (PLAN.md's Phase 3). One code path, one schema — so eval and load
experiments can never silently drift into incompatible data shapes.

Every request goes through run_one(), which never raises: failures are
captured as a record with error_class set, not an exception that kills the
batch. A load/eval run's whole point is observing failures, not crashing on
the first one.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.genai import errors as genai_errors

from llm import LLM
from loadtest.workload import WorkloadItem

DEFAULT_SYSTEM_PROMPT = "Answer concisely and directly."


@dataclass
class RequestRecord:
    """One row per request. Field set matches PLAN.md's "Raw data" schema
    exactly — if you need a new field, add it there first, then here."""

    timestamp: str
    environment: str  # "host" | "container" — see PLAN.md's Docker consistency rule
    concurrency_level: int
    latency_ms: Optional[float]
    input_tokens: int
    visible_output_tokens: int
    thinking_tokens: int
    total_tokens: int
    finish_reason: Optional[str]
    error_class: Optional[str]
    # Which repeat this is among N identical calls in a batch (e.g. the
    # variance experiment's N=100) — NOT the SDK's internal retry attempts,
    # which are opaque to us (tenacity retries inside the SDK and we only
    # ever see the final outcome or the final exception).
    attempt_number: int
    thinking_budget_setting: Optional[int]
    max_output_tokens_setting: Optional[int]
    retry_attempts_setting: Optional[int]
    temperature: float
    category: str
    prompt: str
    answer: str


def classify_error(exc: Exception) -> str:
    """Coarse classification for the failure-mode catalogue (PLAN.md's Load
    harness section). Deliberately not exhaustive — refined against real
    errors as the actual runs surface them, not invented ahead of data."""
    if isinstance(exc, genai_errors.ClientError):
        if exc.code == 429:
            return "rate_limited"
        return f"client_error_{exc.code}"
    if isinstance(exc, genai_errors.ServerError):
        return f"server_error_{exc.code}"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    return f"unclassified_{type(exc).__name__}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_one(
    provider: LLM,
    category: str,
    prompt: str,
    temperature: float,
    concurrency_level: int,
    environment: str,
    attempt_number: int = 1,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> RequestRecord:
    """Runs exactly one request. Always returns a RequestRecord — never
    raises — so a batch of these can be safely gathered without one failure
    losing the rest of the run's data."""
    thinking_budget = getattr(provider, "thinking_budget", None)
    max_output_tokens = getattr(provider, "max_output_tokens", None)
    retry_attempts = getattr(provider, "retry_attempts", None)
    start = asyncio.get_event_loop().time()

    try:
        response = await provider.ask_generic_question(system_prompt, prompt, temperature)
        thinking_tokens = response.thinking_tokens or 0
        return RequestRecord(
            timestamp=_now_iso(),
            environment=environment,
            concurrency_level=concurrency_level,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            visible_output_tokens=response.visible_output_tokens,
            thinking_tokens=thinking_tokens,
            total_tokens=response.input_tokens + response.output_tokens,
            finish_reason=response.finish_reason,
            error_class=None,
            attempt_number=attempt_number,
            thinking_budget_setting=thinking_budget,
            max_output_tokens_setting=max_output_tokens,
            retry_attempts_setting=retry_attempts,
            temperature=temperature,
            category=category,
            prompt=prompt,
            answer=response.answer,
        )
    except Exception as exc:
        elapsed_ms = (asyncio.get_event_loop().time() - start) * 1000
        return RequestRecord(
            timestamp=_now_iso(),
            environment=environment,
            concurrency_level=concurrency_level,
            latency_ms=elapsed_ms,
            input_tokens=0,
            visible_output_tokens=0,
            thinking_tokens=0,
            total_tokens=0,
            finish_reason=None,
            error_class=classify_error(exc),
            attempt_number=attempt_number,
            thinking_budget_setting=thinking_budget,
            max_output_tokens_setting=max_output_tokens,
            retry_attempts_setting=retry_attempts,
            temperature=temperature,
            category=category,
            prompt=prompt,
            answer="",
        )


async def run_batch(
    provider: LLM,
    items: list[WorkloadItem],
    temperature: float,
    concurrency_level: int,
    environment: str,
    repeats: int = 1,
) -> list[RequestRecord]:
    """Runs every item in `items`, each repeated `repeats` times, with at
    most `concurrency_level` requests in flight at once.

    Concurrency here is an explicit parameter, not provider.parallelism() —
    the load harness's whole job is sweeping this value to find where things
    break; parallelism() is what a normal caller would default to, not a
    ceiling an experiment measuring the ceiling should be capped by.
    """
    semaphore = asyncio.Semaphore(concurrency_level)

    async def bounded(item: WorkloadItem, attempt_number: int) -> RequestRecord:
        async with semaphore:
            return await run_one(
                provider,
                item.category,
                item.prompt,
                temperature,
                concurrency_level,
                environment,
                attempt_number,
            )

    tasks = [
        bounded(item, attempt)
        for item in items
        for attempt in range(1, repeats + 1)
    ]
    return await asyncio.gather(*tasks)


def percentile(sorted_values, pct):
    """Shared by analyze.py and the concurrency/burst/escalation experiment
    scripts — was independently defined identically in all four places
    before being consolidated here (caught in a redundancy pass)."""
    if not sorted_values:
        return None
    idx = min(int(len(sorted_values) * pct), len(sorted_values) - 1)
    return sorted_values[idx]


def max_recorded_concurrency(results_path: Path, fallback: int, script_label: str) -> int:
    """Reads a prior experiment's own committed JSONL and returns the
    highest concurrency_level it recorded — used by experiments that
    deliberately target "the measured ceiling" rather than a separately
    guessed number (retry_amplification.py, burst_test.py). Falls back to
    `fallback` with a printed warning if that file doesn't exist yet.

    Was independently defined twice (near_ceiling_level() / burst_level())
    before being consolidated here — the two were identical except for
    the script name in the warning message and the fallback constant.
    """
    results_path = Path(results_path)
    if not results_path.exists():
        print(f"[{script_label}] WARNING: {results_path} not found — "
              f"run concurrency_sweep.py first. Falling back to concurrency={fallback}.")
        return fallback
    levels = set()
    with open(results_path) as f:
        for line in f:
            levels.add(json.loads(line)["concurrency_level"])
    return max(levels) if levels else fallback


def write_jsonl(records: list[RequestRecord], path: Path, append: bool = True) -> None:
    """Commits records to disk — this is what makes FINDINGS.md's numbers
    evidence rather than claims (PLAN.md's "Raw data" section). append=True
    by default so re-running part of an experiment doesn't destroy prior
    data; pass append=False deliberately to start a fresh file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode) as f:
        for record in records:
            f.write(json.dumps(asdict(record)) + "\n")
