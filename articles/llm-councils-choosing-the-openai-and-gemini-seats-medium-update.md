<!--
  Medium update helper for "LLM Councils: Choosing the OpenAI and Gemini Seats for a Claude-Led Council".
  Not for publication. Live post: https://medium.com/@raymondpeck/llm-councils-choosing-the-openai-and-gemini-seats-for-a-claude-led-council-9d0acc62f2a3
  It brings the live post to the source's new structure: a TL;DR and a Contents list at the top, the scraping
  details removed, and the metrics table and the full numbers moved to two appendices.
-->

# Updating the live article

Medium has no API for editing a published post, so the update is done in the Medium editor. Editing in place keeps readers' highlights and responses; a full re-paste would not. The changes:
- new: the TL;DR, the Contents list, three bridging sentences, and one added passage in "Building tiers by task difficulty";
- removed: the scraping details (one paragraph, the code block, and the paragraph about the regex);
- moved: the metrics table and the full numbers, to two appendices;
- corrected: one sentence about Argon's FrontierCode score.

Open `llm-councils-choosing-the-openai-and-gemini-seats-medium.html` in a browser as the copy source: copy blocks from the rendered page, never from raw Markdown or HTML source.

## Steps in the Medium editor

1. **Add the TL;DR.** Under the italic line "In this article, we work out...", paste the "TL;DR" heading and its five bullets from the rendered HTML. Set "TL;DR" as a large heading.
2. **Add a Contents placeholder.** Under the TL;DR, add a large heading "Contents". Leave a blank line under it for now; step 10 fills it.
3. **Remove the scraping details.** In "Getting the benchmark data", delete everything under the heading: the paragraph that starts "Skip ahead...", the code block, and the paragraph that starts "The regex keys on...". In their place, paste the new paragraph from the rendered HTML ("AA's leaderboard has one row per model and reasoning effort... so a short Python script reads the data that the page embeds instead.").
4. **Appendix A.** In "Choosing the metrics", cut the metrics table image (Metric, What it tells me about a seat). Paste it at the very end of the post, under a new large heading "Appendix A: The Nine Metrics". Then change the sentence before it to end: "...so I read nine columns: the Index, six test scores, the non-hallucination rate and the cost per task. Appendix A says what each one tells me about a seat."
5. **The tiers passage.** In "Building tiers by task difficulty", replace "So I sort the work into four tiers and pick one model and one effort per provider for each:" with the new text from the rendered HTML: "So I sort the work into four tiers and pick one model and one effort per provider for each. The tiers set the default by difficulty only: different kinds of work also lean on different benchmarks, so each council adjusts that default for its own kind of work, as I explain later in Adjusting the Council for Each Kind of Task. Here are the tiers:"
6. **Appendix B.** Cut the whole "The Numbers" section: its heading, "These are AA's v5 values...", the first table image, "SWE-Atlas QnA comes from the coding-agent page...", and the second table image. Paste it at the end, after Appendix A, and rename its heading "Appendix B: The Full Numbers". At the top of "What the Numbers Say", add: "The full tables, AA's v5 values as of 2026.10.28, are in Appendix B; the findings below quote the numbers that decide each seat."
7. **One correction.** In "On the Gemini side", change "Argon's FrontierCode 2.0 score of 25.7 trails every other model here by a wide margin" to "Argon's FrontierCode 2.0 score of 25.7 trails the GPT, Claude and Carbon rows at `high` (30.3 to 40.6) by a wide margin". Gemini 3.8 Flash scores 18.7, so "every other model" was wrong.
8. **One reference.** In "Codifying the Technique", change "using the script above" to "with that script".
9. **Publish the update.** Check the published page, not the editor: the order of sections must match the Contents list below.
10. **Fill in the Contents links.** On the published page, open the browser console and run the snippet below. It copies a Markdown list with Medium's own section links. Paste it into the "Contents" block at the end of this file, open this file in a Markdown preview, copy the rendered list, and paste it under the "Contents" heading in the editor. Publish again and click two links on the published page to check them.

Optional: link "Adjusting the Council for Each Kind of Task" in step 5's passage and the "Appendix A" and "Appendix B" mentions in the body to their sections, with the same links.

## Console snippet

```js
// Run on the published post. Copies "- [Heading](url#id)" lines for the Contents entries.
const want = ["The Council Setup", "The Analysis Technique", "What the Numbers Say", "The Council at Each Difficulty",
  "Adjusting the Council for Each Kind of Task", "Where the Numbers Stop", "Codifying the Technique", "Wrapping Up!",
  "Appendix A: The Nine Metrics", "Appendix B: The Full Numbers"];
const base = location.origin + location.pathname;
const found = [...document.querySelectorAll("article [id]")]
  .filter((el) => /^H[1-4]$/.test(el.tagName) && want.includes(el.innerText.trim()))
  .map((el) => `- [${el.innerText.trim()}](${base}#${el.id})`);
copy(found.join("\n"));
console.log(found.length === want.length ? "All entries found and copied." : `Found ${found.length} of ${want.length}:`, found);
```

## Contents (paste the snippet's output here, then copy it from the preview)

- The Council Setup
- The Analysis Technique
- What the Numbers Say
- The Council at Each Difficulty
- Adjusting the Council for Each Kind of Task
- Where the Numbers Stop
- Codifying the Technique
- Wrapping Up!
- Appendix A: The Nine Metrics
- Appendix B: The Full Numbers
