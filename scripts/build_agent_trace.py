"""Write a prompt-review trace next to each model_trace.json.

Keeps the system prompt, the task text, each turn's reasoning, tool call,
and tool result, plus any history the harness replaced. Drops sampling
settings, ids, and duplicate argument copies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _text(message: dict) -> str:
    content = message.get('content')
    if isinstance(content, str):
        return content
    return '' if content is None else json.dumps(content)


def agent_trace(model_trace: dict) -> dict:
    shared = model_trace.get('shared_request') or {}
    prefix = shared.get('message_prefix') or []
    system = next((_text(m) for m in prefix if m.get('role') == 'system'), '')
    task = next((_text(m) for m in prefix if m.get('role') == 'user'), '')
    result = (model_trace.get('final') or {}).get('result') or {}
    turns = []
    replaced = []
    for turn in model_trace.get('turns') or []:
        output = turn.get('output') or {}
        calls = []
        for call in output.get('tool_calls') or []:
            calls.append({
                'name': call.get('name'),
                'arguments': call.get('arguments') if call.get('arguments') is not None else call.get('arguments_raw_json'),
            })
        results = []
        for item in turn.get('tool_results') or []:
            results.append({
                'name': item.get('name'),
                'output': item.get('output') if item.get('output') is not None else item.get('output_raw_json'),
            })
        entry = {'turn': turn.get('turn'), 'caller': turn.get('caller') or 'main'}
        if entry['caller'] != 'main':
            history = (turn.get('input') or {}).get('message_history') or {}
            items = history.get('items') or []
            sub_system = next((_text(m) for m in items if m.get('role') == 'system'), '')
            sub_task = next((_text(m) for m in items if m.get('role') == 'user'), '')
            if sub_system:
                entry['system'] = sub_system
            if sub_task:
                entry['task'] = sub_task
        if output.get('reasoning'):
            entry['reasoning'] = output['reasoning']
        if output.get('assistant_content'):
            entry['reply'] = output['assistant_content']
        if calls:
            entry['calls'] = calls
        if results:
            entry['results'] = results
        turns.append(entry)
        history = (turn.get('input') or {}).get('message_history') or {}
        items = history.get('items') or []
        if history.get('mode') == 'snapshot' and items:
            replaced.append({
                'before_turn': turn.get('turn'),
                'messages': [{'role': m.get('role'), 'content': _text(m)} for m in items],
            })
    return {
        'task_id': (model_trace.get('run') or {}).get('task_id') or result.get('task_id'),
        'resolved': result.get('resolved'),
        'system': system,
        'task': task,
        'turns': turns,
        'replaced_history': replaced,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path, help='Directory to scan for model_trace.json files')
    args = parser.parse_args()
    written = 0
    for path in sorted(args.root.rglob('model_trace.json')):
        trace = json.loads(path.read_text(encoding='utf-8'))
        if trace.get('schema_version') != 'vllm-model-trace-v3':
            continue
        dest = path.with_name('agent_trace.json')
        dest.write_text(json.dumps(agent_trace(trace), indent=2) + '\n', encoding='utf-8')
        written += 1
    print(f'wrote {written} agent traces under {args.root}')


if __name__ == '__main__':
    main()
