"""Transparent factual findings; primary stages are tentative, never causal."""
import re
import pandas as pd
from .models import FindingsData


def classify_failures(e, metrics):
    rows=[];primary=[]
    for r in e.runs.itertuples():
        rid=r.run_id;c=e.tool_calls[e.tool_calls.run_id==rid];res=e.tool_results[e.tool_results.run_id==rid];tests=e.test_evidence[e.test_evidence.run_id==rid];patch=e.patches[e.patches.run_id==rid].iloc[0]
        local=[]
        def finding(category,headline,intervention,ids,confidence='high',severity='warning',observation='observed',stage=None):
            refs=ids or e.evidence[e.evidence.run_id==rid].evidence_id.head(1).tolist()
            row=dict(finding_id=f'{rid}:f{len(local)+1}',run_id=rid,category=category,severity=severity,confidence=confidence,headline=headline,evidence_ids=refs,suggested_intervention=intervention,observation=observation,stage=stage)
            local.append(row)
        if r.harness_status in ('ERROR','FAILED','FAILURE') and r.harness_error_excerpt:
            finding('environment_or_harness','Harness reported an error: '+r.harness_error_excerpt[:250],'Inspect the recorded harness error before attributing an agent failure.',[],stage='environment_or_harness')
        invalid=c[c.schema_valid.eq(False)]
        if len(invalid):
            finding('tool_format',f'{len(invalid)} tool calls fail recorded schema checks','Provide parameter examples and validate tool arguments before dispatch.',invalid.evidence_id.tolist(),stage='tool_format')
        edit_errors=res[res.call_id.isin(c[c.name.isin(['edit_file','write_file'])].call_id)&res.status.eq('error')]
        path_errors=edit_errors[edit_errors.error_type.eq('FileWriteError')]
        if len(path_errors):
            finding('path_restriction','File write rejected by workspace path restriction','Align reproduction-file paths with each tool’s permitted workspace.',path_errors.evidence_id.tolist(),stage='edit_execution')
        other_edit=edit_errors[~edit_errors.error_type.isin(['BudgetExceeded','FileWriteError'])]
        if len(other_edit):
            finding('edit_execution',f'{len(other_edit)} editing attempts returned errors','Inspect exact tool error and use a bounded retry with corrected parameters.',other_edit.evidence_id.tolist(),stage='edit_execution')
        exhaustion=res[res.error_type.eq('BudgetExceeded')]
        if len(exhaustion):
            finding('budget','Harness rejected calls after tool budget exhaustion','Reserve explicit calls for final editing, targeted verification, and submission.',exhaustion.evidence_id.tolist(),stage='budget')
        source=c[c.successful_source_edit.eq(True)]
        agent=tests[tests.source.eq('agent-side')];after=agent[agent.post_last_edit.eq(True)]
        if len(source) and not len(after):
            finding('verification_gap','No recorded agent-side test after the last confirmed source edit','Run a targeted reproduction and relevant test after the final source edit.',source.evidence_id.tolist(),stage='verification')
        if len(source) and source.sequence_index.min()>=max(15,len(c)*.7):
            finding('late_implementation',f'First confirmed source edit at attempt {int(source.sequence_index.min())} of {len(c)}','Set an earlier implementation checkpoint and bound repeated exploration.',source.evidence_id.head(1).tolist(),'medium')
        failed_agent=agent[(agent.exit_code.notna()&agent.exit_code.ne(0))|agent.parsed_failed.fillna(0).gt(0)]
        for test in failed_agent.itertuples():
            later=c[(c.sequence_index>test.sequence_index)&(c.successful_source_edit.eq(True)|c.inferred_mutation.eq(True))]
            if not len(later):
                finding('feedback_gap','Failed agent-side test has no later recorded corrective edit','Inspect the failure and require an explicit recovery or documented baseline failure.',[test.evidence_id],'medium')
                break
        command_errors=res[res.error_type.eq('CommandError')]
        if len(command_errors):
            finding('command_failure',f'{len(command_errors)} command results report CommandError','Separate expected red reproductions from unrelated script or environment errors.',command_errors.evidence_id.tolist(),'medium')
        external=tests[tests.source.eq('external')]
        if not pd.isna(r.test_exit_code) and r.test_exit_code!=0:
            finding('external_verification_failed','Independent final verification exited nonzero','Inspect failing identities and assertions before selecting a corrective intervention.',external.evidence_id.tolist(),stage='verification')
        if r.harness_status=='SUCCESS' and r.resolved_nullable is False:
            finding('outcome_terminology','Harness completed successfully; evaluator did not resolve the task','Keep completion status and official resolution separate in dashboards.',external.evidence_id.tolist(),severity='info')
        if not patch.has_patch and r.resolved_nullable is False:
            finding('no_patch','Unresolved attempt has no final patch','Check submission and whether a source change was ever captured.',[patch.evidence_id],stage='no_patch')
        if c.inferred_mutation.eq(True).any():
            finding('possible_untracked_edit','Shell commands contain possible file mutations','Record explicit diffs or file-state snapshots to establish the final edit boundary.',c[c.inferred_mutation.eq(True)].evidence_id.tolist(),'low',observation='heuristic')
        duplicate=c[c.duplicated(['name','arguments_summary']) & (c.name.isin(['read_file','search_similar_code'])|c.command_category.isin(['read','search']))]
        if len(duplicate):
            finding('repeated_exploration',f'{len(duplicate)} exact repeated read/search calls','Keep concise file and symbol notes to avoid repeating identical exploration.',duplicate.evidence_id.tolist(),'medium')
        compact=e.turns[(e.turns.run_id==rid)&e.turns.is_compaction.eq(True)]
        if len(compact) and len(duplicate[duplicate.turn_number>compact.turn_number.min()]):
            finding('lost_state_hypothesis','Repeated exploration occurs after a compaction candidate','Review compacted notes for retained symbols, changes, and next actions.',compact.evidence_id.tolist()+duplicate.evidence_id.tolist(),'low',observation='hypothesis')
        conflict=e.quality_issues[(e.quality_issues.run_id==rid)&e.quality_issues.issue_type.eq('budget_instruction_conflict')]
        if len(conflict):
            finding('budget_instruction_conflict','Recorded budget instructions disagree','Generate budget instructions from authoritative harness settings.',conflict.evidence_id.tolist())
        if r.resolved_nullable is False and re.search(r'\b(?:all tests pass|tests? (?:are )?passing|successfully (?:fixed|resolved)|i (?:have )?(?:fixed|resolved)|issue (?:is |has been )?(?:fixed|resolved))\b',r.final_text_excerpt or '',re.I):
            finding('success_claim_mismatch','Final response includes a success claim despite unresolved evaluation','Require claims to cite observed verification and distinguish evaluator outcome.',external.evidence_id.tolist(),'medium',observation='heuristic')
        gold=metrics.metrics[(metrics.metrics.run_id==rid)&metrics.metrics.metric_id.eq('C04')]
        if len(gold) and gold.iloc[0].availability=='available' and gold.iloc[0].value<1:
            finding('localization','Some mapped gold functions lack returned-view evidence','Improve targeted retrieval of reference-relevant functions.',gold.iloc[0].evidence_ids,'medium',stage='localization')
        # Primary label ranks observable blockers, not inferred semantic root causes.
        if r.resolved_nullable is True:
            stage='resolved';confidence='high';refs=external.evidence_id.tolist() or [patch.evidence_id]
        else:
            priorities=['environment_or_harness','budget','no_patch','tool_format','edit_execution','localization','verification']
            selected=next((f for s in priorities for f in local if f['stage']==s),None)
            stage=selected['stage'] if selected else 'unknown';confidence=selected['confidence'] if selected else 'low';refs=selected['evidence_ids'] if selected else [patch.evidence_id]
        primary.append(dict(run_id=rid,task_id=r.task_id,primary_stage=stage,confidence=confidence,evidence_ids=refs,attribution='tentative observable stage; not established root cause'))
        rows.extend(local)
        idx=metrics.metrics.run_id.eq(rid)&metrics.metrics.metric_id.eq('C11')
        metrics.metrics.loc[idx,'value']=stage
        metrics.metrics.loc[idx,'availability']='available' if stage!='unknown' else 'unavailable'
        metrics.metrics.loc[idx,'unavailable_reason']=None if stage!='unknown' else 'Insufficient positive evidence for a failure stage'
        for i in metrics.metrics.index[idx]:
            metrics.metrics.at[i,'evidence_ids']=refs
    return FindingsData(pd.DataFrame(rows,columns='finding_id run_id category severity confidence headline evidence_ids suggested_intervention observation stage'.split()),pd.DataFrame(primary,columns='run_id task_id primary_stage confidence evidence_ids attribution'.split()))
