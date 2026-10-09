"""Adapter for vllm-model-trace-v1; raw JSON is only a fallback."""
import json
import math
import re
from collections import Counter
from jsonschema import Draft202012Validator
from ..events import EDIT_TOOLS, GRAPH_TOOLS, command_category, mutation_hint, source_path, parse_patch, parse_test_counts
from ..evidence import excerpt

SCHEMA_VERSION = 'vllm-model-trace-v1'

def mapping(value):
    return value if isinstance(value, dict) else {}

def parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value

def nullable_schema(schema):
    if isinstance(schema, list):
        return [nullable_schema(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: nullable_schema(v) for k,v in schema.items()}
    if out.get('nullable') and isinstance(out.get('type'), str):
        out['type'] = [out['type'], 'null']
    return out

def normalize(data, path, trace_hash, experiment_id, rows, redact_text=True, include_reasoning_in_viewer=False):
    run, final = mapping(data.get('run')), mapping(mapping(data.get('final')).get('result'))
    task_id = str(run.get('task_id') or final.get('task_id'))
    rid = f'{experiment_id}:{task_id}:{trace_hash}'
    # Identical file content at multiple paths is still an explicit attempt.
    if any(r['run_id'] == rid for r in rows['runs']):
        rid += f':copy{sum(r["trace_hash"] == trace_hash for r in rows["runs"])+1}'
    def ev(pointer, text, turn=None, call=None):
        eid = f'{rid}:e{sum(e["run_id"] == rid for e in rows["evidence"])+1}'
        rows['evidence'].append(dict(evidence_id=eid,run_id=rid,trace_path=str(path),json_pointer=pointer,turn_number=turn,tool_call_id=call,excerpt=excerpt(text, enabled=redact_text)))
        return eid
    def issue(field, kind, description, eid=None):
        rows['quality_issues'].append(dict(issue_id=f'{rid}:q{sum(q["run_id"] == rid for q in rows["quality_issues"])+1}',run_id=rid,field_path=field,issue_type=kind,description=description,evidence_id=eid or run_eid))
    def event(turn, order, kind, tool, target, status, eid):
        rows['events'].append(dict(event_id=f'{rid}:v{sum(v["run_id"] == rid for v in rows["events"])+1}',run_id=rid,turn_number=turn,event_order=order,kind=kind,tool_name=tool,target=excerpt(target,200,redact_text),status=status,evidence_id=eid,time_source='sequence_only; model timestamps approximate'))
    root_pointer='/final/result' if isinstance(mapping(data.get('final')).get('result'),dict) else '/run' if isinstance(data.get('run'),dict) else ''
    run_eid = ev(root_pointer, {k:v for k,v in (final or run).items() if k not in ('agent_patch','test_output')})
    def numeric(value, field, integer=False):
        if value is None:
            return None
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or integer and not isinstance(value,int):
            issue(field,'invalid_numeric',f'Expected {"integer" if integer else "finite number"}; preserved raw value in source evidence')
            return None
        return value
    final=dict(final)
    for field in ('duration_seconds','tool_calls','total_llm_calls','total_tokens','test_exit_code'):
        final[field]=numeric(final.get(field),'final.result.'+field,field!='duration_seconds')
    if run.get('task_id') and final.get('task_id') and run['task_id'] != final['task_id']:
        issue('final.result.task_id','task_id_mismatch','run.task_id and final.result.task_id disagree')
    if data.get('schema_version') != SCHEMA_VERSION:
        issue('schema_version','unknown_schema',f"Unknown schema {data.get('schema_version')!r}; compatible-shape fallback")
    resolved = final.get('resolved') if isinstance(final.get('resolved'), bool) else None
    if resolved is None:
        issue('final.result.resolved','missing_outcome','Resolution unknown; excluded from resolved-rate denominator')
    patch = final.get('agent_patch') or ''
    if not isinstance(patch,str):
        issue('final.result.agent_patch','invalid_type','Expected patch text'); patch = ''
    patch_eid = ev('/final/result/agent_patch',patch) if 'agent_patch' in final else run_eid
    rows['patches'].append(dict(run_id=rid, **parse_patch(patch),patch_excerpt=excerpt(patch,16000,redact_text),evidence_id=patch_eid))
    calls, results, claims, seq = [], [], set(), 0
    harness_observed=[]
    call_counts = Counter()
    turn_numbers = []
    for ti, turn in enumerate(data['turns']):
        if not isinstance(turn,dict):
            issue(f'turns.{ti}','invalid_turn','Non-object turn skipped'); continue
        n = turn.get('turn',ti+1)
        if not isinstance(n,(int,float)) or n in turn_numbers:
            issue(f'turns.{ti}.turn','invalid_turn_number','Missing, nonnumeric or duplicate number; using sequence index'); n=ti+1
        turn_numbers.append(n)
        inp, out = mapping(turn.get('input')), mapping(turn.get('output'))
        request = mapping(inp.get('vllm_request'))
        response = mapping(out.get('vllm_response_raw')) or mapping(parsed(out.get('raw_json')))
        choices = response.get('choices') or [{}]
        choice = mapping(choices[0]); message = mapping(choice.get('message'))
        usage = dict(mapping(response.get('usage') or out.get('usage')))
        for field in ('prompt_tokens','completion_tokens'):
            usage[field]=numeric(usage.get(field),f'turns.{ti}.usage.{field}',True)
        details=mapping(usage.get('completion_tokens_details'))
        reasoning_tokens=numeric(details.get('reasoning_tokens'),f'turns.{ti}.usage.reasoning_tokens',True)
        content = out.get('assistant_content',message.get('content'))
        tools = out.get('tool_calls')
        if tools is None:
            tools = message.get('tool_calls') or []
        if not isinstance(tools,list):
            issue(f'turns.{ti}.output.tool_calls','invalid_type','Tool calls must be a list'); tools=[]
        messages = request.get('messages') or []
        prompt = '\n'.join(str(m.get('content') or '') for m in messages if isinstance(m,dict) and m.get('role') in ('system','user'))
        # Extract only explicit limit declarations, excluding checkpoint call numbers.
        for match in re.finditer(r'(?:you have(?: only)?|(?:maximum|max|limit|budget)(?:\s+(?:of|is|:))?|limited to)\s*(\d+)\s+(?:total\s+)?tool\s+calls',prompt,re.I):
            claims.add(int(match[1]))
        for match in re.finditer(r'tool\s+calls?\s+(?:allowance|limit|budget)\s*:\s*(\d+)',prompt,re.I):
            claims.add(int(match[1]))
        next_messages = mapping(mapping(data['turns'][ti+1].get('input')).get('vllm_request')).get('messages',[]) if ti+1<len(data['turns']) and isinstance(data['turns'][ti+1],dict) else []
        summary_cue = bool(re.search(r'summariz|summaris|compaction|context.{0,15}(?:reset|summary)|the ai agent is tasked',prompt+'\n'+str(content or ''),re.I))
        compact = bool(turn.get('is_compaction') or (not tools and summary_cue and len(messages)<=2 and ti+1<len(data['turns']) and len(next_messages)>len(messages)))
        turn_eid = ev(f'/turns/{ti}/output',content or '(no assistant text)',n)
        rows['turns'].append(dict(run_id=rid,turn_number=n,created_epoch=numeric(response.get('created'),f'turns.{ti}.created'),finish_reason=choice.get('finish_reason'),prompt_tokens=usage.get('prompt_tokens'),completion_tokens=usage.get('completion_tokens'),reasoning_tokens=reasoning_tokens,is_compaction=compact,compaction_confidence='explicit' if turn.get('is_compaction') else ('heuristic_high' if compact else None),assistant_text_available=bool(content),assistant_excerpt=excerpt(content or '',enabled=redact_text),reasoning_excerpt=excerpt(out.get('reasoning') or message.get('reasoning') or '',enabled=redact_text) if include_reasoning_in_viewer else None,evidence_id=turn_eid))
        if compact:
            event(n,seq+.1,'compaction',None,None,'heuristic' if not turn.get('is_compaction') else 'explicit',turn_eid)
        schemas = {}
        for decl in request.get('tools') or []:
            f = mapping(decl.get('function'))
            if f.get('name'):
                schemas[f['name']] = f.get('parameters',{})
        for ci, call in enumerate(tools):
            seq += 1
            call = mapping(call); fn = mapping(call.get('function'))
            name = call.get('name') or fn.get('name') or 'unknown'
            original = call.get('id') or f'missing-{ti}-{ci}'
            call_counts[original] += 1
            cid = original if call_counts[original]==1 else f'{original}#duplicate{call_counts[original]}'
            if call_counts[original]>1:
                issue(f'turns.{ti}.output.tool_calls.{ci}.id','duplicate_call_id','Repeated call ID disambiguated; result linkage may be ambiguous')
            raw = call.get('arguments_raw_json',fn.get('arguments'))
            structured = call.get('arguments')
            args_from_raw = parsed(raw)
            valid_json = isinstance(args_from_raw,dict) if raw is not None else None
            args = structured if isinstance(structured,dict) else mapping(args_from_raw)
            errors = []
            if raw is not None and not valid_json:
                errors.append('parse_error')
            if isinstance(structured,dict) and raw is not None and structured != args_from_raw:
                errors.append('raw_parsed_discrepancy')
            schema = schemas.get(name)
            schema_valid = None
            if schema is None:
                errors.append('undeclared_tool' if schemas else 'schema_not_available')
                schema_valid = False if schemas else None
            else:
                try:
                    validation = list(Draft202012Validator(nullable_schema(schema)).iter_errors(args))
                    errors += [f'{e.validator}: {e.message}' for e in validation]
                    unknown = set(args) - set(schema.get('properties',{}))
                    errors += [f'unknown_key: {k}' for k in sorted(unknown)]
                    schema_valid = not errors
                except Exception as exc:
                    errors.append(f'invalid_declared_schema: {type(exc).__name__}'); schema_valid=None
            eid = ev(f'/turns/{ti}/output/tool_calls/{ci}' if 'tool_calls' in out else f'/turns/{ti}/output/vllm_response_raw/choices/0/message/tool_calls/{ci}' if 'vllm_response_raw' in out else f'/turns/{ti}/output/raw_json',call,n,original)
            for error in errors:
                issue(f'turns.{ti}.output.tool_calls.{ci}', 'tool_schema',error,eid)
            target = args.get('filepath') or args.get('path') or args.get('file_path')
            command = str(args.get('command') or '')
            cat = command_category(command) if name=='run_command' else None
            record = dict(_command=command,run_id=rid,call_id=cid,original_call_id=original,turn_number=n,sequence_index=seq,name=name,arguments_json_valid=valid_json,schema_valid=schema_valid,schema_issues=errors,arguments_summary=excerpt(args,3000,redact_text),arguments_raw_excerpt=excerpt(raw,1800,redact_text),requested_path=target,command_category=cat,result_linked=False,successful_source_edit=False,inferred_mutation=mutation_hint(command) if name=='run_command' else False,evidence_id=eid)
            calls.append(record)
        raw_results = turn.get('tool_results') or []
        if not isinstance(raw_results,list):
            issue(f'turns.{ti}.tool_results','invalid_type','Tool results must be a list'); raw_results=[]
        for ri, result in enumerate(raw_results):
            result = mapping(result)
            original = result.get('tool_call_id') or result.get('call_id')
            output = mapping(result.get('output')) or mapping(parsed(result.get('output_raw_json')))
            detail = mapping(output.get('details'))
            # Link by recorded ID, never fabricate result status for absent records.
            matches = [c for c in calls if c['original_call_id']==original and not c['result_linked']]
            c = matches[0] if len(matches)==1 else None
            cid = c['call_id'] if c else f'orphan-{ti}-{ri}'
            if not c:
                issue(f'turns.{ti}.tool_results.{ri}','unlinked_result','No unique unmatched call for result')
            else:
                c['result_linked']=True
                c['successful_source_edit'] = c['name'] in EDIT_TOOLS and output.get('status')=='ok' and source_path(c['requested_path'])
            text = '\n'.join(str(output.get(k) or detail.get(k) or '') for k in ('stdout','stderr','content','error_message'))
            if not text.strip():
                text=json.dumps(output,ensure_ascii=False)
            eid = ev(f'/turns/{ti}/tool_results/{ri}',output,n,original)
            exit_code = numeric(output.get('exit_code',detail.get('exit_code')),f'turns.{ti}.tool_results.{ri}.exit_code',True)
            rec = dict(run_id=rid,call_id=cid,original_call_id=original,turn_number=n,status=output.get('status'),error_type=output.get('error_type'),error_message_excerpt=excerpt(output.get('error_message') or '',enabled=redact_text),exit_code=exit_code,budget_warning=output.get('budget_warning'),patch_size=output.get('patch_size'),files_changed=output.get('files_changed'),output_truncated=bool(output.get('truncated') or output.get('output_truncated')),output_excerpt=excerpt(text,6000,redact_text),evidence_id=eid)
            results.append(rec)
            if c and c['command_category']=='test':
                passed,failed = parse_test_counts(text)
                cmd = c['_command']
                # Summary may be truncated; retain classification from original arguments.
                uncertain = '|' in cmd and 'pipefail' not in cmd
                rows['test_evidence'].append(dict(evidence_id=eid,run_id=rid,source='agent-side',turn_number=n,sequence_index=c['sequence_index'],test_command=c['arguments_summary'],exit_code=exit_code,parsed_passed=passed,parsed_failed=failed,parse_confidence='low' if uncertain else ('high' if passed is not None or failed is not None else 'heuristic'),post_last_edit=None,output_excerpt=excerpt(text,6000,redact_text),pipeline_status_uncertain=uncertain))
            if output.get('max_tool_calls') is not None:
                limit=numeric(output['max_tool_calls'],f'turns.{ti}.max_tool_calls',True)
                if limit is not None: harness_observed.append(limit)
            if output.get('budget_warning') or output.get('error_type')=='BudgetExceeded':
                event(n,(c['sequence_index'] if c else seq)+.2,'budget',result.get('name'),output.get('budget_warning') or output.get('error_message'),output.get('status'),eid)
    result_map={r['call_id']:r for r in results}
    for c in calls:
        r=result_map.get(c['call_id'],{})
        if not c['result_linked']:
            issue('tool_results','missing_result',f"Call {c['original_call_id']} at turn {c['turn_number']} has no linked result; outcome unknown",c['evidence_id'])
        kind = ('edit' if c['name'] in EDIT_TOOLS else 'graph' if c['name'] in GRAPH_TOOLS else 'submit' if c['name']=='submit_patch' else c['command_category'] if c['command_category'] else 'read' if c['name']=='read_file' else 'other')
        event(c['turn_number'],c['sequence_index'],kind,c['name'],c['requested_path'] or c['arguments_summary'],r.get('status') or 'unknown',c['evidence_id'])
    last_edit = max((c['sequence_index'] for c in calls if c['successful_source_edit']),default=None)
    for t in rows['test_evidence']:
        if t['run_id']==rid:
            t['post_last_edit']=t['sequence_index']>last_edit if last_edit is not None else None
    rows['tool_calls'].extend(calls); rows['tool_results'].extend(results)
    external = str(final.get('test_output') or '')
    if external or final.get('test_exit_code') is not None:
        eid=ev('/final/result/test_output',external) if 'test_output' in final else run_eid
        passed,failed=parse_test_counts(external)
        rows['test_evidence'].append(dict(evidence_id=eid,run_id=rid,source='external',turn_number=None,sequence_index=seq+1,test_command=None,exit_code=final.get('test_exit_code'),parsed_passed=passed,parsed_failed=failed,parse_confidence='high' if passed is not None or failed is not None else 'unparsed',post_last_edit=None,output_excerpt=excerpt(external,12000,redact_text),pipeline_status_uncertain=False))
        event(None,seq+1,'final_verifier',None,'Independent final verification','passed' if final.get('test_exit_code')==0 else 'failed' if final.get('test_exit_code') is not None else 'unknown',eid)
    harness_limits=harness_observed
    numeric_claims=sorted(x for x in claims if isinstance(x,int))
    if len(set(numeric_claims+harness_limits))>1:
        issue('input.vllm_request.messages','budget_instruction_conflict',f'Prompt limits {numeric_claims}; authoritative observed harness limits {harness_limits}')
    own_turns=[t for t in rows['turns'] if t['run_id']==rid]
    usage_values=[t[k] for t in own_turns for k in ('prompt_tokens','completion_tokens') if isinstance(t[k],(int,float))]
    if usage_values and final.get('total_tokens') is not None and sum(usage_values)!=final['total_tokens']:
        issue('final.result.total_tokens','token_total_mismatch',f"Reported aggregate {final['total_tokens']}; per-turn reported inference total {sum(usage_values)}")
    if any(t['created_epoch'] is None for t in own_turns):
        issue('turns.output.vllm_response_raw.created','missing_timestamps','Some model timestamps missing; event ordering uses sequence')
    if final.get('duration_seconds') is None:
        issue('final.result.duration_seconds','missing_duration','No authoritative elapsed duration; timestamp span not substituted')
    final_text=next((t['assistant_excerpt'] for t in reversed(own_turns) if t['assistant_text_available'] and not t['is_compaction']), '')
    rows['runs'].append(dict(run_id=rid,task_id=task_id,experiment_id=experiment_id,attempt_id=None,ambiguous_pairing=False,repo=final.get('repo') or run.get('repo') or task_id.rsplit('_',1)[0],resolved_nullable=resolved,harness_status=final.get('status'),test_exit_code=final.get('test_exit_code'),duration_seconds=final.get('duration_seconds'),budgeted_tool_calls=final.get('tool_calls'),reported_llm_calls=final.get('total_llm_calls'),harness_budget=harness_limits[-1] if harness_limits else None,trace_path=str(path),trace_hash=trace_hash,schema_version=data.get('schema_version'),reported_total_tokens=final.get('total_tokens'),prompt_budget_claims=numeric_claims,final_text_excerpt=final_text,harness_error_excerpt=excerpt(final.get('error_message') or '',enabled=redact_text)))
    return rid
