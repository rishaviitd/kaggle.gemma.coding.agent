# Sources

Where each point in `README.md` comes from. Paths are relative to the repo root.

| Topic | File |
|---|---|
| Base run: outcomes, trajectory classes, per-tool results, wasted calls, budget | `src/base/analysis/train_trace_analysis.md` |
| `edit_file` tool failures | `src/base/analysis/edit_file_tool_analysis.md` |
| Read-to-edit gap on the gold file (base), options 1-7 | `src/base/analysis/localization_read_to_edit_gap.md` |
| Rule compliance in the itr-2 run, vs base and itr-1 | `src/base/analysis/itr2_rule_compliance.md` |
| itr-1 changes (C2, C5, C6, C8, C9, S1-S4) | `src/akshay/itr-1/changes.md` |
| itr-2 changes (C10 outline skill, C11 graph tools removed) | `src/akshay/itr-2/changes.md` |
| itr-2 run results: gap, ranged reads, grep-hit rule, `submit_patch` loops; itr-3 changes (C12 `find.py`, C13) | `src/akshay/itr-3/changes.md` |

## Traces

- base: `logs/remote/base/train`
- itr-1: `logs/remote/akshay/itr-1/train`
- itr-2: `logs/remote/akshay/itr-2/train`

## Not written up elsewhere

These came from chat analysis and are only summarised in `README.md`:

- Failure classes per run (wrong fix, never edited, multi-file, never opened): base 17/13/14/10, itr-1 18/19/10/9, itr-2 20/12/11/9.
- Phase split (first 25 calls): calls before the first gold read about 3.6-3.7 per trace in all runs; gold file first read at call 3 (median) in all runs.
- Notes: itr-1 30 of 76 traces wrote a note, itr-2 20 of 76; before the first edit in 8 (itr-1) and 5 (itr-2); read-only read-backs 2 per run.
- Existing tests read: 13 of 76 (base), 13 of 76 (itr-1), 6 of 76 (itr-2).

The scripts were run from `/tmp` and are not kept in the repo.
- Edit tool counts (edit_file and write_file outcomes, fallback runs, rejected /tmp writes): run on the three train sets; scripts `/tmp/edit_final.py`, `/tmp/wf2.py`, `/tmp/wf3.py`, `/tmp/fb2.py`. Earlier base-only detail: `src/base/analysis/edit_file_tool_analysis.md`.

## itr-3 and itr-4 additions

- itr-3 traces: `logs/remote/akshay/itr-3/train`. itr-4 traces (14 tasks, Kaggle version `v1`): `logs/remote/akshay/itr-4/train`.
- itr-4 changes (C20-C27), the rejected summary rule, the notes.md verdict, per-change scores and per-task outcomes: `src/akshay/itr-4/changes.md`.
- Scratch scripts (kept in `/Users/akshay/Documents`, not in the repo): `ck1.py`-`ck3.py` (summaries), `rp1.py`-`rp5.py` (first repro run), `rd80.py` (read sizes), `rerun_ids.py` (the 14 tasks), `itr4_cmp.py`, `itr4_an.py`, `itr4_err.py`, `itr4_rep.py`, `itr4_changes.py`, `itr4_why.py`, `itr4_why2.py`, `itr4_fair.py` (itr-4 analysis), `kver2.py` (download of version `v1` with the SDK).
