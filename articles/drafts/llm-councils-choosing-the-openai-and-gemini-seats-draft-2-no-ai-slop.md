# Choosing the OpenAI and Gemini Seats for a Claude-Led LLM Council

***How I choose the OpenAI and Gemini models for each seat of an LLM council that Claude Code leads, and which models sit in it at each task difficulty in October 2026.***

I use LLM councils for the hard parts of my engineering work. The Claude Code session leads the council and writes the synthesis, while one OpenAI model and one Gemini model review independently through the PAL MCP server. Each provider fails in its own way, so the two outside seats catch mistakes that one model misses.

A policy file fixes the model for each seat; the council agents read it and never pick a model themselves. Two releases this week made that file out of date:

- On 2026-10-27, Artificial Analysis (AA) replaced its Intelligence Index v4.3 with v5, which uses harder tests, most of them private.
- On 2026-10-28, Google released Gemini 4 Carbon, which went straight to the top of the v5 Index.

So I redid the analysis, and this time I wrote the method down as a Claude Code subagent, so the next update follows the same steps.

## The Council Setup

| Seat | Role | Route |
|---|---|---|
| Claude Code session | lead, synthesis, final decision | direct |
| GPT seat | adversarial critic, code-level review | ChatGPT plan through the Codex CLI, in my PAL fork |
| Gemini seat | contract, fact and operations review | Gemini API key |

The routes limit the choice. My PAL fork reaches OpenAI models through a ChatGPT plan, so a GPT seat can only use what the plan serves: on 2026-10-28 that was GPT-6.1 Sol, GPT-6 Astra and GPT-6 Luna, but not GPT-6.1 Terra. The Gemini key served Gemini 4 Argon and the Flash models. It did not serve Gemini 4 Carbon, because Google gives first access to selected cyber-defense partners.

## The Analysis Technique

### Step 1: list what the routes serve

A model the route does not serve cannot fill a seat, so I check three lists: `codex debug models` for the ChatGPT plan, the Gemini API models list for my key, and the OpenRouter models API. OpenRouter is never a council route for me, but new releases show up there first.

### Step 2: get the benchmark data

AA's leaderboard has one row per model and reasoning effort, for example "GPT-6.1 Sol (High)", with the Index value, the score on each test and the cost of the Index run. The web fetch tool in Claude Code truncates the page and misses most rows. The page embeds its data as JSON in script tags, though, and a short script reads all of it. The pattern depends on the page's current structure, so expect to update it.

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

AA's coding-agent page yields to the same approach. It measures each model inside its vendor's agent (Claude Code, Codex or Antigravity) on three tests, and one of them, SWE-Atlas QnA, appears only there.

### Step 3: choose the metrics for council work

A council seat reviews, criticizes and plans; it writes little code. The Index alone does not tell me enough, so I read seven columns:

| Metric | What it tells me about a seat |
|---|---|
| AA Intelligence Index v5 | general reasoning level |
| FrontierCode 2.0 | code-quality judgment: a change passes only if a maintainer would merge it |
| Terminal-Bench 4.0 | long agentic work in a terminal |
| SWE-Atlas QnA | questions about an existing codebase, the nearest measure to planning and architecture review |
| CritPt v2 (hard) | hard, new reasoning problems |
| Non-hallucination rate | how often the model says "I don't know" instead of giving a wrong answer |
| Cost per task | AA's API-equivalent cost; on a ChatGPT plan it predicts quota use |

No public benchmark measures architecture design directly. SWE-Atlas QnA and FrontierCode 2.0 come closest.

### Step 4: compare at equal effort and at best effort

AA measured Carbon and Argon only at `high` and the GPT and Claude models at five efforts. Carbon at `high` against Opus at `max` would be an unfair fight, so I make two comparisons: every model at `high`, and every model at its best measured effort.

### Step 5: build tiers by task difficulty

| Tier | Typical work | Rule |
|---|---|---|
| Routine | triage, data gathering, a quick second opinion | the cheapest row with an Index of about 35 or more |
| Standard | the default council decision | an Index of about 50 at the lowest cost |
| Hard | concurrency, data migrations, security boundaries, architecture decisions, disagreement between seats | the highest Index within about 2.5 times the standard cost |
| Critical | irreversible or client-facing decisions | the best row the route serves |

### Step 6: apply three checks

1. **Dominance.** If a cheaper row matches a pricier one within about one Index point and on both coding tests, I take the cheaper row.
2. **Heterogeneity.** I match each seat's role to its strongest metric and keep it away from its weakest.
3. **Availability.** A model its route doesn't serve goes on a waiting list, never into a tier.

## The Numbers

AA v5 values, read on 2026-10-28. FrontierCode 2.0, Terminal-Bench 4.0, CritPt and non-hallucination are percentages.

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

SWE-Atlas QnA comes from the coding-agent page, with each model inside its vendor's agent at its best measured effort:

| Agent and model | SWE-Atlas QnA |
|---|---|
| Claude Code, Claude Fable 5.1 (max) | 66.4 |
| Claude Code, Claude Opus 5.5 (max) | 65.9 |
| Antigravity CLI, Gemini 4 Carbon (high) | 63.7 |
| Codex, GPT-6.1 Sol (max) | 59.9 |
| Codex, GPT-6 Astra (max) | 59.1 |
| Antigravity CLI, Gemini 4 Argon (high) | 55.1 |

## What the Numbers Say

**Sol at `max` replaces Astra for hard work.** It matches Astra at `high` on the Index (54.5 and 54.8), FrontierCode 2.0 (40.6 and 40.1) and Terminal-Bench 4.0 (56.1 and 54.0) for about 40% of the cost. Astra at `max` is the best GPT row, so it gets the critical tier.

**Luna leaves the roster.** Sol at `low` scores 34.5 for $0.16 a task; Luna at `max` scores 27.8 for $0.15.

**Gemini Flash costs Argon's price per task.** It is cheaper per token but spends more tokens, so AA's cost per task comes out the same, for 20 fewer Index points. Flash stays only as the fallback when Argon is overloaded.

**Gemini models rarely make things up.** Argon answers "I don't know" instead of guessing wrong 96% of the time, Carbon 89%, Sol and Astra 48% to 58%, and the Claude models 30% to 50%. That makes the Gemini seat the council's best fact checker.

**Argon judges code quality poorly.** Its FrontierCode 2.0 score of 25.7 trails every other model here. So the Gemini seat checks contracts, interfaces, facts and operations, and the GPT seat judges the code.

**Claude reads codebases best.** Fable 5.1 and Opus 5.5 lead SWE-Atlas QnA, which is one more reason the Claude session writes the synthesis.

**Carbon is the strongest model at `high`.** It leads the Index, FrontierCode 2.0 and Terminal-Bench 4.0 at that effort; Astra and Sol lead CritPt, and Argon leads non-hallucination. AA hasn't measured Carbon at `max`.

## The Council at Each Difficulty

| Tier | GPT seat | Gemini seat | Claude session |
|---|---|---|---|
| Routine | GPT-6.1 Sol, `low` or `medium` | Gemini 4 Argon, `medium` | any model |
| Standard | GPT-6.1 Sol, `high` | Gemini 4 Argon, `high` | Claude Opus 5.5, `high` |
| Hard | GPT-6.1 Sol, `max` | Gemini 4 Argon, `max` | Claude Opus 5.5, `xhigh` |
| Critical | GPT-6 Astra, `max` | Gemini 4 Carbon, `high` (Gemini 4 Argon, `max`, until Google serves it) | Claude Opus 5.5, `max` |

For the Claude session, Opus 5.5 at `high` beats Fable 5.1 at `high` on the Index (51.7 and 47.0) at less than half the cost per task. Fable leads SWE-Atlas QnA by half a point, so it suits a council that spends its time on questions about a large codebase.

Two models wait for their routes. Carbon takes the critical tier as soon as the Gemini API serves it to my key, and a daily check watches for that. GPT-6.1 Terra at `max` scores 51.0, with 42.3 on FrontierCode 2.0, for $0.96 a task; once my ChatGPT plan serves it, I'll test it against Sol at `max` for the hard tier.

## Limits of This Analysis

- AA measured Carbon and Argon only at `high`.
- The coding-agent page mixes the model with its vendor's agent, so I use it only for SWE-Atlas QnA.
- AA's cost is the API cost. A ChatGPT plan bills in quota, but the relative cost still predicts how fast a model uses it up.
- AA v5 is a day old, and AA can still add rows and change values.
- Public benchmarks don't run on your codebase. They narrow the list of models worth testing on your own work.

## The Subagent

The method lives in a Claude Code subagent, `update-MODEL-SELECTION`, which runs only when I call it by name. It lists the routes, reads the AA data, builds the tables, applies the tier rules and the three checks, and updates the policy file and the council prompts. It asks before it touches the PAL configuration. When AA or a provider changes something, I run it again.

---

## What changed

- **Rhythm.** Joined runs of short, same-shape STE sentences where the thoughts belong together (the intro, the routes paragraph, Steps 1–2 and several findings). Kept the tables and the numbered checks.
- **Binary contrasts.** "They show which models to test, not which model wins on your work" became "They narrow the list of models worth testing on your own work." Step 6's "the role that its strongest metric measures, and not the role that its weakest metric measures" became a direct rule.
- **Signposts and padding.** Cut "The method has six steps.", "The council has three seats:", "I use four tiers. For each tier…", and the repeated "Gemini Flash is not cheaper per task" framing (now a direct claim).
- **Ending.** Removed the "Wrapping Up" paragraph, which only restated the tier table; the piece now ends on the subagent and when to rerun it. (Pass 3 restores a styleguide-shaped "Wrapping Up!".)
- **Precision.** "Fable leads SWE-Atlas QnA by a small margin" became "by half a point" (66.4 against 65.9).
- **Kept.** Every number, the code block, the tables and the claims are unchanged; no new facts.
