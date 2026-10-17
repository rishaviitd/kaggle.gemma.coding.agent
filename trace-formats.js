/* Normalize native provider captures for the shared trace renderer. */
const TraceFormats = (() => {
  const stringify = value => typeof value === 'string' ? value : JSON.stringify(value ?? {});
  function message(content) {
    const parts = content?.parts || [];
    return {role: content?.role === 'model' ? 'assistant' : (content?.role || 'user'),
      content: parts.filter(p => p.text && !p.thought).map(p => p.text).join('\n'),
      reasoning_content: parts.filter(p => p.thought && p.text).map(p => p.text).join('\n'),
      tool_calls: parts.filter(p => p.function_call).map(p => ({function: {
        name: p.function_call.name, arguments: stringify(p.function_call.args)}})),
      tool_responses: parts.filter(p => p.function_response).map(p => p.function_response)};
  }
  function normalize(trace, native, entry) {
    if (trace?.turns?.length && trace.turns[0].output) {
      if (!Array.isArray(native)) return trace;
      let changed = false;
      const turns = trace.turns.map((turn, i) => {
        if (turn.tool_results?.length || !turn.output?.tool_calls?.length) return turn;
        const calls = turn.output.tool_calls;
        const ids = new Map(calls.filter(call => call.id).map(call => [call.id, call.name]));
        const request = native[i + 1]?.request || {};
        const messages = request.messages || request.input || [];
        const results = [];
        for (const msg of messages) {
          if (Array.isArray(msg.content)) {
            for (const part of msg.content) {
              if (part.type !== 'tool_result' || !ids.has(part.tool_use_id)) continue;
              results.push({tool_call_id:part.tool_use_id,name:ids.get(part.tool_use_id),output:part.content,output_raw_json:part.content});
            }
          }
          if (msg.type === 'function_call_output' && ids.has(msg.call_id)) {
            results.push({tool_call_id:msg.call_id,name:ids.get(msg.call_id),output:msg.output,output_raw_json:msg.output});
          }
          if (msg.role === 'tool' && ids.has(msg.tool_call_id)) {
            results.push({tool_call_id:msg.tool_call_id,name:ids.get(msg.tool_call_id),output:msg.content,output_raw_json:msg.content});
          }
        }
        if (!results.length) return turn;
        changed = true;
        return {...turn,tool_results:results};
      });
      return changed ? {...trace,turns} : trace;
    }
    const base = {run: {task_id: entry.task_id}, ...(!Array.isArray(trace) ? trace : {})};
    if (!Array.isArray(native)) native = Array.isArray(trace) ? trace : [];
    if (!native.length && base.turns?.length) {
      base.turns = base.turns.map((t, i) => ({turn: t.turn || i+1,
        input: {vllm_request: {messages: [{role:'system',content:base.system || ''}, {role:'user',content:base.task || ''}]}},
        output: {reasoning:t.reasoning || '',tool_calls:t.calls || []},tool_results:t.results || [],native_record:t}));
      return base;
    }
    base.turns = native.map((exchange, i) => {
      const request = exchange.request || {};
      const response = exchange.response || {};
      const gemini = Array.isArray(request.contents);
      let input = request, output, results = [];
      if (gemini) {
        const config = request.config || {};
        const system = config.system_instruction;
        const messages = request.contents.map(message);
        if (system) messages.unshift({role:'system',content:typeof system === 'string' ? system : message(system).content});
        // Keep function responses visible in formatted input, in addition to raw JSON.
        messages.forEach(m => {if(m.tool_responses?.length) m.content += '\n'+m.tool_responses.map(r => `TOOL RESULT: ${r.name}\n${stringify(r.response)}`).join('\n');});
        input = {...request,messages,tools:(config.tools || []).flatMap(t => t.function_declarations || [])};
        const parts = response.content?.parts || response.candidates?.[0]?.content?.parts || [];
        const calls = parts.filter(p => p.function_call).map(p => p.function_call);
        output = {reasoning:parts.filter(p => p.thought && p.text).map(p => p.text).join('\n'),
          assistant_content:parts.filter(p => p.text && !p.thought).map(p => p.text).join('\n'),
          tool_calls:calls.map(c => ({id:c.id,name:c.name,arguments:c.args,arguments_raw_json:stringify(c.args)})),
          usage:response.usage_metadata, response_status:exchange.response_status,
          error:exchange.error_type || response.error_message, duration_seconds:exchange.duration_seconds};
        const nextContents = native[i+1]?.request?.contents || [];
        results = nextContents.flatMap(c => c.parts || []).filter(p => p.function_response && calls.some(c =>
          c.id ? c.id === p.function_response.id : c.name === p.function_response.name))
          .map(p => ({name:p.function_response.name,output:p.function_response.response}));
      } else {
        const m = response.choices?.[0]?.message || {};
        output = {reasoning:m.reasoning_content || '',assistant_content:m.content || '',
          tool_calls:(m.tool_calls || []).map(c => ({id:c.id,name:c.function?.name || c.name,arguments_raw_json:c.function?.arguments || stringify(c.arguments)})),
          response_status:exchange.response_status,error:response.error || exchange.error_type,
          usage:response.usage,duration_seconds:exchange.duration_seconds};
        const ids = new Set((m.tool_calls || []).map(c => c.id));
        results = (native[i+1]?.request?.messages || []).filter(m => m.role === 'tool' && ids.has(m.tool_call_id))
          .map(m => ({output:m.content}));
      }
      return {turn:i+1,input:{vllm_request:input},output,tool_results:results,native_record:exchange};
    });
    return base;
  }
  return {normalize};
})();
