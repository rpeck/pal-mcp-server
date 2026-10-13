# Model Capability Ranking

Auto mode needs a short, trustworthy list of models to suggest. The server
computes a capability rank for every model at runtime using a simple recipe:

1. Start with the human-supplied `intelligence_score` (1–20). This is the
   anchor—multiply it by five to map onto the 0–100 scale the server uses.
2. Add a few light bonuses for hard capabilities:
   - **Context window:** up to +5 (log-scale bonus when the model exceeds ~1K tokens).
   - **Output budget:** +2 for ≥65K tokens, +1 for ≥32K.
   - **Extended thinking:** +3 when the provider supports it.
   - **Function calling / JSON / images:** +1 each when available.
   - **Custom endpoints:** −1 to nudge cloud-hosted defaults ahead unless tuned.
3. Clamp the final score to 0–100 so downstream callers can rely on the range.

In code this looks like:

```python
base = clamp(intelligence_score, 1, 20) * 5
ctx_bonus = min(5, max(0, log10(context_window) - 3))
output_bonus = 2 if max_output_tokens >= 65_000 else 1 if >= 32_000 else 0
feature_bonus = (
    (3 if supports_extended_thinking else 0)
    + (1 if supports_function_calling else 0)
    + (1 if supports_json_mode else 0)
    + (1 if supports_images else 0)
)
penalty = 1 if provider == CUSTOM else 0

effective_rank = clamp(base + ctx_bonus + output_bonus + feature_bonus - penalty, 0, 100)
```

The bonuses are intentionally small—the human intelligence score does most
of the work so you can enforce organisational preferences easily.

## Picking an intelligence score

The scores in this catalog come from the
[Artificial Analysis (AA) Intelligence Index](https://artificialanalysis.ai/leaderboards/models)
(v4.3). A model that AA measures gets its score from this ladder:

| AA Index | Score | | AA Index | Score |
|---|---|---|---|---|
| 52+ | 20 | | 36-37 | 14 |
| 50-51 | 19 | | 33-35 | 13 |
| 46-49 | 18 | | 30-32 | 12 |
| 43-45 | 17 | | 27-29 | 11 |
| 41-42 | 16 | | 24-26 | 10 |
| 38-40 | 15 | | 21-23 | 9 |

- **Effort anchor:** today the score uses the model's AA value at its highest measured effort (usually
  `max`). PAL tools default to `thinking_mode: medium`, so a typical call can score a few Index points
  lower than the score suggests. A planned re-score anchors every score at `high` effort instead.
- **Measured and estimated scores:** a model that AA does not list keeps a score that follows its
  family and its order in the catalog. That score is an **estimate**, not an Index reading. When AA
  publishes a value, the estimate is replaced.
- **Provisional scores:** a description that says PROVISIONAL marks a score that waits for an AA value,
  for example a "pro" reasoning-mode preset that AA has not measured.
- **Record the source:** each conf description cites the AA value and the effort level behind the
  score, so later updates stay consistent.

The [model catalog](model_catalog.md) lists every measured model with its per-effort Index values.
Generate it again with `python scripts/generate_model_catalog.py` after you change a conf file.

### Fields that support the score

| Field | Meaning |
|---|---|
| `aa_index` | The raw AA Index value behind the score. When two models share a score, the Anthropic provider uses it as the second sort key (for example, Claude Opus 5.5 at 58 wins over Claude Sonnet 5.5 at 56, although both score 20). |
| `aa_index_by_effort` | The AA value at each effort level, for example `{"low": 42, "medium": 48, "high": 50, "xhigh": 51, "max": 52}`. `default` means AA lists a single row. Values that AA marks as incomplete (`*`) are left out. |
| `effort_levels` | The reasoning-effort levels the route accepts. PAL sends the tool's `thinking_mode` as an explicit effort for these models (see [advanced usage](advanced-usage.md#thinking-modes-as-reasoning-effort)). |
| `auto_selectable` | `false` keeps a model out of auto mode. Use it for presets that cost much more per request, such as the OpenRouter `-pro` reasoning-mode presets and the Fusion meta-routers. |
| `dynamic_aliases` | Aliases that point at the model only when `DYNAMIC_MODEL_SELECTION` is on. They let a bare alias such as `haiku` follow the newest model without changing upstream defaults. |

### Known gaps

- A few measured scores are older than the current AA values, for example Gemini 3.1 Pro Preview
  (score 16, AA 30) and MiniMax M3 (score 12, AA 29). The planned re-score at `high` effort corrects
  them.
- The ladder has no rungs below 21-23 yet. Small models with an AA value under 21 keep their earlier
  scores until the re-score adds those rungs.

## How the rank is used

The ranked list is cached per provider and consumed by:
- Tool schemas (`model` parameter descriptions) when auto mode is active.
- The `listmodels` tool’s “top models” sections.
- Fallback messaging when a requested model is unavailable.

Because the rank is computed after restriction filters, only allowed models
appear in these summaries.

## Customising further

If you need a different weighting you can:
- Override `intelligence_score` in your provider or custom model config.
- Subclass the provider and override `get_effective_capability_rank()`.
- Post-process the rank via `get_capabilities_by_rank()` before surfacing it.

Most teams find that adjusting `intelligence_score` alone is enough to keep
auto mode honest without revisiting code.
