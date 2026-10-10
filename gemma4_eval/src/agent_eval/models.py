"""UI-independent table contracts. No raw prompts are retained in memory."""
from dataclasses import dataclass, field
import pandas as pd

COLUMNS = {
    'runs': 'run_id task_id experiment_id attempt_id ambiguous_pairing repo resolved_nullable harness_status test_exit_code duration_seconds budgeted_tool_calls reported_llm_calls harness_budget trace_path trace_hash schema_version reported_total_tokens prompt_budget_claims final_text_excerpt harness_error_excerpt',
    'turns': 'run_id turn_number created_epoch finish_reason prompt_tokens completion_tokens reasoning_tokens is_compaction compaction_confidence assistant_text_available assistant_excerpt reasoning_excerpt evidence_id',
    'tool_calls': 'run_id call_id original_call_id turn_number sequence_index name arguments_json_valid schema_valid schema_issues arguments_summary arguments_raw_excerpt requested_path command_category result_linked successful_source_edit inferred_mutation evidence_id',
    'tool_results': 'run_id call_id original_call_id turn_number status error_type error_message_excerpt exit_code budget_warning patch_size files_changed output_truncated output_excerpt evidence_id',
    'events': 'event_id run_id turn_number event_order kind tool_name target status evidence_id time_source',
    'patches': 'run_id has_patch patch_bytes modified_files added_lines deleted_lines diff_parse_ok apply_status patch_excerpt evidence_id',
    'test_evidence': 'evidence_id run_id source turn_number sequence_index test_command exit_code parsed_passed parsed_failed parse_confidence post_last_edit output_excerpt pipeline_status_uncertain',
    'quality_issues': 'issue_id run_id field_path issue_type description evidence_id',
    'evidence': 'evidence_id run_id trace_path json_pointer turn_number tool_call_id excerpt',
    'intake': 'trace_path accepted reason trace_hash',
}

def table(rows, name):
    return pd.DataFrame(rows, columns=COLUMNS[name].split())

@dataclass
class ExperimentData:
    runs: pd.DataFrame
    turns: pd.DataFrame
    tool_calls: pd.DataFrame
    tool_results: pd.DataFrame
    events: pd.DataFrame
    patches: pd.DataFrame
    test_evidence: pd.DataFrame
    quality_issues: pd.DataFrame
    evidence: pd.DataFrame
    intake: pd.DataFrame
    config: dict = field(default_factory=dict)

@dataclass
class MetricData:
    metrics: pd.DataFrame
    summary: dict
    pairing: pd.DataFrame = field(default_factory=pd.DataFrame)
    gold: pd.DataFrame = field(default_factory=pd.DataFrame)
    modules: pd.DataFrame = field(default_factory=pd.DataFrame)
    reviews: pd.DataFrame = field(default_factory=pd.DataFrame)

@dataclass
class FindingsData:
    findings: pd.DataFrame
    primary: pd.DataFrame
