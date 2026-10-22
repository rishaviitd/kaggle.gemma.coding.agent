#!/usr/bin/env python3
"""Mix clean-Haiku rows and OEC rows into one v6 trajectory dataset.

Usage:
  python scripts/sft_export/merge_datasets.py --clean <v6 trajectory dir> --oec <v6 OEC trajectory dir> \
      --out <new trajectory dir> --oec-share 0.34
All clean rows are kept. OEC rows are added until they make up `oec-share` of the result,
spread round-robin over tasks so no task dominates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys


def select_balanced(rows, count, seed):
    """Round-robin across task_id with a seeded shuffle inside each task."""
    rng = random.Random(seed)
    by_task = {}
    for row in rows:
        by_task.setdefault(row['task_id'], []).append(row)
    queues = []
    for task in sorted(by_task):
        group = list(by_task[task])
        rng.shuffle(group)
        queues.append(group)
    picked = []
    while len(picked) < count and any(queues):
        for queue in queues:
            if queue and len(picked) < count:
                picked.append(queue.pop())
    return picked


def read_rows(directory: Path):
    return [json.loads(line) for line in (directory / 'train.jsonl').read_text().splitlines() if line.strip()]


def merge(clean_dir: Path, oec_dir: Path, out_dir: Path, oec_share: float, seed: int):
    if not 0 < oec_share < 1:
        raise ValueError('oec_share must be strictly between 0 and 1')
    clean = [{**row, 'kind': 'clean'} for row in read_rows(clean_dir)]
    oec_pool = read_rows(oec_dir)
    want = round(len(clean) * oec_share / (1 - oec_share))
    oec = select_balanced(oec_pool, want, seed)
    rows = clean + oec
    out_dir.mkdir(parents=True, exist_ok=False)
    with (out_dir / 'train.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    manifest = json.loads((clean_dir / 'manifest.json').read_text())
    oec_manifest = json.loads((oec_dir / 'manifest.json').read_text())
    manifest.update({
        'schema_version': 'gemma4-sft-v6', 'examples': len(rows),
        'sources': {'clean': len(clean), 'oec': len(oec)},
        'oec': oec_manifest.get('oec'), 'oec_share_requested': oec_share, 'oec_pool': len(oec_pool),
    })
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--clean', type=Path, required=True)
    p.add_argument('--oec', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--oec-share', type=float, default=1 / 3)
    p.add_argument('--seed', type=int, default=1)
    args = p.parse_args()
    manifest = merge(args.clean, args.oec, args.out, args.oec_share, args.seed)
    print(f"wrote {manifest['examples']} rows to {args.out}: {manifest['sources']} (OEC pool {manifest['oec_pool']})")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        sys.exit(1)
