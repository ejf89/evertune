"""Experiment 3 (must-run, top billing): concurrency sweep.

**Revised 2026-08-17** (follow-up pass, see PLAN.md/EXECUTION.md): the
original run (levels 1/5/10/25/50, single n=12 pass per level) found zero
errors and, by its own admission in FINDINGS.md, produced p50/p95 numbers
noisy enough to bounce around non-monotonically — much more likely sampling
noise from n=12 than a real signal. Two changes fix that without changing
the experiment's intent:
  1. `REPEATS` repeated passes per level (not 1), so each level's p50/p95
     is drawn from a real distribution instead of 12 samples.
  2. Levels extended past the old ceiling (75/100/150 added) specifically
     to try to actually trigger errors/429s — "we didn't find a ceiling"
     is a weaker result than "we pushed further and still didn't," and the
     retry-amplification experiment (Experiment 4) needs a real ceiling to
     be a meaningful test of retries under pressure, not just a repeat of
     this experiment's own non-result.

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
import os

from llm import GeminiVertex
from loadtest.runner import percentile, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/concurrency_sweep.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
LEVELS = [1, 5, 10, 25, 50, 75, 100, 150]
REPEATS = 3  # 12-item workload x 3 = 36 requests/level, not n=12 single-pass


async def main():
    all_records = []
    provider = GeminiVertex(retry_attempts=1)  # retries off — see module docstring

    for level in LEVELS:
        print(f"[concurrency_sweep] level={level}")
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
