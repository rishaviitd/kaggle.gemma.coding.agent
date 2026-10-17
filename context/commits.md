# Changelog

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
