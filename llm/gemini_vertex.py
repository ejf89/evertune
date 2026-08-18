"""Gemini provider on Vertex AI.

Design rationale lives in PLAN.md / NOTES.md at the repo root, not repeated
here as comments — in particular: why SimpleResponse is extended rather than
left as-is, why retry is configured on the SDK rather than hand-rolled, and
the thinking-token accounting decision below.

SDK facts referenced here were verified against the installed `google-genai`
source directly (not just docs) — see PLAN.md's Phase 1.
"""

import os
import time
from typing import Optional

from google import genai
from google.genai import types

from .llm import LLM

# Not hardcoded to Gemini 2.5 Flash — `model` is a config value, same idea as
# Together's TOGETHER_MODEL env var. Pointing this at gemini-2.5-pro, or a
# future model, is a config change, not a code change.
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_LOCATION = "us-central1"

# Measured, not asserted (PLAN.md's Phase 2 commitment): the concurrency
# sweep against evertune-tests/us-central1 (see FINDINGS.md) found flat
# latency and zero errors at every level up to 150. IMPORTANT — this is the
# top of the *flat* zone, not evidence the system is unbounded above it:
# a further escalation (same FINDINGS.md, "Escalation past 150") pushed to
# 2,000 concurrent and found latency grows almost perfectly linearly above
# ~150-250 (p50 42, then it queues rather than erroring — 87s p50 at 2,000
# concurrent, still zero HTTP errors). So going meaningfully above this
# default won't throw exceptions, but it will get slow in direct proportion
# to how far over ~150-250 you push it; that escalation data also hasn't
# been confirmed as a real Vertex-side limit vs. a single-test-process
# artifact (see the caveat in FINDINGS.md) — don't treat the implied
# ~1,400 req/min figure as load-bearing without re-verifying it. Override
# via `concurrency=` if a different project/model/region needs its own
# sweep.
DEFAULT_CONCURRENCY = 150

# The SDK defaults to zero retries unless HttpRetryOptions is supplied
# (verified in google/genai/_api_client.py). This is that supply step; the
# attempt count is the only thing we override, everything else (backoff
# shape, which HTTP codes are retryable: 408/429/500/502/503/504) comes from
# the SDK's own defaults, which are sane and shouldn't be reinvented without
# a concrete reason from the load test.
DEFAULT_RETRY_ATTEMPTS = 5


class GeminiVertex(LLM):
    """Gemini models served through Vertex AI.

    Auth is ADC (`gcloud auth application-default login`), not an API key —
    Vertex has no equivalent of Together's bearer token. See NOTES.md for why
    that's a real structural difference from the existing provider, not just
    a config difference.
    """

    def __init__(
        self,
        project: Optional[str] = None,
        location: Optional[str] = None,
        model: Optional[str] = None,
        concurrency: Optional[int] = None,
        retry_attempts: Optional[int] = None,
        thinking_budget: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        client: Optional[genai.Client] = None,
    ):
        """
        Args:
            project: GCP project ID. Falls back to GOOGLE_CLOUD_PROJECT.
                Required unless `client` is supplied.
            location: Vertex region. Falls back to GOOGLE_CLOUD_LOCATION,
                then DEFAULT_LOCATION.
            model: Gemini model name. Falls back to GEMINI_MODEL env var,
                then DEFAULT_MODEL. See DEFAULT_MODEL's comment above.
            concurrency: value returned by parallelism(). Falls back to
                GEMINI_CONCURRENCY env var, then DEFAULT_CONCURRENCY.
                `concurrency=0` is respected as an explicit override (not
                silently treated as "unset") — same `is not None` guard as
                the other config below, rather than Python's usual falsy-`or`
                fallback pattern, which would otherwise swallow a real `0`.
            retry_attempts: passed to the SDK's HttpRetryOptions. 1 disables
                retries. Falls back to GEMINI_RETRY_ATTEMPTS env var, then
                DEFAULT_RETRY_ATTEMPTS — same convention as `model`/
                `concurrency`. **Only takes effect when this constructor
                builds its own client** (i.e. `client=` is not supplied) —
                see the `client` param below.
            thinking_budget: tokens the model may spend on hidden "thinking"
                before answering. 0 disables it, -1 is automatic, a positive
                int sets an explicit budget. None (default, and the
                GEMINI_THINKING_BUDGET env var if set) omits the setting
                entirely and lets the model use its own default — a
                deliberate per-*instance* config choice, not a per-call one;
                see PLAN.md's Phase 1 for the tradeoff this implies. The
                thinking-budget sweep (PLAN.md Phase 3) instantiates one
                GeminiVertex per level rather than varying this per call.
            max_output_tokens: caps visible output length. None omits the
                setting. Paired with a high `thinking_budget`, this is also
                the knob used to deliberately probe the
                finish_reason=MAX_TOKENS-with-empty-content failure mode
                (PLAN.md Phase 3).
            client: pre-built genai.Client, for tests or for sharing one
                client across multiple GeminiVertex instances (e.g. a load
                harness sweeping `model` or `concurrency` without
                reconnecting each time). When supplied, `project`/`location`/
                `retry_attempts` are ignored for client construction — the
                client already has its own retry configuration baked in
                (or none at all). `retry_attempts` is still stored and
                readable via the `.retry_attempts` property in this case
                (the load harness's JSONL logging depends on that), but it
                is then purely a label, not a guarantee about what the
                injected client will actually do on a retryable error —
                don't rely on it to mean "retries are configured this way"
                unless you built the client yourself with matching
                HttpRetryOptions.
        """
        self.__model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.__concurrency = (
            concurrency
            if concurrency is not None
            else int(os.getenv("GEMINI_CONCURRENCY", DEFAULT_CONCURRENCY))
        )
        env_thinking_budget = os.getenv("GEMINI_THINKING_BUDGET")
        self.__thinking_budget = (
            thinking_budget
            if thinking_budget is not None
            else (int(env_thinking_budget) if env_thinking_budget is not None else None)
        )
        self.__max_output_tokens = max_output_tokens
        self.__retry_attempts = (
            retry_attempts
            if retry_attempts is not None
            else int(os.getenv("GEMINI_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS))
        )

        if client is not None:
            self.__client = client
            return

        project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
        location = location or os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)

        if not project:
            raise ValueError(
                "GeminiVertex requires a GCP project — pass project=... or "
                "set GOOGLE_CLOUD_PROJECT. (Vertex AI has no API-key auth; "
                "identity comes from `gcloud auth application-default "
                "login`, not an env-var secret like Together uses.)"
            )

        self.__client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=self.__retry_attempts),
            ),
        )

    @property
    def model(self) -> str:
        """Which model this instance actually calls — useful for logging
        (e.g. the load harness's JSONL records) without reaching into
        private state."""
        return self.__model

    @property
    def thinking_budget(self) -> Optional[int]:
        """For logging (e.g. the eval harness's `thinking_budget_setting`
        JSONL field) — None means "not set, model default"."""
        return self.__thinking_budget

    @property
    def max_output_tokens(self) -> Optional[int]:
        """For logging (e.g. the eval harness's `max_output_tokens_setting`
        JSONL field)."""
        return self.__max_output_tokens

    @property
    def retry_attempts(self) -> int:
        """For logging (the load harness's `retry_attempts_setting` JSONL
        field) — the retry-amplification experiment needs its two runs
        (retries off vs on) to be distinguishable in the data itself."""
        return self.__retry_attempts

    def parallelism(self):
        return self.__concurrency

    async def ask_generic_question(
        self, system_prompt: str, question: str, temperature: float
    ) -> LLM.SimpleResponse:
        config_kwargs = {
            "system_instruction": system_prompt,
            "temperature": temperature,
        }
        # Omitted entirely (not passed as 0/None-valued fields) when unset,
        # so the model's own default behavior applies rather than us
        # accidentally asserting a specific value we didn't intend.
        if self.__thinking_budget is not None:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.__thinking_budget
            )
        if self.__max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = self.__max_output_tokens

        start = time.monotonic()
        response = await self.__client.aio.models.generate_content(
            model=self.__model,
            contents=question,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        latency_ms = (time.monotonic() - start) * 1000

        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage else 0
        # Gemini 2.5 Flash is a reasoning model: it spends "thinking" tokens
        # before the visible answer. Google bills for them
        # (thoughts_token_count is part of total_token_count) but they are
        # NOT part of candidates_token_count (the visible answer alone).
        # Folded in here so SimpleResponse.output_tokens reflects real spend
        # instead of silently undercounting it on every call — see PLAN.md's
        # "First real finding" for the numbers that surfaced this.
        thinking_tokens = (usage.thoughts_token_count or 0) if usage else 0
        output_tokens = ((usage.candidates_token_count or 0) if usage else 0) + thinking_tokens

        candidate = response.candidates[0] if response.candidates else None
        finish_reason = (
            candidate.finish_reason.value
            if candidate and candidate.finish_reason
            else None
        )

        return LLM.SimpleResponse(
            answer=response.text or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
            model=response.model_version,
            latency_ms=latency_ms,
            thinking_tokens=thinking_tokens,
        )
