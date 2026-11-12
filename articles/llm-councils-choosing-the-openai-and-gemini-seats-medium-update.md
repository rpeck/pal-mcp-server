<!--
  Medium update helper for "LLM Councils: Choosing the OpenAI and Gemini Seats for a Claude-Led Council".
  Not for publication. Live post: https://medium.com/@raymondpeck/llm-councils-choosing-the-openai-and-gemini-seats-for-a-claude-led-council-9d0acc62f2a3
  It brings the live post to the source's new structure: a TL;DR and a Contents list at the top, and the scraping
  section, the metrics table and the full numbers moved to three appendices.
-->

# Updating the live article

Medium has no API for editing a published post, so the update is done in the Medium editor. The edits below change only structure: every sentence of the live post stays, except the "Skip ahead" sentence and three short bridging sentences. Editing in place keeps readers' highlights and responses; a full re-paste would not.

Open `llm-councils-choosing-the-openai-and-gemini-seats-medium.html` in a browser as the copy source: copy blocks from the rendered page, never from raw Markdown or HTML source.

## Steps in the Medium editor

1. **Add the TL;DR.** Under the italic line "In this article, we work out...", paste the "TL;DR" heading and its five bullets from the rendered HTML. Set "TL;DR" as a large heading.
2. **Add a Contents placeholder.** Under the TL;DR, add a large heading "Contents". Leave a blank line under it for now; step 8 fills it.
3. **Appendix A.** In "Getting the benchmark data", cut everything under the heading: the paragraph that starts "Skip ahead...", the code block, and the paragraph that starts "The regex keys on...". Paste it at the very end of the post, under a new large heading "Appendix A: Reading the AA Leaderboard Data". Delete the sentence "Skip ahead if you don't care how the scraping works." In its old place, under the "Getting the benchmark data" heading, paste the new bridging paragraph from the rendered HTML ("AA's leaderboard has one row per model and reasoning effort... Appendix A has the script and how it works.").
4. **Appendix B.** In "Choosing the metrics", cut the metrics table image (Metric, What it tells me about a seat). Paste it at the end, under a new large heading "Appendix B: The Nine Metrics". Then change the sentence before it to end: "...so I read nine columns: the Index, six test scores, the non-hallucination rate and the cost per task. Appendix B says what each one tells me about a seat."
5. **Appendix C.** Cut the whole "The Numbers" section: its heading, "These are AA's v5 values...", the first table image, "SWE-Atlas QnA comes from the coding-agent page...", and the second table image. Paste it at the end, and rename its heading "Appendix C: The Full Numbers". At the top of "What the Numbers Say", add: "The full tables, AA's v5 values as of 2026.10.28, are in Appendix C; the findings below quote the numbers that decide each seat."
6. **One reference.** In "Codifying the Technique", change "using the script above" to "using the script in Appendix A".
7. **Publish the update.** Check the published page, not the editor: the order of sections must match the Contents list below.
8. **Fill in the Contents links.** On the published page, open the browser console and run the snippet below. It copies a Markdown list with Medium's own section links. Paste it into the "Contents" block at the end of this file, open this file in a Markdown preview, copy the rendered list, and paste it under the "Contents" heading in the editor. Publish again and click two links on the published page to check them.

Optional: link the three "Appendix A/B/C" mentions in the body to their sections with the same links.

## Console snippet

```js
// Run on the published post. Copies "- [Heading](url#id)" lines for the Contents entries.
const want = ["The Council Setup", "The Analysis Technique", "What the Numbers Say", "The Council at Each Difficulty",
  "Adjusting the Council for Each Kind of Task", "Where the Numbers Stop", "Codifying the Technique", "Wrapping Up!",
  "Appendix A: Reading the AA Leaderboard Data", "Appendix B: The Nine Metrics", "Appendix C: The Full Numbers"];
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
- Appendix A: Reading the AA Leaderboard Data
- Appendix B: The Nine Metrics
- Appendix C: The Full Numbers
