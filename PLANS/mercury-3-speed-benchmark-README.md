# Speed benchmark: Mercury 3 against Claude Haiku 5.5 and GPT-6 Luna

This folder records the speed tests from 2026-10-20 for the new-models announcement. It holds the
scripts, the raw data, and the method, so that a separate write-up can use them.

There were four runs:
1. **A preliminary test.** It is superseded, but it is kept as a record.
2. **A reasoning probe.** It found that Mercury 3 reasons by default, which made run 1 unfair.
3. **The benchmark with reasoning off.** This is the fast bound.
4. **The benchmark at max reasoning.** This is the slow bound.
5. **A Mercury 3 effort map.** It checked what OpenRouter does with "max" for Mercury 3, which has no
   max level.

The new-models deck uses run 3. The benchmark deck uses runs 2, 3, and 4.

## Summary

- **Mercury 3 was the fastest model in every test, at both reasoning settings.** Each model ran on its
  own lab's servers, through OpenRouter. The figures below are for reasoning off (run 3), unless they
  say otherwise.
- **Prose (about 300 words):**
  - Mercury 3 finished in 1.14 s.
  - Claude Haiku 5.5 took 3.75 s, GPT-6 Luna on the fast tier took 3.12 s, and standard GPT-6 Luna
    took 5.17 s.
  - All four answers had 312 to 331 words, so these total times compare fairly.
- **Rate, in characters per second** (this measure does not depend on each lab's tokenizer):
  - Prose: Haiku 5.5 ran at about a third of Mercury's rate. Luna fast ran at about two fifths, and
    standard Luna at under a quarter.
  - Code: Haiku ran at under a third of Mercury's rate. Luna fast ran at about a fifth, and standard
    Luna at about an eighth.
- **Inception's figure of 1,068 tokens/s is not confirmed or refuted.** My best end-to-end figure is
  631 tokens/s on code. That figure includes network and OpenRouter time, which is up to about 0.4 s
  on each call.
- **Mercury 3 sends each answer in a few large blocks:** 2 for a one-sentence answer, 4 or 5 for
  prose, and 4 to 6 for code. On prose and code, the other models stream 129 to 639 small chunks.
  So time to the first content does not compare fairly between Mercury and the others.
- **At max reasoning (run 4), Mercury 3 was still the fastest,** and the gaps grew. On prose, Mercury 3
  finished in 2.6 s, the Luna fast tier in 10.1 s, standard Luna in 21.1 s, and Haiku 5.5 in 73.4 s.
  Haiku used a median of 20,286 reasoning tokens, against 600 for Mercury 3.
- **Bounds on prose:** Haiku took 3.3 to 28 times as long as Mercury 3, standard Luna 4.5 to 8.1 times, and
  the Luna fast tier 2.7 to 3.9 times.
- **Mercury 3 has no max level.** Inception documents only instant, low, medium, and high (default low).
  OpenRouter accepted "max", and in run 5 those calls reasoned about as much as "high" (and as "low").
  So the slow bound for Mercury 3 is roughly its high level, not a true max.
- **Inception published its own quality scores** (a table image on the launch page, all at medium effort).
  Mercury 3 beats GPT-6 Luna and Claude Haiku 5.5 on 8 of 12, for example SWE-Bench Atlas 51.08 against
  33.33 and 32.26, and trails Haiku on DeepSWE v1 (46.31 against 51.62). No third party has checked them.
  An earlier version of this README, the post, slide 11, and the PAL catalog said that Inception published
  no scores. That was wrong, because the page fetch read only the page text and missed the image.
- **Total cost:** $0.016 for run 3, $0.167 for run 4, and $0.008 for run 5.

## Files

```
speed-benchmark/
  README.md                         this file
  1-preliminary/
    measure_speed.py                run 1 script (superseded)
    speed_measurement.json          run 1 results: medians plus every trial
  2-reasoning-probe/
    probe_reasoning.py              run 2 script
    probe_output.txt                run 2 output, verbatim
  3-benchmark/
    benchmark_speed.py              run 3 script; its docstring states the method
    benchmark_results.json          run 3: method, prompts, targets, summary, and all 60 rows
    benchmark_run.log               run 3 console output, one line per call
  4-max-reasoning/
    probe_max_effort.py             checks that each target accepts max, xhigh, and high
    probe_max_output.txt            probe output, verbatim
    benchmark_results_max.json      run 4: the same script with --effort max --max-tokens 64000
    benchmark_run_max.log           run 4 console output, one line per call
  5-mercury-effort-map/
    mercury_effort_map.py           5 Mercury 3 prose calls at each of none, low, medium, high, max
    effort_map.json                 run 5 rows
```

The deck scripts read the results files directly, so no deck number comes from manual copying:
- `../build_models_deck.py` reads `3-benchmark/benchmark_results.json`.
- `../../2026-10-20-mercury-benchmark/build_benchmark_deck.py` reads run 2, run 3, and run 4.

## Environment (all runs)

- **Client:** a MacBook Pro (Mac15,11, Apple M3 Max) on macOS 15.6.1, in the US Pacific time zone.
  The network connection was not recorded.
- **Software:** Python 3.13.3 and the `openai` SDK 2.38.0, pointed at the OpenRouter API
  (`https://openrouter.ai/api/v1`).
- **Timing:** `time.perf_counter()` on the client. So each time includes the network, OpenRouter's
  routing, the provider's queue, and generation.
- **Not measured:** the network path, and OpenRouter's own overhead. The fastest Mercury call (0.43 s
  for 25 tokens) gives an upper limit for the fixed overhead of each call.

## Run 1: preliminary test (superseded)

- **When:** 2026-10-20, finished at 14:30 PDT (21:30 UTC).
- **Prompt:** "Explain how a hash map works, in about 300 words of plain prose."
- **Trials:** 3 per model, in sequence (all trials of one model, then the next model).
- **Settings:**
  - `max_tokens` 1500.
  - Mercury 3: no reasoning parameter, so it used its default.
  - Every other model: `reasoning_effort: "low"`.
  - No provider pinning, so OpenRouter chose the host.

**Results (median of 3):**

| Model | Total s | First content s | Output tokens | Reasoning tokens | Words |
|---|---|---|---|---|---|
| Mercury 3 | 1.90 | 0.63 | 448 | 52 | 337 |
| DeepSeek V4.1 Flash 1017 | 3.47 | 1.89 | 435 | 71 | 300 |
| Claude Haiku 5.5 | 3.89 | 0.83 | 543 | 0 | 323 |
| GPT-6 Luna | 6.46 | 3.66 | 412 | 42 | 324 |
| Gemini 3.8 Flash | 6.81 | 3.44 | 355 | 0 | 295 |

**Why it is superseded:**
- **The reasoning settings were not the same.** Mercury 3 ran at its default, which includes
  reasoning. The other models ran at "low". Run 2 found this problem.
- **Reasoning tokens were part of the total time,** in different amounts for each model.
- **The routes were not pinned.** OpenRouter can send a request to any host of a model.
- **There were only 3 trials, in sequence.** Run 1 had no warm-up call. Gemini 3.8 Flash had one
  14.3 s trial.
- **Gemini's reasoning count is not reliable.** OpenRouter reported 0 reasoning tokens for Gemini 3.8
  Flash at "low". That count can be a reporting gap and not a true zero.

The first deck draft used these numbers. The current deck does not.

## Run 2: reasoning probe

- **When:** 2026-10-20, about 17:24 PDT (2026-10-21 00:24 UTC).
- **Purpose:** to find a reasoning setting that every model accepts, and that gives zero reasoning
  tokens.
- **Method:** one call per model, provider, and setting.
  - Prompt: "What is 17 * 23? Reply with the number only." `max_tokens` 400.
  - Settings: the default (no parameter), `reasoning.effort` set to `none`, `minimal`, and `low`, and
    `reasoning.enabled` set to `false`.
  - Targets: Mercury 3 on `inception`, Haiku 5.5 on `anthropic`, and Luna on `openai` and on
    `openai/fast`.

**Findings:**
- **Mercury 3 reasons by default.** It used 12 reasoning tokens on the default setting. It used 45
  tokens at "minimal" and 47 at "low", which is more than the default.
- **GPT-6 Luna also reasons by default:** 14 tokens on `openai` and 19 on `openai/fast`.
- **Claude Haiku 5.5 did not reason on any setting.** Reasoning is opt-in for Haiku.
- **`effort: none` and `enabled: false` gave 0 reasoning tokens on all four targets.** All 20 calls
  answered 391, which is correct.

Run 3 uses `reasoning: {"effort": "none"}` for every target.

## Run 3: the benchmark with reasoning off

### Method

- **When:** 2026-10-21 00:25:35 to 00:29:07 UTC (2026-10-20, 17:25 to 17:29 PDT).
- **Targets:** every model is pinned to its own lab's servers, with
  `provider: {"order": [<tag>], "allow_fallbacks": false}`:

  | Label | OpenRouter model | Provider tag | Price in / out per M tokens |
  |---|---|---|---|
  | Mercury 3 | `inception/mercury-3` | `inception` | $0.10 / $0.35 (launch price) |
  | Claude Haiku 5.5 | `anthropic/claude-haiku-5.5` | `anthropic` | $0.10 / $0.50 |
  | GPT-6 Luna (fast tier) | `openai/gpt-6-luna` | `openai/fast` | $0.20 / $1.00 |
  | GPT-6 Luna | `openai/gpt-6-luna` | `openai` | $0.10 / $0.50 |

  Every one of the 60 calls reported the expected provider name. Both Luna targets report "OpenAI",
  because the response does not name the tier.
- **Settings for every call:**
  - `reasoning: {"effort": "none"}`.
  - `max_tokens` 3000.
  - `stream: true`, with `stream_options.include_usage`.
  - Default temperature. The Anthropic and OpenAI endpoints do not list `temperature` as a supported
    parameter.
- **Prompts:**
  - **short:** "In one sentence, what does a load balancer do?"
  - **prose:** "Explain how a hash map works, in about 300 words of plain prose." This is the same
    prompt as run 1.
  - **code:** "Write a Python module that implements an LRU cache class with get and put methods, type
    hints, and docstrings, followed by five pytest tests. Return only the code, in one Python code
    block."
- **Order:**
  1. One warm-up call per target, with the short prompt. The script discards these.
  2. Five rounds. Each round runs all 12 target and prompt pairs in a shuffled order, from a fixed
     seed (20261020). So network drift and server load spread over all targets.
- **Quality check:** the script extracts the code block from each code answer and parses it with
  `ast.parse`. All 20 code answers parsed. All 60 calls ended with `finish_reason` "stop", so no
  answer was truncated.

### Metrics

- **Total s:** the time from the request to the end of the stream.
- **First content s:** the time to the first chunk that holds answer text.
- **Output tokens:** `completion_tokens` from the usage record. Each lab counts with its own
  tokenizer.
- **Chars/s:** answer characters / total s. **This is the main rate,** because it does not depend on
  the tokenizer.
- **End-to-end tok/s:** output tokens / total s.
- **Decode tok/s:** (output tokens − 1) / (time of the last chunk − time of the first chunk).
  - The script reports it only for calls with 10 or more chunks and a span over 0.05 s.
  - It is "n/a" for Mercury 3, which sends 2 to 6 chunks.
  - For Haiku on the short prompt, only 1 of 5 calls had 10 or more chunks.
- **Chunks:** the number of content chunks in the stream.

### Results

Each cell gives the median of 5 runs, then the range (minimum to maximum).

**Short answer**

| Model | Total s | First content s | Output tokens | Words | Chars/s | End-to-end tok/s | Decode tok/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 0.48 (0.43–2.24) | 0.47 (0.41–2.09) | 25 (21–27) | 21 (16–22) | 343 (73–368) | 52 (11–56) | n/a | 2 (2–2) |
| Claude Haiku 5.5 | 1.22 (1.18–1.91) | 0.96 (0.91–1.61) | 51 (47–51) | 22 (21–22) | 142 (92–145) | 41 (27–42) | 218 (1 call) | 9 (8–10) |
| GPT-6 Luna (fast tier) | 1.58 (1.44–2.25) | 1.31 (1.26–1.95) | 20 (20–21) | 14 (14–15) | 68 (52–75) | 13 (9–14) | 110 (75–350) | 16 (16–17) |
| GPT-6 Luna | 2.26 (1.50–2.77) | 1.99 (1.13–2.45) | 20 (20–23) | 14 (14–15) | 53 (39–72) | 10 (7–13) | 77 (51–134) | 16 (16–19) |

**Prose (about 300 words)**

| Model | Total s | First content s | Output tokens | Words | Chars/s | End-to-end tok/s | Decode tok/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 1.14 (1.13–1.69) | 0.64 (0.52–0.82) | 388 (351–398) | 319 (298–323) | 1,476 (1,061–1,559) | 308 (236–342) | n/a | 5 (4–5) |
| Claude Haiku 5.5 | 3.75 (3.54–4.41) | 0.95 (0.90–1.12) | 532 (510–577) | 331 (305–348) | 487 (454–506) | 142 (130–146) | 190 (173–194) | 140 (129–162) |
| GPT-6 Luna (fast tier) | 3.12 (2.99–5.12) | 1.21 (1.15–3.24) | 366 (346–377) | 317 (300–324) | 566 (357–601) | 115 (72–122) | 206 (184–211) | 362 (342–373) |
| GPT-6 Luna | 5.17 (4.97–5.96) | 1.49 (1.35–2.10) | 361 (355–377) | 312 (302–325) | 346 (294–363) | 72 (61–74) | 102 (94–112) | 357 (351–373) |

**Code (an LRU cache module with five tests)**

| Model | Total s | First content s | Output tokens | Words | Chars/s | End-to-end tok/s | Decode tok/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 1.46 (1.04–1.64) | 0.65 (0.49–0.74) | 924 (745–1002) | 449 (334–469) | 2,582 (2,167–2,967) | 631 (556–746) | n/a | 5 (4–6) |
| Claude Haiku 5.5 | 7.66 (5.92–8.20) | 1.06 (0.87–1.72) | 2021 (1582–2136) | 649 (518–674) | 740 (668–754) | 267 (249–279) | 322 (317–337) | 293 (237–323) |
| GPT-6 Luna (fast tier) | 3.77 (3.48–3.99) | 1.33 (1.31–1.79) | 594 (556–629) | 226 (215–237) | 561 (502–600) | 158 (139–170) | 258 (244–278) | 590 (552–625) |
| GPT-6 Luna | 6.29 (5.58–16.00) | 1.53 (1.43–3.77) | 571 (558–643) | 217 (213–247) | 324 (142–364) | 91 (40–102) | 132 (53–144) | 567 (554–639) |

**Rate as a fraction of Mercury 3's rate (median chars/s):**

| Model | Prose | Code |
|---|---|---|
| Claude Haiku 5.5 | 0.33 | 0.29 |
| GPT-6 Luna (fast tier) | 0.38 | 0.22 |
| GPT-6 Luna | 0.24 | 0.13 |

**Cost (15 calls per target):**
- Mercury 3: $0.0023
- Claude Haiku 5.5: $0.0062
- GPT-6 Luna (fast tier): $0.0049
- GPT-6 Luna: $0.0025

### Observations

- **Tokenizers differ a lot.** On the prose answers, Claude Haiku 5.5 averaged 3.46 characters per
  token. Mercury 3 averaged 4.57, and GPT-6 Luna averaged 4.85 to 4.93. So for the same text, tok/s
  figures make Haiku look a third or more faster than chars/s does. This is why the deck uses chars/s.
- **The answer lengths for code are very different.** Haiku wrote about 650 words of code, Mercury
  about 450, and Luna about 220. So total time on code shows length as well as speed. Use chars/s for
  code.
- **Mercury 3 sends its answer in blocks.** It used 2 chunks for a one-sentence answer and 4 to 6 for
  300 to 1,000 tokens. This fits a diffusion model that refines a block of text and then sends it.
  The OpenRouter stream can also group the chunks, and this data cannot separate the two causes.
- **The first block of Mercury 3 arrives at about the same time as the first token of the others.**
  For prose, Mercury's first content came at 0.64 s and Haiku's at 0.95 s. But Mercury's first block
  holds much more text, so the two figures do not mean the same thing.
- **The fast tier of GPT-6 Luna doubles the decode rate for twice the price.** On prose, the decode
  rate was 206 tok/s against 102. The time to the first content changed less: 1.21 s against 1.49 s.
- **Two outliers, both in round 3:**
  - GPT-6 Luna, code: 16.00 s total, with 3.77 s to the first content. The median for that cell is
    6.29 s.
  - Mercury 3, short: 2.24 s total, with 2.09 s to the first content. The median for that cell is
    0.48 s.
  - The medians ignore both. The cause is not known. A queue at the provider or a network delay can
    cause this.

### Limits

- **One session, one place, one time of day.** Speeds change with server load. One run on one evening
  does not show the variation over a day or a week.
- **All calls went through OpenRouter.** The direct APIs can be faster. Mercury 3 on Inception's own
  API is the most important case to test. Inception gives its 1,068 tok/s figure for NVIDIA GPUs, and
  does not say which client path it measured.
- **Only two reasoning settings.** Run 3 used "none" and run 4 used "max". The default settings, which
  most users get, lie between the two bounds and were not measured.
- **Five runs per cell.** This number is enough for medians that are stable. It is not enough for
  tail figures, such as p95.
- **Short inputs only.** Every prompt was under 50 input tokens. The benchmark did not measure the
  time to read a long input (prefill), and coding agents often send long inputs.
- **Quality was not measured,** except that the code parsed. A fast model is useful only when its
  answers are good enough. Artificial Analysis has not measured Mercury 3 yet, so its PAL score of
  12 is provisional.

## Run 4: the benchmark at max reasoning

### Method

- **When:** 2026-10-21 01:45:30 to 02:08:55 UTC (2026-10-20, 18:45 to 19:09 PDT).
- **Purpose:** run 3 gives the fast bound, with reasoning off. Run 4 gives the slow bound, with each model at
  its highest reasoning effort.
- **Same as run 3:** the targets, the provider pinning, the prompts, the warm-up, the 5 shuffled rounds, and
  the seed.
- **Differences:**
  - `reasoning: {"effort": "max"}` for every call.
  - `max_tokens` 64000. All three models allow 128K output or more, so no answer was cut off.
- **Probe first:** `4-max-reasoning/probe_max_effort.py` checked that every target accepts "max", "xhigh",
  and "high" (`probe_max_output.txt`). All 12 calls succeeded.
- **Command:**

  ```bash
  .pal_venv/bin/python ANNOUNCEMENTS/2026-10-20-new-models/speed-benchmark/3-benchmark/benchmark_speed.py \
    --effort max --max-tokens 64000 \
    --out ANNOUNCEMENTS/2026-10-20-new-models/speed-benchmark/4-max-reasoning/benchmark_results_max.json
  ```

- **Quality check:** all 60 calls ended with "stop", and all 20 code answers parsed.

### New metrics

- **Reasoning tokens:** from `completion_tokens_details.reasoning_tokens`.
- **Answer tokens:** output tokens minus reasoning tokens.
- **All tokens/s:** all output tokens, reasoning included, / total s. This is the closest figure to the raw
  generation rate, but each lab counts the hidden tokens with its own tokenizer.
- **Decode tok/s** now counts answer tokens only. For run 3 nothing changes, because reasoning was 0.

### Results

Each cell gives the median of 5 runs, then the range (minimum to maximum).

**Short answer, effort max**

| Model | Total s | First content s | Reasoning tokens | Answer tokens | Words | Chars/s | All tokens/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 0.7 (0.7–2.2) | 0.7 (0.6–2.2) | 196 (119–376) | 34 (32–37) | 25 (24–28) | 276 (88–317) | 273 (185–407) | 2 (2–2) |
| Claude Haiku 5.5 | 2.6 (2.4–2.9) | 2.6 (2.3–2.9) | 99 (92–102) | 46 (44–48) | 22 (21–24) | 64 (55–69) | 55 (51–60) | 8 (7–9) |
| GPT-6 Luna (fast tier) | 2.6 (2.3–3.2) | 2.4 (2.1–3.1) | 34 (27–37) | 22 (22–22) | 14 (14–14) | 42 (34–47) | 22 (15–25) | 16 (16–16) |
| GPT-6 Luna | 3.0 (2.9–5.8) | 2.8 (2.8–5.7) | 28 (27–38) | 22 (22–22) | 14 (14–14) | 37 (19–37) | 16 (9–20) | 16 (16–16) |

**Prose (about 300 words), effort max**

| Model | Total s | First content s | Reasoning tokens | Answer tokens | Words | Chars/s | All tokens/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 2.6 (1.5–8.7) | 2.0 (0.7–8.0) | 600 (127–5,073) | 412 (357–424) | 338 (300–351) | 737 (211–1,397) | 441 (372–691) | 4 (4–5) |
| Claude Haiku 5.5 | 73.4 (44.9–90.5) | 73.4 (44.7–90.4) | 20,286 (12,824–25,578) | 528 (516–553) | 304 (299–311) | 23 (19–39) | 290 (283–297) | 67 (65–70) |
| GPT-6 Luna (fast tier) | 10.1 (9.3–15.6) | 9.3 (8.0–14.4) | 1,241 (697–1,871) | 345 (321–358) | 294 (275–304) | 154 (106–173) | 142 (111–161) | 339 (315–352) |
| GPT-6 Luna | 21.1 (19.2–32.4) | 19.2 (16.6–30.6) | 1,741 (1,145–2,955) | 361 (323–366) | 306 (281–310) | 83 (54–92) | 95 (79–108) | 355 (317–360) |

**Code (an LRU cache module with five tests), effort max**

| Model | Total s | First content s | Reasoning tokens | Answer tokens | Words | Chars/s | All tokens/s | Chunks |
|---|---|---|---|---|---|---|---|---|
| Mercury 3 | 2.8 (2.4–3.8) | 2.0 (1.5–2.7) | 843 (197–885) | 945 (735–1,063) | 423 (338–468) | 1,224 (962–1,643) | 548 (468–681) | 6 (5–7) |
| Claude Haiku 5.5 | 104.0 (78.0–149.0) | 103.9 (78.0–149.0) | 22,813 (17,194–32,611) | 2,076 (1,708–2,581) | 693 (559–843) | 52 (45–58) | 241 (236–246) | 260 (215–324) |
| GPT-6 Luna (fast tier) | 15.1 (11.9–28.1) | 13.4 (10.5–25.4) | 2,310 (1,439–4,510) | 620 (572–770) | 235 (208–321) | 137 (98–184) | 187 (173–192) | 614 (566–764) |
| GPT-6 Luna | 21.8 (14.9–49.0) | 19.8 (12.4–45.6) | 2,576 (1,626–6,961) | 648 (585–732) | 268 (214–311) | 94 (55–138) | 148 (142–160) | 642 (579–726) |

**Both bounds: median total seconds, and the multiple of Mercury 3's time**

| Model | Prose, off | Prose, max | Code, off | Code, max |
|---|---|---|---|---|
| Mercury 3 | 1.1 s | 2.6 s | 1.5 s | 2.8 s |
| Claude Haiku 5.5 | 3.7 s (3.3×) | 73.4 s (28.3×) | 7.7 s (5.2×) | 104.0 s (36.7×) |
| GPT-6 Luna (fast tier) | 3.1 s (2.7×) | 10.1 s (3.9×) | 3.8 s (2.6×) | 15.1 s (5.3×) |
| GPT-6 Luna | 5.2 s (4.5×) | 21.1 s (8.1×) | 6.3 s (4.3×) | 21.8 s (7.7×) |

Cost per target (15 calls): Mercury 3 $0.0076, Claude Haiku 5.5 $0.1167, GPT-6 Luna (fast tier) $0.0259, GPT-6 Luna $0.0165

### Observations

- **Mercury 3 stays fast at max.** It finished prose in 2.6 s and code in 2.8 s. Mercury 3 has no max level,
  though; see run 5. Its "max" calls behaved like its "high" calls.
- **"Max" means very different amounts of work.** On prose, Claude Haiku 5.5 used a median of 20,286
  reasoning tokens. GPT-6 Luna used 1,741, the fast tier 1,241, and Mercury 3 600. So the slow bound shows
  how much each lab lets its model think at "max", as well as how fast the model generates.
- **Raw generation rate, reasoning included:** Mercury 3 generated 441 tokens/s on prose and 548 on code.
  Haiku generated 290 and 241, the Luna fast tier 142 and 187, and standard Luna 95 and 148. The tokenizer
  caveat from run 3 applies here too.
- **Mercury 3 varies a lot at max.** Its reasoning on prose ran from 127 to 5,073 tokens, and its total time
  from 1.5 s to 8.7 s.
- **Haiku sends its answer in one burst at max.** Its first content arrived within 0.1 s of the end of the
  stream. So its decode rate is not valid, and the table leaves it out. The cause (Anthropic's API or
  OpenRouter) is not known.
- **The answers were the same length as in run 3.** The prose answers had 294 to 338 words (median per
  model), so the prose times compare fairly. The code answers differed in length again, from 235 to 693 words.
- **Bounds against Mercury 3, on prose:** Claude Haiku 5.5 took 3.3 to 28 times as long. Standard GPT-6 Luna
  took 4.5 to 8.1 times as long, and the Luna fast tier 2.7 to 3.9 times as long.
- **Quality was not measured.** Haiku's 20,000 reasoning tokens can buy a better answer. This benchmark
  measures time only.
- **Total cost:** $0.167 for 60 measured calls.

## Run 5: what "max" means for Mercury 3

- **When:** 2026-10-22, about 09:50 PDT.
- **Why:** a Reddit comment said that Mercury 3 has no xhigh or max level. Inception's docs confirm it: the
  `reasoning_effort` parameter accepts instant, low, medium, and high, and Mercury 3 defaults to low
  (https://docs.inceptionlabs.ai/capabilities/reasoning-efforts). OpenRouter accepted "max" in run 4 without
  an error, so run 4 does not show which level Mercury 3 used.
- **Method:** `5-mercury-effort-map/mercury_effort_map.py`. 5 prose calls at each of `none`, `low`, `medium`,
  `high`, and `max`, pinned to the inception provider, in a shuffled order. `max_tokens` 64000.

| Effort sent | Reasoning tokens, median | All 5 calls | Total s, median |
|---|---|---|---|
| none | 0 | 0, 0, 0, 0, 0 | 1.3 |
| low | 484 | 66, 202, 484, 1,042, 1,152 | 2.4 |
| medium | 38 | 20, 31, 38, 66, 133 | 1.4 |
| high | 511 | 84, 445, 511, 552, 1,219 | 3.5 |
| max | 604 | 86, 271, 604, 622, 4,966 | 2.5 |

**Findings:**
- **"max" behaved like "high"** (and like "low", the default). It did not behave like "medium". The data
  cannot show the exact mapping that OpenRouter uses.
- **Mercury 3's reasoning does not grow steadily with effort.** "medium" used fewer reasoning tokens than
  "low" on this prompt, and every level varies a lot from call to call.
- **Effect on run 4:** the slow bound for Mercury 3 is roughly its high level. The Haiku and Luna bounds are
  true max levels.
- **Cost:** $0.008.

## How to run again

Run each script from the repo root with the PAL virtual environment. Each script needs
`OPENROUTER_API_KEY` in the environment (for example, from the repo's `.env`). Do not print the key.

```bash
set -a; . ./.env; set +a
.pal_venv/bin/python ANNOUNCEMENTS/2026-10-20-new-models/speed-benchmark/2-reasoning-probe/probe_reasoning.py
.pal_venv/bin/python ANNOUNCEMENTS/2026-10-20-new-models/speed-benchmark/3-benchmark/benchmark_speed.py
```

**Caution:** `benchmark_speed.py` overwrites `benchmark_results.json`, and the deck reads that file.
Before you run it again, copy the old results to a new name.

The run takes about 4 minutes and costs about $0.02.

## Where the deck uses these numbers

- **Slide 1 (cover):** prose total s and median words, for all four targets.
- **Slide 2:** prose and code chars/s, with total s, for all four targets.
- **Slide 3:** the block count for the prose answers of Mercury 3 (4 or 5).
- **Slide 4:** Mercury 3 end-to-end tok/s on code (631), next to Inception's 1,068 tok/s.

**Benchmark deck** (`../../2026-10-20-mercury-benchmark/linkedin-deck-benchmark.pdf`, 12 slides):
- **Slide 1 (cover):** prose total s at both settings, and the Haiku multiple (3 to 28).
- **Slides 3, 4, 8, 9, and 10:** run 3 (reasoning off).
- **Slides 5 and 6:** run 4 (max): total s, reasoning tokens, and cost per 1,000 answers at both settings.
- **Slide 7:** both bounds, from run 3 and run 4 prose times.
- **Slide 11:** quality. AA Index values from the PAL catalog (v4.3), Mercury 2.5 from AA's Inception page, and
  5 of Inception's 12 self-reported results.
- **Slides 1, 2, and 5 to 7** carry a note that Mercury 3 has no max level (run 5).
- Run 2 (the probe) is no longer in the deck.

## Ideas for the write-up

- **Test Mercury 3 on Inception's own API.** This is the direct test of the 1,068 tok/s claim.
- **Measure quality on small coding tasks.** The plan is in `pal-mcp-server/PLANS/mercury-3-benchmarks.md`
  (revised 2026-10-28).
  - Models: Mercury 3, Claude Haiku 5.5, and GPT-6 Luna, each at low and high effort.
  - Tasks: 260 small tasks from SWE-bench Verified ("<15 min fix"), Aider Polyglot, and SWE-smith.
  - Measures: pass rate, cost per solved task, and time per solved task.
  - Harness checks: a subset runs again through Codex CLI and Claude Code.
  - The plan does not run Terminal-Bench 4.0 or SciCode. Terminal-Bench tasks take hours, and AA has SciCode
    under review.
- **Run the benchmark at several times of day,** and on more than one day, to show the variation.
- **Add a long-input prompt,** for example a 20,000-token file with a short question. This measures
  prefill, which matters for coding agents.
- **Add a run with each model's default reasoning,** to show the speed that most users get.
- **Separate the block behaviour from OpenRouter,** with a raw SSE capture from Inception's API.
- **Add more fast models:** DeepSeek V4.1 Flash 1017, Gemini 3.8 Flash, and MiniMax M3.1 Flash.
