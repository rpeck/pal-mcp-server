# Model catalog (measured models and explicit-only presets)

Generated from `conf/*_models.json` by `scripts/generate_model_catalog.py` on 2026-10-28. Do not edit by hand; run the script after a catalog change.

- **Scope:** every model with a measured Artificial Analysis (AA) Intelligence Index value, plus the explicit-only presets. Older and unmeasured entries are in the conf files only.
- **Score:** the 1-20 `intelligence_score` that auto mode ranks by. See [model_ranking.md](model_ranking.md) for how it is anchored to the AA Index.
- **AA Index by effort:** the AA Intelligence Index v5 leaderboard value at each effort level (`aa_index_by_effort`). `default` means AA lists one row for the model.
- **Routes:** `native` is the creator's own API (needs that creator's key); `openrouter` is the OpenRouter mirror (needs `OPENROUTER_API_KEY`); `chatgpt` is a ChatGPT plan through the Codex CLI (needs `CHATGPT_ENABLED=true` and `codex login`, see [chatgpt_provider.md](chatgpt_provider.md)).
- **Explicit-only:** `auto_selectable: false`. Auto mode never picks the model; call it by name.
- **Flag-only aliases:** these bare aliases point at the model only when `DYNAMIC_MODEL_SELECTION` is on; otherwise they keep the target that upstream PAL gives them.

## Anthropic

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Claude Opus 5.5 | 20 | low 35, medium 48, high 52, xhigh 57, max 58 | native: `claude-opus-5-5`<br>openrouter: `anthropic/claude-opus-5.5` | `claude-opus`, `claude-opus-5.5`, `opus`, `opus-5.5` |  |
| Claude Fable 5.1 | 19 | low 39, medium 43, high 47, xhigh 50, max 49 | native: `claude-fable-5-1`<br>openrouter: `anthropic/claude-fable-5.1` | `claude-fable-5.1`, `fable`, `fable-5.1` |  |
| Claude Sonnet 5.5 | 18 | low 25, medium 30, high 41, xhigh 50, max 54 | native: `claude-sonnet-5-5`<br>openrouter: `anthropic/claude-sonnet-5.5` | `claude-sonnet`, `claude-sonnet-5.5`, `sonnet`, `sonnet-5.5` |  |
| Claude Haiku 5.5 | 16 | low 19, medium 25, high 30, xhigh 34, max 36 | native: `claude-haiku-5-5`<br>openrouter: `anthropic/claude-haiku-5.5` | `claude-haiku`, `claude-haiku-5.5`, `haiku`, `haiku-5.5`, `or-claude-haiku` |  |

## OpenAI

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| OpenAI (GPT-6.1 Sol) | 20 | low 35, medium 46, high 50, xhigh 52, max 55 | native: `gpt-6.1-sol`<br>openrouter: `openai/gpt-6.1-sol`<br>chatgpt: `chatgpt/gpt-6.1-sol` | `gpt6.1-sol`, `gpt6.1sol`, `or-gpt6.1-sol`, `sol` |  |
| GPT-6.1 Sol Pro | 20 | not measured | openrouter: `openai/gpt-6.1-sol-pro` | `gpt-6.1-sol-pro`, `or-gpt6.1-sol-pro` | explicit-only, provisional score |
| OpenAI (GPT-6 Astra) | 20 | low 43, medium 52, high 55, xhigh 55, max 57 | native: `gpt-6-astra`<br>openrouter: `openai/gpt-6-astra`<br>chatgpt: `chatgpt/gpt-6-astra` | `astra`, `gpt6-astra`, `gpt6astra`, `or-gpt6-astra` |  |
| GPT-6 Astra Pro | 20 | not measured | openrouter: `openai/gpt-6-astra-pro` | `astra-pro`, `gpt6-astra-pro`, `or-gpt6-astra-pro` | explicit-only, provisional score |
| OpenAI (GPT-6.1 Terra) | 18 | non-reasoning 30, low 29, medium 38, high 42, xhigh 45, max 51 | native: `gpt-6.1-terra`<br>openrouter: `openai/gpt-6.1-terra`<br>chatgpt: `chatgpt/gpt-6.1-terra` | `gpt6.1-terra`, `gpt6.1terra`, `or-gpt6.1-terra`, `terra` |  |
| GPT-6.1 Terra Pro | 18 | not measured | openrouter: `openai/gpt-6.1-terra-pro` | `gpt-6.1-terra-pro`, `or-gpt6.1-terra-pro` | explicit-only, provisional score |
| OpenAI (GPT-6 Luna) | 13 | non-reasoning 9, low 10, medium 18, high 21, xhigh 23, max 28 | native: `gpt-6-luna`<br>openrouter: `openai/gpt-6-luna`<br>chatgpt: `chatgpt/gpt-6-luna` | `gpt6-luna`, `gpt6luna`, `luna`, `or-gpt6-luna` |  |
| GPT-6 Luna Pro | 13 | not measured | openrouter: `openai/gpt-6-luna-pro` | `gpt-6-luna-pro`, `or-gpt6-luna-pro` | explicit-only, provisional score |

## Google

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Gemini (4 Argon) | 20 | high 50 | native: `gemini-4-argon`<br>openrouter: `google/gemini-4-argon` | `argon`, `gemini-4-argon-or`, `gemini-argon`, `or-gemini-4-argon` |  |
| Gemini (4 Carbon) | 20 | high 59 | native: `gemini-4-carbon` | `carbon`, `gemini-carbon` | explicit-only |
| Gemini 3.8 Flash | 16 | low 22, medium 30, high 31 | native: `gemini-3.8-flash`<br>openrouter: `google/gemini-3.8-flash` | `flash3.8`, `gemini-3.8-flash`, `gemini-flash-latest`; flag-only: `flash` |  |
| Gemini 3.5 Flash-Lite | 9 | default 12 | native: `gemini-3.5-flash-lite` | `flashlite3.5`, `gemini-flash-lite-latest`; flag-only: `flash-lite`, `flashlite` |  |

## xAI

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| X.AI (Grok 4.7) | 17 | low 31, high 35, xhigh 34 | native: `grok-4.7`<br>openrouter: `x-ai/grok-4.7` | `grok-4.7`, `grok4.7`; flag-only: `grok` |  |

## Meta

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Muse Wonder | 18 | max 43 | openrouter: `meta/muse-wonder` | `muse-wonder`, `wonder` |  |
| Muse Spark Open | 13 | xhigh 25 | openrouter: `meta/muse-spark-open` | `muse-open`, `muse-spark-open` |  |

## DeepSeek

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| DeepSeek V4.1 Flash | 15 | max 33 | native: `deepseek-flash`<br>openrouter: `deepseek/deepseek-v4.1-flash-1017` | `deepseek`, `deepseek-flash`, `deepseek-v4.1-flash`, `deepseek-v4.1-flash-1017`, `v4.1-flash` |  |
| DeepSeek V4 Pro | 11 | max 23 | native: `deepseek-v4-pro`<br>openrouter: `deepseek/deepseek-v4-pro` | `deepseek-v4`, `deepseek-v4-pro` |  |

## Alibaba (Qwen)

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Qwen3.8 Max 0902 | 17 | default 34 | native: `qwen3.8-max-0902`<br>openrouter: `qwen/qwen3.8-max-0902` | `qwen-max`, `qwen3-max`, `qwen3.8-max-0902`, `qwen3.8-max-2026-09-02` |  |
| Qwen3.8 27B | 11 | xhigh 20 | openrouter: `qwen/qwen3.8-27b` | `qwen3.8-27b` |  |

## Z.ai (GLM)

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| GLM-5.3 | 15 | low 23, max 32 | native: `glm-5.3`<br>openrouter: `z-ai/glm-5.3` | `glm`, `glm-5.3`, `glm5` |  |

## Moonshot (Kimi)

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Kimi K3.1 Pro | 19 | default 46 | native: `kimi-k3.1-pro`<br>openrouter: `moonshotai/kimi-k3.1-pro` | `kimi`, `kimi-k3.1-pro` |  |
| Kimi K3.1 Pro Highspeed | 19 | not measured | native: `kimi-k3.1-pro-highspeed` | `kimi-highspeed`, `kimi-k3.1-pro-highspeed` | explicit-only, provisional score |

## MiniMax

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| MiniMax M3.1 Flash | 16 | default 29 | native: `MiniMax-M3.1-Flash`<br>openrouter: `minimax/minimax-m3.1-flash` | `m3.1-flash`, `minimax`, `minimax-m3.1-flash` |  |
| MiniMax M3 | 12 | default 19 | native: `MiniMax-M3`<br>openrouter: `minimax/minimax-m3` | `minimax-m3` |  |

## Xiaomi

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| MiMo V2.6 Pro | 16 | default 30 | openrouter: `xiaomi/mimo-v2.6-pro` | `mimo`, `mimo-pro`, `mimo-v2.6-pro` |  |
| MiMo V2.6 Flash | 15 | default 27 | openrouter: `xiaomi/mimo-v2.6-flash` | `mimo-flash`, `mimo-v2.6-flash` |  |

## Mistral

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Mistral Large 4 | 14 | default 25 | openrouter: `mistralai/mistral-large-4-0` | `mistral-large-4`, `mistral-large-4-0` |  |

## NVIDIA

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Nemotron 3.5 Super VL | 10 | default 14 | native: `nvidia/nemotron-3.5-super-vl-120b-a12b`<br>openrouter: `nvidia/nemotron-3.5-super-vl` | `nemotron-3.5-super-vl`, `nemotron-super-vl` |  |
| Nemotron 3 Ultra 550B A55B | 9 | default 12 | openrouter: `nvidia/nemotron-3-ultra-550b-a55b` | `nemotron-3-ultra` |  |

## StepFun

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Step 5 Preview | 16 | default 29 | openrouter: `stepfun/step-5-preview` | `step-5`, `step-5-preview` |  |

## Cohere

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Command A Plus | 6 | default 6 | openrouter: `cohere/command-a-plus` | `command-a+`, `command-a-plus` |  |

## Reflection

| Model | Score | AA Index by effort | Routes (model ID) | Aliases | Notes |
|---|---|---|---|---|---|
| Beam 1.0 501B A23B | 12 | default 18 | openrouter: `reflection/beam-1.0-501b-a23b` | `beam`, `beam-1.0`, `reflection-beam` |  |
