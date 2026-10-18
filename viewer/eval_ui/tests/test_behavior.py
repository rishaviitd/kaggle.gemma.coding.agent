from types import SimpleNamespace

import pandas as pd

from agent_eval.metrics.behavior import classify_command_result, compute_behavior


def test_zero_match_search_is_expected_evidence_not_command_failure():
    result = classify_command_result('rg "needle" src', 'error', 1, '')

    assert result['category'] == 'search_no_match'
    assert result['valid'] is True


def test_invalid_test_selection_is_separate_from_assertion_failure():
    result = classify_command_result('pytest tests/missing_test.py', 'error', 4, 'file not found')

    assert result['category'] == 'invalid_test_selection'
    assert result['valid'] is False


def test_unknown_command_result_is_not_counted_as_valid():
    result = classify_command_result('rg "needle" src', 'unknown', None, '')

    assert result['category'] == 'unknown_result'
    assert result['valid'] is None


def test_pipeline_failure_is_not_a_passing_post_edit_test():
    experiment = SimpleNamespace(
        runs=pd.DataFrame([{'run_id': 'run-1', 'task_id': 'task-1', 'repo': 'demo', 'resolved_nullable': False}]),
        tool_calls=pd.DataFrame([
            {'run_id': 'run-1', 'call_id': 'c1', 'sequence_index': 1, 'name': 'edit_file', 'successful_source_edit': True},
        ]),
        tool_results=pd.DataFrame([
            {'run_id': 'run-1', 'call_id': 'c1', 'status': 'ok', 'exit_code': None, 'output_excerpt': '', 'error_message_excerpt': ''},
        ]),
        test_evidence=pd.DataFrame([
            {'evidence_id': 'e1', 'run_id': 'run-1', 'source': 'agent-side', 'sequence_index': 2, 'exit_code': 0, 'parsed_failed': 1, 'post_last_edit': True, 'pipeline_status_uncertain': True},
        ]),
    )

    task = compute_behavior(experiment).tasks.iloc[0]

    assert bool(task.post_edit_test_present) is True
    assert bool(task.post_edit_test_passed) is False


def test_behavior_rollup_tracks_edit_and_feedback_recovery():
    experiment = SimpleNamespace(
        runs=pd.DataFrame([{'run_id': 'run-1', 'task_id': 'task-1', 'repo': 'demo', 'resolved_nullable': True}]),
        tool_calls=pd.DataFrame([
            {'run_id': 'run-1', 'call_id': 'c1', 'sequence_index': 1, 'name': 'edit_file', 'command_category': 'command', 'successful_source_edit': False},
            {'run_id': 'run-1', 'call_id': 'c2', 'sequence_index': 2, 'name': 'run_command', 'command_category': 'test', 'arguments_summary': 'pytest tests/test_demo.py'},
            {'run_id': 'run-1', 'call_id': 'c3', 'sequence_index': 3, 'name': 'edit_file', 'command_category': 'command', 'successful_source_edit': True},
            {'run_id': 'run-1', 'call_id': 'c4', 'sequence_index': 4, 'name': 'run_command', 'command_category': 'test', 'arguments_summary': 'pytest tests/test_demo.py'},
        ]),
        tool_results=pd.DataFrame([
            {'run_id': 'run-1', 'call_id': 'c1', 'status': 'error', 'error_type': 'EditError', 'error_message_excerpt': 'old_string not found', 'exit_code': None, 'output_excerpt': ''},
            {'run_id': 'run-1', 'call_id': 'c2', 'status': 'ok', 'error_type': None, 'error_message_excerpt': '', 'exit_code': 1, 'output_excerpt': '1 failed'},
            {'run_id': 'run-1', 'call_id': 'c3', 'status': 'ok', 'error_type': None, 'error_message_excerpt': '', 'exit_code': None, 'output_excerpt': ''},
            {'run_id': 'run-1', 'call_id': 'c4', 'status': 'ok', 'error_type': None, 'error_message_excerpt': '', 'exit_code': 0, 'output_excerpt': '1 passed'},
        ]),
        test_evidence=pd.DataFrame([
            {'evidence_id': 'e2', 'run_id': 'run-1', 'source': 'agent-side', 'sequence_index': 2, 'exit_code': 1, 'parsed_failed': 1, 'post_last_edit': False},
            {'evidence_id': 'e4', 'run_id': 'run-1', 'source': 'agent-side', 'sequence_index': 4, 'exit_code': 0, 'parsed_failed': 0, 'post_last_edit': True, 'pipeline_status_uncertain': False},
        ]),
    )

    behavior = compute_behavior(experiment)
    task = behavior.tasks.iloc[0]
    summary = behavior.summary.set_index('indicator')

    assert task.edit_failures == 1
    assert task.edit_recoveries == 1
    assert task.failed_agent_tests == 1
    assert task.feedback_recoveries == 1
    assert bool(task.post_edit_test_present) is True
    assert bool(task.post_edit_test_passed) is True
    assert summary.loc['Edit recovery', 'numerator'] == 1
    assert summary.loc['Feedback recovery', 'numerator'] == 1
    assert (summary.coverage.dropna() <= 1).all()
