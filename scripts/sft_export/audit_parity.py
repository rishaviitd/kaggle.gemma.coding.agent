#!/usr/bin/env python3
"""Format-parity gate: every source must share system prompt, tools and serialization.

Usage: python scripts/sft_export/audit_parity.py <trajectory dir containing train.jsonl>
Exit status 1 and a problem list if anything differs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def audit(rows):
    problems = []
    systems = {digest(r['messages'][0]['content']) for r in rows if r['messages'][0]['role'] == 'system'}
    if len(systems) != 1:
        problems.append(f'system prompt differs across rows: {sorted(systems)}')
    tool_sets = {digest(r['tools']) for r in rows}
    if len(tool_sets) != 1:
        problems.append(f'tools schema differs across rows: {sorted(tool_sets)}')
    modes = {bool(r.get('enable_thinking', True)) for r in rows}
    if len(modes) != 1:
        problems.append('enable_thinking differs across rows')
    for number, row in enumerate(rows):
        label = f"row {number} ({row.get('kind')}, {row.get('task_id')})"
        names = {t['function']['name'] for t in row['tools']}
        messages = row['messages']
        for index, message in enumerate(messages):
            if message.get('role') != 'assistant' or not message.get('tool_calls'):
                continue
            for call in message['tool_calls']:
                if call['function']['name'] not in names:
                    problems.append(f"{label}: tool {call['function']['name']} not in tools")
                if not isinstance(call['function']['arguments'], dict):
                    problems.append(f'{label}: tool-call arguments are not an object (serialization mismatch)')
            first_of_group = index == 0 or messages[index - 1].get('role') != 'assistant'
            group_start = index if first_of_group else index - 1
            if not (messages[group_start].get('reasoning') or '').strip():
                problems.append(f'{label}: tool-call step at message {index} has no reasoning')
        if row.get('kind') == 'oec':
            assistants = [m for m in messages if m.get('role') == 'assistant']
            if any(m.get('source') == 'student' and m.get('train') for m in assistants):
                problems.append(f'{label}: a student turn is trained (student turn leaked into the loss span)')
            if not any(m.get('source') == 'student' for m in assistants):
                problems.append(f'{label}: OEC row has no student turn marked source=student')
            if not any(m.get('source') == 'teacher' and m.get('train') for m in assistants):
                problems.append(f'{label}: OEC row has no trained teacher turn')
    return problems


def main():
    directory = Path(sys.argv[1])
    rows = [json.loads(l) for l in (directory / 'train.jsonl').read_text().splitlines() if l.strip()]
    problems = audit(rows)
    kinds = {}
    for row in rows:
        kinds[row.get('kind')] = kinds.get(row.get('kind'), 0) + 1
    print(f'{len(rows)} rows {kinds}')
    if problems:
        print(f'{len(problems)} parity problem(s):')
        for problem in problems[:50]:
            print('  -', problem)
        sys.exit(1)
    print('format parity OK')


if __name__ == '__main__':
    main()
