# E1 OEC generation split into two runs: Kaggle L4 student, local Docker teacher

**Status:** design, awaiting review. **Date:** 2026-10-23.
**Amends:** `2026-10-23-e1-oec-dataset-design.md` §1 (handoff) and §2 (generation). Everything else in that spec stands.

## Goal

Generate OEC handoff trajectories when the Kaggle L4 kernel cannot reach the Anthropic API. The student part runs on Kaggle L4 with internet off and the competition attached. The teacher part runs locally in Docker with Haiku. The two parts join at a saved snapshot.

## Constraints

- Competition rule: L4 sessions must have internet off (from the earlier competition-page check; not re-verified this session). The run-A kernel is therefore internet off.
- The 400 `SaveKernel` error on the earlier competition-attached push with internet on is still unexplained. The internet-off push succeeded. The cause is unconfirmed.
- Accelerator for run A is unverified. The page showed T4 when L4 was requested. Run A must be confirmed on L4 before any data is trusted.
- One sandbox backend per dataset (E1 spec §4). Run A uses `subprocess`, so run B must use `subprocess` too.
- The Anthropic key is never written to a file in the repo or in a kernel. It is read from the local environment at run B time and from Kaggle Secrets only if a Kaggle teacher run is ever added.

## Architecture

```
run A (Kaggle, L4, internet off, competition attached, subprocess sandbox)
  E4B student, vLLM on 127.0.0.1, served name gemma4
  runs agent calls 1..k, then stops at the switch
  writes snapshot/ -> Kaggle output

        snapshot/ downloaded and uploaded as a local directory

run B (local Docker, Linux image matching the Kaggle image, subprocess sandbox)
  re-runs the episode from the task snapshot; student calls 1..k are replayed from snapshot/
  tools execute live in the container and are checked against the recorded results
  HandoffLlm teacher = Haiku, continues from call k+1 to the episode end
  writes anthropic_exchanges.json and handoff.json (completed)

local (macOS)
  export_oec -> build_oec_dataset -> merge_datasets -> audit_parity
```

Run A has no Anthropic key. Run B has no vLLM and no GPU.

## Snapshot contract (run A writes, run B reads)

Directory `snapshot/`, all files required:

| File | Contents |
|---|---|
| `handoff.json` | `k`, student call count, tool-call budget used, time budget used, `student_model`, `teacher_model`, `teacher_thinking`, task id, run name |
| `student_responses.json` | The ADK `LlmResponse` objects the student returned for calls 1..k, in order, serialized with `model_dump(mode='json')`. Run B replays these instead of calling vLLM. |
| `session_events.json` | ADK session events at the switch (after call k's tool results are appended, before call k+1). Run B checks its live tool results against these. |
| `vllm_exchanges.json` | Raw student exchanges from the trace proxy. Copied into run B's log directory so `student_reasoning_by_call_id` in `export_oec` finds it. |
| `manifest.json` | `agent_config_sha256`, swegemma version, sandbox backend (`subprocess`), image digest of run A, Kaggle kernel version, accelerator reported by the kernel |

Run B refuses to start if any file is missing, if the sandbox backend is not `subprocess`, or if the agent config hash differs from the local checkout. This enforces the one-config rule (E1 §4) across the two environments.

Compaction before the switch remains invalid (`HandoffLlm._route` raises, F3 fix in `d616892e`). The snapshot therefore never contains a compaction summary.

**Amended 2026-10-23 (replay instead of workspace restore).** The first draft restored a `workspace.tar` and rebuilt the ADK session from a message list. Both need swegemma internals that are not yet verified. Replay avoids both: run B re-executes the episode from the same task snapshot, feeds the recorded student responses, and checks each live tool result against `session_events.json`. Any mismatch fails the run before a teacher call is made on that prefix. Replay also makes the parity gate a per-run check rather than a sample.

## Run A: changes needed

- `HandoffLlm`: on student calls, record each `LlmResponse`. At call k+1, call an `on_switch` hook with the session events, then raise `HandoffStop` so the teacher client is never built.
- `task_pipeline.py`: a `--handoff-stop-after-switch` mode that writes `snapshot/` on `HandoffStop` and exits.

## Run B: changes needed

- `ReplayLlm`: a `BaseLlm` that returns recorded student responses in order and fails if called more times than recorded.
- `task_pipeline.py`: a `--handoff-resume <snapshot_dir>` mode. It builds the registry with `ReplayLlm` as student and Haiku as teacher, runs the episode, and verifies live tool results against `session_events.json`. Budget counters follow the live run; `handoff.json` is written from the live `HandoffLlm.record()`.
- A Docker image built from the Kaggle image's Python and OS packages, with the task environments the subprocess sandbox needs. The image digest is recorded in `manifest.json` by run B.
- Wrapper script `scripts/runs/run_teacher_docker.sh`: mounts `snapshot/` read-only and an output directory read-write, passes the Anthropic key from the local environment by name only, and runs the resume mode.

## Parity gate (blocks dataset build)

Two checks, both automatic:

1. Per run: during replay, each live tool result (stdout, stderr, exit code, rendered result text) is compared with the matching entry in `session_events.json`. Any difference beyond timing fields aborts the run, and no teacher call is made on that prefix.
2. Per batch, before any dataset build: a 5-task sample run in the container and in run A's environment must produce identical rendered tool results. This is the one-backend check. The fix is to change the container, not the rendering code.

## Dry run gate

First end-to-end test is 2 tasks: one run A, one run B, then the local exporters. This is the only run before the full generation, and it needs no cost approval beyond the 2 tasks' Haiku calls. The full run needs the user's cost go-ahead.

## Errors and failure handling

- Run A failure before the switch: no snapshot, nothing to resume. Rerun run A.
- Run B failure mid-continuation: the snapshot is still valid. Run B restarts from the snapshot, with its own `anthropic_exchanges.json` cleared for that run name. Partial teacher output is never merged.
- Kernel reports a non-L4 accelerator: `manifest.json` records it and the export refuses the run's rows as a mismatch with the rule. The data is kept as a dry-run artefact only.

## Testing

- Unit: snapshot writer and reader round-trip on a fixture (`handoff.json`, `student_responses.json`, `session_events.json`, `vllm_exchanges.json`, `manifest.json`); refusal cases (missing file, wrong backend, hash mismatch).
- Stop: `HandoffLlm` with `stop_at_switch` never calls the teacher and calls `on_switch` once with the call count k.
- Replay: `ReplayLlm` returns recorded responses in order and fails on overrun; replay verification flags a planted tool-result difference.
- Dry run: 2 tasks end to end; the existing audit and merge run on the output.

## Open questions

1. Does the Kaggle L4 kernel with internet off and the competition attached actually run on L4? (Must be checked on the kernel page before run A is used for data.)
2. Does the competition rule allow T4 for run A? Not confirmed; L4 is assumed.
3. Does the local Docker image match the Kaggle image closely enough for the parity gate to pass? Decided by the gate, not assumed.
4. Haiku cost for the full run. Needs the user's go-ahead.

## Out of scope

Running E4B locally. Kaggle-side Haiku (a CPU kernel with internet on). Both were considered and set aside: local E4B does not match the scored model, and a Kaggle teacher kernel would reintroduce a second environment for tools.
