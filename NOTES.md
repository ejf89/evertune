# Repo Notes — Evertune LLM Provider Layer

Scope: everything under `llm/` plus `requirements.txt` and `README.md`. The whole
repo is 4 source files and ~55 lines of code total — there is very little given
structure, and several things the task will require (config loading, error
handling, retries, tests) simply don't exist yet. Ambiguities are called out
explicitly rather than assumed.

## 1. Provider interface

Defined in `llm/llm.py:3-14`. It's a plain Python class, **not** `abc.ABC` and
**not** `typing.Protocol` — just a base class whose methods raise
`NotImplementedError()`. Nothing enforces that a subclass overrides them; you'd
only find out at call time.

Two methods make up the contract:

- `async def ask_generic_question(self, system_prompt: str, question: str, temperature: float) -> SimpleResponse`
  (`llm/llm.py:10-11`). Async. Takes exactly one system prompt, one user
  question, one temperature. No message history, no other sampling params in
  the signature.
- `def parallelism(self)` (`llm/llm.py:13-14`). Sync, no type annotation on
  params or return. `Together.parallelism()` returns a bare `int` (`llm/together.py:12-13`,
  returns `100`), so by convention this is "max concurrent requests this
  provider can sustain" — but that's inferred from the one implementation, not
  stated anywhere.

Only one concrete implementation exists today: `Together` (`llm/together.py:7`).

## 2. Response/result object

`LLM.SimpleResponse` — a `@dataclass` nested inside `LLM` (`llm/llm.py:4-8`).
Fields:

- `answer: str`
- `input_tokens: int`
- `output_tokens: int`

That's the entire object. There is **no field for**:
- finish/stop reason
- model name or model version actually served
- request id / trace id
- latency
- multiple candidates, tool calls, or logprobs (Together's call even requests
  `logprobs=1` at `llm/together.py:22`, but that data is discarded — `SimpleResponse`
  has nowhere to put it)

`Together.ask_generic_question` populates it by reading
`response.choices[0].message.content` and `response.usage.prompt_tokens` /
`response.usage.completion_tokens` (`llm/together.py:26-29`) — i.e. it maps two
specific OpenAI-shaped usage fields into `SimpleResponse` and throws everything
else in the vendor response away. If you need finish reason or model metadata
for Gemini, there is currently no home for it in this object — you'd need to
either extend `SimpleResponse` or decide that's out of scope.

## 3. Error handling

There is none. No `try`/`except` anywhere in `llm/llm.py` or `llm/together.py`.
No custom exception classes. No retry library in `requirements.txt` (no
`tenacity`, `backoff`, no manual retry loop). `NotImplementedError` is only used
as the base class's "you must override this" marker — it's a design-time
contract, not a runtime failure signal.

Practically: today, any failure from the Together SDK (rate limit, timeout,
malformed response, auth error) propagates as whatever exception the SDK
raises, uncaught, straight to the caller. **There is no caller code in this
repo** (no `main.py`, no orchestrator, no example of `ask_generic_question`
being invoked) — so what a caller is expected to do with an exception, or
whether retry/backoff is supposed to live inside the provider or outside it, is
completely unspecified by the existing code. This is a real decision point for
the Gemini provider, not something to infer from precedent.

## 4. Concurrency

`ask_generic_question` is `async def` in both the base class and `Together`
(`llm/llm.py:10`, `llm/together.py:15`), so the library is async end-to-end and
uses an async SDK client (`AsyncTogether`, `llm/together.py:2,9`).

`parallelism()` (`llm/llm.py:13-14`, `llm/together.py:12-13`) returns an int
that reads as "how many of these can I run concurrently," but nothing in the
repo consumes it — no semaphore, no worker pool, no batching helper. The
provider only *advertises* a suggested fan-out width; there's no evidence the
library enforces or uses it internally.

**Conclusion: concurrency orchestration is the caller's job.** The library
gives you an async method and a hint number; running N coroutines concurrently
(e.g. `asyncio.gather` behind a `asyncio.Semaphore(provider.parallelism())`) is
something you'd have to build, either as part of the provider package or as a
harness around it. Given the README's "prove it holds up at production scale"
ask, this is one of the central design decisions for the exercise.

## 5. Config

Together reads its credentials and model name straight from environment
variables in `__init__`:
- `os.getenv("TOGETHER_API_KEY")` (`llm/together.py:9`)
- `os.getenv("TOGETHER_MODEL")` (`llm/together.py:10`)

No config file, no `pydantic`-style settings object, no constructor arguments
for these. `temperature` is the only parameter that's passed per-call, as an
argument to `ask_generic_question` (`llm/llm.py:10`, `llm/together.py:15,23`).
Every other sampling parameter is not part of the interface at all —
`logprobs=1` is hardcoded inside `Together.ask_generic_question`
(`llm/together.py:22`), not exposed or configurable.

`.gitignore` excludes `.env` (appears twice: under "Secrets / Configuration"
and again under "Environments") which implies a `.env` file is expected to
exist locally, but nothing in `requirements.txt` or the source loads one (no
`python-dotenv` import anywhere) — so either env vars are expected to be
exported into the shell/deployment environment directly, or a dotenv-loading
step is missing. Ambiguous; not resolved by the repo.

For Gemini on Vertex, note this env-var-API-key pattern won't map cleanly —
Vertex auth is normally ADC / service-account / `gcloud auth` based (the
README explicitly calls out needing the `gcloud` CLI configured), not a bearer
API key read from an env var. That's a concrete deviation point.

## 6. Testing

`requirements.txt` (2 lines total):
```
pytest-asyncio==1.3.0
together==2.12.0
```
`pytest` itself isn't pinned directly (it'll come in transitively via
`pytest-asyncio`, but there's no explicit version pin for it). There are:
- no test files
- no `tests/` directory
- no `conftest.py`
- no `pytest.ini` / `pyproject.toml` / `setup.cfg` with pytest config
  (checked — none exist in the repo)

So test infrastructure is *signaled but not present*: the presence of
`pytest-asyncio` tells you async tests are expected, but there's no example of
how a provider is meant to be instantiated/mocked in a test today. You're
building this from scratch.

One more structural wrinkle worth flagging for testing/packaging: `llm/__init__.py:1-2`
uses relative imports (`from .llm import LLM`, `from .together import
Together`), but `llm/together.py:5` uses an absolute import (`from llm import
LLM`, not `from .llm import LLM`). That only resolves if `llm/`'s *parent*
directory is on `sys.path` — there's no `pyproject.toml`/`setup.py`/`pip
install -e` in the repo to pin down how this package is actually meant to be
installed or run, or from what working directory. Worth resolving before
wiring up a test runner or your own entrypoint, since it affects how imports
need to be written.

## 7. OpenAI-style assumptions baked into the current shape

- **Single-turn, two-message shape.** `ask_generic_question(system_prompt,
  question, temperature)` (`llm/llm.py:10`) hardcodes exactly one system
  message + one user message, mapped directly onto
  `MessageChatCompletionSystemMessageParam` / `MessageChatCompletionUserMessageParam`
  (`llm/together.py:19-20`) — an OpenAI-chat-completions-shaped message list.
  No multi-turn history, no multimodal parts, no tool/function-call turns.
- **One string answer, no candidates/streaming.** `SimpleResponse.answer: str`
  (`llm/llm.py:6`) assumes exactly one completion comes back
  (`response.choices[0].message.content`, `llm/together.py:27`). No `n>1`, no
  streaming, no partial/incremental output.
- **Usage fields named/shaped like OpenAI's.** `input_tokens`/`output_tokens`
  are populated from `response.usage.prompt_tokens` /
  `response.usage.completion_tokens` (`llm/together.py:28-29`) — literally
  OpenAI's usage-object field names. Vertex/Gemini's equivalent lives under a
  differently-shaped `usageMetadata` (`promptTokenCount` /
  `candidatesTokenCount`), so this mapping doesn't carry over 1:1.
- **No finish-reason concept at all.** OpenAI/Together-style APIs put
  `finish_reason` on the choice, and it's simply not captured here. Gemini
  finish reasons that matter operationally — `SAFETY`, `RECITATION`,
  `MAX_TOKENS` vs. a normal `STOP` — currently have nowhere to go in
  `SimpleResponse`. A Gemini provider built strictly to this interface would
  silently drop that signal unless the object is extended.
- **Flat `temperature: float`, no generation-config object.** Gemini's request
  shape groups sampling params (`temperature`, `topP`, `topK`,
  `maxOutputTokens`) under a `generationConfig` object, plus separate
  `safetySettings`. The current interface only has a slot for temperature.
- **API-key-bearer auth baked into the constructor pattern.** `Together.__init__`
  pulls a bearer API key straight from an env var (`llm/together.py:9`). Vertex
  auth (ADC / service account / `gcloud`) doesn't fit that pattern — flagged
  above in Config as well.

## Open questions / things the repo doesn't resolve

- Where retry/backoff logic should live (inside a provider, inside a shared
  base, or in caller code) — no precedent exists.
- Whether `parallelism()` is meant to be enforced by the library or is purely
  advisory metadata for an external harness — no consuming code exists.
- How the package is actually installed/run given the mixed relative/absolute
  import style (`llm/__init__.py:1-2` vs `llm/together.py:5`).
- Whether `.env` is expected to be loaded automatically — `.gitignore` implies
  its existence but nothing loads it.
