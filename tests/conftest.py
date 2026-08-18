"""Shared fixtures for the code harness — see PLAN.md's Phase 2.5/3 recheck
for what "code harness" means here and how it differs from the eval and load
harnesses in loadtest/. This file mocks nothing about the model's behavior;
it only stands in for the network boundary (genai.Client), so tests exercise
GeminiVertex's own mapping/config logic deterministically.
"""

from unittest.mock import AsyncMock, MagicMock

from google.genai import types


def make_response(
    text="Hi.",
    prompt_tokens=6,
    candidates_tokens=2,
    thoughts_tokens=21,
    total_tokens=29,
    finish_reason=types.FinishReason.STOP,
    model_version="gemini-2.5-flash-002",
    include_usage=True,
    include_candidate=True,
):
    """Builds a real google.genai.types.GenerateContentResponse (not a bare
    mock) so a field-name typo in gemini_vertex.py fails the test instead of
    silently returning a MagicMock stand-in. Defaults match the actual live
    smoke test response recorded in PLAN.md (6 / 2 / 21 / 29 tokens).
    """
    candidates = None
    if include_candidate:
        candidates = [
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=text)] if text else [],
                ),
                finish_reason=finish_reason,
            )
        ]

    usage = None
    if include_usage:
        usage = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
            thoughts_token_count=thoughts_tokens,
            total_token_count=total_tokens,
        )

    return types.GenerateContentResponse(
        candidates=candidates,
        usage_metadata=usage,
        model_version=model_version,
    )


def make_fake_client(response=None, side_effect=None):
    """Stand-in for genai.Client exposing only what GeminiVertex actually
    touches: client.aio.models.generate_content(...). Deliberately not a
    MagicMock of the real Client class — GeminiVertex's `client=` injection
    point (see gemini_vertex.py) only needs this one method, and asserting
    against a narrow fake keeps these tests honest about what the code
    under test actually calls.
    """
    client = MagicMock()
    if side_effect is not None:
        client.aio.models.generate_content = AsyncMock(side_effect=side_effect)
    else:
        client.aio.models.generate_content = AsyncMock(
            return_value=response if response is not None else make_response()
        )
    return client
