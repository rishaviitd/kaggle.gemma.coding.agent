"""Descriptive, evidence-linked batch rollups over normalized tables."""
from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import pandas as pd
from .metrics.outcomes import outcome_summary


@dataclass
class BatchReportData:
    overview: dict
    repositories: pd.DataFrame
    workflow: pd.DataFrame
    budget: pd.DataFrame
    tool_reliability: pd.DataFrame
    primary_stages: pd.DataFrame
    finding_prevalence: pd.DataFrame
    cost: pd.DataFrame
    tasks: pd.DataFrame
    quality: pd.DataFrame
    submission: dict
    gold_metrics: pd.DataFrame
    pairing: pd.DataFrame
    modules: pd.DataFrame


def _number(value):
    if value is None:
        return None
    try:
        return None if bool(pd.isna(value)) else value
    except (TypeError, ValueError):
        return value


def _ranked_outcome(value):
    if value is None or bool(pd.isna(value)):
        return 'unknown'
    return 'resolved' if bool(value) else 'unresolved'


def summarize_batch(experiment, metrics, findings):
    """Return deterministic report tables. Rates always include coverage/denominators."""
    e, m, f = experiment, metrics, findings
    runs = e.runs.sort_values(['repo', 'task_id', 'attempt_id', 'run_id']).copy()
    lookup = {(row.run_id, row.metric_id): row for row in m.metrics.itertuples()}
    def metric(rid, mid):
        item = lookup.get((rid, mid))
        return _number(item.value) if item is not None and item.availability != 'unavailable' else None
    primary = f.primary.set_index('run_id') if len(f.primary) else pd.DataFrame()
    patch = e.patches.set_index('run_id') if len(e.patches) else pd.DataFrame()
    issues = e.quality_issues.groupby('run_id').issue_type.apply(list).to_dict() if len(e.quality_issues) else {}
    calls_by_run = {rid: group for rid, group in e.tool_calls.groupby('run_id')} if len(e.tool_calls) else {}
    tests_by_run = {rid: group for rid, group in e.test_evidence.groupby('run_id')} if len(e.test_evidence) else {}
    task_rows = []
    for r in runs.itertuples():
        rid = r.run_id
        calls = calls_by_run.get(rid)
        tests = tests_by_run.get(rid)
        first_edit = metric(rid, 'first_source_edit_call')
        source_edits = metric(rid, 'confirmed_source_edits')
        post_test = metric(rid, 'C08')
        agent_tests = metric(rid, 'agent_tests')
        submit = int(calls.name.eq('submit_patch').sum()) if calls is not None else 0
        agent_test_rows = tests[tests.source.eq('agent-side')] if tests is not None else pd.DataFrame()
        external = tests[tests.source.eq('external')] if tests is not None else pd.DataFrame()
        external_exit = _number(r.test_exit_code)
        stage = primary.loc[rid].primary_stage if rid in primary.index else 'unknown'
        confidence = primary.loc[rid].confidence if rid in primary.index else 'low'
        task_rows.append(dict(
            run_id=rid, task_id=r.task_id, attempt_id=int(r.attempt_id), repo=r.repo,
            outcome=_ranked_outcome(r.resolved_nullable), resolved_nullable=_number(r.resolved_nullable),
            harness_status=r.harness_status, final_test_exit_code=external_exit,
            external_test_recorded=len(external)>0,
            external_test_passed=external_exit == 0 if external_exit is not None else None,
            has_patch=bool(patch.loc[rid].has_patch) if rid in patch.index else None,
            parseable_patch=bool(patch.loc[rid].has_patch and patch.loc[rid].diff_parse_ok) if rid in patch.index else None,
            confirmed_source_edits=source_edits,
            source_edit_recorded=bool(source_edits) if source_edits is not None else None,
            first_source_edit_call=first_edit,
            post_edit_agent_test=post_test,
            post_edit_test_availability=lookup[(rid,'C08')].availability if (rid,'C08') in lookup else 'unavailable',
            agent_test_count=agent_tests,
            agent_test_failed=bool(len(agent_test_rows[(agent_test_rows.exit_code.notna() & agent_test_rows.exit_code.ne(0)) | agent_test_rows.parsed_failed.fillna(0).gt(0)])) if len(agent_test_rows) else False,
            budget_stage=metric(rid,'C09'), budgeted_tool_calls=_number(r.budgeted_tool_calls),
            observed_harness_limit=_number(r.harness_budget),
            tool_attempts=metric(rid,'tool_attempts'), tool_result_records=metric(rid,'tool_result_records'),
            tool_errors=metric(rid,'tool_errors'), unmatched_calls=metric(rid,'unmatched_calls'),
            submit_attempts=submit, repeated_submit_attempts=max(0,submit-1),
            duration_seconds=_number(r.duration_seconds), completion_tokens=metric(rid,'completion_tokens'),
            prompt_tokens=metric(rid,'prompt_tokens'),
            primary_stage=stage, stage_confidence=confidence,
            quality_issue_types=sorted(set(issues.get(rid,[]))),
            trace_path=r.trace_path, trace_hash=r.trace_hash,
        ))
    tasks = pd.DataFrame(task_rows, columns='run_id task_id attempt_id repo outcome resolved_nullable harness_status final_test_exit_code external_test_recorded external_test_passed has_patch parseable_patch confirmed_source_edits source_edit_recorded first_source_edit_call post_edit_agent_test post_edit_test_availability agent_test_count agent_test_failed budget_stage budgeted_tool_calls observed_harness_limit tool_attempts tool_result_records tool_errors unmatched_calls submit_attempts repeated_submit_attempts duration_seconds completion_tokens prompt_tokens primary_stage stage_confidence quality_issue_types trace_path trace_hash'.split())
    overview = outcome_summary(e.runs)
    overview.update(dict(discovered=len(e.intake),accepted=int(e.intake.accepted.sum()) if len(e.intake) else 0,
                         rejected=int((~e.intake.accepted.astype(bool)).sum()) if len(e.intake) else 0,
                         experiment_id=e.config.get('experiment_id'),unit='attempts',
                         outcome_definition='Official final.result.resolved; unknown outcomes excluded',
                         confidence_interval='Wilson 95%, descriptive',
                         method_version=m.summary.get('method_version')))
    repositories=[]
    for repo, group in runs.groupby('repo',sort=True):
        s=outcome_summary(group)
        repositories.append(dict(repo=repo,attempts=s['total_runs'],known_outcomes=s['known_outcomes'],resolved=s['resolved'],unresolved=s['unresolved'],unknown=s['unknown_outcomes'],resolved_rate=s['resolved_rate'],coverage=s['outcome_coverage'],wilson_low=s['wilson_95'][0],wilson_high=s['wilson_95'][1]))
    repository_frame=pd.DataFrame(repositories)
    total=len(tasks)
    def workflow_row(label, numerator, denominator, note, evidence_kind):
        return dict(indicator=label,numerator=numerator,denominator=denominator,rate=numerator/denominator if denominator else None,coverage=denominator/total if total else None,denominator_definition=note,evidence_kind=evidence_kind)
    source_eligible=tasks[tasks.source_edit_recorded.eq(True)] if total else tasks
    known=tasks[tasks.outcome.ne('unknown')] if total else tasks
    test_known=tasks[tasks.external_test_recorded.eq(True)&tasks.final_test_exit_code.notna()] if total else tasks
    workflow=pd.DataFrame([
        workflow_row('Officially resolved',int(known.outcome.eq('resolved').sum()),len(known),'Known official outcomes','official'),
        workflow_row('Parseable final patch',int(tasks.parseable_patch.eq(True).sum()) if total else 0,total,'All accepted attempts; application not verified','trace-derived'),
        workflow_row('Confirmed source edit',int(tasks.source_edit_recorded.eq(True).sum()) if total else 0,total,'All accepted attempts; edit/write tool success on source files','trace-derived'),
        workflow_row('Agent test after final confirmed edit',int(source_eligible.post_edit_agent_test.eq(True).sum()),len(source_eligible),'Attempts with at least one confirmed source edit; test presence, not test pass','heuristic'),
        workflow_row('Independent final test exit 0',int(test_known.external_test_passed.eq(True).sum()),len(test_known),'Attempts with recorded external test exit; test status, not task resolution','external'),
    ])
    budget_labels=['pre_edit','pre_verification','post_edit','not_observed','unknown']
    budget_counts=Counter(tasks.budget_stage.fillna('unknown').tolist()) if total else Counter()
    budget=pd.DataFrame([dict(stage=k,attempts=budget_counts.get(k,0),denominator=total,rate=budget_counts.get(k,0)/total if total else None,meaning={'pre_edit':'Budget reached before a confirmed source edit','pre_verification':'Budget reached after source edit and before a later agent test','post_edit':'Budget reached after source edit and later agent test','not_observed':'Exhaustion not observed in trace or harness result','unknown':'Required budget evidence missing'}[k]) for k in budget_labels])
    result_lookup={(r.run_id,r.call_id):r for r in e.tool_results.itertuples()}
    tool_counts=defaultdict(lambda:Counter())
    for call in e.tool_calls.itertuples():
        result=result_lookup.get((call.run_id,call.call_id))
        status=result.status if result and result.status in ('ok','error') else 'unknown'
        tool_counts[call.name][status]+=1
    tool_rows=[]
    for name in sorted(tool_counts):
        counts=tool_counts[name];attempts=sum(counts.values())
        tool_rows.append(dict(tool=name,attempts=attempts,ok=counts['ok'],error=counts['error'],unknown_result=counts['unknown'],linked_result_coverage=(attempts-counts['unknown'])/attempts if attempts else None,execution_ok_rate=counts['ok']/attempts if attempts else None,note='Execution status only; tests can fail with status ok'))
    tools=pd.DataFrame(tool_rows)
    unresolved=tasks[tasks.outcome.eq('unresolved')] if total else tasks
    stage_counts=Counter(unresolved.primary_stage.tolist()) if total else Counter()
    primary_stages=pd.DataFrame([dict(stage=stage,attempts=count,denominator=len(unresolved),rate=count/len(unresolved) if len(unresolved) else None,attribution='Tentative observable stage; not established root cause') for stage,count in sorted(stage_counts.items(),key=lambda item:(-item[1],item[0]))])
    finding_rows=[]
    if len(f.findings):
        for category, group in f.findings.groupby('category',sort=True):
            affected=group.run_id.nunique()
            finding_rows.append(dict(category=category,affected_attempts=affected,denominator=total,rate=affected/total if total else None,
                                     unresolved_affected=group[group.run_id.isin(unresolved.run_id)].run_id.nunique(),
                                     confidence=', '.join(sorted(group.confidence.dropna().unique())),
                                     observation=', '.join(sorted(group.observation.dropna().unique())),
                                     suggested_intervention=group.suggested_intervention.iloc[0],
                                     evidence_ids=sorted(set(eid for ids in group.evidence_ids for eid in ids))))
    finding_prevalence=pd.DataFrame(finding_rows).sort_values(['affected_attempts','category'],ascending=[False,True]) if finding_rows else pd.DataFrame()
    cost_rows=[]
    for group_name, group in [('all',tasks),('resolved',tasks[tasks.outcome.eq('resolved')] if total else tasks),('unresolved',unresolved),('unknown',tasks[tasks.outcome.eq('unknown')] if total else tasks)]:
        for key,unit in [('duration_seconds','seconds'),('budgeted_tool_calls','calls'),('tool_attempts','attempts'),('completion_tokens','reported tokens'),('prompt_tokens','reported tokens')]:
            values=pd.to_numeric(group[key],errors='coerce').dropna() if len(group) else pd.Series(dtype=float)
            cost_rows.append(dict(group=group_name,metric=key,unit=unit,available=len(values),total_attempts=len(group),coverage=len(values)/len(group) if len(group) else None,
                                  median=float(values.median()) if len(values) else None,p90=float(values.quantile(.9)) if len(values) else None,
                                  min=float(values.min()) if len(values) else None,max=float(values.max()) if len(values) else None))
    cost=pd.DataFrame(cost_rows)
    quality_counts=e.quality_issues.groupby('issue_type').run_id.nunique().to_dict() if len(e.quality_issues) else {}
    quality=pd.DataFrame([dict(issue_type=k,affected_attempts=v,denominator=total,rate=v/total if total else None) for k,v in sorted(quality_counts.items(),key=lambda item:(-item[1],item[0]))])
    submit_total=int(tasks.submit_attempts.sum()) if total else 0
    repeated=int(tasks.repeated_submit_attempts.sum()) if total else 0
    submission=dict(attempts=submit_total,attempts_with_submission=int(tasks.submit_attempts.gt(0).sum()) if total else 0,
                    attempts_with_repeat=int(tasks.submit_attempts.gt(1).sum()) if total else 0,
                    attempts_with_50_plus=int(tasks.submit_attempts.ge(50).sum()) if total else 0,
                    repeated_attempts=repeated,
                    top_tasks=tasks.nlargest(10,'submit_attempts')[['run_id','task_id','repo','outcome','submit_attempts','budgeted_tool_calls','duration_seconds']].to_dict('records') if total else [],
                    note='These are raw attempted submit_patch calls. Repeated calls may occur after an accepted submission; they are not budget-counted calls or distinct patches.')
    gold_metrics=m.metrics[m.metrics.metric_id.isin(['C04','C05'])][['run_id','metric_id','value','availability','unavailable_reason','denominator','evidence_ids']].copy()
    return BatchReportData(overview,repository_frame,workflow,budget,tools,primary_stages,finding_prevalence,cost,tasks,quality,submission,gold_metrics,m.pairing.copy(),m.modules.copy())
