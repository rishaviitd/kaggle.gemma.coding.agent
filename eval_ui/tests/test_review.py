import json
from pathlib import Path

import pytest

from agent_eval import classify_failures, compute_metrics, export_report, load_experiment
from agent_eval.review import attach_reviews, estimate_reviews, run_reviews
from agent_eval.review.client import ReviewResponse
from agent_eval.review.digest import build_digest
from agent_eval.review.schema import parse_review
from conftest import turn


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def review(self, *, model, digest):
        self.calls += 1
        return ReviewResponse(self.response(digest), 100, 30)


def review_json(digest):
    eid = digest['turns'][0]['evidence_id']
    return json.dumps({'summary': 'The agent inspected the task. It made no useful edit. The verifier failed.',
                       'root_cause': {'label': 'incomplete_fix', 'explanation': 'No fix was recorded', 'evidence_ids': [eid]},
                       'flags': [{'type': 'repeated_exploration', 'turn': 1, 'note': 'Inspection repeated', 'evidence_ids': [eid]}],
                       'rule_check': [], 'confidence': 'medium'})


def sample(make_trace, trace, tmp_path):
    trace['turns'] = [turn()]
    make_trace(trace)
    e = load_experiment(tmp_path, trace_globs=['model_trace.json'])
    m = compute_metrics(e)
    f = classify_failures(e, m)
    return e, m, f


def test_review_opt_in_cache_and_exports(make_trace, trace, tmp_path):
    e, m, f = sample(make_trace, trace, tmp_path)
    client = FakeClient(review_json)
    cache = tmp_path / 'cache'
    assert run_reviews(e, f, client=client).empty
    with pytest.raises(PermissionError):
        run_reviews(e, f, enabled=True, client=client)
    assert client.calls == 0
    estimate = estimate_reviews(e, f, cache_dir=cache)
    assert estimate['uncached_requests'] == 1
    reviews = run_reviews(e, f, enabled=True, confirmed=True, client=client, cache_dir=cache)
    assert reviews.iloc[0].status == 'ok' and client.calls == 1
    again = run_reviews(e, f, enabled=True, confirmed=True, client=client, cache_dir=cache)
    assert again.iloc[0].cache_hit and client.calls == 1
    assert estimate_reviews(e, f, cache_dir=cache)['uncached_requests'] == 0
    attach_reviews(m, reviews)
    assert m.modules.set_index('module').loc['reasoning_judge', 'availability'] == 'available'
    out = export_report(e, m, f, tmp_path / 'out')
    assert (out / 'llm_reviews.csv').is_file()
    assert 'id="llmReviewSection"' in (out / 'report.html').read_text()
    assert 'id="llmBatch"' in (out / 'batch_report.html').read_text()
    assert m.summary['resolved_rate'] == 0


def test_invalid_evidence_and_invalid_json_isolated(make_trace, trace, tmp_path):
    e, m, f = sample(make_trace, trace, tmp_path)
    digest, _ = build_digest(e, f, next(e.runs.itertuples()))
    forged = json.loads(review_json(digest))
    forged['root_cause']['evidence_ids'] = ['invented']
    forged['flags'][0]['evidence_ids'] = ['invented']
    parsed, dropped = parse_review(json.dumps(forged), resolved=False,
                                   evidence_ids=e.evidence.evidence_id,
                                   finding_ids=f.findings.finding_id)
    assert parsed['root_cause']['label'] == 'unsure' and not parsed['flags'] and dropped == 2
    client = FakeClient(lambda _: 'not json')
    reviews = run_reviews(e, f, enabled=True, confirmed=True, client=client,
                          cache_dir=tmp_path / 'bad-cache')
    assert reviews.iloc[0].status == 'invalid'
    assert m.reviews.empty


def test_review_limit_and_no_review_export(make_trace, trace, tmp_path):
    e, m, f = sample(make_trace, trace, tmp_path)
    out = export_report(e, m, f, tmp_path / 'out')
    assert not (out / 'llm_reviews.csv').exists()
    assert 'llmReviewSection' not in (out / 'report.html').read_text()
    assert 'llmBatch' not in (out / 'batch_report.html').read_text()
    client = FakeClient(review_json)
    reviews = run_reviews(e, f, enabled=True, confirmed=True, client=client,
                          max_runs=0, cache_dir=tmp_path / 'cache')
    assert reviews.iloc[0].status == 'skipped' and client.calls == 0


def test_review_model_key_and_missing_auth(make_trace, trace, tmp_path, monkeypatch):
    e, _, f = sample(make_trace, trace, tmp_path)
    client = FakeClient(review_json)
    cache = tmp_path / 'cache'
    run_reviews(e, f, enabled=True, confirmed=True, client=client, cache_dir=cache)
    changed = run_reviews(e, f, enabled=True, confirmed=True, client=client,
                          cache_dir=cache, model='alternate-model')
    assert client.calls == 2 and not changed.iloc[0].cache_hit

    class Unauthenticated:
        def authenticated(self):
            return False

    monkeypatch.setattr('agent_eval.review.runner.CodexCLIClient', Unauthenticated)
    assert run_reviews(e, f, enabled=True, confirmed=True).empty


def test_long_digest_is_truncated(make_trace, trace, tmp_path):
    trace['turns'] = [turn(i) for i in range(1, 30)]
    trace['final']['result']['agent_patch'] = 'x' * 50000
    make_trace(trace)
    e = load_experiment(tmp_path, trace_globs=['model_trace.json'])
    f = classify_failures(e, compute_metrics(e))
    digest, truncated = build_digest(e, f, next(e.runs.itertuples()), max_input_tokens=1200)
    assert truncated and digest['elided_middle_turns'] > 0
    assert len(digest['final_agent_patch']) < 50000
