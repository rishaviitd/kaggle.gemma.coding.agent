"""Restore website-readable outputs from recorded ATIF events and provider audits."""
import copy
import json
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'scripts/build_agent_trace.py').exists())
sys.path.insert(0, str(ROOT / 'scripts'))
from build_agent_trace import agent_trace

for model in ('gpt-6-luna', 'gpt-6.1-sol', 'gpt-6-astra'):
    directory = ROOT / 'logs/remote/rishav/itr-1' / f'openai20-newkey-20261016-{model}/val'
    tasks = list(directory.iterdir())
    assert len(tasks) == 20
    for task in tasks:
        path = task / 'model_trace.json'
        capture = task / 'model_trace_capture.json'
        if not capture.exists():
            capture.write_bytes(path.read_bytes())
        original = json.loads(capture.read_text())
        recorded = json.loads(next((task / 'traces').glob('*.json')).read_text())
        audits = json.loads((task / 'provider_audit.json').read_text())
        assert len(audits) == len(original['turns'])
        trace = copy.deepcopy(original)
        trace['schema_version'] = 'openai-atif-view-v1'
        trace['recording_note'] = ('Turns below are recorded ATIF agent events, not necessarily one event per API call. '
                                   'Matched request payloads include derived Chat Completions messages for display. '
                                   'Original per-call capture: model_trace_capture.json; usage and summaries: provider_audit.json.')
        prefix = [{'role': s['source'], 'content': s.get('message', '')}
                  for s in recorded['steps'] if s['source'] in ('system', 'user')][:2]
        trace['shared_request']['message_prefix'] = prefix
        trace['turns'] = []
        for step in recorded['steps']:
            if step['source'] != 'agent':
                continue
            metrics = step.get('metrics') or {}
            match = next((i for i, a in enumerate(audits)
                          if (a.get('usage') or {}).get('input_tokens') == metrics.get('prompt_tokens')
                          and (a.get('usage') or {}).get('output_tokens') == metrics.get('completion_tokens')
                          and metrics.get('prompt_tokens') is not None), None)
            audit = audits[match] if match is not None else {}
            request = copy.deepcopy(original['turns'][match]['input']['vllm_request']) if match is not None else {
                'recording_note': 'No per-call request matched this ATIF event; inspect the original capture.'}
            if request.pop('uses_shared_tools', False):
                request['tools'] = original['shared_request']['tools']
            request.pop('uses_shared_message_prefix', None)
            messages = [{'role': 'system', 'content': request['instructions']}] if request.get('instructions') else []
            for item in request.get('input', []):
                if item.get('type') == 'message' or 'role' in item:
                    messages.append({'role': item.get('role', 'user'), 'content': item.get('content', '')})
                elif item.get('type') == 'function_call':
                    messages.append({'role': 'assistant', 'tool_calls': [{'id': item['call_id'], 'function': {
                        'name': item['name'], 'arguments': item['arguments']}}]})
                elif item.get('type') == 'function_call_output':
                    messages.append({'role': 'tool', 'tool_call_id': item['call_id'], 'content': item.get('output')})
            request['messages'] = messages
            summaries = [part['text'] for item in audit.get('reasoning_items', [])
                         for part in item.get('summary', []) if part.get('text')]
            thinking = (step.get('extra') or {}).get('event_type') == 'thinking'
            calls = [{'id': c['tool_call_id'], 'name': c['function_name'], 'arguments': c['arguments'],
                      'arguments_raw_json': json.dumps(c['arguments'])} for c in step.get('tool_calls', [])]
            output = {'response_status': audit.get('http_status'), 'reasoning': '\n\n'.join(summaries) or
                      (step.get('message') if thinking else None),
                      'assistant_content': step.get('message') if not thinking else None,
                      'tool_calls': calls, 'usage': audit.get('usage') or metrics}
            observation = step.get('observation') or {}
            results = [{'output': observation['content']}] if 'content' in observation else []
            trace['turns'].append({'turn': len(trace['turns']) + 1, 'caller': 'main', 'source_step_id': step['step_id'],
                                   'input': {'vllm_request': request}, 'output': output, 'tool_results': results})
        assert sum(len(t['output']['tool_calls']) for t in trace['turns']) == sum(len(s.get('tool_calls', [])) for s in recorded['steps'])
        path.write_text(json.dumps(trace, indent=2) + '\n')
        (task / 'agent_trace.json').write_text(json.dumps(agent_trace(trace), indent=2) + '\n')
    print(model, 'prepared', len(tasks), 'traces')
