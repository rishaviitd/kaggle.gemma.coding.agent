import copy
import json
from pathlib import Path
import pytest
from agent_eval import load_experiment
from agent_eval.evidence import read_evidence
from conftest import turn,call,result


def test_golden_sample():
    e=load_experiment(Path(__file__).parents[1]/'fixtures',trace_globs=['sample_model_trace.json'])
    assert len(e.runs)==1
    r=e.runs.iloc[0]
    assert r.task_id=='fastapi_5624' and r.schema_version=='vllm-model-trace-v1'
    assert (len(e.turns),len(e.tool_calls),len(e.tool_results),r.budgeted_tool_calls,r.reported_llm_calls)==(30,28,27,25,29)
    assert r.resolved_nullable==False and r.harness_status=='SUCCESS' and r.test_exit_code==1
    assert e.turns[e.turns.is_compaction].turn_number.tolist()==[11]
    assert e.tool_calls[~e.tool_calls.result_linked].turn_number.tolist()==[10]
    assert {'FileWriteError','CommandError','BudgetExceeded'}<=set(e.tool_results.error_type)
    edits=e.tool_calls[e.tool_calls.name=='edit_file'].merge(e.tool_results,on=['run_id','call_id'])
    assert edits.status.tolist()==['ok','error']
    assert e.patches.iloc[0].modified_files==['fastapi/dependencies/utils.py']
    assert e.patches.iloc[0].diff_parse_ok and e.patches.iloc[0].apply_status=='not_verified'
    assert r.prompt_budget_claims==[25,40] and r.harness_budget==25
    assert e.turns.completion_tokens.sum()>0 and r.reported_total_tokens==0
    assert 'token_total_mismatch' in set(e.quality_issues.issue_type)
    assert not e.test_evidence[e.test_evidence.source=='agent-side'].post_last_edit.any()


def test_malformed_isolated(make_trace,tmp_path):
    make_trace();(tmp_path/'trace_bad.json').write_text('{')
    e=load_experiment(tmp_path)
    assert len(e.runs)==1 and len(e.intake)==2 and e.intake.accepted.sum()==1


def test_missing_final_unknown(trace,make_trace,tmp_path):
    del trace['final'];make_trace(trace);e=load_experiment(tmp_path)
    assert e.runs.iloc[0].resolved_nullable is None


def test_recover_task_id(trace,make_trace,tmp_path):
    del trace['run'];make_trace(trace)
    assert load_experiment(tmp_path).runs.iloc[0].task_id=='demo_1'


def test_multicall_and_resultless(trace,make_trace,tmp_path):
    trace['turns']=[turn(calls=[call('a','run_command',{'command':'pytest'}),call('b','run_command',{'command':'cat x'})],results=[result('b',stdout='x')])]
    make_trace(trace);e=load_experiment(tmp_path)
    assert len(e.tool_calls)==2 and e.tool_calls.result_linked.tolist()==[False,True]
    assert e.events[e.events.evidence_id.eq(e.tool_calls.iloc[0].evidence_id)].status.iloc[0]=='unknown'

@pytest.mark.parametrize('raw,args,expected',[('bad',{'command':'x'},'parse_error'),('{"command":"other"}',{'command':'x'},'raw_parsed_discrepancy'),('{"wrong":"x"}',{'wrong':'x'},'required'),('{"command":4}',{'command':4},'type'),('{"command":"x","extra":true}',{'command':'x','extra':True},'unknown_key')])
def test_schema_issues(trace,make_trace,tmp_path,raw,args,expected):
    trace['turns']=[turn(calls=[call('a','run_command',args,raw)])];make_trace(trace);e=load_experiment(tmp_path)
    assert e.tool_calls.iloc[0].schema_valid==False
    assert any(expected in s for s in e.tool_calls.iloc[0].schema_issues)


def test_duplicate_tasks_retained(trace,make_trace,tmp_path):
    make_trace(name='trace_a.json');make_trace(name='trace_b.json');e=load_experiment(tmp_path)
    assert len(e.runs)==2 and e.runs.run_id.nunique()==2 and e.runs.ambiguous_pairing.all()
    assert e.runs.attempt_id.tolist()==[1,2]


def test_unknown_schema(trace,make_trace,tmp_path):
    trace['schema_version']='future-v2';make_trace(trace)
    assert len(load_experiment(tmp_path).runs)==1
    assert len(load_experiment(tmp_path,strict_schema=True).runs)==0


def test_task_mismatch(trace,make_trace,tmp_path):
    trace['final']['result']['task_id']='other';make_trace(trace)
    assert 'task_id_mismatch' in set(load_experiment(tmp_path).quality_issues.issue_type)


def test_excludes_reports_and_escape_patterns(make_trace,tmp_path):
    make_trace();make_trace(directory=tmp_path/'reports'/'old');make_trace(directory=tmp_path/'.cache')
    assert len(load_experiment(tmp_path).runs)==1
    with pytest.raises(ValueError):load_experiment(tmp_path,trace_globs=['../**/*.json'])


def test_bad_turn_rolls_back(trace,make_trace,tmp_path):
    trace['turns']=[None,turn()];make_trace(trace);e=load_experiment(tmp_path)
    assert len(e.runs)==1 and 'invalid_turn' in set(e.quality_issues.issue_type)


def test_lazy_redaction_and_reasoning(trace,make_trace,tmp_path):
    t=turn();t['output']['assistant_content']='api_key=sk-abcdefghijklmnopqrstuvwxyz';t['output']['reasoning']='PRIVATE_REASON';trace['turns']=[t];make_trace(trace)
    e=load_experiment(tmp_path);eid=e.turns.iloc[0].evidence_id
    text=read_evidence(e,eid)
    assert 'PRIVATE_REASON' not in text and 'sk-abcdefghijklmnopqrstuvwxyz' not in text and '[REDACTED]' in text


def test_changed_evidence_rejected(trace,make_trace,tmp_path):
    p=make_trace();e=load_experiment(tmp_path);p.write_text('{}')
    with pytest.raises(ValueError,match='changed'):read_evidence(e,e.evidence.iloc[0].evidence_id)


def test_missing_timestamp_and_budget(trace,make_trace,tmp_path):
    t=turn(messages=[{'role':'system','content':'You have only 40 tool calls.'},{'role':'user','content':'Tool calls allowance: 25 calls'}]);del t['output']['vllm_response_raw']['created'];trace['turns']=[t];make_trace(trace);e=load_experiment(tmp_path)
    assert {'missing_timestamps','budget_instruction_conflict'}<=set(e.quality_issues.issue_type)


def test_invalid_numerics_are_unknown(trace,make_trace,tmp_path):
    from agent_eval import compute_metrics
    trace['final']['result']['duration_seconds']='bad'
    trace['final']['result']['tool_calls']='25'
    t=turn();t['output']['vllm_response_raw']['usage']['completion_tokens']='invalid';trace['turns']=[t]
    make_trace(trace);e=load_experiment(tmp_path);m=compute_metrics(e)
    assert len(e.runs)==1
    assert 'invalid_numeric' in set(e.quality_issues.issue_type)
    assert m.metrics[m.metrics.metric_id.eq('C10')].iloc[0].availability=='unavailable'


def test_missing_final_source_pointer_is_readable(trace,make_trace,tmp_path):
    del trace['final'];make_trace(trace);e=load_experiment(tmp_path)
    assert 'demo_1' in read_evidence(e,e.evidence.iloc[0].evidence_id)
