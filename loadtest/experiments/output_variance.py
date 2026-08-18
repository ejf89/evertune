"""Experiment 2 (must-run, top billing): output variance under identical
inputs.

Same prompt, N=100, at fixed temperature points — quantifies structural
variance (token counts) and content variance (brand mention rate, via the
hand-written candidate list per PLAN.md's Decision 3). Deliberately one
workload item: repeated sampling of the SAME question is the whole point.

Run from repo root: python -m loadtest.experiments.output_variance
"""

import asyncio
import os
from collections import Counter

from llm import GeminiVertex
from loadtest.runner import run_batch, write_jsonl
from loadtest.workload import WORKLOAD

RESULTS_PATH = "loadtest/results/output_variance.jsonl"
ENVIRONMENT = os.environ.get("LOADTEST_ENVIRONMENT", "container")
CONCURRENCY = 10
N = 100
# Includes 0.0 specifically to check whether it actually produces identical
# output — PLAN.md's Decision 3 calls this out as a finding either way.
TEMPERATURES = [0.0, 1.0]

ITEM = WORKLOAD[0]  # running shoes — fixed, not swept; see module docstring


def count_mentions(records, candidate_brands):
    counts = Counter()
    for r in records:
        lowered = r.answer.lower()
        for brand in candidate_brands:
            if brand.lower() in lowered:
                counts[brand] += 1
    return counts


async def main():
    all_records = []
    provider = GeminiVertex(concurrency=CONCURRENCY)

    for temperature in TEMPERATURES:
        print(f"[output_variance] N={N} temperature={temperature} prompt={ITEM.prompt!r}")
        records = await run_batch(
            provider,
            [ITEM],
            temperature=temperature,
            concurrency_level=CONCURRENCY,
            environment=ENVIRONMENT,
            repeats=N,
        )
        errors = [r for r in records if r.error_class]
        successes = [r for r in records if not r.error_class]
        mentions = count_mentions(successes, ITEM.candidate_brands)
        distinct_answers = len(set(r.answer for r in successes))
        token_counts = sorted(r.total_tokens for r in successes)

        print(f"  {len(successes)} ok, {len(errors)} errors, "
              f"{distinct_answers}/{len(successes)} distinct answer strings")
        print(f"  mention counts: {dict(mentions)}")
        if token_counts:
            print(f"  total_tokens: min={token_counts[0]} "
                  f"max={token_counts[-1]} median={token_counts[len(token_counts)//2]}")

        all_records.extend(records)

    write_jsonl(all_records, RESULTS_PATH, append=False)
    print(f"[output_variance] wrote {len(all_records)} records to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
