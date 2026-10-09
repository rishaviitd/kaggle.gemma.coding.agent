"""Optional read-only Python gold localization with explicit baseline mapping.

Function recall requires a pristine baseline root plus gold patch. Unified hunk
old-line spans are mapped to AST function ranges. No patch is applied. Discovery
requires returned content containing the symbol plus a recorded read range or
search snippet; attempted reads alone are insufficient.
"""
import ast
import json
import re
from pathlib import Path
from ..events import parse_patch


def changed_lines(patch):
    path=None; old=None; spans={}
    for line in patch.splitlines():
        if line.startswith('--- '):
            path=re.sub(r'^a/','',line[4:].split('\t')[0]);spans.setdefault(path,set())
        m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
        if m:
            old=int(m[1])
        elif old is not None and path and not line.startswith(('--- ','+++ ')):
            if line.startswith('-'):
                spans[path].add(old);old+=1
            elif line.startswith('+'):
                spans[path].add(old) # insertions belong to following baseline line
            elif line.startswith(' '):
                old+=1
    return spans



def baseline_matches(root, patch):
    """Check recorded old-side context against the supplied pristine baseline."""
    path=None;old=None;contents={}
    for line in patch.splitlines():
        if line.startswith('--- '):
            path=re.sub(r'^a/','',line[4:].split('\t')[0]);old=None
            if path!='/dev/null':
                absolute=(Path(root)/path).resolve()
                if Path(root).resolve() not in absolute.parents:
                    return False
                contents[path]=absolute.read_text().splitlines()
        m=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
        if m:
            old=int(m[1])-1
        elif old is not None and path!='/dev/null' and line[:1] in (' ','-') and not line.startswith('--- '):
            if old<0 or old>=len(contents[path]) or contents[path][old]!=line[1:]:
                return False
            old+=1
    return True


def symbols(root, path):
    absolute=(Path(root)/path).resolve()
    if Path(root).resolve() not in absolute.parents:
        raise ValueError('Patch path escapes repository root')
    if absolute.suffix!='.py':
        raise ValueError('Function mapping currently supports Python AST only')
    tree=ast.parse(absolute.read_text())
    output=[]
    def visit(node, parents=()):
        for child in ast.iter_child_nodes(node):
            if isinstance(child,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                chain=parents+(child.name,)
                if not isinstance(child,ast.ClassDef):
                    output.append(('.'.join(chain),child.lineno,child.end_lineno))
                visit(child,chain)
            else:
                visit(child,parents)
    visit(tree)
    return output


def gold_analysis(exp, run, gold_dir, repo_roots):
    if not gold_dir:
        return None,'Gold patch not supplied',[]
    patch_path=(Path(gold_dir)/(run.task_id+'.patch')).resolve()
    if Path(gold_dir).resolve() not in patch_path.parents:
        return None,'Task identifier cannot escape gold directory',[]
    if not patch_path.is_file():
        return None,'No gold patch keyed by task_id',[]
    gold=patch_path.read_text(); parsed=parse_patch(gold)
    if not parsed['diff_parse_ok']:
        return None,'Gold unified diff could not be validated',[]
    agent=exp.patches[exp.patches.run_id==run.run_id].iloc[0]
    agent_files=set(agent.modified_files);gold_files=set(parsed['modified_files'])
    file_rows=[dict(run_id=run.run_id,task_id=run.task_id,file=f,gold=f in gold_files,agent=f in agent_files,symbol=None,viewed=None,edited=None) for f in sorted(agent_files|gold_files)]
    root=repo_roots.get(run.repo)
    if not root:
        return None,'Gold file overlap available; function metrics require pristine baseline repo root',file_rows
    # Use original final patch from an audited source pointer, rather than truncated excerpts.
    from ..evidence import read_evidence
    patch_eid=agent.evidence_id
    source=exp.evidence.set_index('evidence_id').loc[patch_eid]
    data=json.loads(Path(source.trace_path).read_text())
    agent_patch=data.get('final',{}).get('result',{}).get('agent_patch','')
    try:
        if not baseline_matches(root,gold) or agent_patch and not baseline_matches(root,agent_patch):
            return None,'Supplied baseline does not match old-side patch context',file_rows
        if re.search(r'^\+\s*(?:async\s+)?def\s+',gold,re.M):
            return None,'Added or renamed gold functions require an explicit symbol correspondence map',file_rows
    except (OSError,ValueError) as exc:
        return None,f'Baseline validation unavailable: {exc}',file_rows
    changes=changed_lines(gold);agent_changes=changed_lines(agent_patch)
    calls=exp.tool_calls[exp.tool_calls.run_id==run.run_id]
    results=exp.tool_results[exp.tool_results.run_id==run.run_id].set_index('call_id')
    target=[]
    try:
        for file,lines in changes.items():
            if file=='/dev/null':
                raise ValueError('New-file gold functions lack a baseline function map')
            for name,start,end in symbols(root,file):
                if any(start<=line<=end for line in lines):
                    viewed=False;ids=[];latency=None
                    for call in calls.itertuples():
                        if call.call_id not in results.index:
                            continue
                        result=results.loc[call.call_id]
                        if result.status!='ok':
                            continue
                        # Lazy exact result content, not a truncated evidence excerpt.
                        e=exp.evidence.set_index('evidence_id').loc[result.evidence_id]
                        node=data
                        for key in e.json_pointer.strip('/').split('/'):
                            node=node[int(key)] if isinstance(node,list) else node[key]
                        output=node.get('output') or {}; text=str(output.get('content') or output.get('stdout') or output)
                        args=json.loads(call.arguments_summary) if call.arguments_summary.startswith('{') and '…' not in call.arguments_summary else {}
                        range_ok=call.requested_path==file and (args.get('start_line') or 1)<=end and (args.get('end_line') or 10**9)>=start
                        search_ok=call.name=='search_similar_code' and file in text
                        if (range_ok or search_ok) and re.search(r'\b(?:async\s+)?def\s+'+re.escape(name.rsplit('.',1)[-1])+r'\s*\(',text):
                            viewed=True;ids.append(result.evidence_id);latency=latency or call.sequence_index
                    edited=any(start<=line<=end for line in agent_changes.get(file,set()))
                    target.append(dict(run_id=run.run_id,task_id=run.task_id,file=file,gold=True,agent=file in agent_files,symbol=name,viewed=viewed,edited=edited,discovery_call=latency,evidence_ids=ids+[patch_eid]))
    except (OSError,ValueError,SyntaxError,TypeError) as exc:
        return None,f'Reliable function mapping unavailable: {exc}',file_rows
    if not target:
        return None,'No gold function mapped (module-level changes or new functions)',file_rows
    return dict(discovery=sum(x['viewed'] for x in target)/len(target),edit=sum(x['edited'] for x in target)/len(target),denominator=len(target)),None,target
