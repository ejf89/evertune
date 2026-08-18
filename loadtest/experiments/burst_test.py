"""Experiment 5 (added in the 2026-08-17 follow-up pass — originally tiered
"optional" in PLAN.md, promoted once the four must-run experiments had
already landed and there was runway to be more thorough about the load
half specifically).

The concurrency sweep (Experiment 3) escalates *gradually* — level 1, then
5, then 10, ... — so by the time it reaches a high level, the client/
connection pool is already warm from the lower levels that ran just before
it. That's not what a real traffic spike looks like: idle, then suddenly
high concurrency with no ramp. A token-bucket rate limiter, a connection
pool that needs to open new connections, or a cold TLS handshake could all
behave differently under a sudden spike than under a gradual climb to the
exact same concurrency level — the sweep can't tell those apart because it
never tests a cold spike.

Design: two idle -> spike cycles at the concurrency sweep's own ceiling
level (read the same way retry_amplification.py does — from the sweep's
own committed results, not a separately guessed number), separated by an
idle gap and each using a *fresh* GeminiVertex/client instance. Comparing
cycle 1 (genuinely cold — first thing this process does) to cycle 2 (fired
after an idle gap, but the process/interpreter/DNS cache etc. are now
warm) isolates whether a cold start specifically looks worse than a later
burst of the same shape.

No new field was added to the shared RequestRecord schema for cycle
attribution — record order is preserved by asyncio.gather within
run_batch, and each cycle writes exactly len(WORKLOAD)*REPEATS records
before the next cycle starts, so cycle index = record index // chunk size.
loadtest/analyze.py's analyze_burst_test() relies on that ordering
guarantee; keep CYCLES/REPEATS here and analyze.py's copies in sync if you
change either.

Run from repo root: python -m loadtest.experiments.burst_test
"""

import asyncio
import os
from pathlib import Path

from llm import GeminiVertex
from loadtest.runner import max_recorded_concurrency, percentile, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/burst_test.jsonl"
CONCURRENCY_RESULTS_PATH = Path("loadtest/results/concurrency_sweep.jsonl")
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
REPEATS = 3
FALLBACK_LEVEL = 50
IDLE_SECONDS = 30
CYCLES = 2


def burst_level() -> int:
    return max_recorded_concurrency(CONCURRENCY_RESULTS_PATH, FALLBACK_LEVEL, "burst_test")


async def main():
    level = burst_level()
    print(f"[burst_test] spike level={level} (from concurrency_sweep's own ceiling), "
          f"{CYCLES} idle->spike cycles, {IDLE_SECONDS}s idle gap")

    all_records = []
    for cycle in range(1, CYCLES + 1):
        print(f"[burst_test] cycle {cycle}: idling {IDLE_SECONDS}s before spike")
        await asyncio.sleep(IDLE_SECONDS)

        # Fresh provider (and thus a fresh genai.Client) per cycle — cycle 1
        # is then a genuine cold start, not a warmed-up connection pool
        # reused from setup.
        provider = GeminiVertex(retry_attempts=1)
        print(f"[burst_test] cycle {cycle}: firing spike, concurrency={level}")
        records = await run_batch(
            provider,
            list(WORKLOAD),
            temperature=TEMPERATURE,
            concurrency_level=level,
            environment=ENVIRONMENT,
            repeats=REPEATS,
        )
        errors = [r for r in records if r.error_class]
        latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)
        p50 = percentile(latencies, 0.50)
        p95 = percentile(latencies, 0.95)
        msg = f"  cycle {cycle}: {len(records)} requests, {len(errors)} errors"
        if p50 is not None:
            msg += f", p50={p50:.0f}ms, p95={p95:.0f}ms"
        print(msg)
        if errors:
            print(f"  error classes: {sorted(set(r.error_class for r in errors))}")
        all_records.extend(records)

    write_jsonl(all_records, RESULTS_PATH, append=False)
    print(f"[burst_test] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
