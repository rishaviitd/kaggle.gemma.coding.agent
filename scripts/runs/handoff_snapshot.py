"""Snapshot written by the student run (Kaggle) and read by the teacher run (local subprocess sandbox in a Linux container)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from handoff_llm import config_fingerprint

REQUIRED_FILES = ('handoff.json', 'student_responses.json', 'session_events.json',
                  'vllm_exchanges.json', 'manifest.json')


def write_snapshot(out_dir: Path, *, handoff: dict, student_responses: list[dict],
                   session_events: list[dict], vllm_exchanges: list[dict], manifest: dict) -> None:
    """Write the five files atomically: all-or-nothing at out_dir.

    Every payload is serialized before anything touches disk, so an unserializable payload
    fails cleanly. Files go to a .tmp sibling that is renamed into place only when complete.
    Any failure removes the .tmp directory, and an existing out_dir is replaced only then.
    """
    payloads = {'handoff.json': handoff, 'student_responses.json': student_responses,
                'session_events.json': session_events, 'vllm_exchanges.json': vllm_exchanges,
                'manifest.json': manifest}
    texts = {name: json.dumps(payload, indent=2) + '\n' for name, payload in payloads.items()}
    tmp_dir = out_dir.with_name(out_dir.name + '.tmp')
    shutil.rmtree(tmp_dir, ignore_errors=True)  # debris from an earlier crash
    try:
        tmp_dir.mkdir(parents=True)
        for name, text in texts.items():
            (tmp_dir / name).write_text(text)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        os.replace(tmp_dir, out_dir)
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def read_snapshot(snapshot_dir: Path, *, agent_dir: Path, sandbox: str) -> dict:
    missing = [name for name in REQUIRED_FILES if not (snapshot_dir / name).is_file()]
    if missing:
        raise ValueError('snapshot is missing: ' + ', '.join(missing))
    loaded = {name.removesuffix('.json'): json.loads((snapshot_dir / name).read_text())
              for name in REQUIRED_FILES}
    manifest = loaded['manifest']
    if manifest.get('sandbox') != 'subprocess' or sandbox != 'subprocess':
        raise ValueError('snapshot and run must both use the subprocess sandbox')
    current = config_fingerprint(agent_dir, sandbox)
    if manifest.get('agent_config_sha256') != current['agent_config_sha256']:
        raise ValueError('agent config differs from the snapshot run')
    return loaded
