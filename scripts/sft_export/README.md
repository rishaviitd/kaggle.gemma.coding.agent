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

## Verify

```bash
.venv/bin/python -m unittest discover -s scripts/sft_export/tests -v
```
