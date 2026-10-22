# Export coding-agent traces for Gemma 4 SFT

`export_sft.py` reads a task folder, a parent of task folders, or an
`anthropic_exchanges.json`. It writes chat JSONL. It does not tokenize, mask, or
execute captured commands.

`--mode trajectory` writes one row per compaction segment: `messages` is the
segment. `--mode prefix` writes one row per supervised assistant message:
`messages` is the history before that message and `completion` is the message.
Teacher thinking is dropped unless `--retain-reasoning` is set. `enable_thinking`
defaults to true and is stored on each row so the trainer can render Gemma's
thinking-mode prompt without copying the teacher's thoughts.

Truncation keeps the system prompt and the first user turn in full, then the
newest messages that fit `--max-messages` (default 18). A tool call stays with
its results. Only tool observations are clipped, to `--max-chars` (default 4000).
Trajectory assistant messages carry `train`: false for context-only turns
(compaction carryover or `--exclude-turn`), true for supervised turns. In prefix
rows the only supervised message is `completion`.

```bash
.venv/bin/python scripts/sft_export/export_sft.py \
  logs/remote/rishav/inspiration-one/haiku-5p5-inspiration-one-20-20261017/val/fastapi_5624 \
  --mode prefix \
  --output data/transient/sft-fastapi-5624-prefix
```

Output is `train.jsonl` and `manifest.json` (`gemma4-sft-v3`). An existing output
directory is not overwritten. Any invalid task aborts the batch and publishes
nothing. Pass `--enable-thinking` through to the chat template at train time.

## Conversations-only export

Instead of `--mode`, to prepare normalized JSON for a later Unsloth preprocessing step, without
loading a tokenizer or generating token IDs, use:

```bash
.venv/bin/python scripts/sft_export/export_sft.py INPUT_ROOT [MORE_ROOTS ...] \
  --output "data/transient/haiku datasets v1" \
  --conversations-only --minimal --resolved-only \
  --retain-reasoning --enable-thinking
```

This mode creates `train.jsonl` and, with `--skip-invalid`, `rejected.jsonl`.
With `--minimal`, each training row contains only standard `messages` and
`tools`. Without it, rows also contain source path and SHA-256 provenance, the
pinned Gemma template identity, and the template arguments Unsloth should apply
later; `--source-commit` can add commit provenance. Neither mode renders the
template, tokenizes, or creates labels.
Per-assistant-message `_train` metadata is retained so later preprocessing can
distinguish generated targets from assistant history carried across compaction.
Tool messages are observations and must remain loss-masked when labels are made.
Unsloth documents applying a chat template before response-only training in its
[LoRA guide](https://docs.unsloth.ai/basics/lora-parameters-encyclopedia#training-on-completions-only-masking-out-inputs).
`--minimal` limits each training row to `messages` and `tools`.
`--resolved-only` reads the adjacent `results/<task_id>.json` and includes an
attempt only when `resolved` is exactly `true`; missing or malformed result
metadata fails the export rather than guessing.
`--resolved-task-only` is the task-level variant: it keeps every attempt (including
failed ones) of each `task_id` that resolved in at least one attempt.


## Verify

```bash
.venv/bin/python -m unittest discover -s scripts/sft_export/tests -v
```
