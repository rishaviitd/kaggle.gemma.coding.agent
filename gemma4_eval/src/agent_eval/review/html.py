"""Inject optional review panels without changing ordinary report templates."""

SINGLE = r'''<script>
(function(){
 const reviews=new Map(D.llm_reviews.map(r=>[r.run_id,r]));
 function draw(){
  const r=reviews.get($('task').value),box=$('llmReview');
  if(!r){box.innerHTML='<p>No Codex review for this attempt.</p>';return}
  const head='<p class="muted">AI-generated · '+esc(r.model)+' · '+esc(r.prompt_version)+' · '+esc(r.status)+(r.truncated?' · digest truncated':'')+(r.cache_hit?' · cached':'')+'</p>';
  if(r.status!=='ok'){box.innerHTML=head+'<p>'+esc(r.error||'Review unavailable')+'</p>';return}
  const flags=(r.flags||[]).map(f=>'<li><b>Turn '+esc(f.turn)+' · '+esc(f.type)+'</b>: '+esc(f.note)+' '+sourceButtons(f.evidence_ids)+'</li>').join('');
  const checks=(r.rule_check||[]).map(c=>{const finding=D.findings.find(f=>f.finding_id===c.finding_id);return '<li><b>'+esc(c.verdict)+'</b> · '+esc(finding?.headline||c.finding_id)+' — '+esc(c.reason)+'</li>'}).join('');
  box.innerHTML=head+'<p><b>Summary:</b> '+esc(r.summary)+'</p><p><b>Possible root cause:</b> '+esc(r.root_cause)+' · '+esc(r.confidence)+' confidence. '+esc(r.root_cause_explanation)+' '+sourceButtons(r.root_cause_evidence_ids)+'</p><h3>Flagged turns</h3><ul>'+(flags||'<li>None</li>')+'</ul><h3>Rule finding review</h3><ul>'+(checks||'<li>None</li>')+'</ul>';
  const findingRows=D.findings.filter(f=>f.run_id===r.run_id);
  $('findingList').querySelectorAll(':scope > details').forEach((el,i)=>{const check=(r.rule_check||[]).find(c=>c.finding_id===findingRows[i]?.finding_id);if(check){const chip=document.createElement('span');chip.className='tag';chip.textContent=' Codex: '+check.verdict;el.querySelector('summary').appendChild(chip)}});
  wire();
 }
 $('task').addEventListener('change',draw);$('repo').addEventListener('change',draw);draw();
})();
</script>'''

BATCH = r'''<script>
(function(){
 const rows=D.llm_reviews,ok=rows.filter(r=>r.status==='ok'),unresolved=new Set(D.tasks.filter(t=>t.outcome==='unresolved').map(t=>t.run_id));
 const counts={},verdicts={agree:0,dispute:0,unsure:0};
 ok.filter(r=>unresolved.has(r.run_id)).forEach(r=>{counts[r.root_cause]=(counts[r.root_cause]||0)+1});
 ok.forEach(r=>(r.rule_check||[]).forEach(c=>{verdicts[c.verdict]=(verdicts[c.verdict]||0)+1}));
 $('llmCoverage').textContent=ok.length+'/'+D.tasks.length+' attempts reviewed successfully; '+rows.filter(r=>r.status==='invalid').length+' invalid, '+rows.filter(r=>r.status==='error').length+' errors, '+rows.filter(r=>r.status==='skipped').length+' skipped. Root causes below cover only reviewed unresolved attempts and are judge-derived.';
 const names=Object.keys(counts).sort((a,b)=>counts[b]-counts[a]);
 Plotly.newPlot('llmCauses',[{type:'bar',x:names,y:names.map(n=>counts[n]),marker:{color:'#0072B2'}}],{title:'Possible root causes · reviewed unresolved attempts',paper_bgcolor:'#fff',plot_bgcolor:'#fff',height:360,margin:{l:90,r:20,t:60,b:110}},{responsive:true,displaylogo:false});
 const covered=Object.values(verdicts).reduce((a,b)=>a+b,0),total=D.llm_rule_findings_total??covered;
 $('llmVerdicts').textContent='Rule checks: '+verdicts.agree+' confirmed of '+total+' findings in reviewed attempts · '+verdicts.dispute+' disputed · '+verdicts.unsure+' unsure · '+covered+'/'+total+' verdict coverage';
 table('llmTable',rows.map(r=>({...r,task_id:D.tasks.find(t=>t.run_id===r.run_id)?.task_id||r.run_id,flags_count:(r.flags||[]).length})),[['task_id','Task'],['status','Status'],['root_cause','Possible root cause'],['confidence','Confidence'],['flags_count','Flagged turns'],['summary','Summary'],['error','Error']],{task_id:r=>'<a href="report.html?task='+encodeURIComponent(r.run_id)+'#llmReviewSection">'+esc(r.task_id)+' ↗</a>'},25);
})();
</script>'''


def inject_single(template):
    template = template.replace('<a href="#quality">Data quality</a>', '<a href="#llmReviewSection">Codex review</a><a href="#quality">Data quality</a>', 1)
    template = template.replace('</main>', '<section class="section" id="llmReviewSection"><h2>Codex review</h2><details class="howto"><summary>How to read this</summary><p>AI-generated interpretation of a bounded trace digest. Source links point to trace evidence. This review does not change official outcomes or rule metrics.</p></details><div id="llmReview"></div></section></main>', 1)
    return template.replace('</body>', SINGLE + '</body>', 1)


def inject_batch(template):
    template = template.replace('<a href="#cost">Cost</a>', '<a href="#llmBatch">Codex review</a><a href="#cost">Cost</a>', 1)
    template = template.replace('<section class="section" id="cost">', '<section class="section" id="llmBatch"><h2>Codex review · batch</h2><p class="muted">AI-generated, judge-derived labels; no effect on official resolution or measured metrics.</p><p id="llmCoverage"></p><div class="chart" id="llmCauses"></div><p id="llmVerdicts"></p><details class="fold"><summary>Show review by attempt</summary><div id="llmTable"></div></details></section><section class="section" id="cost">', 1)
    return template.replace('</body>', BATCH + '</body>', 1)
