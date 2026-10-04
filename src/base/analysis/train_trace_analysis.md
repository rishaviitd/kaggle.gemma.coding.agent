# Train trace analysis (76 traces, 2,134 tool calls)

Source: `/Users/akshay/kaggle/wt/main/logs/remote/base/train/*/model_trace.json`, gold patches from `data/train`.
"Gold" = source files in the gold patch. Analysis only; nothing in the agent was changed.
Working data: `/tmp/ds.pkl` (per-call records), replay scripts `/tmp/rp2.py`, `/tmp/tail.py`.

## 1. Outcomes
| Measure | Value |
|---|---|
| Resolved | 20 of 76 (26%): fastapi 12/40, rich 6/28, requests 2/8 |
| Calls per trace | median 26, max 201 (resolved median 18, unresolved 26) |
| Budget | 25 calls. 43 traces used 25 or more; 6 of those resolved |
| Single-file gold tasks | 20 of 53 resolved (38%) |
| Multi-file gold tasks | 0 of 23 resolved |

## 2. Trajectory classes (74 traces; 2 have no source file in the gold patch)
| Class | n | Resolved | Mean calls |
|---|---|---|---|
| T4: single gold file edited | 32 | 17 | 20 |
| T5: gold file opened, other file edited | 10 | 2 | 26 |
| T6/T7/T8: multi-file gold, partly or not edited | 15 | 0 | 24-26 |
| T3: never opened gold file | 9 | 0 | 23 |
| T1: `edit_file` argument loop | 5 | 0 | 108 |
| T2: never submitted | 3 | 0 | 25 |

- Gold file appears in some output or argument by call 3 in 62% of traces, call 5 in 75%, call 12 in 88%.
- Pure localization failure (T3) is 9 of 76: fastapi_14266, 14482, 14512, 14583, requests_6757, rich_3105, 3472, 4077, 4079.
- For the 44 traces that edited a gold file: gold first seen at call 1.9, first read 2.9, first edited 12.6. Median read-to-edit gap is 9 calls; 22% of calls before the edit were wasted.
- 15 of 32 T4 traces edited the right file with a wrong fix (reasoning gap): fastapi_14258, 14356, 14479, 14964, 15588, 15763, requests_6589, 6629, 7328, 7502, rich_3063, 3468, 3506, 3953, 4006.

## 3. Per-tool effectiveness
| Tool | n | OK | Notes |
|---|---|---|---|
| `edit_file` | 576 | 108 (19%) | 413 missing `old_string`, 17 "not found", 13 budget |
| `read_file` | 550 | 505 (92%) | 195 full-file reads; 47 re-reads of an already-read file |
| `run_command` grep | 419 | 326 (78%) | 58 no-match exits returned as errors; gold file in 48% of outputs; 22 empty outputs |
| `run_command` python | 158 | 107 | 35 tracebacks; gold file in 4% of outputs |
| `find` | 39 | 31 | gold file in 31% |
| `search_similar_code` | 85 | 0 | always `count: 0` |
| `get_code_neighbors` | 7 | 5 | works only with fully qualified names |
| `code_analyzer_agent` | 3 | 0 | all returned `""` |
| `get_code_subgraph` | 1 | 0 | empty |
| `submit_patch` | 92 | 83 | 4 traces never submitted successfully |

- First call: `search_similar_code` (24 traces) surfaced gold 0 times; grep 14 of 35; find 5 of 6; read_file 4 of 5.
- Using `search_similar_code` did not help: resolved 9/31 (29%, 30.2 calls) vs 11/45 (24%, 26.6 calls) without it.
- Root cause seen in the `swegemma` 0.2.7 source: `embed()` looks the query up in a cache keyed by stored node name/text, so free-text and bare-name queries miss. Not run end to end (numpy and graph files not available locally).

## 4. Wasted calls
Waste = malformed, error, duplicate, empty or budget-rejected call.
| Category | All calls | First 25 calls |
|---|---|---|
| Malformed `edit_file` arguments | 417 (19.5%) | 32 |
| Exact duplicate read/command | 139 (6.5%) | 128 (7.8%) |
| Error result | 137 (6.4%) | 135 (8.2%) |
| Empty graph tool result | 91 (4.3%) | 90 (5.5%) |
| Empty command output | 40 (1.9%) | 39 (2.4%) |
| Rejected: over budget | 28 (1.3%) | 0 |
| Total | 852 of 2,134 (39.9%) | 424 of 1,651 (25.7%) |

- Per trace in first 25 calls: resolved wasted 4.5 on average, unresolved 6.0.
- Malformed `edit_file` is almost all in 5 traces: rich_2943 (182 of 201 calls), fastapi_14605 (162 of 193), fastapi_15785 (27/58), fastapi_14306 (23/51), rich_3777 (12/39). None resolved. The same bad call repeats up to 66 times.
- Repro loop: 268 calls on `write_file` plus python/pytest; 44 traces wrote scripts, 17 resolved.
- 40 of 99 repeated commands and 37 of 54 duplicate reads happened across a context-summary boundary.

## 5. Context summarization
- All 76 traces were summarized at least once (183 summary turns; typical 2, max 5), first usually at turn 11-19.
- In 100 of 174 calls issued in the turn just before a summary, the result was never recorded (mostly run_command 38, read_file 26, edit_file 25). The model may then re-read or re-edit.
- The summary prompt is about 12.5k characters, so exact line numbers and file contents are likely lost.

## 6. Budget exhaustion before the first source edit
Source edit = successful `edit_file` on a file whose name does not contain reproduce, test_ or check_.

| Outcome | No source edit | Source edit |
|---|---|---|
| Resolved (20) | 2 | 18 |
| Unresolved (56) | 18 | 38 |

- 18 of 56 unresolved traces never edited a source file. 12 of them hit a `BudgetExceeded` rejection: fastapi_14266, 14485, 14616, 15280, 15589, 15785, 9425, rich_3061, 3472, 3486, 3777, 4075.
- The other 6 stopped on their own: fastapi_11355, 14258, 14419, rich_2943, 3105, 4076.
- Only 2 of 20 resolved traces saw a budget rejection, against 17 of 56 unresolved.
- After the budget runs out the model still calls `edit_file` and `submit_patch`. `submit_patch` then ships whatever is in the workspace (repro scripts), e.g. fastapi_15589 submitted 6 files.

## 7. Replays (what the model was doing)
### T3: never opened the gold file (fastapi_14583 middle section not fully read)
| Trace | Gold | What happened |
|---|---|---|
| rich_4079 | rich/markdown.py | Issue "Inline table code". Read rich/table.py and edited a Column docstring. Saw `tests/test_markdown.py::test_inline_styles_in_table` and dismissed it. |
| rich_3472 | rich/pretty.py | "missing field in dataclass". `grep -r dataclass . | head -20` cut off pretty.py; ~24 calls checking console.py and traceback.py dataclasses. |
| rich_3105 | rich/_export_format.py | Issue is only "Fix #3104". ~24 calls of near-identical `grep 3104`, git log, TODO greps. No change of strategy. |
| requests_6757 | src/requests/compat.py, utils.py | "Test on urllib3 1.26.x". Edited tox.ini and submitted at call 8; never looked at src/. |
| fastapi_14266 | fastapi/dependencies/utils.py | Followed the OpenAPI output code (openapi/utils.py, applications.py); read in 150-line chunks; six greps for `self.routes`. |
| fastapi_14512 | fastapi/_compat/v2.py | Wrote several repro scripts, then followed `Body` in params.py and dependencies/utils.py, not the Pydantic compat layer. |
| fastapi_14482 | fastapi/_compat/v2.py | Edited fastapi/utils.py at call 10. `find -name _compat.py` found nothing; `ls -R fastapi/_compat` at call 16 showed a package, after the wrong patch. |
| fastapi_14583 | dependencies/utils.py, routing.py | Edited _compat/v1.py and may_v1.py to add the warning. |
| rich_4077 | rich/file_proxy.py | Issue "proxy isatty". Several `read_file` calls with start_line returned the first 150 lines; went to console.py and progress.py. |

### T5: gold opened, other file edited
| Trace | Gold | What happened |
|---|---|---|
| fastapi_14616 | dependencies/utils.py | Opened gold at call 2; calls 14-27 went to repro scripts; `edit_file` rejected at budget; patch had only reproduce_issue.py. |
| fastapi_15589 | dependencies/utils.py | Opened at call 8; 5 repro scripts; bug did not reproduce; patch shipped 6 script files. |
| fastapi_9425 | dependencies/utils.py | Opened early; check_annotation scripts; budget hit before any edit. |
| rich_3469 | rich/markdown.py | `grep ' + " "'` showed markdown.py:397 at call 4; agent moved to cells.py and syntax.py, edited syntax.py, pytest failed. |
| rich_4075 | rich/console.py | Read console.py at call 2, reproduced by call 5, re-read `_collect_renderables` four times, moved to text.py; patch only reproduce_issue.py. |
| rich_2943 | rich/style.py | Found `clear_meta_and_links` at call 12, then 182 `edit_file` calls without `old_string`. |

## 8. Patterns
1. Vague or one-line issue text (rich_3105, 3469, 4075, 4077, 4079) leads to repeated near-duplicate greps.
2. The model follows the place where the symptom appears, not where it is produced (fastapi_14266, 14512, rich_4079).
3. A correct lead is dropped (rich_3469: gold line seen at call 4).
4. Repro scripts consume the 25-call budget before any source edit (4 of 6 T5 traces patched only scripts).
5. `read_file` with `start_line` sometimes returns the first 150 lines (rich_2943 call 8, rich_4077 calls 5, 7, 9, 12), and bad argument names were accepted silently (fastapi_15280). Cause not confirmed in tool code; not counted across all 550 reads.
6. Grep output is truncated by `head -20` or swamped by tests/docs/CHANGELOG hits. In the first 8 calls, 364 of 980 listed files (37%) were source files.
7. The model reads directories as files (fastapi/_compat.py is a package).
8. Multi-file tasks never resolve (0 of 23).

## 9. Not yet done
- Count of `read_file` range failures across all traces.
- Call index of first source edit vs first repro script for all 76 traces.
- Confirming the `embed()` cache-lookup cause by running the graph tool.
- No fixes implemented.

