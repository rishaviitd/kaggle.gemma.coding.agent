"""Conservative, evidence-linked behavior diagnostics for coding traces."""
from dataclasses import dataclass

import pandas as pd

from ..events import command_category


@dataclass
class BehaviorData:
    events: pd.DataFrame
    tasks: pd.DataFrame
    summary: pd.DataFrame


EVENT_COLUMNS = [
    'event_id', 'run_id', 'task_id', 'kind', 'sequence_index', 'evidence_ids',
    'category', 'valid', 'recovered', 'observation',
]

TASK_COLUMNS = [
    'run_id', 'task_id', 'repo', 'command_count', 'command_evaluable_count',
    'unknown_command_results', 'valid_commands', 'has_valid_command',
    'test_count', 'test_evaluable_count', 'unknown_test_results',
    'invalid_test_selection', 'has_valid_test_selection', 'edit_failures',
    'edit_recoveries', 'has_edit_recovery', 'failed_agent_tests',
    'feedback_recoveries', 'has_feedback_recovery', 'source_edit_present',
    'post_edit_test_present', 'post_edit_test_passed', 'resolved',
]


def _value(row, key, default=None):
    if row is None:
        return default
    value = row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)
    return default if value is None or (isinstance(value, float) and pd.isna(value)) else value


def _as_int(value):
    try:
        return None if value is None or pd.isna(value) else int(value)
    except (TypeError, ValueError):
        return None


def _text(value):
    return str(value or '')


def classify_command_result(command, status, exit_code, output='', error_excerpt='', command_kind=None):
    """Classify a command without treating expected negative evidence as failure."""
    category = command_kind or command_category(_text(command))
    exit_code = _as_int(exit_code)
    text = f'{_text(output)}\n{_text(error_excerpt)}'.lower()

    if status not in ('ok', 'error') and exit_code is None:
        return {'category': 'unknown_result', 'valid': None, 'observation': 'Command result was not recorded'}
    if category == 'search' and exit_code == 1:
        return {'category': 'search_no_match', 'valid': True, 'observation': 'Search returned no matches'}
    if category == 'test' and (exit_code in (4, 5) or 'no tests ran' in text or 'file not found' in text):
        return {'category': 'invalid_test_selection', 'valid': False, 'observation': 'Test command did not select a runnable test'}
    if category == 'test' and exit_code == 0:
        return {'category': 'test_pass', 'valid': True, 'observation': 'Test command completed successfully'}
    if category == 'test' and exit_code not in (None, 0):
        return {'category': 'test_failure', 'valid': True, 'observation': 'Test command ran and reported a failure'}
    if status == 'error':
        return {'category': 'tool_error', 'valid': False, 'observation': 'Tool returned an execution error'}
    if exit_code not in (None, 0):
        return {'category': 'command_failure', 'valid': False, 'observation': 'Command returned a nonzero exit code'}
    return {'category': 'command_ok', 'valid': True, 'observation': 'Command completed successfully'}


def _result_map(results):
    if results is None or len(results) == 0:
        return {}
    return {(row.run_id, row.call_id): row for row in results.itertuples()}


def compute_behavior(experiment):
    """Return event-level behavior evidence and run-level diagnostic rollups."""
    events = []
    tasks = []
    results = _result_map(experiment.tool_results)
    calls = experiment.tool_calls
    tests = experiment.test_evidence

    for run in experiment.runs.itertuples():
        rid = run.run_id
        task_id = _value(run, 'task_id', rid)
        run_calls = calls[calls.run_id.eq(rid)].sort_values('sequence_index') if len(calls) else calls
        run_tests = tests[tests.run_id.eq(rid)].sort_values('sequence_index') if len(tests) else tests
        source_edit_sequences = []
        failed_edit_sequences = []
        edit_failures = 0
        edit_recoveries = 0
        command_count = 0
        command_evaluable_count = 0
        unknown_command_results = 0
        valid_commands = 0
        test_count = 0
        test_evaluable_count = 0
        unknown_test_results = 0
        invalid_test_selection = 0
        test_classifications = {}

        def add(kind, sequence_index, evidence_ids, category=None, valid=None, recovered=None, observation=''):
            events.append(dict(
                event_id=f'{rid}:behavior:{len(events) + 1}', run_id=rid, task_id=task_id,
                kind=kind, sequence_index=_as_int(sequence_index),
                evidence_ids=sorted(set(x for x in evidence_ids if x)), category=category,
                valid=valid, recovered=recovered, observation=observation,
            ))

        for call in run_calls.itertuples():
            result = results.get((rid, call.call_id))
            status = _value(result, 'status', 'unknown')
            exit_code = _value(result, 'exit_code')
            result_evidence = [_value(result, 'evidence_id')]
            call_evidence = [_value(call, 'evidence_id')]
            if call.name == 'run_command':
                command = _value(call, 'arguments_summary', '')
                command_kind = _value(call, 'command_category')
                classified = classify_command_result(command, status, exit_code, _value(result, 'output_excerpt', ''), _value(result, 'error_message_excerpt', ''), command_kind=command_kind)
                command_count += 1
                if classified['valid'] is None:
                    unknown_command_results += 1
                else:
                    command_evaluable_count += 1
                    valid_commands += int(classified['valid'])
                if command_kind == 'test':
                    test_count += 1
                    test_classifications[_as_int(call.sequence_index)] = classified['category']
                    if classified['valid'] is None:
                        unknown_test_results += 1
                    else:
                        test_evaluable_count += 1
                        invalid_test_selection += int(classified['category'] == 'invalid_test_selection')
                add('command', call.sequence_index, call_evidence + result_evidence, classified['category'], classified['valid'], None, classified['observation'])
            if call.name in ('edit_file', 'write_file'):
                successful = bool(_value(call, 'successful_source_edit', False)) and status == 'ok'
                if successful:
                    source_edit_sequences.append(_as_int(call.sequence_index))
                    if failed_edit_sequences:
                        edit_recoveries += len(failed_edit_sequences)
                        for failed_sequence, failed_evidence in failed_edit_sequences:
                            add('edit_recovery', call.sequence_index, failed_evidence + call_evidence + result_evidence, 'edit_recovery', True, True, f'Edit failure at sequence {failed_sequence} was followed by a confirmed source edit')
                        failed_edit_sequences.clear()
                elif status == 'error':
                    edit_failures += 1
                    failed_edit_sequences.append((_as_int(call.sequence_index), call_evidence + result_evidence))
                    add('edit_failure', call.sequence_index, call_evidence + result_evidence, 'edit_failure', False, False, _text(_value(result, 'error_message_excerpt', 'Edit returned an error')))

        failed_agent_tests = 0
        feedback_recoveries = 0
        for test in run_tests.itertuples():
            if _value(test, 'source') != 'agent-side':
                continue
            sequence = _as_int(_value(test, 'sequence_index'))
            test_category = test_classifications.get(sequence)
            test_exit = _as_int(_value(test, 'exit_code'))
            invalid_selection = test_category == 'invalid_test_selection' or test_exit in (4, 5)
            execution_unknown = test_category in ('tool_error', 'unknown_result') and test_exit is None
            if invalid_selection or execution_unknown:
                continue
            failed = (test_exit not in (None, 0)) or (_as_int(_value(test, 'parsed_failed', 0)) or 0) > 0
            if not failed:
                continue
            failed_agent_tests += 1
            later_edit = any(x is not None and sequence is not None and x > sequence for x in source_edit_sequences)
            feedback_recoveries += int(later_edit)
            add('feedback_failure', sequence, [_value(test, 'evidence_id')], 'feedback_recovery', True, later_edit, 'Failed agent-side test was followed by a corrective source edit' if later_edit else 'No later confirmed source edit followed the failed test')
            if later_edit:
                add('feedback_recovery', sequence, [_value(test, 'evidence_id')], 'feedback_recovery', True, True, 'Failed test feedback was followed by a confirmed source edit')

        post_edit_tests = run_tests[(run_tests.source.eq('agent-side')) & run_tests.post_last_edit.eq(True)] if len(run_tests) else run_tests
        post_edit_test_present = bool(len(source_edit_sequences) and len(post_edit_tests))
        last_post_edit_test = post_edit_tests.iloc[-1] if post_edit_test_present else None
        post_edit_test_passed = bool(
            last_post_edit_test is not None
            and _as_int(_value(last_post_edit_test, 'exit_code')) == 0
            and (_as_int(_value(last_post_edit_test, 'parsed_failed', 0)) or 0) == 0
            and not bool(_value(last_post_edit_test, 'pipeline_status_uncertain', False))
        )
        if post_edit_test_present:
            add('post_edit_test', post_edit_tests.iloc[-1].sequence_index, post_edit_tests.evidence_id.tolist(), 'post_edit_verification', True, post_edit_test_passed, 'Agent-side test recorded after the last confirmed source edit')

        tasks.append(dict(
            run_id=rid, task_id=task_id, repo=_value(run, 'repo'),
            command_count=command_count, command_evaluable_count=command_evaluable_count,
            unknown_command_results=unknown_command_results, valid_commands=valid_commands,
            has_valid_command=bool(command_evaluable_count and valid_commands == command_evaluable_count),
            test_count=test_count, test_evaluable_count=test_evaluable_count,
            unknown_test_results=unknown_test_results, invalid_test_selection=invalid_test_selection,
            has_valid_test_selection=bool(test_evaluable_count and invalid_test_selection == 0),
            edit_failures=edit_failures, edit_recoveries=edit_recoveries,
            has_edit_recovery=bool(edit_recoveries), failed_agent_tests=failed_agent_tests,
            feedback_recoveries=feedback_recoveries, has_feedback_recovery=bool(feedback_recoveries),
            source_edit_present=bool(source_edit_sequences),
            post_edit_test_present=post_edit_test_present,
            post_edit_test_passed=post_edit_test_passed,
            resolved=_value(run, 'resolved_nullable'),
        ))

    task_frame = pd.DataFrame(tasks, columns=TASK_COLUMNS)
    event_frame = pd.DataFrame(events, columns=EVENT_COLUMNS)
    total = len(task_frame)

    def row(indicator, numerator, denominator, definition, coverage_count, evidence_kind='heuristic'):
        return dict(indicator=indicator, numerator=int(numerator), denominator=int(denominator),
                    rate=numerator / denominator if denominator else None,
                    coverage=coverage_count / total if total else None,
                    denominator_definition=definition, evidence_kind=evidence_kind)

    summary = pd.DataFrame([
        row('Valid command execution', int(task_frame.has_valid_command.sum()) if total else 0, int(task_frame.command_evaluable_count.gt(0).sum()) if total else 0, 'Attempts with observed command results and no invalid command result', int(task_frame.command_evaluable_count.gt(0).sum()) if total else 0),
        row('Valid test selection', int(task_frame.has_valid_test_selection.sum()) if total else 0, int(task_frame.test_evaluable_count.gt(0).sum()) if total else 0, 'Attempts with observed test results and no invalid test selection', int(task_frame.test_evaluable_count.gt(0).sum()) if total else 0),
        row('Edit recovery', int(task_frame.has_edit_recovery.sum()) if total else 0, int(task_frame.edit_failures.gt(0).sum()) if total else 0, 'Attempts with an edit failure followed by a confirmed source edit', int(task_frame.edit_failures.gt(0).sum()) if total else 0),
        row('Post-edit test present', int(task_frame.post_edit_test_present.sum()) if total else 0, int(task_frame.source_edit_present.sum()) if total else 0, 'Runs with a confirmed source edit', int(task_frame.source_edit_present.sum()) if total else 0),
        row('Post-edit test passed', int(task_frame.post_edit_test_passed.sum()) if total else 0, int(task_frame.post_edit_test_present.sum()) if total else 0, 'Runs with an agent-side test after the last confirmed source edit', int(task_frame.post_edit_test_present.sum()) if total else 0),
        row('Feedback recovery', int(task_frame.has_feedback_recovery.sum()) if total else 0, int(task_frame.failed_agent_tests.gt(0).sum()) if total else 0, 'Attempts with a valid failed agent-side test followed by a confirmed source edit', int(task_frame.failed_agent_tests.gt(0).sum()) if total else 0),
    ])
    return BehaviorData(event_frame, task_frame, summary)
