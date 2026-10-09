"""Safe excerpts and explicit source pointers; never interpret trace text as code."""
import html
import json
import re
from pathlib import Path

_SECRET = re.compile(r'(?i)((?:api[_-]?key|access[_-]?token|password|authorization|secret)\s*[\"\x27]?\s*[:=]\s*[\"\x27]?)([^\s\"\x27,;}]+)')

def redact(value, enabled=True):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if not enabled:
        return text
    text = _SECRET.sub(r'\1[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{20,})\b', '[REDACTED]', text)
    return re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~-]+', r'\1[REDACTED]', text)

def excerpt(value, limit=1800, enabled=True):
    text = redact(value, enabled)
    if len(text) <= limit:
        return text
    # Retain both traceback head and final assertion/footer.
    return text[:limit//2] + '\n… [excerpt truncated] …\n' + text[-limit//2:]

def evidence_html(experiment, ids):
    rows = experiment.evidence[experiment.evidence.evidence_id.isin(ids)]
    parts=[]
    for r in rows.itertuples():
        detail=''
        calls=experiment.tool_calls[experiment.tool_calls.evidence_id.eq(r.evidence_id)]
        if len(calls):
            call=calls.iloc[0]
            results=experiment.tool_results[experiment.tool_results.run_id.eq(call.run_id)&experiment.tool_results.call_id.eq(call.call_id)]
            detail='<p>Schema: '+html.escape(str(call.schema_valid))+' · '+html.escape(str(call.schema_issues))+'</p>'
            if len(results):
                result=results.iloc[0]
                detail+='<p>Tool status: '+html.escape(str(result.status))+' · exit: '+html.escape(str(result.exit_code))+'</p><pre>'+html.escape(result.output_excerpt)+'</pre><p>Result pointer: '+html.escape(str(experiment.evidence.set_index('evidence_id').loc[result.evidence_id].json_pointer))+'</p>'
            else:
                detail+='<p>No linked result; execution outcome unknown.</p>'
        parts.append('<details><summary>'+html.escape(str(r.evidence_id))+' · turn '+html.escape(str(r.turn_number))+'</summary><p>'+html.escape(str(r.trace_path))+' <code>'+html.escape(str(r.json_pointer))+'</code></p><pre>'+html.escape(str(r.excerpt))+'</pre>'+detail+'</details>')
    return ''.join(parts)

def read_evidence(experiment, evidence_id, max_chars=24000):
    """Explicit lazy drill-down with hash verification and configured redaction."""
    row = experiment.evidence.set_index('evidence_id').loc[evidence_id]
    import hashlib
    raw = Path(row.trace_path).read_bytes()
    run = experiment.runs.set_index('run_id').loc[row.run_id]
    if hashlib.sha256(raw).hexdigest() != run.trace_hash:
        raise ValueError('Source trace changed since ingestion; reload before inspecting.')
    node = json.loads(raw)
    for key in row.json_pointer.strip('/').split('/'):
        if key:
            node = node[int(key)] if isinstance(node, list) else node[key.replace('~1','/').replace('~0','~')]
    if not experiment.config.get('include_reasoning_in_viewer', False):
        def strip_reasoning(value):
            if isinstance(value, dict):
                return {k: strip_reasoning(v) for k, v in value.items() if k not in ('reasoning', 'reasoning_content', 'raw_json')}
            if isinstance(value, list):
                return [strip_reasoning(v) for v in value]
            return value
        node = strip_reasoning(node)
    return excerpt(node, max_chars, experiment.config.get('redact_text', True))
