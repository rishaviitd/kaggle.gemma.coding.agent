"""Offline batch export command."""
import argparse
import json
from . import load_experiment,compute_metrics,classify_failures,export_report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trace_dir');p.add_argument('--output-dir',default='reports/latest');p.add_argument('--experiment-id',default='baseline_v1');p.add_argument('--gold-dir');p.add_argument('--repo-roots',help='JSON file mapping repository names to pristine baseline paths');p.add_argument('--compare-dir');p.add_argument('--strict-schema',action='store_true');p.add_argument('--no-html',action='store_true');p.add_argument('--parquet',action='store_true');p.add_argument('--include-reasoning',action='store_true');p.add_argument('--no-redact',action='store_true')
    args=p.parse_args();roots=json.loads(open(args.repo_roots).read()) if args.repo_roots else {}
    e=load_experiment(args.trace_dir,experiment_id=args.experiment_id,strict_schema=args.strict_schema,redact_text=not args.no_redact,include_reasoning_in_viewer=args.include_reasoning)
    m=compute_metrics(e,gold_dir=args.gold_dir,repo_roots=roots,compare_dir=args.compare_dir);f=classify_failures(e,m)
    dest=export_report(e,m,f,args.output_dir,html=not args.no_html,parquet=args.parquet)
    print(json.dumps(m.summary,indent=2));print(f'Report: {dest}')
    return 1 if len(e.intake) and not len(e.runs) else 0

if __name__=='__main__':raise SystemExit(main())
