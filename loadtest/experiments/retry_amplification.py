"""Experiment 4 (must-run, top billing): retry amplification.

Runs the same near-ceiling concurrency (read from concurrency_sweep.jsonl's
own results, not a separately guessed number) with retries off vs. on, to
see whether the SDK's native retry helps or hurts effective throughput
right where the pressure actually is. See PLAN.md's "Retry amplification"
section for why this is framed as an experiment, not just a config choice.

Depends on concurrency_sweep.py having already run. Falls back to a
conservative default if it hasn't (with a clear warning) rather than
failing outright.

Run from repo root: python -m loadtest.experiments.retry_amplification
"""

import asyncio
import os
from pathlib import Path

from llm import GeminiVertex
from loadtest.runner import max_recorded_concurrency, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/retry_amplification.jsonl"
CONCURRENCY_RESULTS_PATH = Path("loadtest/results/concurrency_sweep.jsonl")
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
REPEATS = 3
FALLBACK_LEVEL = 25


def near_ceiling_level() -> int:
    return max_recorded_concurrency(CONCURRENCY_RESULTS_PATH, FALLBACK_LEVEL, "retry_amplification")


async def main():
    level = near_ceiling_level()
    print(f"[retry_amplification] testing at concurrency={level} "
          f"(from concurrency_sweep's own ceiling)")

    all_records = []
    for label, retry_attempts in [("retries_off", 1), ("retries_on", 5)]:
        provider = GeminiVertex(retry_attempts=retry_attempts)
        print(f"[retry_amplification] {label} (retry_attempts={retry_attempts})")
        records = await run_batch(
            provider,
            list(WORKLOAD),
            temperature=TEMPERATURE,
            concurrency_level=level,
            environment=ENVIRONMENT,
            repeats=REPEATS,
        )
        errors = [r for r in records if r.error_class]
        total_tokens = sum(r.total_tokens for r in records)
        wall_ms = sum(r.latency_ms or 0 for r in records)
        print(f"  {len(records)} requests, {len(errors)} errors, "
              f"{total_tokens} total tokens, {wall_ms:.0f}ms summed latency")
        all_records.extend(records)

    write_jsonl(all_records, RESULTS_PATH, append=False)
    print(f"[retry_amplification] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
