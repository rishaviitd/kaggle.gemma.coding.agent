"""Reproducible CSV/JSONL tables and an offline, self-contained HTML report."""
import json
import hashlib
from pathlib import Path
import pandas as pd
from .models import COLUMNS


def records(frame):
    return json.loads(frame.to_json(orient='records',date_format='iso'))

def export_report(e,m,f,output_dir,html=True,parquet=False):
    dest=Path(output_dir).expanduser().resolve()
    sources=[Path(p).resolve() for p in e.intake.trace_path]
    if any(dest==p or dest in p.parents for p in sources):
        raise ValueError('Output directory must not contain source traces')
    dest.mkdir(parents=True,exist_ok=True)
    tables={name:getattr(e,name) for name in COLUMNS}
    tables.update(metrics=m.metrics,findings=f.findings,primary_failures=f.primary,pairing=m.pairing,gold=m.gold,modules=m.modules)
    if not m.reviews.empty:
        tables['llm_reviews']=m.reviews
    for name,df in tables.items():
        flat=df.copy()
        for col in flat.columns:
            if flat[col].map(lambda x:isinstance(x,(list,dict))).any():
                flat[col]=flat[col].map(lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True) if isinstance(x,(list,dict)) else x)
        flat.to_csv(dest/(name+'.csv'),index=False)
        df.to_json(dest/(name+'.jsonl'),orient='records',lines=True,force_ascii=False)
        if parquet:
            try:flat.to_parquet(dest/(name+'.parquet'),index=False)
            except ImportError as exc:raise RuntimeError('Optional parquet export requires pyarrow or fastparquet') from exc
    from .batch import summarize_batch
    batch=summarize_batch(e,m,f)
    batch_tables={'batch_repositories':batch.repositories,'batch_workflow':batch.workflow,'batch_budget':batch.budget,'batch_tools':batch.tool_reliability,'batch_stages':batch.primary_stages,'batch_findings':batch.finding_prevalence,'batch_cost':batch.cost,'batch_tasks':batch.tasks,'batch_quality':batch.quality,'batch_gold_metrics':batch.gold_metrics,'batch_pairing':batch.pairing}
    for name,frame in batch_tables.items():
        flat=frame.copy()
        for col in flat.columns:
            if flat[col].map(lambda x:isinstance(x,(list,dict))).any():
                flat[col]=flat[col].map(lambda x:json.dumps(x,ensure_ascii=False,sort_keys=True) if isinstance(x,(list,dict)) else x)
        flat.to_csv(dest/(name+'.csv'),index=False)
        frame.to_json(dest/(name+'.jsonl'),orient='records',lines=True,force_ascii=False)
    (dest/'batch_summary.json').write_text(json.dumps(dict(batch.overview,submission=batch.submission),indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    summary=dict(m.summary)
    summary['source_traces']=[dict(trace_path=r.trace_path,sha256=r.trace_hash,task_id=r.task_id) for r in e.runs.itertuples()]
    summary['analysis_policy']=('Offline; no trace commands, patches, or tests executed; no causal root-cause claims'
                                if m.reviews.empty else
                                'Trace commands, patches, and tests were not executed; optional Codex labels are judge-derived hypotheses and do not alter official outcomes')
    (dest/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    if html:
        from .plots.html_report import write_html
        write_html(e,m,f,dest/'report.html')
        from .plots.batch_html import write_batch_html
        write_batch_html(batch,dest/'batch_report.html',f,m.reviews)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(dest.iterdir()) if p.is_file() and p.name!='manifest.json'}
    (dest/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return dest
