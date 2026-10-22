"""Report tool-result differences between a recorded snapshot and a container replay.

Non-raising, reportable counterpart to `verify_replay` in `replay_llm.py`. Run B already
enforces parity inside the pipeline; this compares two recorded event dumps after the fact.

Usage:
    python scripts/runs/check_container_parity.py <snapshot_events.json> <container_events.json> [...pairs]

Each argument pair is two JSON files, each holding a list of ADK event dicts.
- snapshot_events.json: the `session_events.json` from a handoff snapshot (the recorded run).
- container_events.json: the events dump from a `RecordingContext` (scripts/runs/task_pipeline.py)
  taken during a run-B-style replay inside the teacher container.

Output per pair: `PASS <snapshot> <container>`, or `FAIL <snapshot> <container>` followed by
one indented line per mismatch. Timing keys are dropped at the top level only (see replay_llm).

Exit codes: 0 all pairs pass, 1 any mismatch, 2 unreadable or invalid input files or bad arguments.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_llm import tool_results  # noqa: E402


def compare_tool_results(snapshot_events: list[dict], container_events: list[dict]) -> list[str]:
    """Return one description per mismatch; an empty list means parity.

    Both lists are treated as complete. Container results beyond the snapshot are allowed
    (the live run may continue past the recording), but a shorter container list is reported.
    """
    expected, actual = tool_results(snapshot_events), tool_results(container_events)
    mismatches = []
    for index, want in enumerate(expected[:len(actual)]):
        got = actual[index]
        if want != got:
            mismatches.append(
                f'tool result {index} ({want["name"]}) differs: '
                f'snapshot={want["response"]!r} container={got["response"]!r}')
    if len(actual) < len(expected):
        mismatches.append(
            f'container is shorter than snapshot: {len(actual)} tool results vs {len(expected)}')
    return mismatches


def _load_events(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list) or not all(isinstance(event, dict) for event in data):
        raise ValueError(f'{path}: expected a JSON list of event objects')
    tool_results(data)  # fail here, not mid-comparison, on malformed function_response parts
    return data


def main(argv: list[str]) -> int:
    if not argv or len(argv) % 2:
        print('usage: check_container_parity.py <snapshot_events.json> <container_events.json> [...pairs]',
              file=sys.stderr)
        return 2
    pairs = list(zip(argv[::2], argv[1::2]))
    try:
        loaded = [(snap, cont, _load_events(snap), _load_events(cont)) for snap, cont in pairs]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2
    failed = False
    for snap, cont, snapshot_events, container_events in loaded:
        mismatches = compare_tool_results(snapshot_events, container_events)
        if mismatches:
            failed = True
            print(f'FAIL {snap} {cont}')
            for line in mismatches:
                print(f'  {line}')
        else:
            print(f'PASS {snap} {cont}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
