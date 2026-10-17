# Export coding-agent traces for Gemma 4 SFT

This folder contains the exporter, its tests, and this usage guide.
`export_sft.py` accepts one task folder, an ancestor containing task folders, or an
`anthropic_exchanges.json` file. It exports **one conversation per task segment**.
It currently supports raw Anthropic exchanges, including the supplied Haiku run.
Recognized ADK summarizer calls split compacted tasks into segments; carried-over
assistant messages remain context-only. Other providers, unrecognized replaced
histories, and multimodal content are rejected.
It reads data only; it never executes captured tool commands.

## Run

From the repository root, using the existing `.venv`:

```bash
.venv/bin/python scripts/sft_export/export_sft.py \
  logs/remote/rishav/inspiration-one/haiku-5p5-inspiration-one-20-20261017/val/fastapi_5624 \
  --output data/transient/sft-fastapi-5624 \
  --retain-reasoning --enable-thinking
```

The tokenizer download requires internet; model weights are never loaded. The
default tokenizer is `google/gemma-4-E4B-it`, pinned to revision
`ee0ef6023621cff504d758262d4e04895a5af4a2`. For offline use, first download just
`config.json`, `tokenizer.json`, `tokenizer_config.json`, and `chat_template.jinja`
from that revision, then add:

```bash
  --tokenizer /path/to/downloaded/tokenizer --local-files-only
```

Point the positional argument at a parent folder to export multiple task folders.
Every input must validate; on any failure the output directory is not published.
An existing output directory is never overwritten. Separate task occurrences are
separate examples, even when their task IDs are the same; the manifest identifies
their source paths. Use a different output directory for subsequent exports.

## Output and training

| File | Purpose |
| --- | --- |
| `train.jsonl` | Direct training input: `input_ids`, `attention_mask`, and `labels` |
| `conversations.jsonl` | Reviewable normalized messages, tools, task ID, and per-message supervision metadata |
| `text/*.txt` | Exact text produced by the official chat template, one file per example |
| `manifest.json` | Source paths/hashes, tokenizer/template hashes, token counts, exclusions, and references |

The `gemma4-sft-v2` manifest records `retain_reasoning` and `enable_thinking`
separately, replacing the ambiguous v1 `thinking` field. Existing exports are
unchanged; re-export into a new directory to get the v2 metadata.

The captured request is authoritative for the **enabled tool set**. The converter
keeps its complete definitions, including enabled but unused tools. It includes
the initial system and user prompts once and validates subsequent requests against
the already reconstructed conversation. Later user instructions are kept when
they actually occur. This follows the conversation-plus-tools dataset structure
described in [TRL's tool-calling SFT documentation](https://huggingface.co/docs/trl/sft_trainer#tool-calling-with-sft).

`labels[i]` equals `input_ids[i]` only for assistant text, retained thinking, tool
calls, their first tool-response stop marker, and assistant end-turn markers.
All system/user prompts, declarations, tool-result bodies and tool-result closing
markers have label `-100`. The model performs the causal shift during training;
do not shift these labels yourself. These conventions follow
[TRL's loss and label-masking documentation](https://huggingface.co/docs/trl/sft_trainer#label-shifting-and-masking).

Consume **the tokenized rows and their labels directly**. Do not use a collator
that regenerates labels from `input_ids`: that would train on tool outputs.
For example, with a compatible loaded model (and any LoRA configuration already
applied), the following uses Hugging Face Trainer without reformatting the text:

```python
from datasets import load_dataset
from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments

dataset = load_dataset(
    "json", data_files="data/transient/sft-fastapi-5624/train.jsonl", split="train"
)
trainer = Trainer(
    model=model,
    args=TrainingArguments(
        output_dir="checkpoints/gemma4-coding",
        per_device_train_batch_size=1,
        num_train_epochs=1,
        learning_rate=2e-5,
        gradient_checkpointing=True,
        bf16=True,
    ),
    train_dataset=dataset,
    data_collator=DataCollatorForSeq2Seq(
        tokenizer=tokenizer, padding=True, label_pad_token_id=-100
    ),
)
trainer.train()
```

This is a consumption example, not a supplied GPU training job. `datasets`,
`accelerate`, and the appropriate training model/weights must be installed in the
training environment. Load the same tokenizer revision used for export. If using
TRL instead, use its pretokenized-dataset path and a label-preserving collator;
do not ask it to reapply the chat template or replace the exported labels.

## Why this template and these masks

- Serialization uses [Google's exact pinned E4B template](https://huggingface.co/google/gemma-4-E4B-it/blob/ee0ef6023621cff504d758262d4e04895a5af4a2/chat_template.jinja).
  Tool arguments are dictionaries. Because this template emits calls before an
  assistant message's text, the converter separates pre-call text from the call
  into consecutive assistant messages. This preserves causal order and prevents
  training that text on future observations.
- Parallel results are ordered by call ID into the original call order before
  rendering. Gemma's native text omits IDs, so this is necessary for two calls to
  the same tool. Failed results retain `is_error: true` in a JSON wrapper around
  their original text; this wrapper is part of the masked observation.
- The exporter annotates only known generated output sites in that template.
  It removes its temporary markers and requires the result to match the
  unmodified upstream rendering exactly. An unfamiliar template hash is rejected
  rather than silently producing incorrect labels. This is our implementation
  strategy; it is not a Google-provided training template.
- The first `<|tool_response>` opener after a group of calls is supervised as the
  model's stop signal; the returned observation is masked. See
  [Google's tool lifecycle and stop-token specification](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4#function-calling).
- The final text is tokenized with `add_special_tokens=False`, avoiding duplicate
  BOS/control tokens as required by
  [Transformers' chat-template guide](https://huggingface.co/docs/transformers/main/en/chat_templating).
- We supply explicit labels because the published inference template does not
  provide training generation spans. Simply enabling `assistant_only_loss=True`
  is not enough: [TRL requires a template with generation annotations](https://huggingface.co/docs/trl/sft_trainer#train-on-assistant-messages-only).
  In particular, Gemma tool observations can appear inside the ongoing model turn;
  masking solely by the outer role would supervise environment-generated output.

## Thinking, exclusions, and sequence length

Teacher reasoning and the target model's thinking-mode prompt are independent:

| Options | Teacher reasoning in targets | Gemma `<\|think\|>` system token |
| --- | --- | --- |
| No options (both default off) | Dropped | Absent |
| `--retain-reasoning` | Retained when available | Absent |
| `--enable-thinking` | Dropped | Present |
| `--retain-reasoning --enable-thinking` | Retained when available | Present |

`--retain-reasoning` controls conversion of the teacher's raw thinking blocks to
the reasoning field. `--enable-thinking` controls `enable_thinking` in the official
chat template. Each has an explicit `--no-...` counterpart. Google's
[thinking-mode guide](https://ai.google.dev/gemma/docs/capabilities/thinking)
documents that prompt switch separately from model-generated reasoning. Enabling
the switch without retaining teacher reasoning trains on the captured actions
under that system instruction; it does not synthesize reasoning targets.

Choose the prompt setting to match the intended inference configuration. The
default off preserves previous exporter behavior; it is not a recommendation for
every server. For an E4B server using thinking on, pass `--enable-thinking`, then
choose whether to retain teacher reasoning separately. The competition harness's
31B configuration is a different target: this exporter pins the E4B template and
does not automatically select 31B's template or its empty-thought conventions.

`--thinking` remains a deprecated shorthand for
`--retain-reasoning --enable-thinking`. It emits a deprecation message and cannot
be combined with either of the new explicit options, preventing silent overrides.
The Python helpers use `retain_reasoning` for `convert_task`/`convert_exchanges`
and `enable_thinking` for `encode_example`.

Provider signatures are always discarded; redacted thinking cannot be retained.
Google's
[reasoning guidance](https://ai.google.dev/gemma/docs/core/prompt-formatting-gemma4#managing-thought-context-between-turns)
explains preserving thoughts inside a tool-use turn. Use the same serialized
template when serving the trained adapter.
If a later genuine user message would cause this template to strip earlier
retained reasoning, export rejects the trajectory and asks you to split it,
regardless of the thinking-mode prompt setting. It does not silently discard
those targets.

`--exclude-turn N` masks every assistant segment generated by the Nth model call,
including its thinking, while keeping that action and its observation in context.
It does not delete history or repair actions. The option is repeatable and applies
to **every task in the export**, so task-specific exclusions should be exported
separately. Per-message `_turn` and `_train` fields are review metadata and never
appear in rendered text.

For `fastapi_5624`, the example command retains every model call, including failed
commands and their recovery context. No filtering is inferred from
`resolved=true`; task-level outcome filtering should happen before export if a
dataset should contain only grader-resolved trajectories.

The default length limit is 32,768 tokens, matching this repo's serving setup.
Oversized trajectories fail with their actual length; they are never truncated
through a tool call or observation. `--max-length` may change that bound when the
training and serving setup support it. Keep all occurrences of an issue together
when splitting training and evaluation; a task trained from `val/` is no longer
held out.

## Verify

```bash
GEMMA_SFT_TOKENIZER=/path/to/downloaded/tokenizer \
  .venv/bin/python -m unittest discover -s scripts/sft_export/tests -v
```

Without `GEMMA_SFT_TOKENIZER`, normalization tests run and real-tokenizer tests are
explicitly skipped. With it, tests cover exact upstream rendering, loss masks,
commentary order, all four reasoning/mode combinations, legacy CLI compatibility,
exclusions, parallel tool calls, invalid
histories, length rejection, and the CLI output contract.
