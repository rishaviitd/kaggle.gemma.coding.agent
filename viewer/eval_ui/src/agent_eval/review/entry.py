"""Shared notebook configuration for the optional Codex review."""

import os
from pathlib import Path

from .client import DEFAULT_MODEL
from .runner import attach_reviews, estimate_reviews, run_reviews


def enabled():
    return os.environ.get('GEMMA_LLM_REVIEW', '').lower() in ('1', 'true', 'yes')


def options(config):
    max_runs = os.environ.get('GEMMA_LLM_REVIEW_MAX_RUNS')
    return dict(
        gold_dir=config['gold_dir'],
        cache_dir=Path(config['output_dir']) / '.review_cache',
        model=os.environ.get('GEMMA_LLM_REVIEW_MODEL', DEFAULT_MODEL),
        max_runs=int(max_runs) if max_runs else None,
        max_input_tokens=int(os.environ.get('GEMMA_LLM_REVIEW_MAX_INPUT_TOKENS', '30000')),
    )


def estimate_text(estimate):
    cost = estimate['api_equivalent_usd']
    cost_text = f'${cost:.3f} API-equivalent' if cost is not None else 'unavailable for custom model'
    return (f"Codex review estimate: {estimate['runs']} attempts; "
            f"{estimate['uncached_requests']} new calls; {estimate['cache_hits']} cached; "
            f"about {estimate['estimated_input_tokens']:,} planned input tokens "
            f"(including a {estimate['codex_overhead_allowance_per_request']:,}/call Codex allowance); "
            f"assumed {estimate['assumed_output_tokens_per_run']:,} output tokens/call; "
            f"{cost_text}. Actual Codex plan usage may differ.")


def review_notebook(experiment, metrics, findings, config):
    if not enabled():
        return metrics
    opts = options(config)
    estimate = estimate_reviews(experiment, findings, **opts)
    print(estimate_text(estimate))
    if estimate['uncached_requests']:
        confirmed = os.environ.get('GEMMA_LLM_REVIEW_CONFIRM') == 'REVIEW'
        if not confirmed:
            confirmed = input('Type REVIEW to run Codex on uncached attempts: ').strip() == 'REVIEW'
        if not confirmed:
            raise PermissionError('Codex review was not confirmed')
    reviews = run_reviews(experiment, findings, enabled=True, confirmed=True, **opts)
    print('Codex review statuses:', reviews.status.value_counts().to_dict() if not reviews.empty else 'unavailable')
    return attach_reviews(metrics, reviews)
