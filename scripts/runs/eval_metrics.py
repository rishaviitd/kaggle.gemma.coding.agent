"""Metrics beyond pass@k for student runs: loops, tool-call validity, steps, recovery."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'sft_export'))
from export_sft import tool_failed
from oec_filters import repetition_reason


def _normalized_calls(messages):
    """OpenAI-format assistant messages -> messages with dict arguments, as the filters expect."""
    out = []
    for message in messages:
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            calls = []
            for call in message['tool_calls']:
                arguments = call['function']['arguments']
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {'_raw': arguments}
                calls.append({**call, 'function': {**call['function'], 'arguments': arguments}})
            out.append({**message, 'tool_calls': calls})
        else:
            out.append(message)
    return out


def run_metrics(task_dir: Path):
    try:
        result = json.loads(next((task_dir / 'results').glob('*.json')).read_text())
        exchanges = json.loads((task_dir / 'vllm_exchanges.json').read_text())
    except (StopIteration, OSError, ValueError):
        return None
    if not isinstance(result.get('resolved'), bool) or result.get('error'):
        return None
    ok = [e for e in exchanges if e.get('response_status', 200) == 200]
    if not ok:
        return None
    last = ok[-1]
    messages = list(last['request'].get('messages') or [])
    choices = (last.get('response') or {}).get('choices') or []
    if choices:
        messages.append(choices[0].get('message') or {})
    messages = _normalized_calls(messages)
    calls = [c for m in messages if m.get('role') == 'assistant' for c in m.get('tool_calls') or []]
    failed = sum(1 for m in messages if m.get('role') == 'tool' and tool_failed(m.get('content')))
    return {'resolved': result['resolved'], 'tool_calls': len(calls), 'failed_results': failed,
            'loop': repetition_reason(messages) is not None}


def summarize(runs):
    total = len(runs)
    calls = sum(r['tool_calls'] for r in runs)
    resolved = [r for r in runs if r['resolved']]
    with_failure = [r for r in runs if r['failed_results'] > 0]
    return {
        'runs': total,
        'pass_at_1': len(resolved) / total if total else 0.0,
        'loop_rate': sum(r['loop'] for r in runs) / total if total else 0.0,
        'tool_call_validity': 1 - sum(r['failed_results'] for r in runs) / calls if calls else 1.0,
        'steps_per_resolved': sum(r['tool_calls'] for r in resolved) / len(resolved) if resolved else 0.0,
        'recovery_rate': (sum(r['resolved'] for r in with_failure) / len(with_failure)
                          if with_failure else 0.0),
        'recovery_runs': len(with_failure),
    }


def pass_at_k(roots):
    """A task passes if any root has a resolved, error-free run of it."""
    outcome = {}
    for root in roots:
        for task_dir in sorted(Path(root).iterdir()):
            metrics = run_metrics(task_dir) if task_dir.is_dir() else None
            if metrics is None:
                continue
            outcome[task_dir.name] = outcome.get(task_dir.name, False) or metrics['resolved']
    return {'tasks': len(outcome), 'passed': sum(outcome.values())}


def main(argv):
    roots = [Path(a) for a in argv]
    runs = []
    for root in roots:
        runs += [r for r in (run_metrics(d) for d in sorted(root.iterdir()) if d.is_dir()) if r]
    print(json.dumps({'per_run': summarize(runs), 'pass_at_k': pass_at_k(roots), 'roots': len(roots)}, indent=2))


if __name__ == '__main__':
    main(sys.argv[1:])
