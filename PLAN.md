# Plan — Gemini 2.5 Flash (Vertex) Provider

Living document. We update this as decisions get made — it should always
reflect current thinking, not a fossil of the first draft. Background/code
analysis lives in `NOTES.md`; this file is the "what we're going to do and
why" on top of it.

## The ask (from README.md)

Two deliverables, weighted roughly equally:
1. A working `Gemini 2.5 Flash` provider on Vertex, fit into (or deliberately
   deviating from) the existing `LLM` interface.
2. Evidence it holds up at production scale — a load test/harness you designed,
   real numbers, failure modes found, headroom (or lack of it).

Write-up should cover: behavior under realistic load, Gemini-specific quirks/
surprises, decisions + tradeoffs (including things that didn't work), and what
you'd want before actually shipping this to production.

Explicitly *not* wanted: a "completed checklist." They want judgment on
display, not just green checkmarks.

## Status

- [x] Read the existing repo, produced `NOTES.md` (provider interface,
      response object, error handling, concurrency, config, testing, and
      OpenAI-shaped assumptions that won't carry over to Gemini).
- [x] Created `gemini-vertex-integration` branch off `main`.
- [x] Environment recon — gcloud installed, `gcloud auth login` +
      `application-default login` done, project set to `evertune-tests`,
      curl smoke test against `us-central1` Gemini 2.5 Flash returned **200**
      with a real response. Auth chain is fully proven end-to-end.
- [x] Design decisions settled — Phase 1's original five, plus the five
      locked in 2026-08-14 (thinking_budget/max_output_tokens location,
      experiment tiering, brand-extraction method, folding the
      empty-response probe into the thinking-budget sweep, empirically
      deriving `parallelism()`).
- [x] Core provider implementation — `GeminiVertex` (`llm/gemini_vertex.py`),
      extended `SimpleResponse` (`llm/llm.py`), code harness
      (`tests/test_gemini_vertex.py` + `conftest.py`, 21 tests). Matches all
      locked decisions including `thinking_budget`/`max_output_tokens` as
      constructor config. Docker+ADC environment verified from inside a
      container (200 on the smoke test) before any code was touched; full
      test suite run and passing (21/21) in that same container, not just
      claimed to work.
- [x] Shared harness infrastructure (`loadtest/workload.py`,
      `loadtest/runner.py`) — 12-item brand-mention workload, request-runner
      with error classification, concurrency-bounded batch runner, JSONL
      writer. 8 more tests (30 total), verified in the same container.
      Not yet run against real Vertex — this only proves the plumbing.
- [x] All four must-run experiments — run for real against `evertune-tests`
      (`us-central1`, container environment): thinking-budget sweep (incl.
      the empty-response probe — reproduced, 9/12), output variance (N=100
      × 2 temperatures — temperature=0 found non-deterministic), concurrency
      sweep (0 errors through 50, no ceiling found within our bounded
      range), retry amplification (honest non-result — no errors, so
      nothing to amplify). Raw JSONL committed under `loadtest/results/`.
      `parallelism()`'s default updated from a placeholder (10) to the
      measured value (50) as a direct result.
- [x] `FINDINGS.md` written from the committed data, with charts
      (`loadtest/results/charts/*.png`), per the standing rule — every
      number traces to a run that actually happened. Optional experiments
      (soak/burst/prompt-length) explicitly not run, stated as such.

## Phase 5: Follow-up pass (2026-08-17, same day) — closing audit findings

I audited the state above (before pushing/PR) and found two real gaps and
several smaller ones. Rather than defer all of them, I closed what I could
same-day — weighing the added cost against the value before spending any
more of `evertune-tests`' shared quota, which is the "checking in before
pushing further" the earlier bounded-load reasoning promised, not skipped
in practice:

- [x] **Code fixes** (no live calls): `SimpleResponse.visible_output_tokens`
      convenience (the `output_tokens` cross-provider semantics gap);
      `concurrency=0` falsy-default bug; `GEMINI_RETRY_ATTEMPTS`/
      `GEMINI_THINKING_BUDGET` env-var fallbacks (matching the existing
      `GEMINI_MODEL`/`GEMINI_CONCURRENCY` convention); a bug the
      retry-attempts fix would otherwise have introduced (the client-build
      path was reading the raw constructor arg instead of the
      env-resolved value) caught before it shipped, not after; pinned
      `pytest`/`matplotlib` in `requirements.txt`. 7 new regression tests,
      37/37 passing in container.
- [x] **Verified Vertex pricing** directly from Google's official pricing
      page (not an aggregator) and added real $/1K-requests cost modeling
      to `loadtest/analyze.py` — the one "what we didn't run" item that
      wasn't actually blocked by needing new experimental data.
- [x] **Concurrency sweep redesigned and rerun**: repeated passes (3x, not
      n=12 single-pass) and levels extended to 150 (3x the original stop),
      specifically to address the audit's core finding — the load half of
      the README's ask was the weakest-evidenced part of the first pass.
      Zero errors across 288 requests, 1-150. `parallelism()`'s default
      updated 50 -> 150 as a direct, traced result.
- [x] **Burst test built and run** (promoted from optional): idle -> sudden
      spike, 2 cycles at the new ceiling. No meaningful cold-vs-warm
      difference found.
- [x] **Retry amplification rerun** at the new 150 ceiling (was 50) — still
      an honest non-result, now better-evidenced rather than resolved.
- [x] `FINDINGS.md` rewritten in place to reflect all of the above, with
      the follow-up pass's provenance called out explicitly rather than
      silently blended into the original numbers.

**Deliberately not done in this pass** (would need another explicit
check-in before spending more shared quota, not a unilateral call): pushing
concurrency past 150 to try to actually trigger a retry-amplification
result; a soak test; prompt-length variation. See `FINDINGS.md`'s "What we
didn't run" for the reasoning.

## Phase 6: Second same-day follow-up — pushing past 150

Revisiting Phase 5's non-result framing ("finding no failures feels
incorrect"), I didn't think that was the end of the story, and pushed to
understand directly why I hadn't gone past 150. The honest answer split
into two parts:

1. The "shared quota, don't crowd out other forks" reasoning behind
   stopping at 150 got checked, not just repeated, and turned out to be
   wrong: `gcloud alpha services quota list` showed `gemini-2.5-flash` has
   **no fixed per-project quota bucket at all** on Vertex — it runs on
   Dynamic Shared Quota (confirmed independently against Google's own DSQ
   docs), a pool shared across every Vertex customer on that model/region,
   not a small allocation this project's 10 forks were competing over.
2. That correction changed my calculus, not just the docs — the actual
   cost turned out to be $1.58 for the entire first two passes combined
   (computed directly from committed JSONL × verified pricing, checked
   directly rather than assumed). Given that and the corrected quota
   picture, I decided to push further.

- [x] Built `loadtest/experiments/escalation_test.py`: escalates
      concurrency (250/400/600/900/1200/1600/2000), stopping early on the
      first real error or at a 2,000 hard sanity cap. Ran for real, with
      `--ulimit nofile=65536:65536` on the container specifically to avoid
      a false-positive "error" from local file-descriptor exhaustion at
      high concurrency, not a real API limit.
- [x] **Result: zero HTTP errors through the entire 2,000-request hard cap
      (6,972 requests in this experiment alone)** — but p50 latency grows
      almost perfectly linearly above ~150-250 concurrent (linear fit:
      r²=0.99, ~42ms per unit of concurrency, implying ~1,400 req/min
      sustained effective throughput). This is a materially different, more
      useful finding than "no ceiling found": there's a real capacity
      limit, it just shows up as queueing delay instead of errors.
- [x] Checked one specific alternative explanation before trusting this as
      a Google-side signal: read the installed `google-genai` SDK source
      directly and confirmed it sets `AiohttpTCPConnector(limit=0)` — no
      client-side connection-pool cap of its own. Did NOT rule out other
      client-side effects (single process/machine, Docker's virtualized
      networking, single event loop) — flagged explicitly as unconfirmed
      in `FINDINGS.md` rather than claimed either way.
- [x] Re-examined the retry-amplification non-result in light of this: it's
      not just unresolved, it's *structurally* unresolvable by an
      attempts-on-vs-off test, since the SDK's retries key off HTTP error
      codes and this system's real failure mode under load produces none.
      Chased one more layer while writing this up — read the SDK's retry
      predicate source and found it's specific to `httpx` exception types,
      but the actual async path this provider uses goes through `aiohttp`,
      not httpx. Whether aiohttp's own timeout exceptions are caught by
      that predicate wasn't confirmed from source alone — left as an
      explicit open question in `FINDINGS.md` rather than guessed at
      either way.
- [x] Added `loadtest/experiments/escalation_test.py`'s analyzer to
      `loadtest/analyze.py` (`analyze_escalation_test()`), producing a
      combined 1-2,000 chart (`concurrency_latency_full_range.png`) with
      the linear fit drawn on it, and rewrote `FINDINGS.md`'s load section,
      TL;DR, decisions, and production-readiness sections to reflect the
      corrected understanding rather than leaving the superseded "no
      ceiling found" framing standing next to the new data.
- [x] Updated `gemini_vertex.py`'s `DEFAULT_CONCURRENCY` comment (value
      unchanged at 150 — still the highest confirmed-flat level) to state
      plainly what's now known to be on the other side of it, so a future
      reader doesn't assume "unbounded" from the absence of errors.

## Phase 7: Closing two remaining open items (2026-08-18)

Reviewing the two remaining open questions from Phase 6's write-up directly
("do we have to leave these things open?"), I pushed for real answers
rather than accepting them as permanently unresolved.

- [x] **The client-timeout-vs-SDK-retry question — fully resolved from
      source, not left as a gap.** Traced past the retry predicate (which
      only told us it matches `httpx` exception types) into the actual
      function `tenacity` wraps (`_async_request_once`) and read its
      inline exception handling directly: it catches five specific
      `aiohttp`/auth connection-error types, and `asyncio.TimeoutError` —
      what a `ClientTimeout` expiry actually raises — is not one of them.
      **Confirmed: a client-side timeout on this provider's real async
      path is never retried by the SDK, full stop.** Moved from "What
      we'd want before production" (an open gap) into the load findings
      (a resolved result) in `FINDINGS.md` and the README.
- [x] **Built and ran a multi-process follow-up to the escalation test**
      (`loadtest/experiments/multi_process_test.py`) — three independent
      Docker containers (separate processes/event loops/connection pools)
      launched simultaneously from the host, each hammering Vertex at
      concurrency=700 (combined ~2,100, comparable to the prior
      2,000-concurrent single-process max). Confirmed via `docker ps`
      that all three were genuinely running concurrently, not
      sequentially.
      - Result was **mixed, not a clean verdict either way** — and
        explicitly written up that way rather than forced into a tidy
        answer: each process's own p50 (~28-30s) matched what a *lone*
        process at 700 would predict (~28.7s), not what a lone process at
        the *combined* 2,100 would predict (~87.5s) — evidence the
        latency curve is substantially per-process. But one of the three
        processes also hit a real `429 rate_limited` — this project's
        **first HTTP error ever**, after ~9,000+ error-free single-process
        requests across every prior experiment. Both facts are true at
        once; write-up says so rather than picking whichever one sounds
        more conclusive.
      - Added `analyze_multi_process_test()` to `loadtest/analyze.py`
        (prints per-process stats plus both competing predictions) and
        committed the three `loadtest/results/multi_process_{a,b,c}.jsonl`
        files, matching the "every number traces to committed data" rule.
      - **Honest limit stated in `FINDINGS.md`, not glossed over:** all
        three processes still ran on one Mac, sharing its network
        interface and Docker Desktop's virtualized networking — this
        experiment separates "single process" from "single machine,"
        rules out one specific alternative explanation, but doesn't reach
        a genuinely independent network. That's still the honest
        remaining gap, now narrower than before rather than closed.
- [x] Updated `README.md`'s load-behavior answer and "next steps" list to
      reflect both resolutions — pyflakes clean, 37/37 tests passing
      throughout.

Remaining: review pass, then push branch + open the PR.

## Phase 8: Independent review before submitting (2026-08-19)

Before opening the PR, I wanted a second, adversarial pair of eyes that
hadn't been steering the work the whole way through — the same instinct
behind Phase 5-7's own audits, just from outside my own context this time.
I wrote up a review prompt (accurate about who actually did what — an
earlier draft of that prompt claimed "I had no part in building this,"
which was false and I corrected it before sending) and had a separate
Claude session review the repo cold, with no access to this plan or my
own reasoning about it.

- [x] **Caught a real, meaningful bug I'd missed:** `concurrency_sweep.py`,
      `burst_test.py`, and `retry_amplification.py` all held their
      repeated-passes count fixed at a small constant regardless of the
      concurrency level under test. Above a level of roughly 25-36, the
      semaphore never actually had enough in-flight tasks to bind at the
      labeled level — "tested at 150" had really only ever tested ~36
      real concurrent requests. I verified this myself against the code
      before trusting it (same discipline as always: don't take a claim
      about this codebase on faith, confirm it against source), confirmed
      it was real, then fixed all three scripts to scale `repeats` with
      the level being tested and reran them live against
      `evertune-tests`.
      - The rerun surfaced a genuine finding the bug had been hiding: 100
        concurrent is the real edge of the flat zone (p50 ~6,100ms); 150
        showed a real, if modest, step up (p50 ~9,400ms) rather than
        staying flat. `parallelism()`'s default moved from 150 to **100**
        as a direct result — see `llm/gemini_vertex.py`'s comment history
        on that constant.
      - Corrected every downstream claim built on the old "150" numbers:
        `FINDINGS.md`'s concurrency-sweep/burst-test/retry-amplification
        sections, `README.md`'s load-behavior table and "what didn't
        work" list, and the report site's (`docs/index.html`) latency
        chart, which had been drawn assuming the flat zone ran through
        150.
      - New JSONL results and regenerated charts committed alongside, same
        "every number traces to a run that actually happened" rule as
        everywhere else in this project.
- [x] Went through the review's other flagged issues one at a time rather
      than batch-accepting all of them — the smaller ones (documentation
      consistency, a couple of stale numbers, this file's inconsistent
      voice) got fixed; a handful of lower-priority style nitpicks (exact
      percentile rounding behavior, a hardcoded fit constant in one
      analysis helper, minor chart-label overlap) were left as-is as
      genuinely low-priority rather than silently ignored.
- [x] **This file's own voice** — most of Phase 5 through 7 above had been
      written about "Eric" in the third person, which read strangely for
      a plan document I'm the author of. Rewrote it in first person
      throughout.
- [x] Double-checked the "empty answer" finding (#2 in `FINDINGS.md`)
      wasn't somehow a forced or cherry-picked result — it isn't. The
      settings that produce it (a high thinking budget paired with a
      tight `max_output_tokens` cap) are a deliberately adversarial
      combination, chosen specifically to probe a suspected failure mode,
      not typical production settings — but the mechanism itself is real
      and already disclosed as deliberately reproduced, not accidentally
      stumbled into.
- [x] Reworded the Phase 3 line about how I settled on what "harness"
      should mean here — it undersold what actually happened. I talked
      the plan through with a friend who works in this space, to
      sanity-check the direction before committing to it; the earlier
      phrasing didn't say that.

Remaining: final full test run + pyflakes check, sync the two private
study artifacts with the corrected numbers, commit and push everything to
`ejf89/evertune`, then open the PR against that fork (never against
`Evertune-AI/takehome` directly).

## Phase 0: Environment recon (de-risking auth before the real work)

Goal: by the time we're actually writing the provider, "can I even talk to
Vertex" is already answered — not something we discover mid-spike.

1. **Install gcloud CLI** (not currently installed on this machine):
   ```
   brew install --cask google-cloud-sdk
   ```
2. **Authenticate** — two different credentials, both usually needed:
   ```
   gcloud auth login                        # your user identity, for gcloud commands
   gcloud auth application-default login    # ADC, what client libraries read
   ```
3. **Set the project** — done:
   ```
   gcloud config set project evertune-tests
   ```
4. **Confirm the Vertex AI API is enabled** on that project:
   ```
   gcloud services enable aiplatform.googleapis.com
   ```
5. **Curl smoke test** — bypass any SDK, hit the REST endpoint directly with a
   bearer token, confirm 200 before writing a line of provider code:
   ```
   PROJECT_ID=evertune-tests
   LOCATION=us-central1

   curl -sS -X POST \
     -H "Authorization: Bearer $(gcloud auth print-access-token)" \
     -H "Content-Type: application/json" \
     "https://${LOCATION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${LOCATION}/publishers/google/models/gemini-2.5-flash:generateContent" \
     -d '{
           "contents": [{"role": "user", "parts": [{"text": "Say hi in one word."}]}]
         }' \
     -o /dev/null -w '%{http_code}\n'
   ```
   A `200` here means: project has Vertex enabled, IAM permissions are correct,
   region has the model, and your local credentials are good. Any 401/403/404
   tells us exactly which of those is wrong *before* it's tangled up with
   Python/SDK debugging.

**Done** — all of Phase 0 is complete: `gcloud` installed and authenticated,
project resolved to `evertune-tests`, smoke test returned 200. (This section
previously tracked what was still blocked on information from Evertune or
setup steps I still needed to do; nothing here is still open.)

## First real finding (from the smoke test response)

The raw response body had a field neither `Together` nor `SimpleResponse`
account for:

```json
"usageMetadata": {
  "promptTokenCount": 6,
  "candidatesTokenCount": 2,
  "totalTokenCount": 29,
  "thoughtsTokenCount": 21
}
```

`6 + 2 + 21 = 29`. Gemini 2.5 Flash is a reasoning model — it spends hidden
"thinking" tokens before producing the visible answer, and those tokens are
**billed** (they're in `totalTokenCount`) but not part of `candidatesTokenCount`
(the visible output). `SimpleResponse.output_tokens` (`llm/llm.py:7`) has
exactly one slot for output tokens — if we naively map `candidatesTokenCount`
into it, `input_tokens + output_tokens` will silently undercount real spend by
the thinking-token amount on every call. This is exactly the kind of
Gemini-specific quirk the README asked us to surface — noting it here as soon
as it showed up rather than losing it. Affects both the `SimpleResponse`
extension design (Phase 1) and cost accounting in the load test (Phase 3).

## Phase 1: Design decisions

These are things `NOTES.md` flagged as unresolved by the existing code — worth
deciding deliberately rather than copying `Together`'s pattern by default,
since the README explicitly invites deviation.

| Decision | Options | **Decided** |
|---|---|---|
| Fit the existing `LLM`/`SimpleResponse` interface, or extend it? | (a) implement as-is, silently drop finish_reason/model metadata; (b) extend `SimpleResponse` with optional fields; (c) new response type | **(b) Extend `SimpleResponse`** with optional fields — `finish_reason`, `model`, `latency_ms` (exact set TBD at implementation time). Backward compatible with `Together` (new fields optional/defaulted), captures Gemini signals that matter operationally (`SAFETY`/`RECITATION`/`MAX_TOKENS` vs. normal `STOP`). Called out as a deliberate deviation in the write-up. |
| Auth model | ADC (`google-auth` + SDK) vs. hand-rolled REST with `gcloud`-issued tokens | **SDK + ADC.** Matches Vertex conventions, avoids reinventing token refresh. (The curl smoke test in Phase 0 still uses raw REST — that's just for verifying access fast, not the shape of the real provider.) |
| Retry/backoff placement | Inside the provider vs. caller's job | Inside the provider, via the SDK's native `HttpRetryOptions` — **resolved**, see SDK findings below. Adopt the SDK's own defaults (`408/429/500/502/503/504`, 5 attempts, exponential+jitter) unless the load test gives us a concrete reason to override them. |
| Concurrency harness | Respect `parallelism()` via caller-side semaphore vs. build a harness that actually uses it | **Decided**: a standalone runnable script/module (e.g. `loadtest/`), not folded into `pytest`. It's a measurement tool that produces raw metrics data (CSV/JSON), which Phase 4's findings report consumes — not a correctness test. |
| Config | Match `Together`'s env-var pattern vs. explicit constructor args | Vertex auth doesn't fit the bearer-API-key env var pattern `Together` uses — project/location will be explicit config, not just "read an API key." Exact shape TBD at implementation time. |
| Where does `thinking_budget` live? | Per-call param on `ask_generic_question` vs. constructor config | **Constructor config** — `GeminiVertex(thinking_budget=N)`. The shared `ask_generic_question(system_prompt, question, temperature)` signature stays untouched and `Together` is unaffected. The sweep instantiates one provider per level and loops. Rationale: provider-specific tuning belongs in construction, not the shared call signature — otherwise the interface accretes the union of every vendor's knobs and stops being a useful abstraction. Matches existing precedent (`Together` takes its model name at construction, not per call). **Tradeoff, stated honestly in `FINDINGS.md` rather than papered over**: this makes thinking budget a per-*instance* setting, not per-*request* — if Evertune later wants cheap thinking for simple queries and expensive thinking for complex ones per-prompt, this design needs revisiting. Same constructor-level treatment applies to `max_output_tokens` (needed for the empty-response probe — see Phase 3's must-run tiering). |

SDK choice: **confirmed** `google-genai==2.18.1`, verified against the actual
installed source (not just docs — a README fetch surfaced a newer
`enterprise=True, location='global'` example that conflicted with our
`us-central1` requirement, so this was checked against `client.py`'s real
source rather than taken from the doc example):

- The older `google-cloud-aiplatform` generative modules
  (`vertexai.generative_models` etc.) are **sunset as of 2026-06-24** — already
  past, as of today. Confirms `google-genai` is the only live option, not just
  the newer-and-preferred one.
- `vertexai=True` still works exactly as expected — `client.py` docstring:
  `enterprise` is the new preferred name, `vertexai` is kept as an explicit
  "legacy flag for `enterprise`" with identical behavior, not deprecated
  functionality. `Client(vertexai=True, project=..., location='us-central1')`
  is a documented pattern in the source itself.
- Env vars the client reads directly: `GOOGLE_CLOUD_PROJECT`,
  `GOOGLE_CLOUD_LOCATION` (and `GOOGLE_GENAI_USE_ENTERPRISE`). Resolves the
  Phase 1 config-shape question — no custom env var names needed, matching
  Google's own convention.
- Async: `client.aio.models.generate_content(...)`.
- **Retry is built into the SDK** via `tenacity`, configured through
  `HttpRetryOptions` — but defaults to **zero retries** (a single attempt)
  unless explicitly configured. Default retryable HTTP codes when enabled:
  `408, 429, 500, 502, 503, 504`. This changes the retry decision above from
  "write retry logic" to "configure the SDK's own retry policy correctly" —
  simpler and less likely to have bugs than hand-rolled backoff.

## Phase 2: Implementation

**Recheck (2026-08-14) — Phase 1 decisions are now locked, so this is no
longer a placeholder:**

- `llm/gemini_vertex.py`: `GeminiVertex(LLM)`. Constructor takes `project`,
  `location`, `model`, `concurrency`, `thinking_budget`, `max_output_tokens`
  (the last two per the constructor-config decision above), plus a `client=`
  injection point for tests and for the load harness to share one client
  across provider instances instead of reconnecting per sweep level.
- `llm/llm.py`: `SimpleResponse` extended with optional `finish_reason`,
  `model`, `latency_ms` — defaulted, so `Together`'s existing construction
  call is unaffected.
- **`parallelism()` is empirically derived, not asserted** — `Together`'s
  `parallelism()` returns a bare `100` with no stated basis (`llm/together.py`)
  and we're deliberately not repeating that. Ship a conservative provisional
  value now; once the concurrency sweep (Phase 3) finds the actual knee in
  the latency/error curve, set `parallelism()` to that measured value and say
  so in `FINDINGS.md` — "measured," not "asserted." This is the concrete
  link between the two halves of the deliverable: the load test doesn't just
  produce a report, it produces a value *in* the code. Worth calling out in
  the PR description.

Tests (`tests/test_gemini_vertex.py` + `conftest.py`) are written alongside
this, not after — `NOTES.md` flagged that this repo has zero test
infrastructure despite `pytest-asyncio` sitting in `requirements.txt`
unused; fixing that is part of implementation, not a separate cleanup pass.

### Environment: Docker, for agent isolation — three things before any code

1. **Mount ADC read-only**, don't copy it into the container: `-v
   ~/.config/gcloud:/root/.config/gcloud:ro`. Credentials live on the host at
   `~/.config/gcloud/application_default_credentials.json`.
2. **Network allowlist** needs `us-central1-aiplatform.googleapis.com` *and*
   `oauth2.googleapis.com` (token refresh) — the second one is easy to miss
   and would silently eat an hour of "why does auth fail" debugging if
   forgotten.
3. **Re-run the curl smoke test from inside the container as the first
   action**, before writing any provider code. Host-side success (Phase 0)
   proves nothing about container-side network/credential access — same
   discipline as Phase 0 itself, just re-applied to the new environment.

**Consistency rule for Phase 3 data**: every load-test run happens in the
same environment. Don't mix host-collected and container-collected latency
numbers in the same table — container overhead (or lack of it) is a
confound we're not trying to measure.

**Done (2026-08-14)**: re-ran the smoke test from inside a fresh container
(ADC mounted read-only, no gcloud CLI needed — token obtained directly via
`google-auth`'s credential refresh) → **200**, real response. Container-side
access confirmed before any code was touched, per the discipline above.
(Aside, not a formal finding: `thoughtsTokenCount` was 31 this run vs. 21 on
the host for the same trivial prompt — real data, but not part of a
controlled run, so it's not going in `FINDINGS.md`. Just a preview of what
Experiment 2 formalizes.)

## Phase 2.5: Tests

**Recheck (2026-08-14): the README never asks for a test suite** — it asks
for a working integration and load-test evidence, and explicitly says it's
"less interested in a completed checklist." Building tests as a checklist
item would be exactly the wrong instinct. The actual justification is
narrower: **the load test's numbers are only trustworthy if the response
mapping feeding them is correct.** Scope follows from that, not from "repos
should have tests":

1. **Keep — response-mapping tests** (`tests/test_gemini_vertex.py`, mocked
   SDK client, no credentials, small and targeted):
   - Happy-path mapping: mocked response → correct extended `SimpleResponse`.
   - **Thinking-token accounting** — the real `6 / 2 / 21 / 29` shape from the
     smoke test, locked in by a regression test. This is the one that
     directly protects the load test's cost numbers.
   - `finish_reason` mapping: `STOP` / `SAFETY` / `RECITATION` / `MAX_TOKENS`
     handled distinctly.
   - Fail-fast config: missing project/location raises a clear error at
     construction.
   - One test confirming `HttpRetryOptions` is actually configured on the
     client (not testing tenacity's internals — that's Google's job, not
     ours).
2. **Cut from "required," downgraded to optional** — a standing opt-in live
   `pytest` test. It would duplicate the smoke test already proven manually
   and the load harness's real traffic. Not worth the added complexity
   (skip markers, env gating) for what it'd add on top of those two.
3. **The load harness** (Phase 3) is a different job entirely — proving
   behavior under stress, not correctness of a single call. Most of the
   remaining effort should go here, not into test breadth.

`tests/` + `conftest.py` (mock fixtures for the Vertex client) is genuinely
new infrastructure for this repo — kept intentionally small. This is **the
code harness** — deterministic, mocked, answers "does the plumbing work,"
never "is the model's output good." See Phase 3 for the other two kinds.

## Phase 3: Three harnesses, not one — code, eval, and load

**Recheck (2026-08-14):** talked through the plan with a friend who works
in this space, to sanity-check what "harness" should actually mean here —
got back "you need one for code, another for eval." Correct instinct,
worth being precise about rather than treating "harness" as one undifferentiated
thing:

- **Code harness** (Phase 2.5, above) — is the plumbing correct? Deterministic,
  mocked, `assert x == y`. Doesn't touch a real model.
- **Eval harness** (below) — is the *model's* behavior good and stable? No
  single right answer for open-ended text, so this needs repeated sampling
  and statistical measurement instead of assertions. Independent of
  concurrency — you could run these experiments at concurrency=1.
- **Load harness** (below) — does the *system* hold up under concurrent
  traffic? Throughput, latency, error rates as a function of load. This is
  the axis the README explicitly asks about ("prove it holds up at
  production scale") that a two-way code/eval split doesn't cover on its
  own — Evertune's ask genuinely needs a third category.

The first draft of this phase (before this recheck) was a generic load test
— concurrency sweep, soak, burst, cost modeling — every item of which would
apply unchanged to a load test for a payments API. It also blended eval-shaped
questions (does output quality change with a setting? how stable is it?) in
with load-shaped ones (does it stay fast under concurrency?) as if they were
the same kind of experiment. They're not, and separating them below makes
`FINDINGS.md` read as two coherent sections instead of one blended pile.

Both harnesses share the same plumbing — one request-runner, one JSONL
schema, the same workload — so the split is conceptual and organizational,
not two separate codebases.

### Experiment tiering (2026-08-14) — cut by plan, not by panic

Seven experiments across both harnesses, each involving hundreds of
multi-second calls, with a Friday 8/21 commitment and no code written yet.
Tiering them now so a time crunch means dropping pre-identified optional
items, not rushing everything equally:

**Must run — these four cover all four of the README's write-up bullets:**
1. Thinking-budget sweep (eval)
2. Output variance under identical inputs (eval)
3. Concurrency sweep (load)
4. Retry amplification (load)

**Optional — only run if the four above land early:**
- Sustained soak
- Burst test
- Prompt-length variation

Three well-run experiments with clean data and sharp interpretation beat
seven half-finished ones. If time runs short, `FINDINGS.md` says which
optional items were skipped and why — no silent scope-cutting.

### The workload (shared by both)

The README's exact wording — "Pick a workload, run it, and tell us what you
observed" — means the choice of workload is itself part of what's graded, not
a throwaway detail. So: **category-style questions where a brand could
plausibly appear in the answer** — e.g. "best running shoes for marathon
training," "top project management tools for a small team," "most reliable
robot vacuums under $500." A set of roughly 10-15 such questions, each
sampled repeatedly. This mirrors Evertune's actual product surface, not a
generic "say hi" prompt — and it's what makes the variance experiment below
meaningful (repeated sampling of the same question is the whole point).

### Eval harness — is the model's behavior good and stable?

Both experiments here could run at concurrency=1. They're not about load at
all — they're about whether the model's *output* is trustworthy, which is
the thing Evertune's whole product depends on.

#### Experiment 1 (top billing): thinking-budget sweep

Verified against real source (`google/genai/types.py`), not assumed:
`GenerateContentConfig(thinking_config=ThinkingConfig(thinking_budget=N))` —
`0` disables thinking, `-1` is automatic, a positive int sets an explicit
token budget (range is model-dependent). A categorical alternative also
exists: `thinking_level` (`MINIMAL` / `LOW` / `MEDIUM` / `HIGH`).

Sweep across the workload: **disabled (0) / low / default (omit — automatic)
/ high**, measuring per level:
- Latency
- Visible output tokens vs. thinking tokens vs. total cost
- Whether the *content* of the answer changes materially — does disabling
  thinking change whether/how a brand gets mentioned?

Why this is the highest-value experiment available: it's the largest cost
lever we've found (21 of 29 billed tokens in the smoke test were invisible
thinking), it has no analogue in `Together` or any OpenAI-style provider, and
"what do we set this to in production" is a question Evertune has to answer
regardless of what we recommend. This is exactly "parameters that mattered,"
the README's own phrase.

**Folded into this experiment, not run separately: deliberately manufacture
the empty-response failure mode.** Thinking tokens consume the output budget
*before* any visible text is produced. So a constrained `max_output_tokens`
combined with a high thinking budget should be able to produce
`finish_reason=MAX_TOKENS` with **empty content** — billed for reasoning,
zero usable output, HTTP 200, no exception raised. Try to reproduce this
deliberately as part of the sweep (it's the same knobs, just pushed to an
extreme combination) rather than as a separate experiment. If reproducible,
it's a much stronger version of the finish-reason argument than "Gemini has
a field we don't capture" — it demonstrates a failure mode the *current
interface literally cannot express*: there's no `finish_reason` field in the
original `SimpleResponse`, so a provider built strictly to spec would return
an empty string here and look like a successful call.

#### Experiment 2 (top billing): output variance under identical inputs

Same prompt, **N=100**, identical params (fixed temperature) — quantify how
stable the output distribution actually is. Measured two ways: structural
(token-count distribution) and, where the workload plausibly elicits them,
content (which named brands/products appear across the 100 runs).

Why this matters specifically to Evertune, not generically: if they sample
model output to compute mention rates, variance is what determines how many
samples they need for a trustworthy number. High variance → their sampling
is expensive. Low variance → it's cheap. Either result is a genuinely useful
answer for them — and it's not something a generic load test would ever
surface, because it has nothing to do with concurrency or speed.

**Brand extraction method**: a hand-written candidate list per prompt (e.g.
for running shoes: Nike, Brooks, Hoka, Asics, New Balance, Saucony, Adidas),
matched case-insensitively as a substring against each response.
Mention rate = count / N. Limitations stated in `FINDINGS.md`, not hidden:
misses brands outside the list, substring matching produces some false
positives. Acceptable here because the experiment measures the *stability of
a measurement*, not a production-grade extractor — a consistent-but-imperfect
method is adequate for that, as long as it's applied identically across all
N runs. **Explicitly not doing LLM-based extraction**: that inserts a second
stochastic process inside a variance measurement, and we'd lose the ability
to attribute observed variance to the model rather than to the extractor.

**Run params**: fix temperature at 2-3 distinct points rather than one — the
actionable question for Evertune is how much temperature buys them in
mention-rate stability, not just "is there variance." Also check explicitly
whether `temperature=0` actually produces identical output across all N —
if it doesn't, that's a finding in itself (worth stating plainly either
way, not just assumed).

#### Silent failures, reframed as sampling bias

A `200` with an empty/missing answer (`finish_reason=SAFETY` or
`RECITATION`, no visible text) is a **dropped sample**, not a generic error
— which is why this belongs to the eval harness, not the load harness's
failure-mode catalogue below. These aren't randomly distributed —
`SAFETY`/`RECITATION` blocks correlate with the content being discussed,
which means dropped samples correlate with content. That's sampling bias in
whatever dataset Evertune builds from repeated queries, not just an error
rate to report in a table. Empty-response rate is tracked as a first-class
metric here, and `FINDINGS.md` frames it as a methodology concern — "this
affects how trustworthy your mention-rate numbers are" — not just "the API
failed sometimes." (It still shows up once more in the load harness's raw
data, tagged by `error_class` — but the *interpretation* lives here.)

### Load harness — does the system hold up under concurrent traffic?

The axis the README explicitly asks about. Throughput, latency, and error
rates as a function of load — not model-quality questions.

#### Bounded load — stated explicitly, not left implicit

`evertune-tests` has **10 forks and 4 open PRs** right now — other
candidates are plausibly hitting the same project/region quota concurrently.
This is shared infrastructure, not a private sandbox. The concurrency sweep
gets a deliberate, stated ceiling (e.g. 1 → 5 → 10 → 25 → 50, stop there
absent a clear reason to push further) rather than open-ended escalation —
and the blast-radius reasoning for that ceiling goes in `FINDINGS.md`
explicitly, not left for a reader to assume we just ran out of time.

#### Retry amplification — an experiment, not just a config choice

The SDK defaults to zero retries (confirmed in Phase 1). The interesting
question isn't "should we enable retries" in the abstract — it's what
happens when we turn them on specifically *under quota pressure*, near the
ceiling found above:
- Retrying a `429` adds load exactly when the system signaled it's
  overloaded — can retries deepen a brownout rather than smooth over it?
- Retrying a request that already burned thousands of thinking tokens is
  expensive in a way retrying a short/cheap request isn't.

Run the same near-ceiling concurrency with retries off vs. on, and measure:
retry-attributable cost (tokens spent on attempts that got retried), and
whether enabling retries improves or worsens *effective* throughput.

#### Optional experiments — only if the must-run four land early

Per the tiering decision above: real experiments, not filler, but explicitly
the first things cut if 8/21 gets tight.

- **Sustained soak** — moderate concurrency held for several minutes, to
  catch what a short burst won't (rate-limit window resets, slow leaks).
- **Burst test** — idle → sudden spike.
- **Prompt-length variation** — short vs. long prompts from the workload set.

**Failure-mode catalogue** is not tiered separately — it's a derived view
over data the must-run concurrency-sweep and retry-amplification experiments
already collect (every request's `error_class` is in the JSONL regardless),
not an additional run of its own. Classified: `429` quota, `5xx`, timeout,
safety-blocked. (Empty-response/safety-blocked counts appear here too, but
see the eval harness above for what they mean.)

### Raw data — committed, not just summarized (shared by both harnesses)

Every request writes one JSONL line: `timestamp, environment,
concurrency_level, latency_ms, input_tokens, visible_output_tokens,
thinking_tokens, total_tokens, finish_reason, error_class, attempt_number,
thinking_budget_setting, max_output_tokens_setting, retry_attempts_setting,
temperature`.
`environment` (`host` / `container`) exists specifically so host-collected
and container-collected runs are never silently averaged together in
analysis — see Phase 2's Docker consistency rule. The harness's only job is
writing this file;
analysis (every chart/table in `FINDINGS.md`) is a **separate script** that
reads it. The JSONL file gets committed to the repo — that's the difference
between claiming numbers and evidencing them, and it means the numbers can
be re-analyzed without re-running the whole load test.

## Phase 4: Findings report (the write-up)

**Recheck (2026-08-14): `FINDINGS.md` at repo root, not `docs/findings.html`.**
Reversing the earlier HTML-report plan — markdown renders inline in the
GitHub PR diff; HTML requires cloning the repo and opening a browser.
Evertune's review thread has seven people on it and most will read this on
GitHub itself, not clone a branch to view a webpage. Chart images get
committed as PNG/SVG and referenced from the markdown via image links — no
`dataviz`-skill HTML artifact for this one.

**Hard rule from here forward: every number that appears in `FINDINGS.md`
must trace to a committed JSONL record from a run that actually happened.**
No illustrative figures, no placeholder tables, no plausible-looking numbers
pending a real run. If a section can't be written yet because the run hasn't
happened, it stays empty with a note saying why — never filled with a
placeholder that looks like a result.

- Built **after** both harnesses run, generated from the committed JSONL
  records — never authored ahead of the data.
- Two headline sections mirroring the eval/load split, not one blended pile:
  **eval findings** (thinking-budget sweep, output variance / sampling bias)
  get top billing, since they're specific to what Evertune measures; **load
  findings** (concurrency ceiling, retry amplification, failure catalogue)
  follow. Structured against the README's four write-up bullets within each.
- Raw per-request JSONL committed alongside (e.g. `loadtest/results/*.jsonl`);
  the analysis that produces `FINDINGS.md`'s charts/tables is a separate
  script over that file, not computed inline in either harness.
- Linked from the PR description.

## Confirmed by Evertune's kickoff email (2026-08-14)

- Project: `evertune-tests` (project number `480179867040`) — confirmed, and
  we're authenticated against it.
- Region: **`us-central1`** — explicitly specified ("resources for this
  project are provisioned there"), matches what we'd assumed as a default.
- Auth: **ADC only, no API key** — `gcloud auth application-default login`,
  exactly the SDK+ADC decision already made above. Completed.
- Repo flow confirmed: fork → branch → implement → push branch to fork → PR
  from fork to `Evertune-AI/takehome:main` → send link. Verified `ejf89/evertune`
  actually is a fork of `Evertune-AI/takehome` (renamed from the default fork
  name) via `gh repo view` — `gh` is authenticated with `repo` scope, so
  push/PR will work when we get there. Already on branch
  `gemini-vertex-integration`.
- ~~Timeline estimate~~ **Sent — committed to Friday, 2026-08-21.**

## Open questions for you

- ~~Any constraint on how much load we're allowed to throw at
  `evertune-tests`?~~ **Resolved without needing to ask** — the repo itself
  shows 10 forks and 4 open PRs, meaning other candidates are plausibly on
  the same project/region quota concurrently. Phase 3's bounded-load section
  above treats this as a known constraint (deliberate ceiling, stated
  reasoning) rather than something to check with Evertune first.
- ~~The `serviceusage.services.use` gap found earlier~~ **Resolved/closed** —
  the smoke test returned 200 despite it, so it doesn't block
  `generateContent` calls. Only affects the unrelated ADC quota-project
  setting, which we don't need.

No open questions remain blocking Phase 2.
