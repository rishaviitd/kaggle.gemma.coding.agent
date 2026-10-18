"""Prompts that make Gemma write the thought it would have had before a step.

The thought is only context in training (loss is masked), so it needs to read like
a short, natural engineer's inner monologue. Iterate here: bump the key in VERSIONS
and pass --prompt-version on the CLI. Examples below are hand-written and generic.
"""

BASE_RULES = """You write the private thinking of a software engineer who is fixing a bug in a repository, one step at a time.

You will see the work so far (the task, earlier commands and their outputs) and then the NEXT STEP the engineer actually took. Write the thought the engineer had in their head just BEFORE taking that step.

Rules:
- First person, plain prose, 1 to 3 sentences, about 25 to 75 words (roughly 150 to 450 characters).
- Explain what they know so far, what they want to find out or change, and why this step is the sensible move.
- Only use facts visible in the work so far. Never mention anything that would only be known after the step runs (its output, whether it works).
- Do not describe the step as something you were told to do. Do not say "the next step", "the action", "the tool call" or name the tool as a function. Plain talk like "let me look at ..." or "I'll run the tests for ..." is fine.
- No markdown, no bullet points, no code fences, no quotes around the thought. Short inline names of files or functions are fine.
- Natural tone: it is thinking, not an explanation to a reader. Slightly informal is good. No headings like "Thought:".
- Output only the thought."""

EXAMPLES = """Examples of the style (different tasks):

Work so far: task says login fails when the email has capital letters. Engineer ran grep and found a lookup in auth/users.py.
Next step: read_file auth/users.py
Thought: The grep points at get_user_by_email in auth/users.py, and that's the only place the email gets compared. I want to see whether it lowercases anything before the lookup.

Work so far: engineer edited parser.py to skip empty lines.
Next step: run_command pytest tests/test_parser.py -x
Thought: The edit is in, so I should run the parser tests now to make sure empty-line handling works and I haven't broken the other cases."""

EXAMPLES_V4 = """Examples of the style (different tasks, different openings):

Work so far: task says login fails when the email has capital letters. Engineer ran grep and found a lookup in auth/users.py.
Next step: read_file auth/users.py
Thought: That grep points at get_user_by_email in auth/users.py, the only place the email gets compared. Worth checking whether it lowercases anything before the lookup.

Work so far: engineer edited parser.py to skip empty lines.
Next step: run_command pytest tests/test_parser.py -x
Thought: Edit's in, so the parser tests are the obvious check. Empty-line handling is what I touched and the other cases share the same loop.

Work so far: engineer has only read the failing test, no source yet.
Next step: run_command grep -rn "retry" src/client.py
Thought: The test expects a retry after a timeout, but I haven't seen where retries live. client.py is the likeliest home, so a quick grep for "retry" there should tell me."""

VERSIONS = {
    "v1": {"system": BASE_RULES + "\n\n" + EXAMPLES},
    # v2: stricter against narration of the action; edit here after reviewing samples.
    "v5": {
        "system": BASE_RULES
        + "\n- The thought must be about the exact file, command or edit in the NEXT STEP. Name that file or target (short form)."
        + "\n- Earlier results are shown only as short excerpts (marked [cut] when trimmed). Rely only on what you can see. If an excerpt does not show a result, do not invent one; talk about the plan or suspicion instead. If the excerpts show tests passing or a diff applied, you may say so briefly."
        + "\n- Think about THIS step only. Do not recap the task or earlier steps; one clause of context at most."
        + "\n- If the engineer's own comment is shown with the step, do not paraphrase it. Give the reasoning behind it."
        + "\n- Banned phrases: 'I need to', 'logical', 'seems like', 'to confirm that', 'in order to', 'a good way', 'the right way', 'Okay, so'."
        + "\n- Never restate the command or its arguments word for word; say the intent instead."
        + "\n- Each thought must open differently from the usual 'Okay'/'I see'. Start with the suspicion, the file, the goal, or a short remark.\n\n"
        + EXAMPLES_V4
    },
    "v4": {
        "system": BASE_RULES
        + "\n- The thought must be about the exact file, command or edit in the NEXT STEP. Name that file or target (short form)."
        + "\n- You do NOT know the outcome of any earlier command or test. Tool results are hidden from you. Unless the engineer's own comment states a result, never say what a command showed, found, printed, or that tests passed or failed. Talk about the plan, the suspicion, and what you hope to learn instead."
        + "\n- Think about THIS step only. Do not recap the task or earlier steps; one clause of context at most."
        + "\n- If the engineer's own comment is shown with the step, do not paraphrase it. Give the reasoning behind it."
        + "\n- Banned phrases: 'I need to', 'logical', 'seems like', 'to confirm that', 'in order to', 'a good way', 'the right way', 'Okay, so'."
        + "\n- Never restate the command or its arguments word for word; say the intent instead."
        + "\n- Each thought must open differently from the usual 'Okay'/'I see'. Start with the suspicion, the file, the goal, or a short remark.\n\n"
        + EXAMPLES_V4
    },
    "v3": {
        "system": BASE_RULES
        + "\n- The thought must be about the exact file, command or edit in the NEXT STEP. Name that file or target (short form) so the thought clearly belongs to this step. Never talk about a different file than the step touches."
        + "\n- Think about THIS step only. Do not recap the whole task; one clause of context at most."
        + "\n- If the engineer's own comment is shown with the step, do not paraphrase it. Give the reasoning behind it (what they suspect, what they want to rule out)."
        + "\n- Banned phrases: 'I need to', 'logical', 'seems like', 'to confirm that', 'in order to', 'a good way'."
        + "\n- Never restate the command or its arguments word for word; say the intent instead."
        + "\n- Vary the opening; start with the observation or suspicion (e.g. 'That grep only hit one file, so...', 'Hmm, ...', 'Okay, so ...').\n\n"
        + EXAMPLES
    },
    "v2": {
        "system": BASE_RULES
        + "\n- Think about THIS step only. Do not recap the whole task; one clause of context at most."
        + "\n- If the engineer's own comment is shown with the step, do not paraphrase it. Give the reasoning behind it (what they suspect, what they want to rule out)."
        + "\n- Banned phrases: 'I need to', 'logical', 'seems like the next', 'to confirm that', 'in order to'."
        + "\n- Never restate the command or its arguments word for word; say the intent instead."
        + "\n- Vary the opening; start with the observation or suspicion (e.g. 'That grep only hit one file, so...', 'Hmm, ...', 'Okay, so ...').\n\n"
        + EXAMPLES
    },
}

USER_TEMPLATE = """=== WORK SO FAR ===
{history}

=== NEXT STEP THE ENGINEER TOOK ===
{step}

Write the engineer's thought just before this step."""
