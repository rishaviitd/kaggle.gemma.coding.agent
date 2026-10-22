#!/usr/bin/env python3
"""Build the OEC trajectory dataset from handoff run directories.

Usage:
  python scripts/sft_export/build_oec_dataset.py --logs logs/remote/rishav/inspiration-one --output data/transient/oec-v1
Scans for task directories that contain handoff.json (any depth), keeps only verified,
filtered runs, and refuses to mix agent configs, sandbox backends or harness versions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_oec import OecRejected, build_oec_examples

CONSISTENT_KEYS = ('agent_config_sha256', 'sandbox', 'swegemma_version', 'teacher_thinking')


def discover(roots):
    return sorted({path.parent for root in roots for path in Path(root).rglob('handoff.json')})


def build(roots, output: Path, policy='none'):
    rows, rejected, used = [], [], []
    for task_dir in discover(roots):
        try:
            examples = build_oec_examples(task_dir, policy=policy)
        except (OecRejected, OSError, json.JSONDecodeError, StopIteration) as error:
            rejected.append({'task_dir': str(task_dir), 'reason': str(error) or type(error).__name__})
            continue
        used.append(task_dir)
        for example in examples:
            example['source'] = str(task_dir)
            rows.append(example)
    if not rows:
        raise ValueError('No usable handoff runs found; rejected: ' + json.dumps(rejected[:5]))
    for key in CONSISTENT_KEYS:
        values = sorted({str(row['handoff'].get(key)) for row in rows})
        if len(values) != 1:
            raise ValueError(f'Runs disagree on {key}: {values}; regenerate with one configuration')
    first = rows[0]['handoff']
    target = output / 'trajectory'
    target.mkdir(parents=True, exist_ok=False)
    with (target / 'train.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    manifest = {
        'schema_version': 'gemma4-sft-v5', 'mode': 'trajectory', 'examples': len(rows),
        'tasks': len({r['task_id'] for r in rows}), 'observation_policy': policy,
        'enable_thinking': True, 'retain_reasoning': False,
        'oec': {**{key: first.get(key) for key in CONSISTENT_KEYS},
                'runs_used': len(used), 'runs_rejected': len(rejected), 'rejected': rejected},
    }
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--logs', nargs='+', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--policy', default='none', choices=('none', 'causal', 'hindsight'))
    args = p.parse_args()
    manifest = build(args.logs, args.output, args.policy)
    oec = manifest['oec']
    print(f"{manifest['examples']} rows from {oec['runs_used']} runs "
          f"({manifest['tasks']} tasks); rejected {oec['runs_rejected']}")
    for item in oec['rejected'][:20]:
        print(f"  reject {item['task_dir']}: {item['reason']}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
