"""Experiment 6 (2026-08-17, second same-day follow-up round) — escalation
past the concurrency sweep's 150 stopping point, specifically hunting for a
real failure.

Why this exists: the original bounded-load reasoning (stop at a modest
ceiling, don't crowd out other forks on shared project quota) turned out to
rest on a wrong assumption. Checking the project's actual GCP quota showed
`gemini-2.5-flash` has no fixed per-project quota bucket at all — it runs
on Dynamic Shared Quota, a pool shared across every Google Cloud customer
using the model/region, not a small allocation carved out for
`evertune-tests` specifically. That doesn't mean "push with no limit" (it's
still real infrastructure, still costs real money, and a single local
process has its own limits regardless of what the server allows) — but it
does mean the original stopping point was more conservative than the
actual constraint justified. Run only after explicit sign-off given both
of those things.

Design: escalate through LEVELS, stopping as soon as ANY error is observed
(no reason to keep spending once the thing we're looking for is found) or
at a hard sanity cap, whichever comes first. `repeats` scales with the
level so each level actually reaches that many requests in flight at once
(a `concurrency=1200` test with only 36 total requests never really tests
1200 concurrent — the semaphore has nothing to saturate).

Caveat baked into the analysis, not just this docstring: if errors do
appear, `error_class` (in the JSONL, via loadtest/runner.py's
classify_error) matters for *interpreting* them, not just counting them.
A burst of `rate_limited`/`server_error_5xx` is a real server-side signal.
A burst of `timeout`/`unclassified_*` at very high concurrency could
instead mean *this single process* (its container's file-descriptor limit,
its asyncio event loop, or the SDK's own internal HTTP connection pool) hit
a wall before Google's infrastructure did — a client-side artifact, not
evidence about Vertex's real ceiling. Report whichever it turns out to be
honestly rather than treating any error as automatically "found the
ceiling."

Run from repo root: python -m loadtest.experiments.escalation_test
"""

import asyncio
import math
import os

from llm import GeminiVertex
from loadtest.runner import percentile, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/escalation_test.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
LEVELS = [250, 400, 600, 900, 1200, 1600, 2000]  # hard sanity cap at 2000


async def main():
    all_records = []
    provider = GeminiVertex(retry_attempts=1)  # retries off — isolate raw capacity, same as concurrency_sweep.py

    for level in LEVELS:
        repeats = math.ceil(level / len(WORKLOAD))
        n_requests = repeats * len(WORKLOAD)
        print(f"[escalation_test] level={level} repeats={repeats} (~{n_requests} requests in flight)")
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
        all_records.extend(records)
        # Write incrementally so a mid-run failure/interrupt doesn't lose
        # progress already spent.
        write_jsonl(all_records, RESULTS_PATH, append=False)

        if errors:
            classes = sorted(set(r.error_class for r in errors))
            print(f"  error classes: {classes}")
            print(f"[escalation_test] errors found at level={level} — stopping escalation")
            break
    else:
        print(f"[escalation_test] no errors through the hard sanity cap ({LEVELS[-1]})")

    print(f"[escalation_test] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
