# Gemma 4 SFT exporter implementation plan

**Goal:** Convert a task folder (or a tree of task folders) containing raw Anthropic exchanges into one deduplicated, correctly masked Gemma 4 training example per task.

**Spec:** The conversation's agreed format: system and user prompts once, only enabled tools (the captured request is authoritative), chronological assistant/tool messages, Google Gemma 4 formatting, and explicit source citations.

**Architecture:** Reconstruct and validate raw request history, normalize tools and content blocks, then render using the pinned upstream tokenizer/template. Add generation annotations without changing rendered text, and use character offsets to mask only generated spans. Export structured conversations, readable text, tokenized JSONL, and an audit manifest. Default to dropping teacher thinking; allow opt-in thinking and excluding individual model-call targets. Never execute captured commands.

**Tech stack:** Python, existing Transformers/tokenizers/Jinja dependencies, unittest. Work in the current checkout using new files; preserve existing user changes.

## Tasks

- [x] Write failing reconstruction tests: preserve prompts/tools once, match call IDs, split commentary before actions, reject changed context, unsupported blocks, missing results and incomplete responses.
- [x] Implement `scripts/sft_export/export_sft.py` normalization and strict validation for `anthropic_exchanges.json`.
- [x] Write failing formatting tests using the actual official tokenizer: exact upstream text equivalence, tool-result/prompt masking, tool stop tokens, optional reasoning, excluded turns, and no truncation.
- [x] Implement annotated rendering with fail-closed template compatibility, token-level masks, and a recursive CLI with atomic outputs and provenance.
- [x] Document CLI commands, direct training consumption of labels, source links and trace-quality caveats. Run the repository tests and export the supplied FastAPI task.

## Verification and review

`GEMMA_SFT_TOKENIZER=/private/tmp/gemma4-sft-assets .venv/bin/python -m unittest discover -s scripts/tests -v`: 14 tests passed, including real-tokenizer integration and CLI tests. Fresh independent review identified parallel-result association, tool-error metadata, and silently stripped historical reasoning; each was reproduced in a failing regression test and fixed. No review findings deferred.

The final supplied-task export retains reasoning, enables thinking, and masks no
model calls (7,532 tokens, 1,015 supervised tokens). Generated artifacts are under
`data/transient/`; the official tokenizer was downloaded without model weights to
`/private/tmp/gemma4-sft-assets`. No training job was run.

## Review focus

Parallel tool results and duplicate IDs; assistant commentary before observations; mid-run context/tool changes; partial/truncated responses; token boundaries that overlap masked and generated spans. Test these contracts explicitly. Review the completed diff separately.

## Follow-up: separate reasoning retention and prompt mode

Follow-up requested by the user: separate retaining teacher reasoning from the
target thinking-mode system token. Conversion uses `retain_reasoning`; encoding
uses `enable_thinking`. Add independent positive/negative CLI flags, preserve
`--thinking` as deprecated shorthand, and record both settings in manifest v2.
Verify all four combinations, raw reasoning preservation with mode off, prompt
and observation masks, legacy compatibility/conflicts, and the FastAPI export.
Preserve the separately added ADK compaction segmentation behavior.

Verification: 18 tests pass with the official tokenizer, including all four mode
combinations and legacy CLI behavior. Independent follow-up review found no
substantive issues. Real FastAPI exports verify the two newly supported settings:
thinking-mode prompt on with teacher reasoning dropped, and teacher reasoning
retained with the thinking-mode prompt off.

## Sources

- https://huggingface.co/google/gemma-4-E4B-it/blob/main/chat_template.jinja
- https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4
- https://huggingface.co/docs/transformers/main/en/chat_templating
- https://huggingface.co/docs/trl/sft_trainer
