"""Opt-in review orchestration. Rule metrics and official outcomes stay unchanged."""

import hashlib
import json
from pathlib import Path
import pandas as pd

from .client import CodexCLIClient, DEFAULT_MODEL
from .digest import SYSTEM_PROMPT, build_digest, token_estimate
from .schema import PROMPT_VERSION, REVIEW_COLUMNS, parse_review


def empty_reviews():
    return pd.DataFrame(columns=REVIEW_COLUMNS)


def _cache_path(cache_dir, model, digest):
    key = hashlib.sha256(json.dumps(
        {'model': model, 'prompt_version': PROMPT_VERSION, 'digest': digest},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    return Path(cache_dir) / f'{key}.txt'


def estimate_reviews(experiment, findings, *, gold_dir=None, cache_dir=None,
                     model=DEFAULT_MODEL, max_runs=None, max_input_tokens=30000,
                     assumed_output_tokens=1500):
    if max_runs is not None and max_runs < 0:
        raise ValueError('max_runs must be nonnegative')
    runs = list(experiment.runs.itertuples())[:max_runs]
    digest_tokens, hits, digest_errors = 0, 0, 0
    for run in runs:
        try:
            digest, _ = build_digest(experiment, findings, run, gold_dir=gold_dir,
                                     max_input_tokens=max_input_tokens)
        except Exception:
            digest_errors += 1
            continue
        if cache_dir and _cache_path(cache_dir, model, digest).is_file():
            hits += 1
        else:
            digest_tokens += token_estimate(SYSTEM_PROMPT) + token_estimate(digest)
    requests = len(runs) - hits - digest_errors
    # The first live Codex CLI pilot used ~15.6k more input tokens than the
    # serialized digest estimate. Reserve 16k per request for CLI context and
    # iterative agent overhead; this is a planning allowance, not a guarantee.
    overhead_tokens_per_request = 16000
    tokens = digest_tokens + requests * overhead_tokens_per_request
    # API-equivalent estimate for the default model. Codex subscription billing
    # can differ, and reasoning/tool overhead is not predictable from a digest.
    dollars = ((tokens * 2 + requests * assumed_output_tokens * 10) / 1_000_000
               if model == DEFAULT_MODEL else None)
    return dict(runs=len(runs), uncached_requests=requests, cache_hits=hits,
                digest_errors=digest_errors, digest_estimated_input_tokens=digest_tokens,
                codex_overhead_allowance_per_request=overhead_tokens_per_request,
                estimated_input_tokens=tokens,
                assumed_output_tokens_per_run=assumed_output_tokens,
                api_equivalent_usd=dollars, model=model)


def run_reviews(experiment, findings, *, enabled=False, confirmed=False,
                client=None, gold_dir=None, cache_dir=None, model=DEFAULT_MODEL,
                max_runs=None, max_input_tokens=30000):
    """Call the client only after explicit opt-in and cost confirmation."""
    if not enabled:
        return empty_reviews()
    if client is None:
        try:
            client = CodexCLIClient()
            authenticated = client.authenticated()
        except (RuntimeError, OSError):
            authenticated = False
        if not authenticated:
            return empty_reviews()
    if not confirmed:
        raise PermissionError('Review estimate must be confirmed before any Codex call')
    cache_dir = Path(cache_dir or '.review_cache')
    rows = []
    allowed = len(experiment.runs) if max_runs is None else max_runs
    if allowed < 0:
        raise ValueError('max_runs must be nonnegative')
    for index, run in enumerate(experiment.runs.itertuples()):
        row = dict.fromkeys(REVIEW_COLUMNS)
        row.update(run_id=run.run_id, status='skipped', model=model,
                   prompt_version=PROMPT_VERSION, truncated=False,
                   dropped_evidence=0, cache_hit=False)
        if index >= allowed:
            row['error'] = 'max_runs limit'
            rows.append(row)
            continue
        try:
            digest, truncated = build_digest(experiment, findings, run,
                                             gold_dir=gold_dir,
                                             max_input_tokens=max_input_tokens)
            row['truncated'] = truncated
            cached = _cache_path(cache_dir, model, digest)
            if cached.is_file():
                text = cached.read_text()
                row['cache_hit'] = True
            else:
                response = client.review(model=model, digest=digest)
                text = response.text
                row['input_tokens'] = response.input_tokens
                row['output_tokens'] = response.output_tokens
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_text(text)
        except Exception as exc:
            row.update(status='error', error=f'{type(exc).__name__}: {exc}')
            rows.append(row)
            continue
        try:
            evidence = experiment.evidence[experiment.evidence.run_id.eq(run.run_id)].evidence_id
            finding_ids = findings.findings[findings.findings.run_id.eq(run.run_id)].finding_id
            parsed, dropped = parse_review(text,
                                           resolved=not pd.isna(run.resolved_nullable) and bool(run.resolved_nullable),
                                           evidence_ids=evidence, finding_ids=finding_ids)
            root = parsed['root_cause']
            row.update(status='ok', summary=parsed['summary'], root_cause=root['label'],
                       root_cause_explanation=root['explanation'],
                       root_cause_evidence_ids=root['evidence_ids'],
                       flags=parsed['flags'], rule_check=parsed['rule_check'],
                       confidence=parsed['confidence'], dropped_evidence=dropped)
        except ValueError as exc:
            row.update(status='invalid', error=str(exc))
        except Exception as exc:
            row.update(status='invalid', error=f'{type(exc).__name__}: {exc}')
        rows.append(row)
    return pd.DataFrame(rows, columns=REVIEW_COLUMNS)


def attach_reviews(metrics, reviews):
    """Attach judge output without modifying any measured metric or rate."""
    if reviews is None or reviews.empty:
        return metrics
    metrics.reviews = reviews.copy()
    mask = metrics.modules.module.eq('reasoning_judge')
    metrics.modules.loc[mask, 'availability'] = 'available'
    metrics.modules.loc[mask, 'prerequisites'] = (
        'Optional Codex review; judge-derived labels are uncalibrated and do not alter rule metrics'
    )
    return metrics
