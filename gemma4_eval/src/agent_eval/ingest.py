"""Bounded deterministic discovery, isolated per-file validation, explicit attempts."""
import hashlib
import json
from pathlib import Path
from .models import COLUMNS, ExperimentData, table
from .adapters.vllm_trace_v1 import normalize, SCHEMA_VERSION

EXCLUDED = {'reports','__pycache__','.cache','cache','.git','.venv','node_modules','.ipynb_checkpoints'}

def load_experiment(trace_dir, experiment_id='baseline_v1', trace_globs=None, strict_schema=False, redact_text=True, include_reasoning_in_viewer=False, exclude_dirs=None):
    root = Path(trace_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'Trace directory does not exist: {root}')
    patterns = trace_globs or ['**/model_trace.json','**/trace_*.json']
    if any(Path(p).is_absolute() or '..' in Path(p).parts for p in patterns):
        raise ValueError('Trace patterns must stay inside trace_dir')
    excluded = EXCLUDED | set(exclude_dirs or [])
    paths = sorted({p.resolve() for pattern in patterns for p in root.glob(pattern) if p.is_file() and root in p.resolve().parents and not excluded.intersection(p.relative_to(root).parts)})
    rows = {k:[] for k in COLUMNS}
    for path in paths:
        digest = None
        lengths={k:len(v) for k,v in rows.items()}
        try:
            raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
            data = json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('turns'),list):
                raise ValueError('Trace must be an object with a turns list')
            run = data.get('run') if isinstance(data.get('run'),dict) else {}
            final = data.get('final') if isinstance(data.get('final'),dict) else {}
            result = final.get('result') if isinstance(final.get('result'),dict) else {}
            if not (run.get('task_id') or result.get('task_id')):
                raise ValueError('Missing recoverable task ID')
            if strict_schema and data.get('schema_version')!=SCHEMA_VERSION:
                raise ValueError(f'Unsupported schema: {data.get("schema_version")}')
            normalize(data,path,digest,experiment_id,rows,redact_text,include_reasoning_in_viewer)
            rows['intake'].append(dict(trace_path=str(path),accepted=True,reason=None,trace_hash=digest))
        except Exception as exc:
            # Roll back partial normalization, keeping the rest of the batch intact.
            for k in rows:
                del rows[k][lengths[k]:]
            rows['intake'].append(dict(trace_path=str(path),accepted=False,reason=f'{type(exc).__name__}: {exc}',trace_hash=digest))
    counts={}
    for run in rows['runs']:
        key=run['task_id'];counts[key]=counts.get(key,0)+1;run['attempt_id']=counts[key]
    for run in rows['runs']:
        run['ambiguous_pairing']=counts[run['task_id']]>1
        if run['ambiguous_pairing']:
            rows['quality_issues'].append(dict(issue_id=f'{run["run_id"]}:duplicate',run_id=run['run_id'],field_path='run.task_id',issue_type='duplicate_task',description='Separate attempt retained; automatic paired comparison excluded',evidence_id=rows['evidence'][next(i for i,e in enumerate(rows['evidence']) if e['run_id']==run['run_id'])]['evidence_id']))
    return ExperimentData(**{k:table(v,k) for k,v in rows.items()},config=dict(trace_dir=str(root),experiment_id=experiment_id,redact_text=redact_text,include_reasoning_in_viewer=include_reasoning_in_viewer))
