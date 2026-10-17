import copy
import json
from pathlib import Path
import pandas as pd
import pytest
from agent_eval import *
from agent_eval.events import parse_patch,command_category
from agent_eval.metrics import paired_outcomes
from agent_eval.metrics.outcomes import outcome_summary
from conftest import turn,call,result


def val(m,key):return m.metrics[m.metrics.metric_id.eq(key)].iloc[0]


def test_outcome_coverage():
    s=outcome_summary(pd.DataFrame({'resolved_nullable':[True,False,None]}))
    assert s['resolved_rate']==.5 and s['known_outcomes']==2 and s['outcome_coverage']==2/3
    assert s['wilson_95'][0]<.5<s['wilson_95'][1]
    assert outcome_summary(pd.DataFrame({'resolved_nullable':[None]}))['resolved_rate'] is None


def test_sample_metrics_findings():
    e=load_experiment(Path(__file__).parents[1]/'fixtures',trace_globs=['sample_model_trace.json']);m=compute_metrics(e);f=classify_failures(e,m)
    assert val(m,'regular_agent_llm_calls').value==29
    assert val(m,'C08').value==False and val(m,'C09').value=='pre_verification'
    assert val(m,'C04').availability=='unavailable' and val(m,'C04').value is None
    assert {'budget','verification_gap','path_restriction','command_failure','late_implementation'}<=set(f.findings.category)
    assert f.primary.iloc[0].primary_stage=='budget'
    assert all(ids for ids in m.metrics.evidence_ids) and all(ids for ids in f.findings.evidence_ids)
    assert set(sum(m.metrics.evidence_ids.tolist(),[]))<=set(e.evidence.evidence_id)
    assert val(m,'completion_tokens').value==4899


def test_tests_ok_status_nonzero_exit(trace,make_trace,tmp_path):
    trace['turns']=[turn(calls=[call('a','run_command',{'command':'pytest tests/test_x.py'})],results=[result('a',exit_code=1,stdout='1 failed')])]
    make_trace(trace);e=load_experiment(tmp_path)
    assert e.tool_results.iloc[0].status=='ok' and e.test_evidence.iloc[0].exit_code==1 and e.test_evidence.iloc[0].parsed_failed==1


def test_edit_test_boundary_and_pipeline(trace,make_trace,tmp_path):
    edit={'filepath':'lib.py','old_string':'x','new_string':'y'}
    trace['turns']=[turn(1,[call('a','edit_file',edit)],[result('a')]),turn(2,[call('b','run_command',{'command':'pytest tests/x.py | tail -25'})],[result('b',exit_code=0,stdout='1 failed')])]
    make_trace(trace);e=load_experiment(tmp_path);m=compute_metrics(e)
    assert val(m,'C08').value==True
    agent=e.test_evidence[e.test_evidence.source=='agent-side'].iloc[0]
    assert agent.post_last_edit and agent.pipeline_status_uncertain and agent.parse_confidence=='low'


def test_empty_patch_and_duration_missing(trace,make_trace,tmp_path):
    trace['final']['result'].pop('duration_seconds');make_trace(trace);e=load_experiment(tmp_path);m=compute_metrics(e)
    assert val(m,'C03').value==False and val(m,'C10').availability=='unavailable'
    assert val(m,'C07').availability=='unavailable'


def test_patch_validation():
    assert parse_patch('--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x\n+y\n')['diff_parse_ok']
    assert not parse_patch('--- a/x.py\n+++ b/x.py\n@@ -1,3 +1 @@\n-x\n+y\n')['diff_parse_ok']
    assert not parse_patch('just some text')['diff_parse_ok']

@pytest.mark.parametrize('cmd',['pytest x','python3 /tmp/repro.py','python -m unittest tests','python -c "assert 1"'])
def test_command_test_detection(cmd):assert command_category(cmd)=='test'


def test_pairing_duplicate_unknown(trace,make_trace,tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';make_trace(trace,directory=a);trace['final']['result']['resolved']=True;make_trace(trace,directory=b)
    left=load_experiment(a);right=load_experiment(b,experiment_id='compare')
    assert paired_outcomes(left,right).iloc[0].transition=='failed→solved'
    make_trace(trace,directory=b,name='trace_duplicate.json')
    assert paired_outcomes(left,load_experiment(b)).iloc[0].unavailable_reason=='duplicate_attempts'


def test_determinism(trace,make_trace,tmp_path):
    make_trace(trace)
    e=load_experiment(tmp_path);a=compute_metrics(e);fa=classify_failures(e,a);b=compute_metrics(load_experiment(tmp_path));fb=classify_failures(e,b)
    pd.testing.assert_frame_equal(a.metrics,b.metrics);pd.testing.assert_frame_equal(fa.findings,fb.findings)


def test_gold_with_python_map(trace,make_trace,tmp_path):
    repo=tmp_path/'repo';repo.mkdir();(repo/'lib.py').write_text('def target():\n    return 1\n')
    gold=tmp_path/'gold';gold.mkdir();patch='--- a/lib.py\n+++ b/lib.py\n@@ -1,2 +1,2 @@\n def target():\n-    return 1\n+    return 2\n'
    (gold/'demo_1.patch').write_text(patch)
    trace['final']['result']['agent_patch']=patch
    trace['turns']=[turn(1,[call('a','run_command',{'command':'cat lib.py'})],[result('a',stdout='def target():\n    return 1')])]
    trace['final']['result']['repo']='demo';make_trace(trace)
    e=load_experiment(tmp_path,trace_globs=['model_trace.json']);m=compute_metrics(e,gold_dir=gold,repo_roots={'demo':repo})
    assert val(m,'C05').value==1 and val(m,'C04').value==0 # shell read has no explicit range map
    wrong=tmp_path/'wrong';wrong.mkdir();(wrong/'lib.py').write_text('def target():\n    return 99\n')
    task_roots=compute_metrics(e,gold_dir=gold,repo_roots={'demo':wrong,'demo_1':repo})
    assert val(task_roots,'C05').value==1 # task-specific commit overrides repository fallback
    assert compute_metrics(e,gold_dir=gold).metrics.query("metric_id=='C04'").iloc[0].availability=='unavailable'


def test_deleted_file_patch():
    patch='--- a/lib.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def f():\n-    pass\n'
    p=parse_patch(patch)
    assert p['diff_parse_ok'] and p['modified_files']==['lib.py'] and p['deleted_lines']==2


def test_gold_rejects_wrong_baseline(trace,make_trace,tmp_path):
    repo=tmp_path/'repo';repo.mkdir();(repo/'lib.py').write_text('def target():\n    return 99\n')
    gold=tmp_path/'gold';gold.mkdir();patch='--- a/lib.py\n+++ b/lib.py\n@@ -1,2 +1,2 @@\n def target():\n-    return 1\n+    return 2\n'
    (gold/'demo_1.patch').write_text(patch);trace['final']['result']['repo']='demo';make_trace(trace)
    m=compute_metrics(load_experiment(tmp_path,trace_globs=['model_trace.json']),gold_dir=gold,repo_roots={'demo':repo})
    assert val(m,'C04').availability=='unavailable' and 'does not match' in val(m,'C04').unavailable_reason
