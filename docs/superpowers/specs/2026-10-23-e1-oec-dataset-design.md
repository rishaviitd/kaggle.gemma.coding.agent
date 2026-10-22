# E1: On-policy Expert Corrections (OEC) dataset for Gemma 4 E4B

**Status:** design, awaiting review. **Date:** 2026-10-23.

## Goal

Build SFT rows in which E4B's own early steps are context (loss-masked) and Claude Haiku's recovery is the supervised target, then test whether adding them beats the current clean-Haiku data. Method follows OEC (arXiv 2512.14895): student runs, teacher takes over at a random turn in the same working tree, only test-verified runs are kept.

## Constraints (verified against the live competition pages, 2026-10-23)

- Scored base: `gemma-4-e4b-it-qat-w4a16-ct`, plus optional `.safetensors` LoRA. Training uses the unquantized sibling `gemma-4-e4b-it-qat-q4_0-unquantized` in bf16 (existing trainer), served on w4a16-ct. E2B/E4B are eligible for the $10k small-model bonus. `docs/context/*.md` still say 31B-only and are stale.
- L4 sessions must have internet disabled. Whether an L4 session with internet is available to this team is **unconfirmed (owner: user)**. The design keeps student rollout and teacher continuation separable (see Handoff) so it can run as two Kaggle sessions if needed.
- Kaggle has no Docker; the repo's Kaggle eval uses the `subprocess` sandbox.
- Data scope: all 107 tasks with a passing reference patch. Comparison eval: val20 at pass@3. val20 is inside the training tasks, so results measure fit, not generalization; arm-vs-arm comparisons remain fair.

## Existing code this builds on

| Area | Where |
|---|---|
| Agent loop (Google ADK), one container + one session per run | `.venv/.../swegemma/harness/agent_runner.py::run_agent_sandbox` |
| Run entry, model registry, Haiku/vLLM provider setup | `scripts/runs/task_pipeline.py` (`build_model_registry`, `prepare_agent`, `haiku_request_rewriter`) |
| Parallel attempts, pass@3 | `scripts/runs/run_pass3.py`, `run_haiku87.py` |
| Trace proxy recording raw exchanges | `scripts/traces/vllm_trace_proxy.py` |
| SFT export (Anthropic traces, ADK compaction split, `mask_unsound_steps`, rows) | `scripts/sft_export/export_sft.py` |
| Thought rationalization (context-only reasoning, schema v6) | `scripts/sft_export/rationalize.py` |
| Trainer with per-message `train` flags | `training/gemma4_e4b_lora_parallel.py` (`render_row`, `check_manifest`) |

## Design

### 1. Handoff component

A switching LLM wrapper registered in `build_model_registry`:

- Routes the first `k` LLM calls to the **base** E4B (vLLM, `--served-model-name gemma4`, no LoRA adapter loaded; adapters such as aurora are only used as the student in later refresh rounds, E3), the rest to Haiku (`anthropic/claude-haiku-5-5`), in the same ADK session and the same Container A.
- `k` is drawn per run from a stratified range (early / mid / late), capped so the teacher keeps at least about 8 of the 28 tool calls. Tool-call (28) and time (8 min) budgets are shared across both models, matching deployment.
- At the switch it writes `handoff.json` (`k`, student/teacher call counts, model ids), a workspace tarball and the message snapshot. These make a two-session variant possible without redesign.
- A run is invalid if `submit_patch` happens before the switch.
- `--handoff-teacher-thinking {adaptive,off}` (default adaptive): Anthropic may reject a request with thinking enabled when the last assistant turn has no thinking block, which is the state at the handoff. The dry run decides; the choice is recorded in the manifest.
- Student and teacher exchanges are recorded in separate files (`vllm_exchanges.json`, `anthropic_exchanges.json`) with `handoff.json` giving the boundary.

### 2. Generation plan

107 tasks × several handoff points × several attempts. Counts are fixed after sizing from existing Haiku cost and yield logs (`logs/remote/rishav/inspiration-one`). A 2-task dry run precedes any full run.

### 3. Filtering

Keep a run only if all hold:

1. The verifier reports `resolved`.
2. The handoff occurred and the teacher made at least one call after it.
3. The OEC repetition filter passes over the whole trajectory (no 3 identical consecutive actions including arguments; no 20+ consecutive file-reading commands).
4. Stop reasons are clean (a Haiku `max_tokens` cutoff rejects the trace, as the exporter already does).

The existing `mask_unsound_steps` is then applied to teacher turns.

### 4. Format parity (hard requirement)

Student prefix, teacher continuation and the existing clean-Haiku rows must differ only in who wrote the tokens. Everything else is pinned and asserted:

- **One agent config for all sources:** same submission directory (`src/rishav/inspiration-one`: `agent.yaml`, prompts, tool set, `eval_config.yaml`, sampling config), same `swegemma` version, same thinking setting. The manifest records the config hash and swegemma version, and the exporter refuses to mix rows from different hashes.
- **One sandbox backend per dataset:** generation uses a single backend (docker or `subprocess`), recorded in the manifest. Mixing backends within a dataset is rejected. Before the full run, a sample of tool results from each backend is compared for formatting differences (path rewriting, shell output), and the backend closest to the scored environment is used.
- **One rendering path:** every row, whatever its source, is converted by the same exporter code and rendered once with the canonical Gemma chat template (`chat_template.jinja`, the existing `TEMPLATE_MARKER` check). Tool-call serialization, the commentary/call split, tool-result formatting and the ADK compaction-summary format must be identical across sources.
- **One thought treatment:** student turns keep their native reasoning as context. Teacher turns go through the same `rationalize.py` pass used for the clean-Haiku rows, so reasoning is present and context-only in both. The thought opener and channel handling in `render_row` is unchanged.
- **Format audit (gate before training):** a script renders a sample of student-only, teacher-only and handoff rows and asserts equal system-prompt hash, tools-schema hash and tool-call/result structure, and that no row contains the thought text in a supervised span (`thought_leaks` stays fatal). A failure blocks the dataset build.
- The student prefix is replayed inside Haiku's first request and normalized by the same code as Haiku's turns, so tool-call serialization is identical by construction; student reasoning is recovered from the last vLLM request.

### 5. Export and schema

- New mixed-trace path in `scripts/sft_export` merges student and teacher exchanges into trajectory rows. Student assistant messages get `train:false`; teacher messages get `train:true`.
- Rows ship as schema gemma4-sft-v6 (the rationalize step stamps it and the trainer accepts it). OEC provenance is the per-row kind and handoff keys plus an oec manifest section; no trainer change.
- Trainer change is expected to be limited to the manifest check, since `render_row` already honours per-message `train`.
- Rows over 12288 tokens are rejected as today.
- OEC rows come from the same sandbox/config as the clean rows, so the two can be mixed in one dataset.

### 6. Dataset arms

Trained with the existing recipe (r=64, alpha=128, lr 1e-4, 3 epochs, bf16, `unsloth_balanced` on 4 L4s):

- **A:** 442 clean-Haiku rows (baseline; aurora adapter exists).
- **E1a:** OEC rows plus clean rows at about 1:2 (the best mix reported by OEC).
- **E1b (optional, later):** E1a with the student prefix unmasked.

### 7. Evaluation

val20 at pass@3 via `run_pass3.py`. Report pass@3 and per-run resolve counts, plus loop rate, tool-call validity, steps per resolved task and recovery rate (resolved given a failed test or tool error earlier in the run). Reference points: base E4B 5/20 pass@3; aurora epoch1 3/20, epoch2 7/20. With 20 tasks, differences under about 5 points are noise.

### 8. Testing

- Switching wrapper with fake models: call counts, state continuity, budget sharing, invalid-if-early-submit.
- Filters: repetition rules and stop-reason handling.
- Exporter: mixed-trace merge, compaction at the boundary, masks.
- Trainer: a masked-prefix row through the real-template tests in `training/tests/test_parallel_sft.py`.
- Format audit script, run on fixtures from all three sources.
- 2-task dry run end to end before full generation.

## Risks

- Haiku thinking blocks may not round-trip through the ADK session history.
- ADK compaction (interval 15) can summarize student turns at the handoff boundary; the exporter and handoff must tolerate it.
- Serving E4B in a Kaggle session depends on the unconfirmed L4-plus-internet access.
- val20 overlap with training tasks: comparisons measure fit only.
- Haiku API use for training data must stay within the competition's "reasonably accessible, minimal cost" external-data rule; winners must document the method.

## Out of scope

Stuck-triggered handoffs, harmful-step masking on student failures, loss weighting, iterative refresh rounds (E2–E4 in the experiment plan).
