"""Strict contract for optional, judge-derived annotations."""

import json
from jsonschema import Draft202012Validator

PROMPT_VERSION = 'phase1-v1'
ROOT_CAUSES = (
    'wrong_localization', 'misunderstood_issue', 'incomplete_fix',
    'symptom_only_fix', 'regression_introduced', 'verification_skipped',
    'budget_exhausted', 'environment_problem', 'resolved_no_issue',
    'other', 'unsure',
)
FLAG_TYPES = (
    'repeated_exploration', 'ignored_failure', 'hypothesis_switch',
    'late_edit', 'unsupported_claim', 'other',
)
REVIEW_COLUMNS = (
    'run_id status model prompt_version summary root_cause '
    'root_cause_explanation root_cause_evidence_ids flags rule_check confidence '
    'truncated dropped_evidence input_tokens output_tokens cache_hit error'
).split()

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['summary', 'root_cause', 'flags', 'rule_check', 'confidence'],
    'properties': {
        'summary': {'type': 'string'},
        'root_cause': {
            'type': 'object', 'additionalProperties': False,
            'required': ['label', 'explanation', 'evidence_ids'],
            'properties': {
                'label': {'enum': list(ROOT_CAUSES)},
                'explanation': {'type': 'string'},
                'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
            },
        },
        'flags': {
            'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['type', 'turn', 'note', 'evidence_ids'],
                'properties': {
                    'type': {'enum': list(FLAG_TYPES)},
                    'turn': {'type': 'integer'},
                    'note': {'type': 'string'},
                    'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
                },
            },
        },
        'rule_check': {
            'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['finding_id', 'verdict', 'reason'],
                'properties': {
                    'finding_id': {'type': 'string'},
                    'verdict': {'enum': ['agree', 'dispute', 'unsure']},
                    'reason': {'type': 'string'},
                },
            },
        },
        'confidence': {'enum': ['high', 'medium', 'low']},
    },
}
VALIDATOR = Draft202012Validator(SCHEMA)


def parse_review(text, *, resolved, evidence_ids, finding_ids):
    """Validate exactly once, then remove invented references without repair."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid JSON: {exc}') from exc
    errors = sorted(VALIDATOR.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        first = errors[0]
        raise ValueError(f'Schema error at {"/".join(map(str, first.path)) or "$"}: {first.message}')
    if (not data['summary'].strip() or not data['root_cause']['explanation'].strip()
            or any(flag['turn'] < 0 or not flag['note'].strip() for flag in data['flags'])):
        raise ValueError('Empty review text or negative flag turn')
    if resolved and data['root_cause']['label'] not in ('resolved_no_issue', 'other'):
        raise ValueError('Resolved attempt has a failure root-cause label')

    known_evidence, known_findings = set(evidence_ids), set(finding_ids)
    dropped = 0

    def valid_refs(ids):
        nonlocal dropped
        kept = [eid for eid in ids if eid in known_evidence]
        dropped += len(ids) - len(kept)
        return kept

    root = data['root_cause']
    root['evidence_ids'] = valid_refs(root['evidence_ids'])
    if not root['evidence_ids']:
        root['label'] = 'unsure'
        data['confidence'] = 'low'
    if resolved and root['label'] not in ('resolved_no_issue', 'other'):
        raise ValueError('Resolved attempt has no evidenced root-cause label')
    flags = []
    for flag in data['flags']:
        flag['evidence_ids'] = valid_refs(flag['evidence_ids'])
        if flag['evidence_ids']:
            flags.append(flag)
    data['flags'] = flags
    data['rule_check'] = [check for check in data['rule_check']
                          if check['finding_id'] in known_findings]
    return data, dropped
