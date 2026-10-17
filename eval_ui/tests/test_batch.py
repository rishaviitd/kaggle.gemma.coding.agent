import copy
from pathlib import Path
import pandas as pd
from agent_eval import load_experiment, compute_metrics, classify_failures, export_report, summarize_batch
from agent_eval.plots.batch import build_batch_figures


def batch_for(root):
    e=load_experiment(root)
    m=compute_metrics(e)
    f=classify_failures(e,m)
    return e,m,f,summarize_batch(e,m,f)


def test_reference_batch_rollup():
    e,m,f,b=batch_for('/Users/akshay/kaggle/wt/main/logs/remote/akshay/itr-1')
    assert b.overview['total_runs']==76
    assert b.overview['known_outcomes']==76
    assert b.overview['resolved']==20
    assert b.overview['resolved_rate']==20/76
    assert b.repositories.attempts.sum()==76
    assert b.repositories.resolved.sum()==20
    w=b.workflow.set_index('indicator')
    assert w.loc['Confirmed source edit','numerator']==49
    assert w.loc['Agent test after final confirmed edit','denominator']==49
    assert w.loc['Agent test after final confirmed edit','numerator']==42
    assert b.budget.attempts.sum()==76
    assert b.submission['attempts']==1707
    assert b.submission['attempts_with_50_plus']==5
    assert b.tool_reliability.attempts.sum()==len(e.tool_calls)
    assert b.tool_reliability.unknown_result.sum()>=len(e.tool_calls)-len(e.tool_results)
    assert len(b.tasks)==76 and b.tasks.run_id.nunique()==76
    assert b.tasks[~b.tasks.outcome.eq('unknown')].shape[0]==76
    assert all(Path(p).exists() for p in b.tasks.trace_path)
    assert set(build_batch_figures(b))=={'outcomes','repositories','workflow','first_edit','budget','submission','tools','stages','cost','quality'}


def test_unknown_outcome_denominator_and_missing_edit(trace,make_trace,tmp_path):
    first=copy.deepcopy(trace)
    first['final']['result'].pop('resolved')
    make_trace(first,directory=tmp_path/'a')
    second=copy.deepcopy(trace)
    second['run']['task_id']=second['final']['result']['task_id']='demo_2'
    second['final']['result']['resolved']=True
    make_trace(second,directory=tmp_path/'b')
    e,m,f,b=batch_for(tmp_path)
    assert b.overview['known_outcomes']==1 and b.overview['unknown_outcomes']==1
    resolved=b.workflow[b.workflow.indicator.eq('Officially resolved')].iloc[0]
    assert resolved.numerator==1 and resolved.denominator==1
    post=b.workflow[b.workflow.indicator.eq('Agent test after final confirmed edit')].iloc[0]
    assert post.denominator==0 and pd.isna(post.rate)
    assert b.tasks.outcome.tolist()==['unknown','resolved']
    dest=export_report(e,m,f,tmp_path/'reports')
    assert (dest/'batch_report.html').exists()
    assert (dest/'batch_summary.json').exists()
    assert (dest/'batch_tasks.csv').exists()
    html=(dest/'batch_report.html').read_text()
    assert 'Whole-batch report' in html and 'report.html?task=' in html
    assert 'plotly' in html
    assert len(load_experiment(tmp_path).runs)==2


def test_empty_batch(tmp_path):
    e,m,f,b=batch_for(tmp_path)
    assert b.overview['resolved_rate'] is None
    assert b.workflow.denominator.tolist()==[0]*5
    assert b.submission['attempts']==0
    assert build_batch_figures(b)
    export_report(e,m,f,tmp_path/'reports')


def test_notebook_generates_batch_report():
    import nbformat
    root=Path(__file__).parents[1]
    nb=nbformat.read(root/'notebooks/01_agent_trace_report.ipynb',as_version=4)
    source='\n'.join(c.source for c in nb.cells)
    assert 'render_report(experiment, metrics, findings)' in source
    assert 'render_batch_report(batch)' in source
    assert 'export_report(experiment, metrics, findings' in source


def test_reports_have_distinct_scopes():
    root=Path(__file__).parents[1]/'src/agent_eval/plots'
    single=(root/'report_template.html').read_text()
    batch=(root/'batch_template.html').read_text()
    assert 'id="timeline"' in single and 'id="outcomesChart"' not in single
    assert 'id="outcomesChart"' in batch and 'id="workflowChart"' in batch
    assert 'id="goldTable"' in batch and 'id="pairTable"' in batch
    assert 'report.html?task=' in batch
