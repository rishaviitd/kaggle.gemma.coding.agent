"""Filters for OEC trajectories (repetition rules from arXiv 2512.14895, handoff validity)."""

from __future__ import annotations

import json
import re

READ_COMMAND = re.compile(r'^\s*(cat|head|tail|nl|less|more|bat)\b|^\s*sed\s+-n\b', re.IGNORECASE)


def tool_calls_of(messages):
    return [call for message in messages if message.get('role') == 'assistant'
            for call in message.get('tool_calls') or []]


def _arguments(call):
    arguments = call['function']['arguments']
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return {'_raw': arguments}
    return arguments if isinstance(arguments, dict) else {'_raw': arguments}


def _is_file_read(call):
    name = call['function']['name']
    if name == 'read_file':
        return True
    return name == 'run_command' and bool(READ_COMMAND.match(str(_arguments(call).get('command', ''))))


def repetition_reason(messages, max_identical=3, max_reads=20):
    """Return why a trajectory is rejected for repetition, or None.

    Rejected: `max_identical` identical consecutive actions (arguments included) or
    `max_reads` consecutive file-reading commands.
    """
    identical, reads, previous = 0, 0, None
    for call in tool_calls_of(messages):
        key = json.dumps({'name': call['function']['name'], 'arguments': _arguments(call)}, sort_keys=True)
        identical = identical + 1 if key == previous else 1
        previous = key
        if identical >= max_identical:
            return f"{max_identical} identical consecutive actions: {call['function']['name']}"
        reads = reads + 1 if _is_file_read(call) else 0
        if reads >= max_reads:
            return f'{max_reads} consecutive file-reading commands'
    return None


def handoff_problem(handoff, result):
    """Return why a handoff run cannot be used, or None."""
    if result.get('resolved') is not True:
        return 'not resolved'
    if result.get('error') or result.get('error_message'):
        return 'run error'
    if handoff.get('teacher_calls', 0) < 1:
        return 'no teacher call after the handoff (student finished first)'
    if handoff.get('summary_calls_before_handoff', 0) > 0:
        return 'context compaction before the handoff changed the student prefix'
    return None
