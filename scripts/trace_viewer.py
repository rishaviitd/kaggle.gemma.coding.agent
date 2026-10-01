"""Serve the latest structured model trace as a simple local page."""

from __future__ import annotations

import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PAGE = '''<!doctype html>
<meta charset="utf-8">
<title>Model trace viewer</title>
<style>
body{font:14px system-ui,sans-serif;margin:24px;background:#f4f6f8;color:#17202a} h1{font:22px sans-serif;margin-bottom:20px}
details{border:1px solid #cbd5df;border-radius:8px;margin:14px 0;padding:14px;background:#fff;box-shadow:0 1px 3px #0001} summary{cursor:pointer;font:bold 16px sans-serif;color:#173b63;padding:4px}
h3{margin:20px 0 8px;padding:8px 10px;border-radius:5px;font-size:14px;line-height:1.2;font-weight:700;letter-spacing:.02em;color:#17202a;background:transparent;text-align:left}
pre{clear:both;white-space:pre-wrap;overflow:auto;max-height:520px;background:#f8fafc;color:#17202a;padding:14px;margin:0 0 12px;border:1px solid #d7e0e8;border-left:4px solid #78909c;border-radius:4px;line-height:1.45;box-sizing:border-box;width:100%}
.inner-section{display:block;margin:16px 0 10px;color:#16834b;font-size:20px;font-weight:800;letter-spacing:.03em}
button{float:right;margin:0 0 8px 5px;padding:5px 11px;cursor:pointer;border:1px solid #9aaaba;border-radius:4px;background:#fff;color:#24415c}button:hover{background:#eaf2f8}
.input{border-left-color:#2e8b70}.output{border-left-color:#3f72af}.tool{border-left-color:#b07a21}.error{color:#a22}
</style>
<h1 id="title">Loading trace…</h1><div id="steps"></div>
<script>
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const pretty=s=>esc(s).replace(/={16} ([A-Z ]+) ={16}/g,'<span class="inner-section">$1</span>');
const raw=s=>JSON.stringify(s??null,null,2);
function formattedInput(req){
 const ms=req?.messages||[];
 const show=(xs)=>xs.map(m=>{
   const content=m.content == null ? '' : (typeof m.content==='string' ? m.content : JSON.stringify(m.content));
   const reasoning=m.reasoning_content ? `REASONING\\n${m.reasoning_content}` : '';
   const calls=(m.tool_calls||[]).map(c=>`TOOL CALL: ${c.function?.name||c.name||'(unknown)'}\\n${c.function?.arguments||JSON.stringify(c.arguments||{})}`).join('\\n');
   return `[${m.role}]\\n${[reasoning,content,calls].filter(Boolean).join('\\n')}`;
 }).join('\\n\\n');
 const system=ms.filter(m=>m.role==='system');
 const user=ms.filter(m=>m.role==='user');
 const history=ms.filter(m=>m.role!=='system'&&m.role!=='user');
 const tools=(req?.tools||[]).map(t=>{const f=t.function||t; return `- ${f.name}: ${f.description||''}\\n  parameters: ${JSON.stringify(f.parameters||{})}`}).join('\\n');
 return `================ SYSTEM PROMPT ================\\n${show(system)}\\n\\n================ USER PROMPT ================\\n${show(user)}\\n\\n================ PRIOR ASSISTANT OUTPUTS AND TOOL RESPONSES ================\\n${show(history)}\\n\\n================ AVAILABLE TOOL DEFINITIONS ================\\n${tools}`;
}
function formattedOutput(o){
 let s=[];
 if(o?.reasoning) s.push('REASONING\\n'+o.reasoning);
 if(o?.assistant_content) s.push('ANSWER\\n'+o.assistant_content);
 for(const c of (o?.tool_calls||[])) s.push(`TOOL CALL: ${c.name}\\n${c.arguments_raw_json||JSON.stringify(c.arguments||{})}`);
 return s.join('\\n\\n')||'(no text output)';
}
function formattedTools(xs){return (xs||[]).map(x=>`TOOL RESULT${x.name?' — '+x.name:''}\\n${typeof x.content==='string'?x.content:JSON.stringify(x.content)}`).join('\\n\\n')||'(none)';}
function block(label, cls, formatted, value){return `<h3>${label}</h3><button data-mode="formatted">Formatted</button><button data-mode="raw">Raw JSON</button><pre class="${cls} formatted">${pretty(formatted)}</pre><pre class="${cls} raw" hidden>${esc(raw(value))}</pre>`}
fetch('/trace.json').then(r=>r.json()).then(t=>{
 document.querySelector('#title').textContent=`${t.run?.task_id||'trace'} — ${t.turns.length} model turns`;
 const root=document.querySelector('#steps');
 for(const x of t.turns){
  const d=document.createElement('details'); d.open=true;
  d.innerHTML=`<summary>Turn ${x.turn}: ${x.output?.tool_calls?.[0]?.name||'assistant response'}</summary>`+
   block('Input sent to vLLM','input',formattedInput(x.input?.vllm_request),x.input?.vllm_request)+
   block('Output from vLLM','output',formattedOutput(x.output),x.output)+
   block('Tool result(s) sent in next input','tool',formattedTools(x.tool_results),x.tool_results);
  d.querySelectorAll('button').forEach(b=>b.onclick=()=>{const h=b.parentElement; h.querySelectorAll('pre').forEach(p=>p.hidden=p.classList.contains('raw') !== (b.dataset.mode==='raw'));});
  root.appendChild(d);
 }
}).catch(e=>document.querySelector('#title').textContent='Error: '+e);
</script>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--trace', type=Path, help='Optional legacy default trace file')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    site_root = Path(__file__).resolve().parent.parent
    trace_root = site_root / 'logs' / 'remote'
    splits = ('train', 'dev', 'val')

    def available_iterations() -> list[Path]:
        if not trace_root.is_dir():
            return []
        return sorted(
            path for path in trace_root.iterdir()
            if path.is_dir() and path.name.startswith('iteration-')
        )

    def available_traces() -> dict[str, dict[str, list[str]]]:
        def task_entry(task_dir: Path) -> dict[str, object]:
            resolved = None
            try:
                trace = json.loads((task_dir / 'model_trace.json').read_text(encoding='utf-8'))
                value = trace.get('final', {}).get('result', {}).get('resolved')
                resolved = value if isinstance(value, bool) else None
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
            return {'task_id': task_dir.name, 'resolved': resolved}

        return {
            iteration.name: {
                split: [task_entry(task_dir) for task_dir in sorted(
                    task_dir
                    for task_dir in (iteration / split).iterdir()
                    if task_dir.is_dir() and (task_dir / 'model_trace.json').is_file()
                )] if (iteration / split).is_dir() else []
                for split in splits
            }
            for iteration in available_iterations()
        }

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path == '/trace-index.json':
                payload = json.dumps({'iterations': available_traces()}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)
                return
            if request.path == '/trace.json':
                query = parse_qs(request.query)
                iteration_name = query.get('iteration', [''])[0]
                split = query.get('split', [''])[0]
                task_id = query.get('task_id', [''])[0]
                valid_iterations = {path.name for path in available_iterations()}
                if (
                    iteration_name not in valid_iterations
                    or split not in splits
                    or not task_id
                    or Path(task_id).name != task_id
                ):
                    self.send_error(400, 'A valid iteration, split, and task_id are required')
                    return
                trace_path = trace_root / iteration_name / split / task_id / 'model_trace.json'
                try:
                    trace_path.resolve().relative_to(trace_root.resolve())
                except ValueError:
                    self.send_error(400, 'Invalid trace path')
                    return
                if not trace_path.is_file():
                    self.send_error(404, 'Trace not found')
                    return
                payload = trace_path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)
                return
            super().do_GET()

    handler = partial(Handler, directory=str(site_root))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    print(f'http://127.0.0.1:{args.port}/', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
