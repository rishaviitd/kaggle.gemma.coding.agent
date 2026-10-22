"""Turn one student->teacher handoff run into OEC trajectory rows.

The teacher's Anthropic exchanges replay the student prefix inside the first request.
That prefix is normalized by the same code as the teacher's own turns (identical tool-call
serialization) and is marked context-only. The student's native reasoning is recovered from
the last successful vLLM request.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_sft
from oec_filters import handoff_problem, repetition_reason

NO_CLIP = 10**9  # observation policy "none": keep tool observations exactly as returned


class OecRejected(ValueError):
    """The run is valid input but unusable as training data; the message says why."""


def student_reasoning_by_call_id(student_exchanges):
    """Native Gemma reasoning keyed by tool-call id, from the last successful student request."""
    ok = [e for e in student_exchanges if e.get('response_status', 200) == 200]
    if not ok:
        return {}
    last = ok[-1]
    messages = list((last.get('request') or {}).get('messages') or [])
    choices = (last.get('response') or {}).get('choices') or []
    if choices:
        messages.append(choices[0].get('message') or {})
    found = {}
    for message in messages:
        if message.get('role') != 'assistant':
            continue
        reasoning = (message.get('reasoning_content') or message.get('reasoning') or '').strip()
        calls = message.get('tool_calls') or []
        if reasoning and calls:
            found[calls[0]['id']] = reasoning
    return found


def count_student_groups(row):
    """Number of contiguous runs of student (turn 0) assistant messages."""
    groups, previous = 0, False
    for m in row['messages']:
        current = m.get('role') == 'assistant' and m.get('_turn') == 0
        groups += current and not previous
        previous = current
    return groups


def mark_sources(row, segment):
    """Tag every assistant message `source`: 'student' (segment 1, turn 0) or 'teacher'.

    The key has no leading underscore, so export_sft.clip_message keeps it. The audit uses
    it as an explicit boundary instead of inferring one from the train flags.
    """
    for m in row['messages']:
        if m.get('role') == 'assistant':
            m['source'] = 'student' if segment == 1 and m.get('_turn') == 0 else 'teacher'


def attach_student_reasoning(row, by_call_id):
    """Put reasoning on the first assistant message of each student (turn 0) response group."""
    messages, attached, i = row['messages'], 0, 0

    def is_student(index):
        return messages[index].get('role') == 'assistant' and messages[index].get('_turn') == 0

    while i < len(messages):
        if not is_student(i):
            i += 1
            continue
        j = i
        while j < len(messages) and is_student(j):
            j += 1
        call_ids = [c['id'] for m in messages[i:j] for c in m.get('tool_calls') or []]
        reasoning = next((by_call_id[c] for c in call_ids if c in by_call_id), None)
        if reasoning:
            messages[i]['reasoning'] = reasoning
            attached += 1
        i = j
    return attached


def build_oec_examples(task_dir: Path, *, policy='none', max_messages=512):
    handoff = json.loads((task_dir / 'handoff.json').read_text())
    result = json.loads(next((task_dir / 'results').glob('*.json')).read_text())
    problem = handoff_problem(handoff, result)
    if problem:
        raise OecRejected(problem)
    teacher = json.loads((task_dir / 'anthropic_exchanges.json').read_text())
    student = json.loads((task_dir / 'vllm_exchanges.json').read_text())
    try:
        rows = export_sft.convert_task(teacher, retain_reasoning=False, first_context_prefix=True)
    except (ValueError, KeyError, TypeError) as error:
        raise OecRejected(f'cannot convert exchanges: {error}') from error
    problem = handoff_problem(handoff, result, rows[0]['messages'] if rows else ())
    if problem:
        raise OecRejected(problem)
    by_call_id = student_reasoning_by_call_id(student)
    task_id = task_dir.name
    examples = []
    for segment, row in enumerate(rows, 1):
        reason = repetition_reason(row['messages'])
        if reason:
            raise OecRejected(reason)
        mark_sources(row, segment)
        if segment == 1:
            groups = count_student_groups(row)
            attached = attach_student_reasoning(row, by_call_id)
            if attached < groups:
                raise OecRejected(f'student reasoning missing for {groups - attached} of {groups} student turns')
        for example in export_sft.build_examples(
                row, mode='trajectory', task_id=task_id, segment=segment, enable_thinking=True,
                max_messages=max_messages, max_chars=NO_CLIP, observation_policy=policy):
            example['kind'] = 'oec'
            example['handoff'] = handoff
            examples.append(example)
    if not examples:
        raise OecRejected('no supervised teacher turns')
    return examples
