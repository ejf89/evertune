"""Experiment 3 (must-run, top billing): concurrency sweep.

**Revised 2026-08-17** (follow-up pass): the original run (levels
1/5/10/25/50, single n=12 pass per level) found zero errors and, by its own
admission in FINDINGS.md, produced p50/p95 numbers noisy enough to bounce
around non-monotonically — much more likely sampling noise from n=12 than a
real signal. Two changes were made at the time: repeated passes per level,
and levels extended past the old ceiling (75/100/150 added).

**Second revision, 2026-08-19** (external review caught a real bug): the
2026-08-17 fix used a FIXED `REPEATS=3` at every level. Since `run_batch`
creates exactly `len(WORKLOAD) * repeats` tasks behind a semaphore of size
`concurrency_level`, a fixed repeats count means the semaphore never binds
once its capacity exceeds the actual task count — at levels 50/75/100/150,
only 36 tasks ever existed, so the semaphore's 50-150 capacity was never
reached. Every level above 25 was silently re-testing the same ~36 real
concurrent requests. `escalation_test.py` (built the same day, later) got
this right — `repeats = ceil(level / len(WORKLOAD))` — but the fix was
never retrofitted here. This revision scales repeats per level so each one
is genuinely saturated, while keeping at least 2 full rounds through the
semaphore at every level (not just 1) so the original noise-reduction intent
of repeated passes is preserved, not undone by the saturation fix.

Blast-radius reasoning is unchanged from the original (evertune-tests has
multiple forks/PRs sharing quota) — this still stops at a stated, bounded
ceiling rather than escalating open-ended; 150 is ~3x the original stopping
point, a deliberate, moderate escalation, not "push until it breaks."

Retries are OFF for this experiment on purpose — retry behavior is
Experiment 4's job; keeping this one clean isolates "does concurrency alone
break it" from "does retrying make it better or worse."

Run from repo root: python -m loadtest.experiments.concurrency_sweep
"""

import asyncio
import math
import os

from llm import GeminiVertex
from loadtest.runner import percentile, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/concurrency_sweep.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
LEVELS = [1, 5, 10, 25, 50, 75, 100, 150]
MIN_REPEATS = 3  # floor for low levels, where 3x already comfortably saturates


async def main():
    all_records = []
    provider = GeminiVertex(retry_attempts=1)  # retries off — see module docstring

    for level in LEVELS:
        # At least 2 full rounds through a saturated semaphore, and never
        # fewer than MIN_REPEATS — see the "Second revision" note above for
        # why a fixed repeats count was wrong.
        repeats = max(MIN_REPEATS, math.ceil(level * 2 / len(WORKLOAD)))
        n_requests = repeats * len(WORKLOAD)
        print(f"[concurrency_sweep] level={level} repeats={repeats} (~{n_requests} requests)")
        records = await run_batch(
            provider,
            list(WORKLOAD),
            temperature=TEMPERATURE,
            concurrency_level=level,
            environment=ENVIRONMENT,
            repeats=repeats,
        )
        errors = [r for r in records if r.error_class]
        latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)
        p50 = percentile(latencies, 0.50)
        p95 = percentile(latencies, 0.95)
        msg = f"  {len(records)} requests, {len(errors)} errors"
        if p50 is not None:
            msg += f", p50={p50:.0f}ms, p95={p95:.0f}ms"
        print(msg)
        if errors:
            print(f"  error classes: {sorted(set(r.error_class for r in errors))}")
        all_records.extend(records)

    write_jsonl(all_records, RESULTS_PATH, append=False)
    print(f"[concurrency_sweep] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
