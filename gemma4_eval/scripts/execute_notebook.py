"""Execute only our authored analytics notebook; no trace code is executed."""
import os
import argparse
import json
import sys
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description='Execute the analytics notebook')
parser.add_argument('--estimate', action='store_true', help='Estimate optional Codex review without running the notebook')
parser.add_argument('--max-runs', type=int, help='Limit Codex review attempts')
args=parser.parse_args()
if args.max_runs is not None:
    if args.max_runs < 0:
        parser.error('--max-runs must be nonnegative')
    os.environ['GEMMA_LLM_REVIEW_MAX_RUNS']=str(args.max_runs)
if args.estimate or os.environ.get('GEMMA_LLM_REVIEW', '').lower() in ('1','true','yes'):
    sys.path.insert(0,str(root/'src'))
    from agent_eval import load_experiment, compute_metrics, classify_failures
    from agent_eval.review.entry import estimate_text, options
    from agent_eval.review import estimate_reviews
    default_trace=next((p for p in [root.parent/'logs/remote/akshay/itr-1',Path('/Users/akshay/kaggle/wt/main/logs/remote/akshay/itr-1')] if p.is_dir()),root.parent/'logs/remote/akshay/itr-1')
    roots_file=os.environ.get('GEMMA_REPO_ROOTS_JSON')
    config={'trace_dir':os.environ.get('GEMMA_TRACE_DIR',str(default_trace)),
            'trace_globs':os.environ.get('GEMMA_TRACE_GLOBS','**/model_trace.json,**/trace_*.json').split(','),
            'gold_dir':os.environ.get('GEMMA_GOLD_DIR'),
            'repo_roots':json.loads(Path(roots_file).read_text()) if roots_file else {},
            'output_dir':os.environ.get('GEMMA_OUTPUT_DIR',str(root/'reports'/'itr-1')),
            'experiment_id':os.environ.get('GEMMA_EXPERIMENT_ID','itr-1')}
    experiment=load_experiment(config['trace_dir'],experiment_id=config['experiment_id'],trace_globs=config['trace_globs'],strict_schema=False,redact_text=True,include_reasoning_in_viewer=False)
    metrics=compute_metrics(experiment,gold_dir=config['gold_dir'],repo_roots=config['repo_roots'],compare_dir=None)
    findings=classify_failures(experiment,metrics)
    estimate=estimate_reviews(experiment,findings,**options(config))
    print(estimate_text(estimate),flush=True)
    if args.estimate:
        raise SystemExit(0)
    if estimate['uncached_requests'] and os.environ.get('GEMMA_LLM_REVIEW_CONFIRM')!='REVIEW':
        if not sys.stdin.isatty() or input('Type REVIEW to run Codex on uncached attempts: ').strip()!='REVIEW':
            raise SystemExit('Codex review was not confirmed; no calls made')
        os.environ['GEMMA_LLM_REVIEW_CONFIRM']='REVIEW'
os.environ['JUPYTER_PATH']=str(root/'.jupyter/share/jupyter')
os.environ['IPYTHONDIR']=str(root/'.jupyter/ipython')
os.environ['JUPYTER_RUNTIME_DIR']=str(root/'.jupyter/runtime')
nb=nbformat.read(root/'notebooks/01_agent_trace_report.ipynb',as_version=4)
km=KernelManager(kernel_name='gemma4-eval',transport='ipc',ip=str(root/'.jupyter/report-ipc'))
cell_timeout=360 if os.environ.get('GEMMA_LLM_REVIEW', '').lower() in ('1','true','yes') else 120
client=NotebookClient(nb,km=km,timeout=cell_timeout,startup_timeout=30,resources={'metadata':{'path':str(root/'notebooks')}})
client.on_cell_start=lambda cell,cell_index: print(f'Cell {cell_index+1}/{len(nb.cells)}',flush=True)
try:
    client.execute()
finally:
    if km.has_kernel:km.shutdown_kernel(now=True)
executed_name = os.environ.get('GEMMA_EXECUTED_NOTEBOOK', '01_agent_trace_report.executed.ipynb')
if Path(executed_name).name != executed_name or not executed_name.endswith('.ipynb'):
    raise ValueError('GEMMA_EXECUTED_NOTEBOOK must be an .ipynb filename')
executed_path = root / 'notebooks' / executed_name
nbformat.write(nb, executed_path)
errors=sum(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs)
print('Notebook executed:',sum(c.cell_type=='code' for c in nb.cells),'cells; errors:',errors)
print('Executed notebook:', executed_path)
