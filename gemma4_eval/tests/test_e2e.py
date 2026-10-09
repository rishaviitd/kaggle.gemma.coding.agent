import copy
import json
from pathlib import Path
import pytest
from agent_eval import *
from agent_eval.plots.charts import build_figures


def test_mixed_batch_export(trace,make_trace,tmp_path):
    for i in range(17):
        d=copy.deepcopy(trace);d['run']['task_id']=d['final']['result']['task_id']=f'demo_{i}';d['final']['result']['resolved']=i%2==0
        make_trace(d,directory=tmp_path/f'task{i}')
    sample=Path(__file__).parents[1]/'fixtures/sample_model_trace.json'
    (tmp_path/'trace_sample.json').write_bytes(sample.read_bytes());(tmp_path/'trace_bad.json').write_text('no')
    e=load_experiment(tmp_path);m=compute_metrics(e);f=classify_failures(e,m)
    assert len(e.runs)==18 and m.summary['rejected']==1
    output=export_report(e,m,f,tmp_path/'reports')
    assert all((output/name).is_file() for name in ['runs.csv','events.jsonl','metrics.csv','findings.csv','quality_issues.csv','summary.json','report.html','manifest.json'])
    html=(output/'report.html').read_text();assert '<script id="reportData"' in html and 'cdn.plot.ly' not in html.split('<script id="reportData"')[1]
    assert len(load_experiment(tmp_path).runs)==18
    for ids in [None,[e.runs.iloc[0].run_id],[]]:
        figs=build_figures(e,m,f,ids)
        assert figs and all(fig.to_json() for fig in figs.values())


def test_reject_source_directory_as_output(make_trace,tmp_path):
    make_trace();e=load_experiment(tmp_path);m=compute_metrics(e);f=classify_failures(e,m)
    with pytest.raises(ValueError):export_report(e,m,f,tmp_path)


def test_empty_batch(tmp_path):
    e=load_experiment(tmp_path);m=compute_metrics(e);f=classify_failures(e,m)
    assert m.summary['resolved_rate'] is None
    assert build_figures(e,m,f)['overview']
    export_report(e,m,f,tmp_path/'reports')
