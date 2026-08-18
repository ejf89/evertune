# Evertune Take-home Exercise: Adding Gemini 2.5 Flash

This repo contains a small sample of our LLM vendor integration. We'd like you to add support for Gemini 2.5 Flash on Google Vertex and report back on your findings.

# Setup

You'll need the `gcloud` CLI installed and configured against our project, which we will provide for you.

# What to build

Implement Gemini 2.5 Flash as a provider in this system, and demonstrably prove it will hold up at production scale. We care about both halves of that sentence: a working integration *and* the evidence that it will not fall over when we point real traffic at it.

How you structure the code is up to you — the existing providers are a reference, not a template. If something about Gemini doesn't fit those patterns, deviate and tell us why.

For the "prove it works at scale" half: design and run whatever load tests, harnesses, or experiments you'd want to see before signing off on this for production. Show us the numbers, the failure modes you uncovered, and the headroom (or lack thereof) you found.

# Deliverables

We're less interested in a "completed checklist" and more interested in what you learned. In your write-up, we'd like to see:

- How the integration behaves under realistic load. Pick a workload, run it, and tell us what you observed.
- Anything you discovered about this model — quirks, failure modes, parameters that mattered, things that surprised you compared to other LLMs you've used.
- Decisions you made and the tradeoffs behind them. If you tried something that didn't work, that's worth including too.
- What you'd want to do next if this were going to production, and what you'd want to know before getting there.

---

# Results: what we built and found

This section is a self-contained digest of the whole submission — everything
below traces to a committed file and re-derives from raw data, nothing is
asserted without a source. For the full depth behind any of it:

- **[`PLAN.md`](PLAN.md)** — every design decision, the alternatives considered
  and rejected, and a chronological log of two follow-up audit rounds run
  after the first pass was "done."
- **[`FINDINGS.md`](FINDINGS.md)** — the complete write-up this section
  summarizes, with full data tables and caveats.
- **[`NOTES.md`](NOTES.md)** — analysis of the starting repo before any
  Gemini work began.
- **`loadtest/results/*.jsonl`** — the raw per-request data behind every
  number below. Run `python -m loadtest.analyze` to regenerate every chart
  and printed summary from it yourself.

**Quick facts:** `GeminiVertex` provider (`llm/gemini_vertex.py`) · 37 unit
tests passing, `pyflakes` clean · 6 load/eval experiments · 2 follow-up audit
rounds after the initial submission · every chart below regenerable from
committed data, zero illustrative numbers.

## The provider, in one paragraph

`GeminiVertex` implements the existing `LLM` interface against Gemini 2.5
Flash on Vertex AI, using ADC auth (`gcloud auth application-default login`)
rather than the bearer-API-key pattern the existing `Together` provider
uses — Vertex has no equivalent of a static key. `LLM.SimpleResponse` was
extended with optional `finish_reason`, `model`, `latency_ms`, and
`thinking_tokens` fields (backward compatible — `Together`'s construction
call is unaffected). Every design decision (why extend rather than replace
the response type, why SDK-native retry over hand-rolled backoff, why
`thinking_budget` is constructor config rather than a per-call param) is in
`PLAN.md` with the rejected alternative and the reason it lost.

## What we found

### 1. Thinking tokens dominate cost — and can silently bill you for nothing

![Token spend by thinking-budget setting](loadtest/results/charts/thinking_budget_tokens.png)

Gemini 2.5 Flash spends hidden "thinking" tokens before its visible answer.
They're billed, but never appear in the response text. At default settings
they were **3.4× the visible output** (596 vs. 177 tokens, averaged) —
**9.4× the dollar cost** of thinking disabled, verified against Google's
live Vertex pricing (thinking is billed at the *same* rate as visible
output, per the pricing page's own row label).

Pushed further: constraining `max_output_tokens` while thinking is active
produced **`HTTP 200` responses with an empty answer in 9 of 12 requests** —
billed for the full thinking spend, no exception raised, no field in the
*original* response object even able to express what happened. A
mention-rate pipeline that doesn't check `finish_reason` would silently
undercount every category where this triggers.

### 2. `temperature=0` is not fully deterministic

![Brand mention rate by temperature](loadtest/results/charts/output_variance_mentions.png)

100 identical calls, same prompt, `temperature=0`: **7 distinct answer
strings**, not 1. Variance isn't uniform across brands — five brands held
85–98% mention rates regardless of temperature, while two sat right on the
model's inclusion/exclusion boundary (4% and 56% across temperatures). A
single sample at `temperature=0` is not a clean-room-reproducible number,
and which brands need more samples can't be known without a variance check
per category.

### 3. There's a real ceiling — it's a latency wall, not an error wall

![Latency vs. concurrency, full range](loadtest/results/charts/concurrency_latency_full_range.png)

Zero HTTP errors from 1 through **2,000 concurrent requests** (~7,400 total
across all load experiments). But latency is only flat up to ~150–250
concurrent — past that, p50 grows almost perfectly linearly (**r² = 0.99**),
from ~7s up to **87 seconds at 2,000 concurrent**. Nothing ever fails; it
just queues, implying roughly **1,400 requests/minute** of sustained
effective throughput before things back up. A caller with a timeout would
see what looks like an outage well before any error ever appears — this
also means retry-based backpressure structurally can't help here, since
retries key off error codes this failure mode never produces. (Caveat we
didn't hide: this was measured from one process on one machine — see
"What we'd want before production" below.)

### 4. A cold traffic spike looks the same as a gradual ramp

Two idle→spike cycles at the measured ceiling showed no meaningful
cold-vs-warm difference — a sudden burst to 150 concurrent, from a cold
client, landed in the same latency band as gradually ramping up to it.

## Cost, at Vertex list price

| Thinking budget | Avg output tokens (visible+thinking) | $ / 1,000 requests |
|---|---:|---:|
| disabled | 81 | $0.207 |
| low | 245 | $0.617 |
| default | 773 | $1.938 |
| high | 880 | $2.205 |

## What we'd want before production

- **Confirm the ~1,400 req/min wall from more than one process/machine** —
  the single highest-value open item. If it holds independently, it's a
  real Vertex/DSQ-side limit to design around (client-side concurrency
  limiting, or Google's Provisioned Throughput); if it doesn't, the limit
  is on our side, not Google's.
- **A `finish_reason`-aware guard** in front of any mention-rate pipeline —
  the empty-response failure mode is silent and reproducible.
- **Resolve whether client-side timeouts actually trigger the SDK's
  retries** — the retry predicate is `httpx`-specific, but this provider's
  async path uses `aiohttp`; left as an explicitly unconfirmed gap rather
  than an assumed answer.
- A decision on whether `temperature=0` is trustworthy as a single
  ground-truth call for Evertune's methodology, given it isn't fully
  deterministic.

Full list, plus everything explicitly *not* run and why (a sustained soak
test, prompt-length variation), in `FINDINGS.md`.