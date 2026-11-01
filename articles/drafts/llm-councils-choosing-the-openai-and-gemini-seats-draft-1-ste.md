# Choosing the OpenAI and Gemini Seats for a Claude-Led LLM Council

***This article shows how I choose the OpenAI and Gemini models for each seat of an LLM council that Claude Code leads, and which models I use at each task difficulty in October 2026.***

I use LLM councils for the hard parts of my engineering work. A council has three seats. The Claude Code session leads it and writes the synthesis. One OpenAI model and one Gemini model give independent reviews through the PAL MCP server. Each provider makes different errors, so the council catches errors that one model alone does not catch.

The seats are fixed in a policy file. Each council agent reads the policy and does not choose a model by itself. Two events this week made the policy out of date:

- On 2026-10-27, Artificial Analysis (AA) replaced its Intelligence Index v4.3 with v5. The new version uses harder tests, and most of its tests are private.
- On 2026-10-28, Google released Gemini 4 Carbon. It is at the top of the v5 Index.

So I did the analysis again. I also wrote the method down as a Claude Code subagent, so that the next update uses the same steps.

## The Council Setup

The council has three seats:

| Seat | Role | Route |
|---|---|---|
| Claude Code session | lead, synthesis, final decision | direct |
| GPT seat | adversarial critic, code-level review | ChatGPT plan through the Codex CLI, in my PAL fork |
| Gemini seat | contract, fact and operations review | Gemini API key |

The routes limit the choice. My PAL fork reaches OpenAI models through a ChatGPT plan, so a GPT seat can use only the models that the plan serves. On 2026-10-28 the plan served GPT-6.1 Sol, GPT-6 Astra and GPT-6 Luna. It did not serve GPT-6.1 Terra yet. The Gemini key served Gemini 4 Argon and the Gemini Flash models. It did not serve Gemini 4 Carbon, because Google gives first access to selected cyber-defense partners.

## The Analysis Technique

The method has six steps.

### Step 1: list what the routes serve

A model that a route does not serve cannot fill a seat. I check three sources:

- `codex debug models` for the ChatGPT plan.
- The Gemini API models list for my key.
- The OpenRouter models API. I do not use OpenRouter as a council route, but its list shows new releases quickly.

### Step 2: get the benchmark data

AA is the main source. Its leaderboard shows one row for each model and each reasoning effort, for example "GPT-6.1 Sol (High)". Each row has the Index value, the score on each test, and the cost of the Index run.

The web fetch tool in Claude Code cuts long pages short, so it does not see most of the rows. The page embeds its data as JSON in script tags, so a short script reads all of it. The pattern depends on the current page structure, so expect to update it.

```python
"""Read per-effort benchmark rows from the Artificial Analysis leaderboard page."""
import json
import re
import urllib.request

URL = "https://artificialanalysis.ai/leaderboards/models"
ROW = re.compile(
    r'\{"slug":"(?P<slug>[^"]+)","name":"(?P<name>[^"]+)","shortName"[^{}]*?'
    r'"intelligenceIndex":(?P<index>[^,]+),[^{}]*?"scores":(?P<scores>\{[^{}]*\})'
    r'(?:[^{}]*?"intelligenceIndexCostPerTask":(?P<cost>[0-9.]+|null))?'
)
METRICS = {
    "frontiercode-2-0": "FrontierCode 2.0",
    "terminalbench-4-0": "Terminal-Bench 4.0",
    "critpt-v2-hard": "CritPt v2 (hard)",
    "omniscience-non-hallucination": "Non-hallucination",
}


def fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response:
        # The page embeds its data as escaped JSON inside script tags.
        return response.read().decode("utf-8").replace('\\"', '"')


def rows(page: str, wanted: tuple[str, ...]):
    seen = set()
    for match in ROW.finditer(page):
        if match["slug"] in seen or match["index"] == "null" or not match["name"].startswith(wanted):
            continue
        seen.add(match["slug"])
        scores = json.loads(match["scores"])
        cost = match["cost"]
        yield {
            "model": match["name"],  # for example "GPT-6.1 Sol (High)"
            "index": round(float(match["index"]), 1),
            "cost_per_task": float(cost) if cost not in (None, "null") else None,
            **{label: scores.get(key) for key, label in METRICS.items()},
        }


if __name__ == "__main__":
    page = fetch(URL)
    for row in rows(page, ("GPT-6", "Gemini 4", "Gemini 3.8", "Claude Opus 5.5", "Claude Fable 5.1")):
        print(row)
```

AA's coding-agent page uses the same method. That page measures each model inside its vendor's agent (Claude Code, Codex or Antigravity) on three tests. One of them, SWE-Atlas QnA, is on that page only.

### Step 3: choose the metrics for council work

A council seat reviews, criticizes and plans. It writes little code. So the Index alone is not enough. I use these columns:

| Metric | What it tells me about a seat |
|---|---|
| AA Intelligence Index v5 | general reasoning level |
| FrontierCode 2.0 | code-quality judgment: AA counts a change as a pass only if a maintainer would merge it |
| Terminal-Bench 4.0 | long agentic work in a terminal |
| SWE-Atlas QnA | questions about an existing codebase, the nearest measure to planning and architecture review |
| CritPt v2 (hard) | hard, new reasoning problems |
| Non-hallucination rate | how often the model says "I do not know" instead of a wrong answer |
| Cost per task | AA's API-equivalent cost; on a ChatGPT plan it predicts quota use |

No public benchmark measures architecture design directly. SWE-Atlas QnA and FrontierCode 2.0 are the closest measures.

### Step 4: compare at equal effort and at best effort

AA measured Carbon and Argon only at `high`. It measured the GPT and Claude models at five efforts. A comparison of Carbon at `high` with Opus at `max` is not fair, so I make two comparisons: all models at `high`, and each model at its best measured effort.

### Step 5: build tiers by task difficulty

I use four tiers. For each tier and each provider, I choose one model and one effort:

| Tier | Typical work | Rule |
|---|---|---|
| Routine | triage, data gathering, a quick second opinion | the cheapest row with an Index of about 35 or more |
| Standard | the default council decision | an Index of about 50 at the lowest cost |
| Hard | concurrency, data migrations, security boundaries, architecture decisions, disagreement between seats | the highest Index within about 2.5 times the standard cost |
| Critical | irreversible or client-facing decisions | the best row that the route serves |

### Step 6: apply three checks

1. **Dominance.** If a cheaper row matches a more expensive row within about one Index point and on the two coding tests, I use the cheaper row.
2. **Heterogeneity.** I give each seat the role that its strongest metric measures, and not the role that its weakest metric measures.
3. **Availability.** A model that its route does not serve goes on a waiting list. It does not go into a tier.

## The Numbers

These are AA's v5 values, read on 2026-10-28. FrontierCode 2.0, Terminal-Bench 4.0, CritPt and non-hallucination are percentages.

| Model, effort | Index | FrontierCode 2.0 | Terminal-Bench 4.0 | CritPt v2 (hard) | Non-hallucination | Cost per task |
|---|---|---|---|---|---|---|
| GPT-6.1 Sol, low | 34.5 | 20.2 | 30.8 | 59.5 | 50 | $0.16 |
| GPT-6.1 Sol, high | 50.2 | 36.7 | 51.5 | 67.5 | 51 | $0.59 |
| GPT-6.1 Sol, max | 54.5 | 40.6 | 56.1 | 68.5 | 48 | $1.27 |
| GPT-6 Astra, high | 54.8 | 40.1 | 54.0 | 71.5 | 58 | $3.24 |
| GPT-6 Astra, max | 56.9 | 42.4 | 59.1 | 70.5 | 50 | $6.24 |
| GPT-6 Luna, max | 27.8 | 25.2 | 12.6 | 41.5 | 21 | $0.15 |
| Gemini 4 Argon, high | 50.4 | 25.7 | 57.1 | 52.5 | 96 | $7.68 |
| Gemini 4 Carbon, high | 59.4 | 40.6 | 65.2 | 63.0 | 89 | $7.76 |
| Gemini 3.8 Flash, high | 30.5 | 18.7 | 19.7 | 43.5 | 45 | $7.77 |
| Claude Opus 5.5, high | 51.7 | 35.9 | 56.6 | 58.0 | 36 | $3.46 |
| Claude Opus 5.5, max | 58.4 | 46.4 | 59.6 | 61.0 | 50 | $10.04 |
| Claude Fable 5.1, high | 47.0 | 30.3 | 52.0 | 60.5 | 30 | $7.56 |

SWE-Atlas QnA, from the coding-agent page (each model inside its vendor's agent, at its best measured effort):

| Agent and model | SWE-Atlas QnA |
|---|---|
| Claude Code, Claude Fable 5.1 (max) | 66.4 |
| Claude Code, Claude Opus 5.5 (max) | 65.9 |
| Antigravity CLI, Gemini 4 Carbon (high) | 63.7 |
| Codex, GPT-6.1 Sol (max) | 59.9 |
| Codex, GPT-6 Astra (max) | 59.1 |
| Antigravity CLI, Gemini 4 Argon (high) | 55.1 |

## What the Numbers Say

**Sol at max replaces Astra for hard work.** Sol at `max` matches Astra at `high` on the Index (54.5 and 54.8), on FrontierCode 2.0 (40.6 and 40.1) and on Terminal-Bench 4.0 (56.1 and 54.0). It costs about 40% as much. Astra at `max` is the best GPT row, so it holds the critical tier.

**Luna leaves the roster.** Sol at `low` scores 34.5 for $0.16 per task. Luna at `max` scores 27.8 for $0.15. Sol at `low` is better for the same cost.

**Gemini Flash is not cheaper per task.** Gemini 3.8 Flash costs less per token than Argon, but it uses more tokens. Its cost per task on AA is the same as Argon's, and it scores 20 Index points lower. Flash stays only as a fallback when Argon is overloaded.

**Gemini models rarely make things up.** Argon gives "I do not know" instead of a wrong answer 96% of the time, and Carbon 89%. Sol and Astra are at 48% to 58%, and the Claude models at 30% to 50%. A Gemini seat is the best fact checker in the council.

**Argon is weak at code-quality judgment.** Its FrontierCode 2.0 score is 25.7, far below the other models. So the Gemini seat checks contracts, interfaces, facts and operations, and the GPT seat judges code quality.

**Claude understands codebases best.** Fable 5.1 and Opus 5.5 lead SWE-Atlas QnA. The Claude session is the right place for synthesis.

**Carbon is the strongest model at `high`.** At `high`, it leads the Index, FrontierCode 2.0 and Terminal-Bench 4.0. Astra and Sol lead CritPt, and Argon leads non-hallucination. Its result at `max` is unknown, because AA measured only `high`.

## The Council at Each Difficulty

| Tier | GPT seat | Gemini seat | Claude session |
|---|---|---|---|
| Routine | GPT-6.1 Sol, `low` or `medium` | Gemini 4 Argon, `medium` | any model |
| Standard | GPT-6.1 Sol, `high` | Gemini 4 Argon, `high` | Claude Opus 5.5, `high` |
| Hard | GPT-6.1 Sol, `max` | Gemini 4 Argon, `max` | Claude Opus 5.5, `xhigh` |
| Critical | GPT-6 Astra, `max` | Gemini 4 Carbon, `high` (until Google serves it: Gemini 4 Argon, `max`) | Claude Opus 5.5, `max` |

For the Claude session, Opus 5.5 at `high` scores higher than Fable 5.1 at `high` (51.7 and 47.0) for less than half the cost per task. Fable leads SWE-Atlas QnA by a small margin, so it is a good choice for a council that asks questions about a large codebase.

Two models wait for their routes. Gemini 4 Carbon goes into the critical tier when the Gemini API serves it to my key; a daily check watches for it. GPT-6.1 Terra at `max` scores 51.0, with a FrontierCode 2.0 score of 42.3, for $0.96 per task. When my ChatGPT plan serves it, I compare it with Sol at `max` for the hard tier.

## Limits of This Analysis

- AA measured Carbon and Argon only at `high`.
- The coding-agent page measures each model inside its vendor's agent. The agent affects the result, so I use that page only for SWE-Atlas QnA.
- AA's cost is the API cost. On a ChatGPT plan, the plan's quota limits the use, but the relative cost still predicts quota use.
- AA v5 is one day old. AA can add rows and change values.
- Public benchmarks do not use your codebase. They show which models to test, not which model wins on your work.

## The Subagent

The method is in a Claude Code subagent named `update-MODEL-SELECTION`. It runs only when I call it by name. It lists the routes, reads the AA data, builds the tables, applies the tier rules and the three checks, and updates the policy file and the council prompts. It asks before it changes the PAL configuration.

## Wrapping Up

The council for October 2026 is: GPT-6.1 Sol at increasing effort for the routine, standard and hard tiers, GPT-6 Astra at `max` for the critical tier, and Gemini 4 Argon at every tier until Gemini 4 Carbon is available. The Gemini seat checks facts and contracts, the GPT seat challenges code and reasoning, and Claude writes the synthesis. When AA or a provider changes something, I run the subagent again.
