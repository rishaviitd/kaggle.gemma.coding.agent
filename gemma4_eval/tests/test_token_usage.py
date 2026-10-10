import json

from agent_eval import compute_metrics, load_experiment
from agent_eval.evidence import read_evidence
from conftest import call, turn


def companion(folder, steps, total_prompt, total_completion):
    path = folder / 'traces' / 'trace_demo_1.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'schema_version': 'ATIF-v1.7', 'agent': {'name': 'example'},
        'steps': [{'step_id': 1, 'source': 'system', 'message': 'system'},
                  {'step_id': 2, 'source': 'user', 'message': 'issue'}, *steps],
        'final_metrics': {'total_prompt_tokens': total_prompt,
                          'total_completion_tokens': total_completion,
                          'total_tokens': total_prompt + total_completion},
    }))
    return path


def agent_step(step_id, prompt, completion, tools=None):
    return {'step_id': step_id, 'source': 'agent', 'message': 'working',
            'tool_calls': tools or [],
            'metrics': {'prompt_tokens': prompt, 'completion_tokens': completion,
                        'cached_tokens': 0, 'total_tokens': prompt + completion}}


def test_atif_companion_enriches_and_links_source(trace, make_trace, tmp_path):
    trace['turns'] = [turn(1), turn(2)]
    for item in trace['turns']:
        item['output']['vllm_response_raw'].pop('usage')
    folder = tmp_path / 'demo_1'
    make_trace(trace, directory=folder)
    sidecar = companion(folder, [agent_step(3, 100, 20), agent_step(4, 150, 30)], 250, 50)
    e = load_experiment(tmp_path)
    assert len(e.runs) == 1 and len(e.intake) == 1  # ATIF is a sidecar, not a second attempt.
    assert e.turns.prompt_tokens.tolist() == [100, 150]
    assert e.turns.completion_tokens.tolist() == [20, 30]
    assert e.turns.usage_source.tolist() == ['atif_companion', 'atif_companion']
    evidence_id = e.turns.iloc[0].usage_evidence_id
    evidence = e.evidence.set_index('evidence_id').loc[evidence_id]
    assert evidence.trace_path == str(sidecar)
    assert evidence.json_pointer == '/steps/2/metrics'
    assert '100' in read_evidence(e, evidence_id)
    m = compute_metrics(e)
    assert m.metrics.query("metric_id == 'prompt_tokens'").iloc[0].value == 250
    sidecar.write_text(sidecar.read_text().replace('100', '101'))
    try:
        read_evidence(e, evidence_id)
    except ValueError as exc:
        assert 'changed' in str(exc)
    else:
        raise AssertionError('Changed companion was accepted as evidence')


def test_atif_split_tool_step_and_partial_coverage(trace, make_trace, tmp_path):
    tool = call('a', 'run_command', {'command': 'pytest test_one.py'})
    trace['turns'] = [turn(1, calls=[tool]), turn(2)]
    for item in trace['turns']:
        item['output']['vllm_response_raw'].pop('usage')
    folder = tmp_path / 'demo_1'
    make_trace(trace, directory=folder)
    companion(folder, [agent_step(3, 80, 10),
                       {'step_id': 4, 'source': 'agent', 'message': None,
                        'tool_calls': [{'function_name': 'run_command',
                                        'arguments': {'command': 'pytest test_one.py'}}]}], 80, 10)
    e = load_experiment(tmp_path)
    assert e.turns.prompt_tokens.tolist()[0] == 80
    assert e.turns.prompt_tokens.isna().sum() == 1
    assert 'token_usage_partial' in set(e.quality_issues.issue_type)


def test_mismatched_atif_never_assigns_tokens(trace, make_trace, tmp_path):
    trace['turns'] = [turn(1, calls=[call('a', 'run_command', {'command': 'pytest correct.py'})])]
    trace['turns'][0]['output']['vllm_response_raw'].pop('usage')
    folder = tmp_path / 'demo_1'
    make_trace(trace, directory=folder)
    companion(folder, [agent_step(3, 80, 10, [{'function_name': 'run_command',
                                                 'arguments': {'command': 'pytest wrong.py'}}])], 80, 10)
    e = load_experiment(tmp_path)
    assert e.turns.prompt_tokens.isna().all()
    assert 'token_companion_invalid' in set(e.quality_issues.issue_type)
