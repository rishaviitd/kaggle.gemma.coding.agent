Source URL: https://machinelearningmastery.com/prompt-engineering-for-agentic-ai/
Title: Prompt Engineering for Agentic AI
By Shittu Olumide — May 19, 2026 — MachineLearningMastery.com

In this article, you will learn how prompt engineering changes fundamentally when applied to agentic AI systems, and what principles and patterns enable reliable agent behavior at scale.

Topics we will cover include:

* Why prompting agents differs from prompting chatbots, and what context engineering means in practice.
* The four components every agent prompt needs, including system prompts, tools, examples, and context state management.
* The reasoning architectures that make agents more reliable, from chain of thought to ReAct and Reflexion.

## Introduction

You have probably spent time learning how to prompt AI well. Better phrasing, clearer instructions, more context upfront. That knowledge is genuinely useful, and it will take you only so far once you move into agentic AI.

The prompting skills that work in a chat window break down the moment the AI starts taking actions across multiple steps. A well-crafted question produces one good response. A well-designed agent prompt steers a system that reads files, calls APIs, makes decisions, delegates to sub-agents, recovers from errors, and delivers a finished output, all without you shepherding each step. Those are two different disciplines. One is asking. The other is designing how a system thinks.

This article is about the second thing. It is written for builders and practitioners who are moving past chat and into agents, people who want to know how prompting actually works inside autonomous systems, what the reliable patterns look like, and where most people go wrong.

## Why Prompting an Agent is Different From Prompting a Chatbot

When you prompt a chatbot, your only job is to produce a good next response. You write something, the model replies, you adjust and go again. The feedback loop is short and visible. If the output is wrong, you can see it immediately and re-prompt.

Agents do not work that way. An agent receives a goal, builds a plan, executes it across many steps, uses tools, generates intermediate outputs that feed into later steps, and eventually delivers a final result. The problem is that an ambiguous instruction at step one does not visibly fail at step one; it drifts. By step seven, the agent is technically doing what it inferred from your prompt, which may be something you never intended. And by that point, you have already consumed significant compute, time, and tool calls getting there.

This is the core challenge of agentic prompting: the effects of your prompt are distributed across time and steps, not concentrated in a single response.

There is also a structural issue that compounds this. Research on context degradation shows that as the number of tokens in an agent’s context window grows, the model’s ability to accurately recall and reason over that information decreases, a phenomenon researchers call context rot. Every tool call result, every intermediate output, every completed step adds tokens. By the middle of a long task, an agent operating on a poorly designed context may lose track of constraints that were clearly stated at the beginning.

This is exactly why Anthropic’s engineering team introduced the concept of context engineering as the natural evolution of prompt engineering. Their framing: prompt engineering asks “**what are the right words?**” Context engineering asks “**what is the optimal set of information this model should have at every point during execution?**” That is a bigger, more architectural question, and it is the right question for building agents that behave reliably.

## The Four Components Every Agent Prompt Needs

Based on Lilian Weng’s foundational framework for LLM-powered agents and Anthropic’s engineering guidance, a well-designed agent operates on four categories of context. Each one needs deliberate design. Leaving any of them to chance is where most failures originate.

### The System Prompt

The system prompt is the brief your agent operates under for the entire task. It defines the role the agent plays, the tools available to it, the constraints it must respect, and the output it should deliver. It is the most consequential piece of text in your entire agent architecture, and it is also the easiest one to write badly.

Anthropic’s engineering team describes two failure modes that bracket the wrong approaches. On one side: **over-specification**. Prompts packed with brittle if-else logic that try to anticipate every possible scenario, hardcoding behavior that should be left to the model’s judgment. These prompts are fragile — one edge case they did not anticipate, and the whole system misbehaves. On the other side: **under-specification**. Vague, high-level goals that assume the model shares context it does not have. These prompts leave the agent to fill in blanks you did not know you were leaving.

The right approach is what Anthropic calls the **right altitude**: specific enough to meaningfully constrain behavior, flexible enough to handle situations you did not explicitly script. Here is what that looks like in practice.

Weak system prompt:

You are a helpful research assistant. Help the user with their research tasks

Strong system prompt:

You are a research assistant helping a B2B SaaS product team synthesize competitive intelligence. You have access to a web search tool and a file-writing tool. Your work will be reviewed by a product manager before any decisions are made. When given a research task: 1. Clarify the scope if the goal is ambiguous before starting 2. Search for information from primary sources first (company websites, official announcements, earnings calls) before secondary sources 3. Flag any information older than 12 months as potentially outdated 4. Do not draw conclusions about competitor strategy -- report findings only and let the human interpret them Deliver a structured report with: Executive Summary (3-5 sentences), Findings by category, and a Sources section with URLs. Format as Markdown.

The second version does not over-specify every action the agent might take. It gives the agent a clear role context, behavioral constraints, a source priority hierarchy, a scope on what it should and should not conclude, and an output format. Those are heuristics, not scripts, and that is exactly what makes them durable.

### Tools

Every tool you give an agent is a decision point and a token cost. Tool descriptions consume attention budget. Overlapping tools create ambiguity. According to Anthropic’s guidance, one of the most common failure modes in production agents is bloated tool sets, where the agent cannot reliably decide which tool to use in a given situation.

The test is simple: if you, as a human looking at the agent’s situation, cannot instantly decide which tool applies, the agent will not reliably decide either. Every tool should have exactly one job, a description that makes its purpose unambiguous, and parameters that are descriptive enough to use without external documentation.

**Weak tool description:**

{ "name": "search", "description": "Search for information" }

**Strong tool description:**

{ "name": "web_search", "description": "Search the public web for current information on a topic. Use this when you need facts, news, or data that may have changed recently or that is not in your training knowledge. Do NOT use this to retrieve documents already provided in the task context.", "parameters": { "query": "A specific, targeted search query of 3-8 words", "max_results": "Number of results to return (default: 5, max: 10)" } }

The stronger version tells the agent not just what the tool does, but when to use it and — critically — when not to. That boundary is what prevents the agent from defaulting to a web search for information that is already in its context, wasting tokens and time.

### Examples (Few-Shot Prompting)

Research consistently shows that examples outperform instruction lists for shaping agent behavior. When the model sees two or three concrete input-output pairs, it activates in-context learning, identifying the pattern and applying the same transformation logic to new inputs, often more reliably than natural language instructions can achieve.

For agents, examples serve a specific purpose: they demonstrate the expected reasoning format, output structure, and decision style — not just the right answer. A good few-shot example for an agent shows the thinking, not just the result.

**Example: Two-shot prompt for a data analysis agent**

Example 1: Input: "Summarize the sales data in Q1_sales.csv" Thought: I need to read the file first to understand its structure before summarizing anything. Action: read_file("Q1_sales.csv") Observation: File contains columns: Date, Region, Product, Revenue, Units. 547 rows covering January-March 2025. Thought: Now I can summarize. I'll calculate totals by region and product, then identify the top performer and any notable trends. Output: \[Structured summary with totals, top performers, and one key trend\] Example 2: Input: "Compare our pricing to competitors" Thought: This is too vague to act on -- I need to know which competitors and which products before I start searching. Output: "To compare pricing accurately, I need two things: which competitors should I focus on, and which of your products should I benchmark? Please clarify and I'll proceed."

Notice that example two shows the agent recognizing ambiguity and pausing to clarify — that is a behavior you want to demonstrate explicitly, because it is not obvious from instructions alone.

### Message History and Context State

The message history is every prior turn, tool call result, and intermediate output the agent has produced during the current task. It is also the main source of context rot in long-running agents.

Anthropic’s research describes the transformer’s attention mechanism as an **attention budget**: every token in the context window competes for the model’s focus, and that budget gets stretched as context grows. The model remains capable in longer contexts but shows measurably reduced precision for information retrieval and long-range reasoning compared to shorter ones.

The practical implication is that dumping everything into the context window — every tool result in full, every intermediate step — is a way to make your agent dumber as it gets further into a task.

The better approach is **just-in-time context**: instead of pre-loading all relevant data upfront, agents maintain lightweight references (file paths, stored query results, URLs) and fetch what they need at the moment they need it. This is how Claude Code handles large codebases: it stores file paths and uses targeted reads rather than loading entire repositories into context. The model sees only the specific files relevant to the current step, keeping the active context lean and attention focused.

## The Reasoning Architectures That Actually Work

How you structure an agent’s reasoning matters as much as what you put in the prompt. Research from Google’s team published in 2022 established the foundational proof: on Game of 24 puzzles, a frontier model went from 4% success to 74% success — not from a model upgrade, but from giving it a structured way to reason through the problem. The model did not get smarter; its reasoning architecture did.

### Chain of Thought (CoT)

Chain of thought prompting is the simplest architectural upgrade available and the foundation on which everything else builds. Instead of jumping from question to answer, the model generates its reasoning steps explicitly before committing to an output.

The original research by Wei et al. showed that simply appending “**Let’s think step by step**” to a prompt produced significant accuracy gains on multi-step problems. That phrase activates a reasoning mode. The model externalizes its working, which both improves accuracy and makes the reasoning visible and auditable — valuable for any high-stakes application.

**Basic CoT prompt addition:**

You are a financial analysis agent. When given an analysis task, always think through the following before producing output: - What data do I have, and what data is missing? - What assumptions am I making that could be wrong? - What is the most likely interpretation of this data? - What would change my conclusion? Then produce your analysis based on that thinking.

The key is that CoT works best when the reasoning structure is matched to the task type. Financial analysis needs different reasoning steps than code debugging or competitive research. Tailor the thinking framework to what your agent actually does.

### ReAct (Reason + Act)

ReAct is the dominant pattern for agents that use tools. The name comes from the loop it implements: **Thought → Action → Observation → Thought**. The model reasons about what to do, takes an action using a tool, observes the result, and then reasons again based on what it learned. This loop continues until the task is complete.

What makes ReAct powerful is that it grounds the model’s reasoning in actual evidence. Instead of reasoning into a vacuum and producing a confident but hallucinated answer, the model is forced to test its assumptions against real tool outputs at each step.

ReAct prompt template:

You are a research agent with access to web_search and save_file tools. Work through tasks using this loop: Thought: \[Reason about what you know and what you need to find out next\] Action: \[Choose a tool and specify exactly what to do with it\] Observation: \[This will be filled in with the tool result -- do not write this yourself\] Thought: \[Reason about what the observation tells you and what to do next\] ... repeat until complete ... Final Answer: \[Deliver the completed output\] Never skip the Thought step. Never take an Action without a Thought that justifies it. If an Observation is unexpected, reason about why before deciding how to proceed.

Example of the ReAct loop in action:

Task: "Find the current pricing for Notion's Team plan and compare it to Coda's Team plan." Thought: I need current pricing for both products. I'll search for each separately to get accurate, up-to-date figures rather than relying on my training data. Action: web_search("Notion Team plan pricing 2025") Observation: Notion Team plan is $16/user/month billed annually, $20/user/month billed monthly as of March 2025\. Thought: I have Notion's pricing. Now I need Coda's. Action: web_search("Coda Team plan pricing 2025") Observation: Coda Team plan is $30/user/month billed annually. Thought: I have both data points. Notion is significantly cheaper at annual billing. I should note when each price was verified. Final Answer: \[Structured comparison with pricing, billing options, and date of data retrieval\]

The loop forces the agent to commit to a specific action, see a real result, and update its reasoning rather than generating a response based purely on internal assumptions.

### Reflexion (Self-Correction)

Reflexion takes ReAct one step further. After completing a task or a major step, the agent evaluates its own output against the original goal, identifies specific failures or gaps, and generates a revised plan before continuing or delivering a final result. It is how you build agents that catch their own mistakes without requiring human intervention at every step.

Reflexion prompt addition:

After completing each major task step, before moving to the next one, run a self-check: Reflection: - Does this output fully address what was asked? - Are there any claims I made that I cannot verify from the data I retrieved? - Did I miss any constraints stated in the original task? - If I were the human reviewing this, what would I flag? If you identify a gap or error, correct it before proceeding. State what you found and what you changed.

Reflexion in practice:

\[Agent completes a first draft of a competitor analysis report\] Reflection: Reviewing against the original task -- the user asked for pricing, feature comparison, AND market positioning. I covered pricing and features, but I did not address how each competitor positions themselves in marketing materials. That section is missing. Correcting: Running an additional search on each competitor's homepage and recent press releases to capture positioning language before delivering the final report. Action: web_search("Competitor A positioning messaging 2025") ...

Reflexion is most valuable for tasks where quality matters more than speed: reports, analysis, and structured documents. The self-check loop adds latency but meaningfully reduces the rate of incomplete or inconsistent outputs reaching the end user.

## Context Engineering in Practice

Understanding the theory is one thing. Translating it into agent prompts you actually write is another. These four patterns cover the most impactful practical moves.

### Keep the System Prompt at the Right Altitude

Both failure modes cost you. An over-specified prompt tries to script the agent’s every decision; it reads like a flowchart embedded in natural language, and it breaks the moment reality does not match the script. An under-specified prompt hands the agent a vague goal and assumes it shares context it does not.

The right altitude gives the agent a clear role context, behavioral principles, and output expectations without trying to pre-answer every decision it will face. When you find yourself writing “**if the user asks X, do Y; if the user asks Z, do W**” in your system prompt, that is a signal you have slipped into over-specification. Replace the if-else with a principle: “**Prioritize accuracy over speed. When in doubt, retrieve fresh data rather than relying on prior context.**”

### Write Outcome Prompts, Not Procedure Lists

The same principle applies here as to agentic tools more broadly. Telling an agent what to deliver produces better results than telling it each step to follow. Procedure lists constrain the agent’s ability to adapt when a step does not go as expected, and in multi-step tasks, steps rarely go exactly as expected.

Procedure list (fragile):

1\. Open the CSV file 2\. Find the revenue column 3\. Sum the values by region 4\. Write a paragraph describing the results 5\. Save the output as report.docx

Outcome prompt (resilient):

Analyze the sales CSV in the working directory. Produce a Word document with: total revenue by region, the top-performing region with a brief explanation of why it stands out, and any data quality issues you noticed (missing values, inconsistent formatting). Save as report.docx

The outcome version tells the agent what the finished product looks like. The agent figures out how to get there and can adapt when the CSV has unexpected columns or a region name is formatted inconsistently.

### Use Just-in-Time Context Over Pre-Loaded Context

Pre-loading everything you think the agent might need into the context window is a natural instinct and a reliable way to degrade performance on long tasks. Instead, design your agent to maintain lightweight references and fetch specific information at the moment it is needed.

In practice, this means your system prompt should reference where information lives, not contain the information itself:

\## Data Access Customer data is stored in /data/customers.csv. Product catalog is in /data/products.json. Do not load these files upfront. Load only the specific rows or fields relevant to the current step of the task using the read_file tool with targeted queries.

This keeps the active context lean throughout the task, preserving attention budget for the reasoning that matters at each step rather than filling the window with data that will only be relevant later.

### Dynamic Persona Priming

A single agent architecture can serve very different users if you inject context-specific persona information at runtime rather than hardcoding it. This is useful for agents that serve both technical and non-technical audiences, or agents that adapt tone and depth based on the user’s role.

**Runtime injection example:**

# Injected based on user role at session start # For a non-technical user: role_context = """ The user is a business stakeholder with no technical background. Explain findings in plain language. Avoid jargon. Use analogies where helpful. Never show raw data -- always interpret it first. """ # For a technical user: role_context = """ The user is a senior data engineer. Use precise technical terminology. Include relevant SQL or code snippets where they add clarity. Focus on implementation details over high-level summaries. """ system_prompt = base_system_prompt + "\\n\\n" + role_context

One agent architecture, two very different outputs — without maintaining separate agents or prompt files for each user type.

## Prompting Multi-Agent Systems

Single agents have limits. Complex tasks that require parallel workstreams, specialized domain knowledge in multiple areas, or checks and balances between generation and review are better served by multi-agent systems. The dominant pattern is orchestrator-worker: one agent receives the goal, breaks it into subtasks, delegates each subtask to a specialized worker agent, and synthesizes the results.

Prompting a multi-agent system means prompting each agent individually while designing the handoffs between them. Each agent needs to know exactly what it is responsible for, what it should receive as input, and what it should deliver as output. It does not need to understand the full architecture — only its own role within it.

Orchestrator system prompt:

You are a research orchestration agent. Your job is to coordinate a team of specialized agents to complete research tasks. You have access to three worker agents: - search\_agent: Retrieves information from the web. Send it: a specific search objective and the output format you need. - analysis\_agent: Analyzes data and identifies patterns. Send it: structured data and a specific analytical question. - writer\_agent: Produces polished written outputs. Send it: structured findings and the target document format. Your responsibilities: - Break the user's task into clear subtasks for each agent - Specify exactly what each agent should deliver before you delegate - Validate that each agent's output meets the spec before passing it to the next agent - Synthesize the final output from all agent results Do not attempt to do any of the specialized work yourself.

Worker agent system prompt (search\_agent):

You are a specialist search agent. You receive a specific search objective from an orchestrator and return structured research findings. Input you will receive: - A clear search objective - The output format required (e.g., bullet points, JSON, table) Your responsibilities: - Execute targeted web searches to fulfill the objective - Return only information that directly addresses the objective - Flag any information that is older than 6 months - Do not interpret or editorialize -- return findings only You do not need to understand the larger task. Focus entirely on the search objective you were given.

The critical design principle here is **minimal shared context**. Each worker agent knows only what it needs to do its job. It does not need the full task context, the user’s history, or what the other agents are doing. This keeps each agent’s context lean, reduces the chance of cross-contamination between tasks, and makes the system easier to debug when something goes wrong.

## Common Mistakes and How to Fix Them

Even well-intentioned agent prompts fail for predictable reasons. These are the five that come up most often.

1. **Giving the agent too many tools**: More tools feel like more capability, but they create ambiguity at every decision point. If two tools could plausibly apply to the same situation, the agent will hesitate, choose inconsistently, or use the wrong one. The fix: audit your tool set before every deployment. If you cannot instantly and unambiguously identify which tool applies to a given scenario, prune until you can.
2. **Vague success criteria**: An agent that does not know what “done” looks like will keep going, second-guess its outputs, or stop at the wrong point. Vague endings like “complete the analysis” invite interpretation. Specific ones like “deliver a Word document with these four sections, all populated with data from the provided CSV” do not. Every task specification should define the output format, the expected content, and any conditions that must be met before the agent considers itself finished.
3. **Overloaded context**: Front-loading everything into the context window — all background documents, all prior session history, all reference data — degrades performance on long tasks as the attention budget gets stretched. Use just-in-time retrieval. Load specific data at the moment it is needed, not all at once at the start.
4. **No examples**: Instructions tell the agent what to do. Examples show what success looks like. For any task pattern you will run repeatedly, two or three well-chosen examples are worth more than an extra page of instructions. The model can infer format, tone, decision style, and output structure from examples in ways that natural language descriptions cannot fully capture.
5. **Treating a multi-step agent like a one-shot chat**: A chatbot prompt can be vague because the human corrects in real time. An agent running autonomously across 15 steps has no such correction mechanism until it delivers a final output. Every ambiguity you leave in the prompt becomes a decision the agent makes on its own, and that decision compounds across every step that follows. Invest more time in prompt design upfront. It pays back in fewer failed runs and more reliable outputs.

## Conclusion

Prompt engineering for agentic AI is not a more advanced version of the same skill. It is a different discipline built on a different premise. Chat prompting is about getting a good response. Context engineering is about designing a reliable system — one that makes consistent decisions across many steps, uses tools correctly, manages its own attention budget, and delivers finished work without requiring you to intervene at every turn.

The teams getting the most out of agentic AI right now are the ones who stopped asking “how do I phrase this better?” and started asking “what does this model need to know at every step to behave the way I want?” That shift from phrasing to architecture is where the real leverage lives. Start with the system prompt at the right altitude. Give the agent tools that it can actually distinguish between. Show it examples of the reasoning style you want. Then design the context to stay lean as the task runs. Those four habits will take you further than any single clever prompt ever will.

For further reading, Anthropic’s context engineering post is the most practical deep dive on the underlying principles. The Prompt Engineering Guide’s agents section covers ReAct, Reflexion, and related architectures with additional technical depth. Both are worth keeping open while you build.
