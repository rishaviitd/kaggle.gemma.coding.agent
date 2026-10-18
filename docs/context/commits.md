# Changelog

## codebase — 2026-10-18 (SFT data v4 and trainer fix)

- Audit with the real Gemma 4 tokenizer showed the earlier trainer would have rejected 1667 of 1876 prefix rows and all 240 trajectory rows (generation prompt after a tool result opens a thought channel, and growing renders retokenized differently). `render_row` now renders once, tokenizes once with offsets, and labels by character span; the thought opener is not part of the sequence. Real-template tests added in `scripts/tests/test_parallel_sft.py` (they skip if the tokenizer is not in `models/llm/gemma-4-e4b-it-tokenizer`).
- Prefix rows are now one per model response: commentary and tool call are supervised together (before, the commentary row ended with the turn token and taught the model to stop). `completion` is a list for those rows.
- Tool results are shortened inside JSON fields (middle cut, marker, still valid JSON), cap 3000 chars. Failed and repeated-without-edit steps stay in context but are not supervised. Prefix capped at 24 rows per task; trajectory is not capped.
- New data `data/transient/sft-haiku-solved-v4`: 378 solved traces (the reference107 runs landed after the first build), 72 tasks, prefix 1601 rows (2620 before the cap), trajectory 440 rows. Longest row 10645 tokens, none over 16384, 0 rejected. Uploaded as a new version of `atomstack001/gemma4-e4b-unsloth-sft` and verified byte-identical by download; kernel relaunched. The unused `xformers` import was removed from the trainer.

## codebase — 2026-10-18 (parallel QLoRA)

- Solved-Haiku JSONL now keeps every solved attempt, not one per task: 209 traces, prefix 1876 rows, trajectory 240 rows. Wheels and JSONL are uploaded as `atomstack001/gemma4-e4b-unsloth-sft`. The first dataset version held wheels only because `datasets create` skips directories. Version 2 was uploaded with `-r tar` and the JSONL was downloaded back and checked (prefix 1876 rows, trajectory 240 rows). Kernel v2 was pushed against it. The Kaggle trainer runs prefix and trajectory together for 3 epochs at LoRA r=64, alpha=128, and reads only `/kaggle/input`.

## codebase — 2026-10-18 (SFT JSONL truncation)

- Exporter no longer clips the system prompt or the task text. Tool observations still clip at `--max-chars`. Trajectory assistant messages now carry `train` so compaction carryover and excluded turns are not supervised again.

## codebase — 2026-10-18 (SFT JSONL)

- `scripts/sft_export/export_sft.py` now writes prefix or trajectory chat JSONL. Teacher thinking is dropped by default, `enable_thinking` is recorded for the trainer, and long traces are clipped by message count and field length. Token labels are no longer produced.

## codebase — 2026-10-18 (E4B LoRA smoke)

- Added `server/runpod/sft_smoke.py`: rank-64 LoRA on `unsloth/gemma-4-E4B-it`, one step, first turn of `requests_7427`. The QAT w4a16 checkpoint stays inference-only.

## eval — 2026-10-17 (12B pass@3)

- Finished 3 samples each of itr-1 and inspiration-one on Gemma 4 12B, 4 workers, same 20 val tasks. itr-1 solved 4, 4, 2 (union 6). inspiration-one solved 4, 3, 3 (union 5). Summary: `logs/remote/rishav/pass3-12b/summary.json`.

## eval — 2026-10-17 (pass@3)

- Started 3 repeats each of itr-1 and inspiration-one on the same 20 val tasks, 8 workers. Summary will land in `logs/remote/rishav/pass3-e4b/summary.json`.

## codebase — 2026-10-17 (inspiration-one)

- Copied `inspiration/one/extracted` to `src/rishav/inspiration-one` (28 tool calls, 8 minutes, tighter prompt). `run_eval.py` accepts `BENCHMARK_SUBMISSION`.

## codebase — 2026-10-17

- RunPod inference: vLLM upstream moved to port **8002** (pod image uses 8001), `benchmark_api.py` (API load), `run_eval.py` + `run_benchmark_tasks.sh` (20 val tasks, itr-1 configs, `EVAL_WORKERS` default 8, logs under `logs/remote/rishav/itr-1/e4b-benchmark/`).

## codebase — 2026-10-13

- Added **rishav itr-1_v2**: copy of itr-1 with `evaluation.max_time_minutes: 5.5` in `src/rishav/itr-1_v2/` and `submissions/rishav-itr-1_v2/submission/`.
- Built Kaggle archive `submissions/rishav-itr-1_v2/submission.zip` (~3 KiB, `agent.yaml` at zip root, swegemma size/extension checks passed).

## codebase — 2026-10-13 (itr-1_v3)

- Added **rishav itr-1_v3**: copy of itr-1 with `temperature: 1` in `configs/sampling.yaml` (`src/rishav/itr-1_v3/`, `submissions/rishav-itr-1_v3/submission/`).

## codebase — 2026-10-18 (QAT base, 4-rank trajectory sweep)
- Trainer `notebooks/gemma4_e4b_qlora_parallel.py`: prefix experiment dropped. Four trajectory runs, one per L4: r=8/16/32/64, alpha=2r, 3 epochs, LR 1e-4.
- Base is now `gemma-4-e4b-it-qat-q4_0-unquantized` with bf16 LoRA (no 4-bit), so adapters match the served `-qat-w4a16-ct` weights. `MAX_SEQ_LENGTH` 16384 -> 12288 (longest row 10,645 tokens; 440 rows, 0 rejected, 316k supervised tokens).
- Canonical Gemma 4 chat template (18,569 bytes) shipped in the dataset and forced in training; Kaggle model copies carry the older template that closes the turn between commentary and a tool call.
- Added a preflight forward/backward on the longest row (logs `PREFLIGHT_OK peak_gib`), adapter-only epoch checkpoints, `assert_parallel` generalised to 4 GPUs.
- Dataset `atomstack001/gemma4-e4b-unsloth-sft` new version: adds `chat_template.jinja`, removes `sft/prefix`. Kernel `atomstack001/gemma-4-e4b-qat-trajectory-lora-rank-sweep` v1 pushed (old v4 kernel cancelled). Tests: 30 pass. No eval result yet.

## codebase — 2026-10-18 (SFT data v5: compact tool observations)
- `export_sft.py`: `compact_observations` runs before row building. read_file keeps only the block a later `edit_file` copies as `old_string` (exact-text match, then stripped-line match) plus 3 lines of context, cap 2500 chars; a read no edit uses keeps a 400-char head. run_command stdout 600 chars (70% from the end, so the test summary survives; blocks a later edit copies are also kept), stderr 300. edit_file diff 300. Status, exit_code, occurrences, paths are kept; `is_truncated` becomes true when text is cut; gaps read `…[N lines omitted]…`. 6 new tests.
- Earlier line-by-line version scattered a read into ~10 fragments because boilerplate lines repeat; replaced by whole-block matching.
- `build_solved_dataset.py`: trajectory only, output `data/transient/sft-haiku-solved-v5` (schema `gemma4-sft-v5`). v4 untouched. Not uploaded; no training started.
- Result (QAT tokenizer, 440 rows, 0 rejected): total tokens 2.76M -> 2.30M (-17%), tool-result tokens 942k -> 479k (-49%; read_file 118k, run_command 243k, edit_file 108k), longest row 10,645 -> 8,191. System prompt + tool schema + task is 1.42M (62% of v5). Edits whose old_string is visible in context: 538 of 539 that were visible before compaction (738 edits total).

## codebase — 2026-10-18 (observation policies: causal vs hindsight, from web evidence)
- Researched how public agent SFT data handles tool output: SWE-agent (elide above 100k chars, explicit note), mini-SWE-agent (keep first and last 5000 of outputs over 10k, `elided_chars`), OpenHands (first and last half of 30k, truncation marker), SWE-Master/Harvard fine-tuning text (observations are context only, loss masked; keep what the runtime showed), JetBrains "Complexity Trap" (mask only observations older than the last M=10 turns at inference; ~half the cost, same solve rate). None of them cut inside the normal size range or use later actions to decide what to keep.
- `export_sft.py`: `compact_observations(messages, policy)`. `causal` (new default): cut depends only on the observation (read 2500, command stdout 1500 head 30%/tail 70%, diff 700 chars; head+tail with `…[N characters elided]…`, `is_truncated` true). `hindsight`: previous v5 behaviour (keeps the block a later edit copies). 4 new tests, 40 pass.
- `build_solved_dataset.py [causal|hindsight]` writes `data/transient/sft-haiku-solved-v5-<policy>`; no longer copies into `notebooks/wheels/archive`. Old v5 renamed `v5-hindsight`. Nothing uploaded; no training.
- Measured (QAT tokenizer, 440 rows, 0 rejected): v4 2.76M tokens / tool 942k / max 10,645 / edits grounded 492 of 738; v5-causal 2.65M / 832k / 9,621 / 474; v5-hindsight 2.30M / 479k / 8,191 / 538. Raw (uncapped) grounded 539. 99% of grounded edits copy from one of the last 5 tool results.

## codebase — 2026-10-18 (observation policy `none`: no truncation)
- User decision: do not truncate tool output; keep what the harness returned (it already caps read_file at 150 lines/10,000 chars, commands at 5,000, diff at 5,000).
- `export_sft.py`: policy `none` returns messages untouched. `build_solved_dataset.py none` also disables the old 3,000-char clip and writes `data/transient/sft-haiku-solved-v5-none`. 41 tests pass. Not uploaded; no training.
- Measured (QAT tokenizer, 440 rows, 0 rejected): 2.85M tokens total, 1.03M tool tokens, 316k supervised, longest row 11,841 (p99 10,798), edits with source visible 539 of 738 (all of the possible ones). Needs `MAX_SEQ_LENGTH` >= 12,288 (current value); bf16 memory fit on the L4 still untested.

## codebase — 2026-10-18 (drop QLoRA from the plan)
- Decision: QLoRA is not a fallback for the QAT base (NF4 differs from the served w4a16 scheme). The trainer has no 4-bit path. Renamed `notebooks/gemma4_e4b_qlora_parallel.py` -> `gemma4_e4b_lora_parallel.py`, output dir `e4b-lora-parallel`, tests and `kernel-metadata.json` `code_file` updated. Memory fallbacks if the preflight fails: shorter `MAX_SEQ_LENGTH`, shard the model (FSDP/ZeRO-3), or a larger GPU. Not pushed; no training.

## codebase — 2026-10-18 (Unsloth model split across GPUs)
- Trainer `notebooks/gemma4_e4b_lora_parallel.py`: each run is now split over `GPUS_PER_RUN` (default 2) L4s with Unsloth's own placement, `device_map="unsloth_balanced"` (planner in the shipped unsloth 2026.10.6 / unsloth_zoo; no FSDP, no DDP). Runs go in waves: wave 1 = r8 on GPUs 0,1 and r16 on GPUs 2,3; wave 2 = r32 and r64. `GPUS_PER_RUN = 1` restores one rank per card; 4 runs one rank at a time.
- Guards: worker checks it sees exactly its GPUs, prints `PLACEMENT ... devices_holding_weights` and fails if the weights are not on every GPU of the group; preflight on the longest row reports peak GiB per GPU; status files are `r{rank}.json`; `assert_parallel` checks each wave (ranks, GPU groups, L4, distinct processes, overlap). 14 trainer tests pass (41 incl. exporter).
- Not run on a GPU. Not pushed to Kaggle (copied into `data/transient/kaggle-sft-kernel/` only); the Kaggle dataset still holds v4 data. Risks: split models are only lightly documented by Unsloth for Gemma 4; wall time is about two single-run times because the cards in one run work one after another.

## codebase — 2026-10-18 (simplify: one rank-64 run, model split over 4 GPUs)
- User decision: prove one working run first. Trainer now trains only r=64, alpha=128, trajectory, 3 epochs, with the model split by Unsloth (`unsloth_balanced`) over all 4 GPUs (`GPUS_PER_RUN = 4`, `RANKS = (64,)`). Ranks 8/16/32 and parallel runs are deferred; the wave/GPU-group code stays so they can be switched on via the two constants. Tests: 14 trainer tests pass. Copied to `data/transient/kaggle-sft-kernel/`, not pushed.

## codebase — 2026-10-18 (Kaggle dataset now v5-none)
- `atomstack001/gemma4-e4b-unsloth-sft` new version: `sft/trajectory/{train.jsonl,manifest.json}` replaced with `data/transient/sft-haiku-solved-v5-none` (440 rows, untruncated observations, sha256 e3253db7…b9c verified identical after download). Still holds wheels and `chat_template.jinja`. No kernel pushed; no training started.

## codebase — 2026-10-18 (thought rationalization script and prompts)
- Added `scripts/sft_export/rationalize.py` (`sample` / `run` / `apply`) and `prompts/rationalize_thought.py` (versions v1–v3). Gemma E4B QAT on vLLM (RunPod A100, pod `bij9hs456lhmyd`, SSH tunnel to `localhost:8002`) writes a 1–3 sentence thought (100–520 chars, target from measured Haiku thought lengths: median 227, p90 556) before each tool-call step. The generator sees the history and the step's action, never the step's own tool output. Thoughts get attached as `reasoning` on the first assistant message of the group, to be loss-masked in training. 8 workers by default; validator rejects markdown, length, leak/narration and banned stock phrases, with retries.
- Prompt iterations on random samples of 4 (seeds 1, 2, 3): v1 generic/stock phrasing and paraphrased the commentary; v2 fixed stock phrases but one thought was about a different file than the step; v3 adds "be about the exact file/command in the step" and reads grounded. Remaining issue: occasional invented facts about earlier steps. Nothing pushed, no dataset rebuilt yet.

## codebase — 2026-10-18 (rationalize prompts v4/v5, tool output excerpts)
- First full run on v3 with tool outputs hidden (260 steps audited): 14% rejects (banned phrases), 18% of thoughts open "Okay, so", and the model invented outcomes ("the tests ran and the fix worked") because it could not see results. Run stopped; partial output kept as `data/transient/rationalize/thoughts-v3.partial.jsonl`, not used.
- v4 (hide outputs + "you do not know any result" rule): fixed openings and stock phrases, but verification and `submit_patch` steps still invented test outcomes. v5: same style rules, generator sees a 300-char excerpt of earlier results (`--result-chars 300`, default now) and may only state what the excerpts show. 8-sample test: 8/8 accepted, outcome claims grounded. Default prompt is v5. Full run on 2,483 tool-call steps started with v5.

## codebase — 2026-10-18 (thoughts in the training data, loss masked)
- Generated a Gemma-written thought (prompt v5, 300-char result excerpts, 8 workers; median 229 chars) for all 2,483 tool-call steps of the 440 trajectory rows; 244 first-pass rejects (banned phrases) were regenerated until all had one. Built `data/transient/sft-haiku-solved-v6-thoughts` (`rationalize.py apply`): `reasoning` on the first assistant message of each response group; rows with a mid-run user turn (2 rows) are split into parts because the Gemma template only shows thoughts after the last user message. 442 rows.
- Trainer `notebooks/gemma4_e4b_lora_parallel.py` `render_row`: thought text is context only. Loss starts at `<channel|>` and covers commentary, tool call and stop token; on the first turn (where the generation prompt has no opener) `<|channel>thought\n` is also supervised. Two new real-template tests; 45 tests pass (2 skipped).
- Audit with the real template: 442/442 rows render, max 12,143 tokens (cap 12,288), 2,240 spans start at `<channel|>`, 0 thought texts inside supervised labels, 319k supervised tokens (v5-none: 314k).
- Kaggle dataset `atomstack001/gemma4-e4b-unsloth-sft` new version holds the v6 data (sha256 9bd8bd16…079a verified identical after download). Kernel not pushed, no training. RunPod pod `bij9hs456lhmyd` (A100, $1.79/hr) stopped after generation; volume `gemma-qat-weights` kept.
- Open: whether the harness re-sends earlier thoughts in history is still unverified; v6 shows all thoughts in context after the last user turn.

## codebase — 2026-10-19 (trainer audit, kernel v2 pushed)
- Audit of `notebooks/gemma4_e4b_lora_parallel.py` before the Kaggle run. Confirmed: one run r=64/alpha=128 split over GPUs 0-3 with `device_map="unsloth_balanced"`; thought text masked (loss from `<channel|>`); canonical template forced from the dataset. The dataset template (18,569 bytes, sha256 0a2c8073…) is byte-identical to `google/gemma-4-E4B-it-qat-w4a16-ct`'s `chat_template.jinja` on Hugging Face, so training and serving use the same format.
- Fixes: `check_manifest` refuses any dataset that is not `gemma4-sft-v6` with thoughts or whose row count differs from the manifest; a row that fails to render is now fatal (was skipped); `thought_leaks` check on every row (fatal if any thought text carries loss); warmup as an integer step count (17 of 333 steps) instead of `warmup_steps=0.05`; guard that the HF Trainer treats the split model as model parallel with `n_gpu == 1` (no DataParallel copy); adapter saved with `safe_serialization=True` and `adapter_model.safetensors` asserted. New log lines: `DATA`, `TRAINER`.
- Tests: dataset test now points at v6 and checks thought leaks; 3 manifest tests; 48 tests pass (2 skipped). Dry run of the data path on the staged Kaggle files: 442 rows, 442 with thoughts, 0 leaks, 319,363 supervised tokens, longest 12,143 (cap 12,288).
- Pushed kernel `atomstack001/gemma-4-e4b-qat-trajectory-lora-rank-sweep` version 2 (L4, internet off, timeout 9 h); status RUNNING.
- Kaggle v2 log (user-provided, first ~4 min): `PLACEMENT devices_holding_weights=['0','1','2','3']`; `DATA rows=442 rows_with_thoughts=442 thought_leaks=0 supervised_tokens=319363 longest=12143`; `PREFLIGHT_OK peak_gib_per_gpu=[11.3, 6.3, 6.5, 2.9]` (22 GB cards; GPU 0 also holds vision/audio towers, GPU 3 the head); `TRAINER model_parallel=True n_gpu=1 steps=333 warmup=17`; Unsloth: Data Parallel GPUs = 1, total batch 4, 139.5M trainable params (1.73%). Transformers 5.16.1, Torch 2.11. Note for next run: `lora_dropout=0` enables Unsloth's fast LoRA path (0.05 costs speed).

## codebase — 2026-10-19 (repo layout: docs/, viewer/, training/)
- Moved `reports/`, `assets/`, `resources/` into `docs/`; `index.html`, `trace-formats.js`, `trace-index.json` and `presentation/` (loaded by `index.html`) into `viewer/`; the trainer `gemma4_e4b_lora_parallel.py`, `gemma4_unsloth_sft_haiku.py`, `wheels/` and `test_parallel_sft.py` into `training/` (+ `training/tests/`). `notebooks/` keeps only the competition eval kernel (`starter.ipynb`, `kernel-metadata.json`). `server/` unchanged.
- Path fixes: Pages workflow copies from `viewer/` (published site layout unchanged); `trace_viewer.py` serves `viewer/` first, then the repo root for `logs/` and `data/`; `build_trace_index.py` and `--write-index` write `viewer/trace-index.json`; `run_haiku87.py` reads `docs/reports/`; `build_solved_dataset.py` copies to `training/wheels/archive/sft`; README image and layout paths; `/training/wheels/` gitignored. Trainer tests 19 OK (2 skipped), script tests 7 OK, trace-format test OK, local viewer returns 200 for `/`, slides, `trace-index.json`, `data/tasks.jsonl`. The staged Kaggle kernel (`data/transient/kaggle-sft-kernel/`) and the running v2 job are unaffected.
- Moved `eval_ui/` to `viewer/eval_ui/` (self-contained: only relative paths inside, nothing outside references it). Pages workflow ignores `viewer/eval_ui/**` so report edits do not redeploy the site. Its tests need `pytest`/`plotly`, which are not in the main `.venv` (not run).

## codebase — 2026-10-19 (scripts/ grouped by stage)
- `scripts/` regrouped: `tasks/` (plan_observed_task_splits, collect_task_dependencies, repair_task_wheels, build_split_wheels.sh), `runs/` (task_pipeline, run_pass3, run_haiku87, run_parallel_iterations, run_reference_checks, evaluate, run_kaggle_notebook, run_fastapi_pair/haiku20/luna20.sh), `traces/` (vllm_trace_proxy, build_agent_trace, build_trace_index, trace_viewer, langfuse_bridge, stitch_langfuse_trace). `sft_export/` and `tests/` unchanged; `shell/` removed.
- Fixes: repo root is `parents[2]` in every moved script; `task_pipeline.py` and `run_kaggle_notebook.py` add `scripts/traces` to `sys.path` for `langfuse_bridge`/`vllm_trace_proxy`; subprocess paths (`run_pass3` → `runs/task_pipeline.py`, `run_haiku87` → `runs/run_pass3.py`, `run_reference_checks` → `runs/evaluate.py` and `docs/reports/`, `langfuse_bridge` → `traces/stitch_langfuse_trace.py`, `run_parallel_iterations`); shell scripts, `server/runpod/run_eval.py` and `benchmark_api.py`, Pages workflow, README, `context/split-program.md`, tests.
- Checks: script tests 7 OK, trainer tests 19 OK, trace-format test OK, `bash -n` on all shell scripts, `--help` works on 13 of 16 moved scripts (`repair_task_wheels` needs `pip`, `stitch_langfuse_trace` needs `langfuse` in `.venv`; `run_reference_checks` has no `--help`), local viewer serves 200.
- Side effect during checks (cleaned): `run_reference_checks.py` and `server/runpod/run_eval.py` have no `--help` and started real runs; stopped within a minute, no task ran. Removed the stray root `reports/` CSV and the appended usage error in `logs/remote/rishav/itr-1/e4b-benchmark/worker-1.log`.

## codebase — 2026-10-19 (context/ moved to docs/context/)
- `context/` → `docs/context/` (commits.md, data.md, harness.md, overview.md, split-program.md, tool_description.md, simplified_code_search). Updated README links and trees, `viewer/presentation/slides/what-not-to-do.js`, `src/base/analysis/edit_file_tool_analysis.md`. No code reads these files.
