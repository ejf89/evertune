"""Experiment 1 (must-run, top billing): thinking-budget sweep.

Sweeps disabled(0) / low / default(unset, automatic) / high across the full
workload, holding temperature fixed. Also folds in the empty-response probe
(PLAN.md's Decision 4): a constrained max_output_tokens paired with a high
thinking budget, deliberately trying to manufacture
finish_reason=MAX_TOKENS with empty visible content.

Run from repo root: python -m loadtest.experiments.thinking_budget_sweep
"""

import asyncio
import os

from llm import GeminiVertex
from loadtest.runner import run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/thinking_budget_sweep.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
TEMPERATURE = 0.7
CONCURRENCY = 10  # fixed and modest — this experiment is about the model's
                   # behavior, not about load; see PLAN.md's eval/load split.

# "high" is a guess at a generously large budget, not a verified model
# maximum (the SDK's own docs say the range is model-dependent and don't
# state it). If it's out of range, the API's response (error or clamped
# behavior) is itself real data, not a bug in this script.
LEVELS = [
    ("disabled", {"thinking_budget": 0}),
    ("low", {"thinking_budget": 128}),
    ("default", {}),  # omit entirely — model's own default/automatic
    ("high", {"thinking_budget": 8192}),
]


async def main():
    all_records = []

    for label, kwargs in LEVELS:
        provider = GeminiVertex(concurrency=CONCURRENCY, **kwargs)
        print(f"[thinking_budget_sweep] level={label} kwargs={kwargs}")
        records = await run_batch(
            provider,
            list(WORKLOAD),
            temperature=TEMPERATURE,
            concurrency_level=CONCURRENCY,
            environment=ENVIRONMENT,
            repeats=1,
        )
        errors = [r for r in records if r.error_class]
        print(f"  {len(records)} requests, {len(errors)} errors")
        if errors:
            print(f"  error classes: {sorted(set(r.error_class for r in errors))}")
        all_records.extend(records)

    print("[thinking_budget_sweep] empty-response probe: "
          "max_output_tokens=16, thinking_budget=8192")
    probe_provider = GeminiVertex(
        concurrency=CONCURRENCY, thinking_budget=8192, max_output_tokens=16
    )
    probe_records = await run_batch(
        probe_provider,
        list(WORKLOAD),
        temperature=TEMPERATURE,
        concurrency_level=CONCURRENCY,
        environment=ENVIRONMENT,
        repeats=1,
    )
    empty = [r for r in probe_records if not r.error_class and r.answer == ""]
    print(f"  {len(probe_records)} requests, {len(empty)} empty-but-200 responses, "
          f"finish_reasons seen: {sorted(set(r.finish_reason for r in probe_records if r.finish_reason))}")
    all_records.extend(probe_records)

    write_jsonl(all_records, RESULTS_PATH, append=False)
    print(f"[thinking_budget_sweep] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
