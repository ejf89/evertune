from dataclasses import dataclass
from typing import Optional

class LLM:
    @dataclass
    class SimpleResponse:
        answer: str
        input_tokens: int
        # WARNING — output_tokens does not mean the same thing across
        # providers, and that's deliberate, not an oversight: it is always
        # the honest *billed* generation cost, so `input_tokens +
        # output_tokens` is always real spend. For Together (and any other
        # non-reasoning provider) that equals the visible answer's length,
        # since there's nothing else to bill. For Gemini it's
        # visible + thinking (see thinking_tokens below) — thinking tokens
        # are billed but never appear in `answer`, so folding them in here
        # is what makes cost accounting correct rather than a silent
        # undercount (PLAN.md's first finding). The cost: this field is
        # NOT "how long is the answer" for every provider. Use
        # `visible_output_tokens` below when you specifically want the
        # length of `answer`, not total spend.
        output_tokens: int
        # Optional and defaulted so existing callers (Together) are unaffected.
        # Added for Gemini: today's OpenAI-shaped providers have no finish
        # reason or model metadata to report, but Gemini's finish_reason
        # (SAFETY / RECITATION / MAX_TOKENS vs. a normal STOP) is operationally
        # important enough that dropping it silently isn't acceptable. See
        # PLAN.md's Phase 1 for the reasoning.
        finish_reason: Optional[str] = None
        model: Optional[str] = None
        latency_ms: Optional[float] = None
        # The breakdown backing output_tokens' definition above. None for
        # providers with no such concept (Together) or when the model
        # didn't report it — never a second source of truth that could
        # drift out of sync with output_tokens, since output_tokens is
        # always defined as visible + this.
        thinking_tokens: Optional[int] = None

        @property
        def visible_output_tokens(self) -> int:
            """Length of `answer` alone, in tokens — output_tokens minus
            any hidden thinking spend. Use this (not output_tokens) for
            anything that means "how long was the visible answer" (e.g.
            checking whether a response got truncated); use output_tokens
            for anything that means "what did this call cost." The two
            are the same number for providers with no thinking concept."""
            return self.output_tokens - (self.thinking_tokens or 0)

    async def ask_generic_question(self, system_prompt: str, question: str, temperature: float) -> SimpleResponse:
        raise NotImplementedError()

    def parallelism(self):
        raise NotImplementedError()