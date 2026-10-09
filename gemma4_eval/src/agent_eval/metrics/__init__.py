"""Deterministic metrics. Availability is data, never a disguised zero."""
import pandas as pd
from .outcomes import outcome_summary
from .localization import gold_analysis

METHOD_VERSION='1.0.0'

def paired_outcomes(a,b):
    rows=[]
    for task in sorted(set(a.runs.task_id)|set(b.runs.task_id)):
        left=a.runs[a.runs.task_id==task];right=b.runs[b.runs.task_id==task]
        reason=None
        if len(left)!=1 or len(right)!=1:
            reason='duplicate_attempts' if len(left)>1 or len(right)>1 else 'missing_pair'
        elif pd.isna(left.iloc[0].resolved_nullable) or pd.isna(right.iloc[0].resolved_nullable):
            reason='unknown_outcome'
        elif left.iloc[0].repo!=right.iloc[0].repo:
            reason='repository_mismatch'
        x=left.iloc[0] if len(left) else None;y=right.iloc[0] if len(right) else None
        rows.append(dict(task_id=task,baseline_trace_path=x.trace_path if x is not None else None,comparison_trace_path=y.trace_path if y is not None else None,baseline_trace_hash=x.trace_hash if x is not None else None,comparison_trace_hash=y.trace_hash if y is not None else None,baseline_experiment=x.experiment_id if x is not None else None,comparison_experiment=y.experiment_id if y is not None else None,baseline_run_id=x.run_id if x is not None else None,comparison_run_id=y.run_id if y is not None else None,baseline_resolved=x.resolved_nullable if x is not None else None,comparison_resolved=y.resolved_nullable if y is not None else None,transition=None if reason else f'{"solved" if x.resolved_nullable else "failed"}→{"solved" if y.resolved_nullable else "failed"}',duration_delta=None if reason or pd.isna(x.duration_seconds) or pd.isna(y.duration_seconds) else y.duration_seconds-x.duration_seconds,availability='unavailable' if reason else 'available',unavailable_reason=reason,comparability='task/repository matched; configuration comparability requires researcher review'))
    return pd.DataFrame(rows)

def compute_metrics(experiment, gold_dir=None, repo_roots=None, compare_experiment=None, compare_dir=None):
    e=experiment;out=[];gold_rows=[]
    def add(run,mid,value,unit='count',availability=None,reason=None,ids=None,numerator=None,denominator=None,coverage=None):
        if value is None and availability is None:
            availability='unavailable'; reason=reason or 'Required evidence missing'
        elif availability is None:
            availability='available'
        refs=ids or e.evidence[e.evidence.run_id==run].evidence_id.head(1).tolist()
        out.append(dict(run_id=run,metric_id=mid,value=value,unit=unit,availability=availability,unavailable_reason=reason,method_version=METHOD_VERSION,evidence_ids=refs,numerator=numerator,denominator=denominator,coverage=coverage))
    if compare_dir:
        from ..ingest import load_experiment
        compare_experiment=load_experiment(compare_dir,experiment_id='comparison',redact_text=e.config.get('redact_text',True))
    pairing=paired_outcomes(e,compare_experiment) if compare_experiment is not None else pd.DataFrame()
    for r in e.runs.itertuples():
        rid=r.run_id
        t=e.turns[e.turns.run_id==rid];c=e.tool_calls[e.tool_calls.run_id==rid];res=e.tool_results[e.tool_results.run_id==rid];tests=e.test_evidence[e.test_evidence.run_id==rid]
        patch=e.patches[e.patches.run_id==rid].iloc[0]
        known=not pd.isna(r.resolved_nullable)
        add(rid,'C01',bool(r.resolved_nullable) if known else None,'resolved',denominator=1 if known else 0,coverage=int(known))
        final_tests=tests[tests.source=='external']
        ft=final_tests.iloc[0] if len(final_tests) else None
        add(rid,'C02',r.test_exit_code if not pd.isna(r.test_exit_code) else None,'final_test_exit_code',reason=None if ft is not None else 'No external verifier evidence',ids=final_tests.evidence_id.tolist())
        add(rid,'test_transitions',None,'transition',reason='Baseline statuses and stable test identities not supplied')
        add(rid,'C03',bool(patch.has_patch and patch.diff_parse_ok),'parseable_nonempty_patch',availability='partial',reason='Patch application not_verified in read-only analysis',ids=[patch.evidence_id],denominator=1,coverage=1)
        gold,reason,details=gold_analysis(e,r,gold_dir,repo_roots or {})
        gold_rows.extend(details)
        for mid,key in [('C04','discovery'),('C05','edit')]:
            add(rid,mid,gold[key] if gold else None,'ratio',reason=reason,denominator=gold['denominator'] if gold else None,ids=list({eid for x in details for eid in x.get('evidence_ids',[])}) or [patch.evidence_id])
        valid=c.schema_valid.dropna();valid_n=int(valid.eq(True).sum())
        add(rid,'C06',valid_n/len(valid) if len(valid) else None,'ratio',ids=c.evidence_id.tolist(),numerator=valid_n,denominator=len(valid),coverage=len(valid)/len(c) if len(c) else None)
        edits=c[c.name.isin(['edit_file','write_file'])]
        er=res[res.call_id.isin(edits.call_id)]
        good=int(er.status.eq('ok').sum())
        add(rid,'C07',good/len(edits) if len(edits) else None,'ratio',ids=edits.evidence_id.tolist()+er.evidence_id.tolist(),numerator=good,denominator=len(edits),coverage=len(er)/len(edits) if len(edits) else None,reason='No edit attempts' if not len(edits) else None)
        source_edits=c[c.successful_source_edit.eq(True)]
        agent_tests=tests[tests.source=='agent-side']
        after=agent_tests[agent_tests.post_last_edit.eq(True)]
        uncertain=bool(c[(c.sequence_index>source_edits.sequence_index.max())&c.inferred_mutation.eq(True)].shape[0]) if len(source_edits) else bool(c.inferred_mutation.eq(True).any())
        add(rid,'C08',bool(len(after)) if len(source_edits) else None,'post_last_confirmed_source_edit_test',availability='partial' if len(source_edits) and uncertain else None,reason='possible_untracked_edit: shell mutation heuristics present' if len(source_edits) and uncertain else 'No confirmed successful source edit' if not len(source_edits) else None,ids=source_edits.evidence_id.tolist()+after.evidence_id.tolist())
        exhaustion=res[res.error_type.eq('BudgetExceeded')]
        exhausted=bool(len(exhaustion)) or r.harness_budget is not None and not pd.isna(r.harness_budget) and not pd.isna(r.budgeted_tool_calls) and r.budgeted_tool_calls>=r.harness_budget
        stage=('pre_edit' if not len(source_edits) else 'pre_verification' if not len(after) else 'post_edit') if exhausted else 'not_observed'
        add(rid,'C09',stage,'stage',ids=exhaustion.evidence_id.tolist())
        add(rid,'C10',r.duration_seconds if not pd.isna(r.duration_seconds) else None,'seconds')
        add(rid,'C11',None,'stage',reason='Populated by classify_failures rule engine')
        pair=pairing[pairing.task_id==r.task_id] if len(pairing) else pd.DataFrame()
        pr=pair.iloc[0] if len(pair) else None
        add(rid,'C12',pr.transition if pr is not None and pr.availability=='available' else None,'paired_transition',reason=pr.unavailable_reason if pr is not None else 'Second batch not supplied')
        supplementary={'turn_count':len(t),'regular_agent_llm_calls':int((~t.is_compaction.astype(bool)).sum()),'compaction_turns':int(t.is_compaction.sum()),'tool_attempts':len(c),'tool_result_records':len(res),'budgeted_tool_calls':r.budgeted_tool_calls,'reported_llm_calls':r.reported_llm_calls,'confirmed_source_edits':len(source_edits),'first_source_edit_call':int(source_edits.sequence_index.min()) if len(source_edits) else None,'agent_tests':len(agent_tests),'post_edit_agent_tests':len(after),'possible_untracked_edit':uncertain,'patch_bytes':patch.patch_bytes,'patch_file_count':len(patch.modified_files),'has_patch':bool(patch.has_patch),'tool_errors':int(res.status.eq('error').sum()),'unmatched_calls':int((~c.result_linked.astype(bool)).sum()),'budget_instruction_conflict':bool(e.quality_issues[(e.quality_issues.run_id==rid)&e.quality_issues.issue_type.eq('budget_instruction_conflict')].shape[0])}
        for k,v in supplementary.items():
            add(rid,k,None if v is None or (isinstance(v,float) and pd.isna(v)) else v,ids=c.evidence_id.tolist() if k in ('tool_attempts','confirmed_source_edits','first_source_edit_call') else None)
        for col in ['prompt_tokens','completion_tokens','reasoning_tokens']:
            values=t[col].dropna()
            add(rid,col,int(values.sum()) if len(values) else None,'reported_inference_tokens',availability='partial' if 0<len(values)<len(t) else None,reason='Usage missing in some turns' if 0<len(values)<len(t) else 'No per-turn usage' if not len(values) else None,ids=t.evidence_id.tolist(),coverage=len(values)/len(t) if len(t) else None)
        for k in ['parsed_passed','parsed_failed']:
            value=ft[k] if ft is not None and not pd.isna(ft[k]) else None
            add(rid,'final_'+k,value,'tests',ids=final_tests.evidence_id.tolist())
        exploratory=c[c.name.isin(['read_file','search_similar_code']) | c.command_category.isin(['search','read'])]
        add(rid,'duplicate_reads_searches',int(exploratory.duplicated(['name','arguments_summary']).sum()),ids=exploratory.evidence_id.tolist())
        for tool in sorted(c.name.unique()):
            tc=c[c.name==tool];tr=res[res.call_id.isin(tc.call_id)]
            for label,value in [('attempts',len(tc)),('ok',int(tr.status.eq('ok').sum())),('error',int(tr.status.eq('error').sum())),('unknown',len(tc)-int(tr.status.isin(['ok','error']).sum()))]:
                add(rid,f'tool.{tool}.{label}',value,ids=tc.evidence_id.tolist()+tr.evidence_id.tolist())
    summary=outcome_summary(e.runs)
    summary.update(dict(discovered=len(e.intake),accepted=int(e.intake.accepted.sum()),rejected=int((~e.intake.accepted.astype(bool)).sum()),method_version=METHOD_VERSION,experiment_id=e.config.get('experiment_id'),percentage_policy='Known outcomes only; counts and coverage accompany rates'))
    modules=pd.DataFrame([
        dict(module='gold_localization',availability='available' if gold_rows else 'unavailable',prerequisites='Gold patch + pristine Python baseline + returned content; per-run coverage in C04/C05'),
        dict(module='patch_scope',availability='available',prerequisites='Final unified diff; file counts and byte size only'),
        dict(module='graph_retrieval',availability='available' if e.events.kind.eq('graph').any() else 'unavailable',prerequisites='Graph tool events; semantic retrieval quality requires gold'),
        dict(module='edit_survival',availability='unavailable',prerequisites='Intermediate file snapshots and final-line provenance required'),
        dict(module='test_recovery',availability='partial',prerequisites='Ordered agent-side test output + edit evidence; surfaced by findings'),
        dict(module='reasoning_judge',availability='unavailable',prerequisites='Optional calibrated manual/judge rubric; no remote judge in offline V1'),
        dict(module='reliability',availability='partial',prerequisites='Wilson resolution interval; repeated attempts required for pass@k'),
        dict(module='cross_config',availability='partial' if len(pairing) else 'unavailable',prerequisites='Second batch + researcher confirmation of comparable evaluation configurations'),
    ])
    return __import__('agent_eval.models',fromlist=['MetricData']).MetricData(pd.DataFrame(out,columns='run_id metric_id value unit availability unavailable_reason method_version evidence_ids numerator denominator coverage'.split()),summary,pairing,pd.DataFrame(gold_rows),modules)
