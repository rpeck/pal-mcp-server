I use LLM Councils for all the tricky bits of my work: planning, code review, architecture, and hairy code changes. I've written about this before, and am about to dive into an in-depth series in which I've used my Claude Explorer project's MCP server to mine real numbers from my real sessions. I haven't seen anyone back up the LLM Council pattern with actual numbers, even Andrej Karpathy, who coined the term a few weeks after I started using it. 🤓 Well, to be fair, he's got better things to do.

Ok, but: Which OpenAI and Gemini models should sit next to Claude in an LLM Council? I worked it out from this week's Artificial Analysis v5 data, and I wrote up both the answer and the technique: [MEDIUM LINK]


The tl;dr version: GPT-6.1 Sol at rising effort for routine, standard and hard decisions, GPT-6 Astra at max for the critical ones, and Gemini 4 Argon in every tier until Google opens up Gemini 4 Carbon. The Gemini seat checks facts and contracts, since Argon says "I don't know" instead of guessing 96% of the time; the GPT seat challenges the code and the reasoning; and Claude writes the synthesis.


The whole roster lives in one policy file, and none of my council prompts names a model, so when the next model comes out, I run one Claude Code agent and every council picks up its new seats. As I often say, laziness is the mother of invention!


It's the first piece in my LLM Councils series. Follow along!


#LLM #ClaudeCode #Claude #OpenAI #Gemini #AIEngineering

