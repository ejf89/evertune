"""Experiment 7 (2026-08-18 follow-up) — same load, from independent
processes, to separate "the latency wall is shared/server-side" from
"the latency wall is an artifact of one process."

escalation_test.py found a real latency wall above ~150-250 concurrent
(FINDINGS.md's "Escalation past 150") but flagged an honest limit: every
request in that experiment came from one Python process, one asyncio
event loop, one connection pool, on one machine. If that single process
were itself the bottleneck (not Vertex), the wall would be an artifact of
how we tested, not a real capacity limit.

This experiment doesn't fully resolve that (a true resolution needs a
genuinely separate machine/network path — see FINDINGS.md's "what we'd
want before production") but it does resolve one real alternative
explanation cheaply: it rules out (or confirms) that a *single process's*
own event loop / connection pool is the cause, by running the exact same
work from THREE independent OS processes at once, each with its own
Python interpreter, event loop, and aiohttp session.

Design: run this script multiple times concurrently — for example, three
separate `docker run` invocations launched from the host in the same
shell command/message so they genuinely overlap in time (sequential
invocations would not) — each with a distinct PROCESS_TAG so results
don't collide. Each
process independently requests LEVEL concurrent in-flight requests. If
each process sees roughly what a LONE process would see at LEVEL (per the
escalation fit: p50 ~= 42ms * LEVEL - 665ms), that's evidence each
process has its own independent bottleneck (leans client-side/per-process).
If each process instead sees roughly what a lone process would see at the
COMBINED total (LEVEL * process count), that's evidence the constraint is
shared across all callers, not specific to any one process (leans
server-side/shared-pool — the more likely real-Vertex-limit explanation,
though still not provably different from "all three processes share this
one machine's network path," which is the honest remaining gap).

Run from repo root, three times concurrently, one per terminal/container:
    PROCESS_TAG=a python -m loadtest.experiments.multi_process_test
    PROCESS_TAG=b python -m loadtest.experiments.multi_process_test
    PROCESS_TAG=c python -m loadtest.experiments.multi_process_test
"""

import asyncio
import os

from llm import GeminiVertex
from loadtest.runner import percentile, run_batch, write_jsonl
from loadtest.workload import WORKLOAD

PROCESS_TAG = os.environ.get("PROCESS_TAG", "solo")
RESULTS_PATH = f"loadtest/results/multi_process_{PROCESS_TAG}.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
LEVEL = 700  # per-process; 3 processes at once = ~2,100 combined, comparable
             # to escalation_test.py's 2,000-concurrent hard cap
REPEATS = 60  # 12-item workload x 60 = 720 requests, enough to saturate LEVEL


async def main():
    provider = GeminiVertex(retry_attempts=1)
    print(f"[multi_process_test:{PROCESS_TAG}] level={LEVEL} repeats={REPEATS} "
          f"(~{REPEATS * len(WORKLOAD)} requests)")
    records = await run_batch(
        provider,
        list(WORKLOAD),
        temperature=TEMPERATURE,
        concurrency_level=LEVEL,
        environment=ENVIRONMENT,
        repeats=REPEATS,
    )
    errors = [r for r in records if r.error_class]
    latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)
    p50 = percentile(latencies, 0.50)
    p95 = percentile(latencies, 0.95)
    print(f"[multi_process_test:{PROCESS_TAG}] {len(records)} requests, "
          f"{len(errors)} errors, p50={p50:.0f}ms, p95={p95:.0f}ms" if p50
          else f"[multi_process_test:{PROCESS_TAG}] {len(records)} requests, {len(errors)} errors")
    if errors:
        print(f"  error classes: {sorted(set(r.error_class for r in errors))}")

    write_jsonl(records, RESULTS_PATH, append=False)
    print(f"[multi_process_test:{PROCESS_TAG}] wrote {len(records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
