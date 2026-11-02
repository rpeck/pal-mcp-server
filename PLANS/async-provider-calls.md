# Plan: async calls to the model providers

Status: to consider, not started. It follows the parallel tool calls change (PR #11), which runs each model call in
a worker thread. Do step 0 first: its result decides whether the rest is worth doing.

## Goal

Make a model call that the client cancels stop at once, and remove the limit on parallel model calls that the
worker pool sets.

## What PAL does now

- Each tool calls the synchronous `provider.generate_content(...)` through `run_model_call`
  (`utils/model_calls.py`), in a pool of `PAL_MAX_PARALLEL_MODEL_CALLS` threads (default 16). More calls wait in a
  queue.
- When the client cancels a tool call, a queued model call never starts, a Codex process is killed, a `clink`
  CLI is killed, and no retry starts.
- A running HTTP request to a provider cannot be stopped. A thread cannot be interrupted from outside, so the
  request runs until the provider answers, and PAL drops the result. The thread stays busy until then.

## What async calls would change

- A canceled call cancels its asyncio task, and the HTTP client closes the connection at once.
- No thread is busy while a request waits for the provider, so the pool limit no longer applies to HTTP calls.

## Step 0: check what a closed connection saves

Closing the connection frees PAL's resources. It does not prove that the provider stops generating, or stops
billing. Check this for each main provider before any code change:

1. Send a long, non-streaming request (for example a prompt that asks for 4,000 words).
2. Close the connection after 5 seconds.
3. Read the billed output tokens in the provider's usage dashboard or usage API.
4. Do steps 1 to 3 again with a streaming request.

| Provider | Non-streaming: billing stops? | Streaming: billing stops? |
|---|---|---|
| OpenAI | to measure | to measure |
| Gemini | to measure | to measure |
| Anthropic | to measure | to measure |
| OpenRouter | to measure | to measure |

If billing continues in all cases, the gain is only the free thread. Then do this plan only if the logs show
that calls often wait in the queue: search `mcp_server.log` for "waits in the queue" and "canceled the call".
If billing stops only for streaming requests, the async path must also stream.

## Design

Add an async path next to the sync one, and move one provider at a time.

1. Add `async def agenerate_content(...)` to `ModelProvider` (`providers/base.py`), with the same parameters as
   `generate_content`. The default body is `return await run_model_call(self.generate_content, ...)`, so a
   provider without an async version works as it does now.
2. Add an async retry loop next to `_run_with_retries`, with `asyncio.sleep` in place of `time.sleep`.
3. Change the four call sites to `await provider.agenerate_content(...)`: `tools/simple/base.py` (call and
   retry), `tools/workflow/workflow_mixin.py`, `tools/consensus.py`.
4. Override `agenerate_content` provider by provider, in this order:

| Order | Provider file | Async client | Covers |
|---|---|---|---|
| 1 | `providers/openai_compatible.py` | `openai.AsyncOpenAI` | OpenAI, OpenRouter, xAI, DeepSeek, Mistral, Moonshot, Qwen, Z.ai, MiniMax, NVIDIA, Perplexity, custom |
| 2 | `providers/gemini.py` | `client.aio.models.generate_content` (google-genai) | Gemini |
| 3 | `providers/anthropic.py` | `anthropic.AsyncAnthropic`; check for an async Foundry client | Anthropic, Foundry |
| 4 | `providers/azure_openai.py` | `openai.AsyncAzureOpenAI`, with an async Entra token provider | Azure OpenAI |
| 5 | `providers/dial.py` | `openai.AsyncOpenAI` per deployment | DIAL |
| none | `providers/chatgpt.py` | Keep `run_cancellable`: a cancel already kills Codex | ChatGPT |

## Risks

- **Two code paths per provider** until the sync path goes away. The request building and the response parsing
  must be shared functions, so that only the client call differs.
- **Event loops**: an `httpx.AsyncClient` belongs to the event loop that first uses it. PAL has one loop, but the
  tests make one loop per test. Create the async client per loop, or per call.
- **Client settings**: `openai_compatible.py` builds its `httpx.Client` with proxy and timeout settings. The
  `httpx.AsyncClient` needs the same settings.
- **Tests**: 27 test files mock `generate_content` (138 references). The default `agenerate_content` keeps those
  tests working. A test that mocks a provider's HTTP client (for example `client.chat.completions.create`) needs
  an async mock when its provider gets its own `agenerate_content`.

## Done when

- A canceled call to each converted provider closes its connection within 1 second (a unit test with a slow fake
  server).
- The step 0 table is filled in, and `docs/configuration.md` says which providers stop billing on a cancel.
- The full test suite passes.
