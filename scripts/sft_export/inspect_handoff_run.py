#!/usr/bin/env python3
"""Print what a handoff run recorded, to check the risks listed in the E1 plan."""
import json
import sys
from pathlib import Path


def main(task_dir: Path) -> None:
    handoff = json.loads((task_dir / 'handoff.json').read_text())
    teacher = json.loads((task_dir / 'anthropic_exchanges.json').read_text())
    student = json.loads((task_dir / 'vllm_exchanges.json').read_text())
    result = json.loads(next((task_dir / 'results').glob('*.json')).read_text())
    print('handoff:', json.dumps({k: handoff[k] for k in (
        'switch_after', 'student_calls', 'teacher_calls', 'summary_calls_before_handoff',
        'teacher_thinking', 'sandbox')}))
    print('resolved:', result.get('resolved'), 'error:', result.get('error') or result.get('error_message'))
    statuses = [e.get('response_status') for e in teacher]
    print('teacher exchanges:', len(teacher), 'statuses:', sorted(set(statuses)))
    bad = [e for e in teacher if e.get('response_status') != 200]
    if bad:
        print('FIRST TEACHER ERROR:', json.dumps(bad[0].get('response'))[:600])
    if teacher:
        history = teacher[0]['request']['messages']
        ids = [b['id'] for m in history if m['role'] == 'assistant' and isinstance(m['content'], list)
               for b in m['content'] if b.get('type') == 'tool_use']
        kinds = sorted({b.get('type') for m in history if m['role'] == 'assistant'
                        and isinstance(m['content'], list) for b in m['content']})
        print('student tool_use ids in the first teacher request:', ids[:3], '...', len(ids), 'total')
        print('assistant block types in that history:', kinds)
        print('thinking requested:', teacher[0]['request'].get('thinking'))
    tokens = {'input': 0, 'output': 0}
    for e in teacher:
        usage = (e.get('response') or {}).get('usage') or {}
        tokens['input'] += usage.get('input_tokens', 0)
        tokens['output'] += usage.get('output_tokens', 0)
    print('teacher tokens:', tokens, '| student exchanges:', len(student))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
