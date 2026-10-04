# Part C: read-to-edit gap on the gold file

Follow-up to `train_trace_analysis.md` section 2 ("gold first seen at call 1.9, first read 2.9, first edited 12.6").
Source: `/Users/akshay/kaggle/wt/main/logs/remote/base/train/*/model_trace.json`, per-call records in `/tmp/ds.pkl`.
Scripts: `/tmp/g1.py` (gap composition), `/tmp/g3.py` (size and truncation of the first gold read).
Analysis only; nothing in the agent was changed.

## 1. Finding
Most calls between the first read of the gold file and the first edit go on finding the relevant part of that file.
The model reads it in full, gets the top 150 lines (mostly imports), then re-reads it and greps it for line numbers.

## 2. The first read lands on the wrong part of the file
| Measure | Value |
|---|---|
| Traces that read and then edited a gold file | 46 |
| First gold read had no line range | 42 of 46 |
| Gold file size at first read | median 784 lines; 56 of 66 over 150; 38 over 500 |
| First gold read cut off at 150 lines | 51 of 66 |
| Largest files | `fastapi/routing.py` ~5,600 lines, `fastapi/applications.py` ~4,600, `rich/console.py` ~2,650 |

## 3. What fills the gap
Calls strictly between the first gold read and the first successful gold edit, about 389 in total.

| What the model did | Calls | Share |
|---|---|---|
| Re-read the gold file in pieces | 120 | 31% |
| grep | 110 | 28% |
| Repro scripts (`write_file` + python/pytest) | 91 | 23% |
| Read other files | 37 | 10% |
| Failed edit, find/ls, other | ~31 | 8% |

- 47 of the 110 greps searched the gold file itself; 63 used `-n`. The model knew the file and was looking for line numbers.
- Page-by-page reading: fastapi_13713 read from lines 151, 301, 451, 601, 751, 981. rich_3468 read 7 overlapping ranges. rich_3675 read 9 ranges around lines 920-970.
- Median gap: 7 calls overall; resolved 6, unresolved 10.
- The 7 differs from the report's 9 because of counting: 46 traces here, and only calls strictly between read and edit.

## 4. The location was often already on screen
The gold file first appears around call 1.9, usually in grep output that already shows `file:line`.
The model does not jump to that line; it opens the whole file from the top.

## 5. Options
**Option 1: Show an outline instead of the top of a big file.**
`read_file` with no range on a file over ~150 lines returns each class and function with its line range (the "skeleton" from Agentless).
- Gain: the next call can request the right range; an outline is small next to a 784-line file.
- Cost: one extra call; Python files only.

**Option 2: Read one function or class by name.**
For example `read_file(path, symbol="Console.is_terminal")`, returning just that definition with line numbers (similar to AutoCodeRover's `search_method_in_file`).
- Gain: one call, least context; matches how the model thinks ("I need `get_request_handler`").
- Cost: a new argument the model has to learn.

**Option 3: Read around a line.**
For example `read_file(path, around_line=969)`, returning about ±40 lines. Pairs with grep's `file:line` output.
- Gain: uses the location that already appeared at call 1-2.
- Cost: does not help before a line has been found.

**Option 4: Grep that shows the enclosing function.**
Exclude tests/docs/CHANGELOG, group matches by file, label each line with its function (e.g. `rich/console.py:969 in Console.is_terminal`), cap at ~50 results.
- Gain: the model sees which function to open without reading the file. SWE-agent found summarized search helped.
- Cost: a wrapper to build and maintain.

**Option 5: Don't resend lines the model already has.**
If a read covers lines already shown, return only the new ones with a note like "lines 930-960 already shown at call 7".
- Gain: less context, fewer page-by-page loops.
- Cost: wrong after a context summary, when the model has lost the text; must reset at each summary.

**Option 6: Prompt rules only.**
"If grep shows `file:line`, read ±40 lines around it. Never read a file over 300 lines without a range. Stop writing repro scripts once the code is located."
- Gain: free, quick to test.
- Cost: weakest alone; the model already ignores parts of the current prompt.

**Option 7: Find the location before the agent starts (Agentless-style).**
Before the main loop: issue + repo tree -> top files -> outlines -> candidate functions, passed to the agent as a starting hint.
- Gain: also addresses the 9 traces that never opened the gold file (T3).
- Cost: biggest change; extra model calls that may count against the 25-call budget.

## 6. How the options relate
- 1 and 2 address the main problem (first read in the wrong place). 3 and 4 make early grep locations usable. 5 and 6 are cheap additions. 7 is a separate, larger project.
- Some re-reads are really Part A: broken `start_line` argument keys (e.g. `start_line"`, 20 calls) are dropped and the read falls back to line 1 (rich_4077 turns 4, 6, 8, 11). Fixing Part A first or alongside makes these numbers cleaner.

## 7. External references
- SWE-agent (Yang et al. 2024, arXiv 2405.15793): 100-line view window beat both 30 lines and the full file; search capped at 50 results.
- Agentless (Xia et al. 2024, arXiv 2407.01489): hierarchical localization: files, then skeletons, then edit locations.
- AutoCodeRover (Zhang et al. 2024, arXiv 2404.05427): structured search APIs such as `search_method_in_file`.
- Aider repo map (aider.chat/docs/repomap.html): ranked map of symbols and signatures within a token budget.

## 8. Not yet done
- Expected savings are an estimate, not measured. Rough guess: Option 1 or 2 plus Option 3 would remove most of the 120 re-reads and the 47 greps on the found file, about 40% of the gap (3-4 calls per trace).
- Replay of the relevant traces against a chosen option to count calls saved.
- No fixes implemented.
