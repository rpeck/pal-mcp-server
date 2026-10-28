# Plan: small coding task benchmarks for Mercury 3, Claude Haiku 5.5, and GPT-6 Luna

**Owner:** Raymond Peck. **Written:** 2026-10-22. **Revised:** 2026-10-28. **Runs on:** a work laptop and one
exe.dev VM on a work account, with Claude Code.

Read this whole file before you start. It is self-contained: the session that wrote it is not available to you.

## 0. Status (2026-10-28)

No run has started. Two prerequisites are open:

1. **Keys for Haiku 5.5 and Luna.** The Anthropic and OpenAI keys in `~/.config/secrets/` belong to a client
   project. Raymond decides whether this benchmark may use them, or whether to create dedicated keys (§5).
2. **The VM.** A team admin must turn on standalone VMs on the work exe.dev team before you can create the VM (§7).

Done: the plan, the speed benchmark, the cause of the key-loading problem (§5), and the PAL conf entries for
Mercury 3, Haiku 5.5, and Luna.

## 1. Goal

Get reasonable evidence for how well three models do **small coding tasks** when a coding agent (Claude Code or
Codex CLI) sends work to them as subagents:
- Mercury 3 (Inception)
- Claude Haiku 5.5 (Anthropic)
- GPT-6 Luna (OpenAI)

The goal is evidence for all three models. It is not a claim for one model.

**Questions to answer, for each model at each effort:**
1. What share of small coding tasks does the model solve?
2. What does each solved task cost, and how long does it take? Count the cost and time of failed trials too.
3. Does the result hold on tasks that the model cannot have memorized?
4. Does the result hold in a real agent (Codex CLI, Claude Code), and not only in the test harness?

## 2. Decisions (2026-10-24)

| Topic | Decision |
|---|---|
| Effort | `low` and `high` for all three models. They give a lower and an upper bound. |
| Main suites | SWE-bench Verified "<15 min fix" (80 tasks), Aider Polyglot (120), SWE-smith (60) |
| Repeats | 1 per task and effort; a second run on 40 tasks, to measure noise |
| Harness | mini-swe-agent through Harbor, the same for all models |
| Harness checks | 48 tasks through Codex CLI (all three models); the same 48 through Claude Code (Haiku only) |
| Mercury 3 route | OpenRouter, pinned to the `inception` provider, with Raymond's personal key |
| Haiku 5.5 route | Anthropic API, with a work key (which key is open, see §0) |
| Luna route | OpenAI API, with a work key (which key is open, see §0). Codex check only: the work ChatGPT plan |
| Compute | One standalone x86 VM on the work exe.dev account: 16 vCPU, 32 GiB |
| Output | `benchmarks/mercury-3/output/` in this repository |
| Not in scope | Terminal-Bench 4.0, SciCode, IFBench (see §4) |

**Why this design:** a review on 2026-10-23 asked five models from five vendors (Claude Opus 5.5, GPT-6 Astra,
Gemini 4 Argon, Kimi K3.1 Pro, DeepSeek V4 Pro). All five agreed on these points:
- Do not run Terminal-Bench. Its tasks take hours, so they are not small tasks.
- Run all models on the same tasks, with the same harness and limits.
- Use SWE-bench Verified "<15 min fix" and Aider Polyglot.
- Effort labels do not mean equal compute at different labs. So report the trade-off between pass rate, cost,
  and time for each model, and do not treat the labels as equal.

## 3. What is already known (as of 2026-10-24)

**The models:**

| Model | Efforts | Input / cached input / output, $ per M tokens |
|---|---|---|
| Mercury 3 | `instant`, `low`, `medium`, `high`. Default `low`. No `xhigh` or `max` | 0.10 / 0.01 / 0.35 (launch price) |
| Claude Haiku 5.5 | Up to `max` | 0.10 / 0.01 / 0.50. Cache writes 0.125 |
| GPT-6 Luna | Up to `max` | 0.10 / 0.01 / 0.50 |

- Mercury 3 is a diffusion LLM, released 2026-10-19. Text only, 1M context. Its efforts are documented at
  https://docs.inceptionlabs.ai/capabilities/reasoning-efforts.
- The prices are OpenRouter's list prices on 2026-10-23. AA lists the same prices for the direct APIs.

**Speed** (Raymond's benchmark, 2026-10-20, through OpenRouter, about 300 words of prose):
- Reasoning off: Mercury 3 1.1 s, Haiku 5.5 3.7 s, Luna 5.2 s.
- At max effort (`high` for Mercury 3): Mercury 3 2.6 s, Luna 21.1 s, Haiku 5.5 73.4 s.
- At max, Haiku 5.5 used about 34 times as many reasoning tokens as Mercury 3.
- OpenRouter added up to about 0.4 s to each call.

**Published quality data.** Use it as context only. It does not compare with this plan's results:
- The AA Intelligence Index (v4.3), read 2026-10-27:

  | Model | low | medium | high | xhigh | max |
  |---|---|---|---|---|---|
  | Claude Haiku 5.5 | 29 | 34 | 38 | 41 | 43 |
  | GPT-6 Luna | 22 | 30 | 33 | 35 | 38 |

- AA has no Mercury 3 data yet. On 2026-10-27 it listed only Mercury 2.5 (12) and Mercury 2 (11).
- AA publishes total scores only, with no per-task data. Its benchmark pages show only the max-effort variants
  of Haiku 5.5 and Luna.
- Vals AI measured Mercury 3 at `high` (https://www.vals.ai/models/inception_mercury-3):
  - Vals Index 43.66%, rank 25 of 28.
  - Vibe Code Bench v1.1 60.93%, rank 56 of 120.
  - Terminal-Bench 4.0 3.03%, rank 49 of 56.
- Inception's own results, all at `medium`:

  | Benchmark | Mercury 3 | GPT-6 Luna | Claude Haiku 5.5 |
  |---|---|---|---|
  | SWE-Bench Atlas | 51.08 | 33.33 | 32.26 |
  | DeepSWE v1 | 46.31 | 37.76 | 51.62 |

  No third party has checked these results.

**A new model since the plan was written:** GPT-6.1 Terra (OpenAI, 2026-10-26).
- AA Index 36 at low and 44 at high. $1 / $0.05 / $5 per M tokens, which is 10 times Luna's price.
- OpenAI made it available in Codex. On 2026-10-27, Codex 0.164.0 on the work ChatGPT plan did not list it yet.
- It is not in this plan. Raymond decides whether to add it (see "Optional" in §4).

## 4. Scope

**In scope:**

| Suite (Harbor dataset) | Pool | Sample | Why |
|---|---|---|---|
| SWE-bench Verified (`swebench-verified` 1.0) | 194 tasks labeled "<15 min fix" | 80, in proportion to each repository's share | Small fixes in real repositories, with hidden tests. The size labels come from human annotators |
| Aider Polyglot (`aider-polyglot` 1.0) | 225 | 120: 20 for each of the 6 languages | Small, mostly single-file tasks in several languages |
| SWE-smith (`swesmith` 1.0) | 100 | 60, in proportion to each repository's share | Synthetic bugs in real repositories. This suite checks for memorization |

**Contamination:** SWE-bench Verified has been public since 2024, so the models probably trained on it. SWE-smith
bugs are synthetic, so memorization is less likely. Compare the two suites (§11).

**Not in scope:**
- **Terminal-Bench 4.0.**
  - The median expert time estimate is 4 hours.
  - Each task has an 8-hour agent timeout and needs 2 to 16 CPUs.
  - Three tasks need an H100 GPU.
  - These are not small tasks.
- **SciCode.** AA marks it "under review", because an audit found possible errors in the dataset.
- **IFBench.** It tests instruction following, not coding.

**Optional, only if the budget allows and Raymond approves:**
- SWE-Atlas QnA (Harbor `scale-ai/swe-atlas-qna`, 124 tasks). It tests code exploration, a common subagent job.
  It also checks Inception's SWE-Bench Atlas claim directly.
- Haiku 5.5 and Luna at `max`, on a 24-task subset.
- New private SWE-smith bugs, made after the three models were released. This is a stronger memorization
  check, but it needs more setup work.
- GPT-6.1 Terra as a fourth model, if Codex starts to use it for subagents. On the main set at two efforts, it
  adds 520 trials. At 10 times Luna's price per token and the trial costs in §12, that is about $50–$300
  (estimate). This can exceed the $150 limit in §12, so it needs a separate budget.

## 5. Models, routes, and keys

| Model | Route | Model ID | Key |
|---|---|---|---|
| Mercury 3 | OpenRouter, with `provider: {"order": ["inception"], "allow_fallbacks": false}` | `inception/mercury-3` | Raymond's personal OpenRouter key |
| Claude Haiku 5.5 | Anthropic API | `claude-haiku-5-5` (confirm in Anthropic's docs) | Work Anthropic key (see below) |
| GPT-6 Luna | OpenAI API | Confirm in OpenAI's docs. On OpenRouter it is `openai/gpt-6-luna` | Work OpenAI key (see below) |

**Keys:**
- **The personal OpenRouter key:** `source ~/.config/secrets/env` sets `OPENROUTER_API_KEY` in any shell.
- **The Anthropic and OpenAI keys:** the `*.keys` files in `~/.config/secrets/` read them with `op read`, but
  each file starts with `case $- in *i*) ;; *) return 0 ;; esac`. In a non-interactive shell (a script, or
  Claude Code's Bash tool) the file stops there, so these keys are not set and you get no error. This was the
  cause of the "not set" results on 2026-10-24, not the `op` sign-in.
- **Do not use those keys for this benchmark unless Raymond approves.** They belong to a client project.
- **Dedicated keys:** Raymond creates one Anthropic key and one OpenAI key, each with a spend limit, and stores
  them in 1Password. The run script reads them with `op read '<op:// reference>'`. Raymond gives you the
  references. Never write a reference or a key into this repository.
- **`op` from Claude Code:** the 1Password app integration with the CLI works from the Bash tool, outside the
  sandbox. Raymond approves each new session with Touch ID. `op whoami` reports "not signed in" even when `op`
  works, so do not use it as a check.
- **The VM:** `op` does not work on the VM. The run script reads the keys on the laptop and copies them into a
  file with mode 600 on the VM. Harbor passes each key into every task container. Delete the file and revoke the
  dedicated keys when the runs end.
- **Never print a key.** Print only "set" or "not set".

**Effort:** send `low` or `high` as the reasoning effort to every model.
- Harbor passes it with `--ak reasoning_effort=<level>`.
- LiteLLM drops `reasoning_effort`, with no error, for a model that it does not know. Register all three models
  with mini-swe-agent's `litellm_model_registry` option. Give the registry the prices in §3, so that the
  per-trial cost limit works.

**Timing bias:** Mercury 3 goes through OpenRouter, and the other two models go direct.
- Measure the OpenRouter overhead in Phase 0 (§9).
- Report the overhead next to every time figure.

## 6. Harness and limits

**Main harness:** mini-swe-agent through Harbor (agent name `mini-swe-agent`). It is bash-only and does not depend
on the model. AA uses it.

**Keep these settings the same for every model and effort:**
- The task timeouts in each suite's own task definitions.
- A step limit of 100 per trial.
- A cost limit of $1 per trial.
- `max_tokens` 32,000 per call.
- The provider's default temperature. The Anthropic and OpenAI reasoning endpoints do not accept
  `temperature`.
- The prompt caching settings. Turn on mini-swe-agent's cache-control option for Haiku.
- One VM and one concurrency value.

Record every trial that reaches a limit. Do not run a failed trial again. Run a trial again only if it failed
because of the infrastructure (for example, an image pull error). Record each of those reruns.

**Harness checks** (§10): Harbor agent names `codex` and `claude-code`.

## 7. Compute

- **VM:** create one standalone VM on the work exe.dev account. Use the `using-exe-dev` skill:
  - 16 vCPU and 32 GiB of memory.
  - The `exeuntu` image, which can run Docker.
  - Enough disk for the SWE-bench images, about 150–250 GB (estimate). Alternatively, remove the images after
    each batch.
- **Do not put the VM in any current pool.** Those pools belong to other work.
- **Team setting:** on a Work team, standalone VMs are off until a team admin turns them on
  (`team settings standalone admins-only` or `all-users`). Sizes up to 32 vCPU need no approval from exe.dev
  support. Raymond's login is probably not a team admin: on 2026-10-25, `ssh exe.dev team` returned "command
  not available". Ask Raymond before you create the VM.
- **CPU architecture:** run `uname -m` on the new VM. It must show `x86_64`. If it shows `aarch64`, stop and
  report to Raymond.
- **Concurrency:** each task needs 1 CPU and 4 GB of memory. Start with 6 trials in parallel. Do not overload the
  VM, because an overloaded VM changes the times. Keep the same value for every configuration, and record it.
- **Cost:** $0.84 per hour.
- **At the end:** delete the VM after you copy the raw data from it.

## 8. Prerequisites (stop if one fails)

1. **Keys.** Load the keys as §5 says. Report "set" or "not set" for `OPENROUTER_API_KEY`,
   `ANTHROPIC_API_KEY`, and `OPENAI_API_KEY`.
2. **The ChatGPT plan, for the Codex check only.** Raymond signs in to the Codex CLI with the work ChatGPT
   plan. Harbor's `codex` agent reads `~/.codex/auth.json` by default.
3. **The VM** (§7), with Docker working.
4. **Harbor.** Install it on the VM as the Harbor docs say (https://harborframework.com/docs).
   - Confirm the options with `harbor run --help`. Do not invent commands.
   - Options seen in Harbor's source code on 2026-10-23: `--agent`, `--model`, `--ak`, `-k/--n-attempts`,
     `-n/--n-concurrent`, `--include-task-name`, `-x/--exclude-task-name`, `--job-name`, `-o/--jobs-dir`,
     and `-e/--env`.
5. **Python 3.11 or newer, with `uv`.**

## 9. Phase 0: setup, oracle check, route checks, and pilot

**1. Oracle check.** Run Harbor's `oracle` agent (the reference solutions) once on all three candidate pools:
194 + 225 + 100 tasks.
- This step costs compute only.
- Drop each task whose reference solution fails. Record the dropped tasks.

**2. Sample.** Select the tasks from the tasks that pass the oracle check. Use the fixed seed `20261024`.
- Main set: 80 SWE-bench Verified + 120 Aider Polyglot + 60 SWE-smith = 260 tasks.
- Repeat subset, in proportion to the main set: 12 SWE-bench Verified + 18 Aider Polyglot + 10 SWE-smith = 40
  tasks.
- Harness-check subset, in proportion to the main set: 15 SWE-bench Verified + 22 Aider Polyglot + 11 SWE-smith
  = 48 tasks.
- Write all task IDs to `benchmarks/mercury-3/tasks/` **before** any model run.
- Never change the sample because of a model's results.

**3. Route checks.**
- Send one short call to each model at `low` and at `high`, through its route. Confirm the model and the provider
  in each response.
- **OpenRouter overhead:** send 20 short calls to Haiku 5.5 through OpenRouter (pinned to `anthropic`) and 20
  through the Anthropic API. The difference in the median times is the overhead per call. This costs a few cents
  on the personal key.

**4. Pilot.** 9 tasks (3 from each suite) × 3 models × 2 efforts = 54 trials. For every trial, record:
- pass or fail
- input, cached input, output, and reasoning tokens
- cost
- wall time
- steps

**Checks on the pilot:**
- At `high`, reasoning tokens are greater than zero for every model. If they are zero, the effort did not
  arrive. Fix this before you continue.
- At `low`, every model uses fewer reasoning tokens than at `high`.
- Cached input tokens are greater than zero after the first step, for every model.
- A cost is recorded for every trial, so the $1 limit works.
- From the pilot, project the cost of the whole plan (§12). **If the projection exceeds the budget, stop and
  report to Raymond.**

## 10. Phases 1 to 3: the runs

**Phase 1: main runs.** 260 tasks × 3 models × 2 efforts = 1,560 trials, with mini-swe-agent.
- Split the tasks into 10 batches. Run all 6 configurations on each batch, in a shuffled order from the seed.
  This spreads server load and network changes over all the models.

**Phase 2: repeat.** Run the 40-task repeat subset once more: 40 × 3 × 2 = 240 trials.
- Use only the first runs to calculate pass rates.
- Use the repeats only to measure the noise. Never count the better of two runs.

**Phase 3: harness checks.** Use the 48-task subset at `low` effort.
- **Codex CLI:** 48 tasks × 3 models = 144 trials.
  - Luna uses the work ChatGPT plan. This is how Raymond runs Codex subagents.
  - Mercury 3 uses OpenRouter, through a Codex model provider setting.
  - Haiku 5.5 uses Anthropic's OpenAI-compatible endpoint. If Codex does not accept it, Haiku uses OpenRouter
    pinned to `anthropic`.
  - Confirm in Phase 0 that Codex can call each model. If a model is not reachable, record that, and drop the
    model from this check.
- **Claude Code:** 48 tasks × Haiku 5.5 = 48 trials, with the work Anthropic key. Claude Code runs only
  Claude models, so this check shows how well Haiku does as a real Claude Code subagent.

## 11. Analysis and report

Write `benchmarks/mercury-3/output/report.md` with these sections:

1. **Setup:**
   - routes, model IDs, efforts, limits, and concurrency
   - the versions of Harbor and mini-swe-agent
   - dataset versions, task IDs, the seed, and the oracle drops
   - dates, and the VM's CPU type
2. **Pass rates:** report pass@1 for each suite, model, and effort.
   - Give a 95% confidence interval from a paired bootstrap over tasks.
   - For SWE-bench Verified and SWE-smith, resample tasks in clusters by repository.
3. **Paired comparisons:** report the difference in pass rate between each pair of models, at each effort.
   - Use exact McNemar tests on the first runs, with a Holm correction for the three pairs.
   - Report where each model wins and loses.
4. **Bounds:** for each model, plot pass rate against cost and against time, at `low` and at `high`. This shows
   what the extra effort buys on small tasks.
5. **Cost and time per solved task:**
   - Cost per solved task: the total API cost of all trials, failed trials included, divided by the number of
     solved tasks. Use the list prices in §3 for every model, so that the payer does not change the figure.
   - Show a second figure that includes the VM cost.
   - Time: the median and the p90 wall time per trial, and the total time divided by the number of solved tasks.
   - Report the OpenRouter overhead next to the Mercury 3 times.
6. **Tokens and steps:** for each model and effort, report the median input, cached input, output, and reasoning
   tokens, and the median steps per trial.
7. **Failure types:**
   - wrong fix
   - tool or format error
   - timeout
   - step limit or cost limit
   - infrastructure
8. **Memorization check:** compare each model's drop in pass rate from SWE-bench Verified to SWE-smith. A much
   larger drop for one model is a warning sign.
9. **Noise:** report the agreement between the first run and the repeat on the 40-task subset.
10. **Harness checks:** these results are exploratory.
    - For each model, compare the mini-swe-agent pass rate with the Codex pass rate, on the same 48 tasks.
    - Report whether the order of the models changes between the two harnesses.
    - Compare Haiku in Claude Code with Haiku in mini-swe-agent.
11. **Context:** list AA's and Inception's published numbers, with the differences in setup. Do not calculate a
    gap between them and these results.
12. **Guidance for subagent use:** write a draft rule for each model, for example "use model X at effort Y for
    tasks like Z". Show the evidence for each rule. Leave the final choice to Raymond.
13. **Limits:** state what the run did not measure, for example real delegation context and long tasks.

## 12. Cost and budget

These figures are estimates. The pilot replaces them with real numbers.

| Item | Paid by | Estimate |
|---|---|---|
| Mercury 3 API | Raymond (OpenRouter) | $5–$25 |
| Haiku 5.5 API | Work account (Anthropic key) | $15–$35 |
| GPT-6 Luna API | Work account (OpenAI key) | $10–$25 |
| Harness checks | Work accounts, plus a small part on OpenRouter | $3–$10 |
| VM, about 30–65 hours | Work account (exe.dev) | $25–$55 |

**Trial counts:**

| Part | Trials |
|---|---|
| Pilot | 54 |
| Phase 1 | 1,560 |
| Phase 2 | 240 |
| Phase 3 | 192 |
| **Total** | **2,046** |

The oracle runs are not in this count.

**Assumptions:**
- An average trial takes 5 to 10 minutes, with 6 trials in parallel.
- An average trial costs $0.01–$0.06 when prompt caching works.

**Limits:**
- **Stop and ask Raymond if the total projected cost exceeds $150.**
- **Stop and ask Raymond if the spend on his personal OpenRouter key exceeds $30.**

## 13. Output and files

All files go in `benchmarks/mercury-3/` in this repository:

| Path | Content |
|---|---|
| `benchmarks/mercury-3/` | Run scripts, the LiteLLM model registry, and the analysis script |
| `benchmarks/mercury-3/tasks/` | Sampled task IDs, the oracle results, and the seed |
| `benchmarks/mercury-3/output/report.md` | The report (§11) |
| `benchmarks/mercury-3/output/trials.json` | One record per trial |
| `benchmarks/mercury-3/output/summary.json` | The tables in the report, as data |
| `benchmarks/mercury-3/output/raw/` | Harbor job folders and logs. Exclude this folder with `.gitignore` |

**Caution:** this repository is a personal project. Before you write any file into it, remove these items from
logs and data:
- employer and client names
- organization IDs and account IDs
- workspace names
- email addresses
- fragments of keys

Search the output for each type of item before you finish.

## 14. Rules and cautions

**Caution:** do not send employer or client code, data, or documents to any API. This plan uses public tasks
only.

- Never print or log an API key.
- Do not commit, push, or open PRs unless Raymond asks.
- Ask Raymond before you create any cloud resource. The VM in §7 also needs Raymond's approval.
- Delete the VM and revoke the dedicated keys when the runs end.
- Before you report a number, check it against the raw data. Mark every estimate as an estimate.
- If a step fails, report the failure with its output. Do not skip it silently.

## 15. Sources

- Inception launch post, with the results table image: https://www.inceptionlabs.ai/blog/introducing-mercury-3
- Inception reasoning efforts: https://docs.inceptionlabs.ai/capabilities/reasoning-efforts
- AA methodology: https://artificialanalysis.ai/methodology/intelligence-benchmarking
- AA Intelligence Index: https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index
- Harbor docs: https://harborframework.com/docs
- Harbor source code and dataset registry (`registry.json`): https://github.com/harbor-framework/harbor
- SWE-bench Verified, with the `difficulty` labels: https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified
- SWE-smith: https://github.com/SWE-bench/SWE-smith
- Aider Polyglot: https://github.com/Aider-AI/polyglot-benchmark
- exe.dev docs: https://exe.dev/docs.md
- AA leaderboard (per-effort values): https://artificialanalysis.ai/leaderboards/models
- Vals AI, Mercury 3: https://www.vals.ai/models/inception_mercury-3
- AA on GPT-6.1 Terra: https://artificialanalysis.ai/articles/gpt-6-1-terra-pushes-the-cost-efficiency-frontier
- Speed benchmark method and data: `PLANS/mercury-3-speed-benchmark-README.md`
