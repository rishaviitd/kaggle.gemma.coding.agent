"""Join measured ATIF token usage to the matching model trace's main turns."""

import hashlib
import json
from pathlib import Path

from .evidence import excerpt


def _tools_from_model(turn):
    return [(call.get('name'), call.get('arguments'))
            for call in (turn.get('output') or {}).get('tool_calls', [])]


def _tools_from_group(group):
    return [(call.get('function_name'), call.get('arguments'))
            for _, step in group for call in step.get('tool_calls', [])]


def _measured_groups(steps):
    groups = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or step.get('source') != 'agent':
            continue
        if isinstance(step.get('metrics'), dict) and step['metrics']:
            groups.append([(index, step)])
        elif groups:
            # ATIF can emit a tool-call step after the measured assistant step.
            groups[-1].append((index, step))
    return groups


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('ATIF token count must be a nonnegative integer')
    return value


def enrich_token_usage(data, path, rows, lengths, redact_text=True):
    """Add source-linked counts only when a sibling ATIF trace aligns exactly."""
    path = Path(path)
    if path.name != 'model_trace.json':
        return
    run = rows['runs'][-1]
    rid, task_id = run['run_id'], run['task_id']
    if Path(task_id).name != task_id:
        return
    companion = path.parent / 'traces' / f'trace_{task_id}.json'
    if (not companion.is_file()
            or companion.resolve().parent != (path.parent / 'traces').resolve()):
        return

    def issue(kind, detail, eid=None):
        rows['quality_issues'].append(dict(
            issue_id=f'{rid}:q{sum(q["run_id"] == rid for q in rows["quality_issues"])+1}',
            run_id=rid, field_path='traces/trace_<task>.json', issue_type=kind,
            description=detail, evidence_id=eid or rows['evidence'][lengths['evidence']]['evidence_id']))

    try:
        raw = companion.read_bytes()
        atif = json.loads(raw)
        if not isinstance(atif, dict) or not str(atif.get('schema_version', '')).startswith('ATIF-'):
            raise ValueError('Sibling trace is not ATIF')
        steps = atif.get('steps')
        if not isinstance(steps, list):
            raise ValueError('ATIF steps missing')
        groups = _measured_groups(steps)
        raw_turns = data['turns']
        normalized = rows['turns'][lengths['turns']:]
        if len(raw_turns) != len(normalized) or not all(isinstance(t, dict) for t in raw_turns):
            raise ValueError('Model turn sequence cannot be aligned')
        main = [(source, target) for source, target in zip(raw_turns, normalized)
                if source.get('caller', 'main') == 'main']
        if not groups or len(groups) > len(main):
            raise ValueError('ATIF measured-step count does not match main turns')
        for (source, _), group in zip(main, groups):
            if _tools_from_model(source) != _tools_from_group(group):
                raise ValueError('ATIF tool sequence differs from model trace')
        counts = []
        for group in groups:
            metrics = group[0][1]['metrics']
            prompt = _count(metrics.get('prompt_tokens'))
            completion = _count(metrics.get('completion_tokens'))
            cached = _count(metrics.get('cached_tokens', 0))
            total = _count(metrics.get('total_tokens', prompt + completion))
            if total != prompt + completion or cached > prompt:
                raise ValueError('Inconsistent ATIF per-step token counts')
            counts.append((prompt, completion, cached, total))
        final = atif.get('final_metrics') or {}
        if (final.get('total_prompt_tokens') != sum(v[0] for v in counts)
                or final.get('total_completion_tokens') != sum(v[1] for v in counts)
                or final.get('total_tokens') != sum(v[3] for v in counts)):
            raise ValueError('ATIF final token totals disagree with its steps')
        for (_, target), group, values in zip(main, groups, counts):
            direct = (target.get('prompt_tokens'), target.get('completion_tokens'))
            if any(value is not None for value in direct):
                if any(value is not None and value != measured
                       for value, measured in zip(direct, values[:2])):
                    raise ValueError('Model-trace usage disagrees with ATIF')
        source_hash = hashlib.sha256(raw).hexdigest()
        for (_, target), group, values in zip(main, groups, counts):
            step_index, step = group[0]
            eid = f'{rid}:e{sum(e["run_id"] == rid for e in rows["evidence"])+1}'
            rows['evidence'].append(dict(
                evidence_id=eid, run_id=rid, trace_path=str(companion), source_hash=source_hash,
                json_pointer=f'/steps/{step_index}/metrics', turn_number=target['turn_number'],
                tool_call_id=None, excerpt=excerpt(step['metrics'], enabled=redact_text)))
            for field, value in zip(('prompt_tokens', 'completion_tokens', 'cached_tokens', 'total_tokens'), values):
                if target.get(field) is None:
                    target[field] = value
            if target.get('usage_source') is None:
                target['usage_source'] = 'atif_companion'
                target['usage_evidence_id'] = eid
        if len(groups) < len(main):
            issue('token_usage_partial',
                  f'ATIF measured {len(groups)} of {len(main)} main turns; unmatched turns remain unavailable')
        reported = run.get('reported_total_tokens')
        if reported is not None and reported != final['total_tokens']:
            issue('token_total_mismatch',
                  f'Harness total_tokens={reported}; ATIF measured main-agent total={final["total_tokens"]}',
                  rows['evidence'][-1]['evidence_id'])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        issue('token_companion_invalid', f'ATIF usage skipped: {exc}')
