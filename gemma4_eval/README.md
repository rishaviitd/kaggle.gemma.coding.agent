# Gemma 4 post-run trace analytics

An offline notebook and reusable Python engine for completed coding-agent traces. The notebook uses a sibling repository’s `logs/remote/akshay/itr-1` when present, then the original local trace path; set `GEMMA_TRACE_DIR` to override it. Source traces are read only: the library never executes commands, patches, tests, or code found inside them.

## Open the results

- `reports/itr-1/report.html`: self-contained **single-attempt explorer**. Select a task to see only its timeline, tool calls/results, tests, patch, tokens, findings, and source evidence.
- `reports/itr-1/batch_report.html`: self-contained **whole-batch report**. It shows official resolution, repository breakdown, implementation/verification coverage, budget stages, submission loops, tool outcomes, cost, findings, quality flags, and a linked task index. Click a task to open the detailed explorer.
- `notebooks/01_agent_trace_report.ipynb`: run **one notebook** to render both views and regenerate both HTML files plus CSV/JSONL exports. The executed copy is `notebooks/01_agent_trace_report.executed.ipynb`.
- `reports/itr-1/summary.json` and `batch_summary.json`: outcome counts, coverage, Wilson interval, submission summary, source hashes and method version.

## Environment and execution

Launch the notebook with `scripts/launch_notebook.sh` from this folder. The project has its own `.venv`. Dependencies were installed once during setup; no notebook cell installs packages or calls remote services. To recreate the environment:

```bash
cd /Users/akshay/kaggle/gemma4_eval
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,notebook]'
# Launch the included notebook:
scripts/launch_notebook.sh
```

Rebuild both supplied HTML reports without Jupyter:

```bash
PYTHONPATH=src .venv/bin/python -m agent_eval.cli \
  "${GEMMA_TRACE_DIR:-../logs/remote/akshay/itr-1}" \
  --experiment-id itr-1 --output-dir reports/itr-1
.venv/bin/python -m pytest -q
```

CLI options: `--gold-dir`, `--repo-roots` (JSON file), `--compare-dir`, `--strict-schema`, `--parquet`, `--no-html`, `--include-reasoning`, and `--no-redact`. Text redaction is on and reasoning is hidden by default, including in lazy source inspection. Redaction is a best-effort pattern scrub, not a guarantee that arbitrary private text is removed. Source paths remain for auditability. Review exports before sharing them.

## Stable Python interfaces

```python
from agent_eval import load_experiment, compute_metrics, classify_failures, summarize_batch, export_report
from agent_eval.plots import render_report, render_batch_report

experiment = load_experiment(
    TRACE_DIR, experiment_id="itr-1",
    trace_globs=["**/model_trace.json", "**/trace_*.json"],
    strict_schema=False, redact_text=True,
    include_reasoning_in_viewer=False,
)
metrics = compute_metrics(experiment, gold_dir=None, repo_roots={}, compare_dir=None)
findings = classify_failures(experiment, metrics)
render_report(experiment, metrics, findings)
batch = summarize_batch(experiment, metrics, findings)
render_batch_report(batch)
export_report(experiment, metrics, findings, "reports/new_batch")
```

`classify_failures` also populates metric C11 in the supplied `MetricData`. Repeated computation is deterministic. `ExperimentData` contains the normalized Pandas tables; `MetricData.metrics` is long format with availability, evidence IDs, method version, numerator, denominator, and coverage. `FindingsData` separates factual findings from tentative primary failure stages. Each evidence row has a source path, JSON pointer, turn, call ID, and safe excerpt. `agent_eval.evidence.read_evidence` lazily reads a source item after verifying its original hash.

## Report sections

The single-attempt report contains the ordered event timeline, tool outcomes and budget, agent-side and independent final tests, per-turn tokens/compaction, final patch, findings, and evidence pointers. Its notebook view can load larger redacted source excerpts on demand.

The separate whole-batch report contains official outcome counts and Wilson interval, repository rates with coverage, independent implementation/verification indicators with stated denominators, budget exhaustion stages, raw submission loops, tool reliability, tentative primary stages, runtime/call/token distributions, finding prevalence, data quality, and a filterable task index linked to the trace explorer. The same notebook renders this view inline and exports both reports. Optional gold and paired metrics remain in the metrics tables with explicit availability reasons.

## Metric interpretation

| ID | Meaning and limits |
|---|---|
| C01 | Official resolution; unknowns excluded, coverage and denominator shown. Batch rate and Wilson interval in summary. |
| C02 | Final verifier exit status plus parsed pass/fail counts. Test transitions unavailable without baseline test identities/statuses. |
| C03 | Nonempty parseable unified patch. Application remains `not_verified`; no patches are applied. |
| C04/C05 | Gold-function discovery/edit recall. Require gold patch and reliable baseline AST function mapping. File overlap works without symbol mapping. |
| C06 | JSON/schema checks against each turn’s declared tools: parse errors, raw/parsed disagreement, missing/type/unknown keys tracked separately. Undeclared schema means unknown, not valid. |
| C07 | Successful edit/write executions divided by all edit/write attempts; unmatched results remain unknown, with result coverage shown. Includes reproduction-file writes; confirmed source edits tracked separately. |
| C08 | Recorded agent-side test after final confirmed source edit. Shell writes are inferred separately and make boundary confidence partial. Test execution does not imply passing. |
| C09 | Observed exhaustion stage: pre-edit, pre-verification, post-edit; direct budget rejections and authoritative status/final counts preferred. |
| C10 | Authoritative final duration; calls and per-turn inference usage separately exposed. Missing duration is unavailable. |
| C11 | Transparent tentative stage, ranked by observable blockers. Unresolved alone never implies a semantic reasoning failure. |
| C12 | Matched official outcome transition with second batch. Duplicate task attempts, missing pairs, unknown outcomes, and repository mismatches are excluded and explained. |

`turn_count`, observed regular model calls, reported LLM calls, raw tool attempts, result record count, and budget-counted tool calls remain distinct. Prompt tokens sum recurrent context, not unique context. Aggregate usage conflicts are quality warnings. Tool `status=ok` is independent of command exit status. Test command recognition, possible shell writes, budget wording extraction, compaction detection, and final-response success claims are explicitly heuristic. Shell pipelines without `pipefail` have uncertain exit status. The report avoids funnel/Sankey graphs because the traces do not establish uniform prerequisite stages.

## Optional enrichment inputs

Gold files are keyed as `GOLD_DIR/<task_id>.patch`. `repo_roots` maps the exact recorded repository name (for example `fastapi/fastapi`) to a **pristine baseline** checkout. Function mapping supports Python AST ranges; unsupported languages, unmappable additions/module changes, malformed gold diffs, and missing paths give unavailable reasons. The supplied batch does not include gold/reference repositories, so C04/C05 are unavailable. No symbol recall is inferred from file overlap.

A comparison batch is loaded with a separate experiment ID and paired by unique task ID and repository. Researchers must confirm that model/configuration/evaluation conditions are comparable. These are descriptive transitions, not causal effects or pass@k estimates.

Advanced modules report their prerequisites in `modules.csv`. Patch scope, recorded graph-tool usage, test recovery, descriptive reliability and cross-config views are supported to the extent evidence exists. Edit survival requires intermediate snapshots. A calibrated semantic judge is an explicit optional non-goal and remains unavailable; no judge API is contacted.

## Exports and reproducibility

Every normalized table, metrics, findings, primary stages, gold overlap, pairing, module availability, and batch rollup table is exported to CSV and JSONL. Git tracks the two HTML reports, summaries, and compact batch CSVs; running the notebook recreates the larger detailed exports. Structured cells are JSON in CSV. Parquet is optional with `pyarrow`. `manifest.json` hashes exported artifacts. Discovery is recursive only within the supplied root and excludes reports, caches, virtual environments, Git metadata and node_modules. Per-file failures are isolated; duplicate task IDs remain separate attempts, including identical traces at different paths. Output directories containing source files are rejected.

The golden fixture is an exact local copy of `fastapi_5624/model_trace.json` with SHA-256 `df6d664f4c923c3ebad2775159b44674c98b2918c7b3368f1c8961462868c9e9`. Its tests verify 30 turns / 28 attempts / 27 results / 25 budgeted calls / 29 reported model calls, turn-10 missing result, turn-11 compaction, the 40-vs-25 budget conflict, per-turn completion usage, patch capture, and external-verifier separation. Synthetic mixed-batch tests cover malformed files, missing fields, duplicates, raw/parsed conflicts, multiple calls per turn, pipeline ambiguity and optional enrichments.

## Validation status

The analytics notebook was executed with a local IPC Jupyter kernel (all six code cells completed with no errors). HTML JavaScript passes Node syntax validation, and the tests build all chart types with real/synthetic/empty selections. Automated browser interaction checks were unavailable; JavaScript syntax and notebook execution were verified. The original requested specification is preserved in `docs/specification.md`.
