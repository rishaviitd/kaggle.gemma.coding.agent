"""All plots consume normalized tables. None parse source JSON."""
import pandas as pd
import plotly.graph_objects as go
from ..metrics.outcomes import outcome_summary

COLORS={'search':'#0072B2','read':'#56B4E9','graph':'#009E73','edit':'#E69F00','test':'#CC79A7','command':'#777777','inspect_patch':'#888833','submit':'#009E73','budget':'#D55E00','compaction':'#AA66AA','final_verifier':'#222222','other':'#999999'}

def empty(title, message='No matching evidence available'):
    fig=go.Figure();fig.add_annotation(text=message,x=.5,y=.5,xref='paper',yref='paper',showarrow=False)
    fig.update_layout(title=title,template='plotly_white',height=320)
    return fig

def finish(fig,title,height=380):
    fig.update_layout(title=title,template='plotly_white',height=height,margin=dict(l=80,r=30,t=65,b=65),legend=dict(orientation='h',y=-.25),font=dict(size=12))
    return fig

def run_matrix(e,m,f,ids):
    runs=e.runs[e.runs.run_id.isin(ids)].copy()
    if runs.empty:return runs
    wide=m.metrics[m.metrics.run_id.isin(ids)].pivot(index='run_id',columns='metric_id',values='value')
    runs=runs.merge(wide[['C08','tool_errors','confirmed_source_edits','C09','tool_attempts','completion_tokens']],left_on='run_id',right_index=True,how='left')
    return runs.merge(f.primary[['run_id','primary_stage','confidence']],on='run_id',how='left')

def build_figures(e,m,f,run_ids=None):
    ids=list(e.runs.run_id if run_ids is None else run_ids)
    r=run_matrix(e,m,f,ids);c=e.tool_calls[e.tool_calls.run_id.isin(ids)];res=e.tool_results[e.tool_results.run_id.isin(ids)];ev=e.events[e.events.run_id.isin(ids)];t=e.turns[e.turns.run_id.isin(ids)];tests=e.test_evidence[e.test_evidence.run_id.isin(ids)]
    figs={}
    def add(key,fig,title,height=380):figs[key]=finish(fig,title,height)
    if r.empty:
        return {'overview':empty('Batch overview','No runs match the filters')}
    summary=outcome_summary(r)
    coverage=f"known {summary['known_outcomes']}/{summary['total_runs']}; resolved {summary['resolved']}/{summary['known_outcomes']}"
    add('overview',go.Figure(go.Bar(x=['Resolved','Unresolved','Unknown'],y=[summary['resolved'],summary['unresolved'],summary['unknown_outcomes']],marker_color=['#009E73','#D55E00','#999999'])),f'Official outcomes · {coverage} · trace-derived')
    repos=[]
    for repo,group in r.groupby('repo'):
        s=outcome_summary(group);repos.append((repo,s))
    fig=go.Figure()
    for label,col in [('resolved','#009E73'),('unresolved','#D55E00'),('unknown_outcomes','#999999')]:
        fig.add_bar(name=label,x=[x[0] for x in repos],y=[x[1][label] for x in repos],marker_color=col)
    fig.update_layout(barmode='stack');add('repositories',fig,'Outcomes by repository · raw attempt counts')
    indicators=['resolved','source edit','post-edit test','budget exhausted','tool errors']
    z=[]
    for row in r.itertuples():
        z.append([None if pd.isna(row.resolved_nullable) else int(row.resolved_nullable),int(row.confirmed_source_edits>0),None if pd.isna(row.C08) else int(row.C08),int(row.C09!='not_observed'),int(row.tool_errors>0)])
    add('matrix',go.Figure(go.Heatmap(z=z,x=indicators,y=r.task_id+' #'+r.attempt_id.astype(str),zmin=0,zmax=1,colorscale=[[0,'#eeeeee'],[1,'#0072B2']],hoverongaps=False,colorbar=dict(tickvals=[0,1],ticktext=['No','Yes']))),'Task indicators · gray gaps mean unknown · trace-derived',min(1700,max(380,24*len(r))))
    stages=r[r.resolved_nullable.ne(True)].primary_stage.value_counts()
    add('failures',go.Figure(go.Bar(x=stages.index,y=stages.values,marker_color='#D55E00')),'Tentative observable failure stages · rule-derived; no causal claim')
    joined=c.merge(res[['run_id','call_id','status']],on=['run_id','call_id'],how='left')
    fig=go.Figure()
    tools=sorted(c.name.unique())
    for status,color in [('ok','#009E73'),('error','#D55E00'),('unknown','#999999')]:
        counts=joined[joined.status.fillna('unknown').eq(status)].name.value_counts()
        fig.add_bar(name=status,x=tools,y=[int(counts.get(tool,0)) for tool in tools],marker_color=color)
    fig.update_layout(barmode='stack');add('tools',fig,'Tool attempts and execution outcomes · unknown results retained')
    errors=res[res.status.eq('error')].error_type.fillna('unspecified').value_counts()
    add('errors',go.Figure(go.Bar(x=errors.index,y=errors.values,marker_color='#D55E00')),'Error-type Pareto · CommandError can include expected failing repros')
    fig=go.Figure()
    for outcome,color in [('resolved','#009E73'),('unresolved','#D55E00'),('unknown','#999999')]:
        group=r[r.resolved_nullable.eq(True)] if outcome=='resolved' else r[r.resolved_nullable.eq(False)] if outcome=='unresolved' else r[r.resolved_nullable.isna()]
        fig.add_scatter(x=group.budgeted_tool_calls,y=group.duration_seconds,mode='markers',name=outcome,text=group.task_id,customdata=group.run_id,marker=dict(color=color,size=11),hovertemplate='%{text}<br>budget-counted calls %{x}<br>duration %{y:.2f}s<extra></extra>')
    add('efficiency',fig,'Calls versus authoritative duration · missing durations omitted')
    fig=go.Figure()
    fig.add_bar(x=r.task_id,y=r.budgeted_tool_calls,name='budget-counted calls',marker_color='#0072B2')
    fig.add_scatter(x=r.task_id,y=r.harness_budget,name='observed harness limit',mode='markers',marker=dict(symbol='line-ew',size=15,color='#D55E00'))
    add('budgets',fig,'Budget usage · authoritative final/status counts; unknown limits are gaps')
    fig=go.Figure()
    for kind,color in COLORS.items():
        group=ev[ev.kind.eq(kind)].merge(r[['run_id','task_id','attempt_id']],on='run_id')
        if len(group):
            fig.add_scatter(x=group.event_order,y=group.task_id+' #'+group.attempt_id.astype(str),mode='markers',name=kind,marker=dict(color=color,symbol=['x' if s=='error' else 'circle' for s in group.status],size=9),customdata=group[['evidence_id','turn_number','target','status']].values,hovertemplate='event %{x}<br>turn %{customdata[1]}<br>%{customdata[2]}<br>status %{customdata[3]}<br>%{customdata[0]}<extra></extra>')
    add('timeline',fig,'Event sequence · approximate ordering, no inferred tool duration',min(1600,max(400,25*len(r))))
    files=c[c.requested_path.notna()].merge(r[['run_id','task_id']],on='run_id')
    add('files',go.Figure(go.Scatter(x=files.sequence_index,y=files.requested_path,mode='markers',text=files.task_id,customdata=files.evidence_id,hovertemplate='%{text}<br>%{y}<br>attempt %{x}<br>%{customdata}<extra></extra>')),'File activity waterfall · recorded path-bearing calls')
    add('verification',go.Figure(go.Heatmap(z=[[None if pd.isna(v) else int(v)] for v in r.C08],x=['Agent test after last confirmed edit'],y=r.task_id,zmin=0,zmax=1,colorscale=[[0,'#D55E00'],[1,'#009E73']],hoverongaps=False,colorbar=dict(tickvals=[0,1],ticktext=['Absent','Present']))),'Final-edit verification · shell mutations can make boundary uncertain',min(1600,max(380,24*len(r))))
    fig=go.Figure()
    for source in ['agent-side','external']:
        group=tests[tests.source.eq(source)]
        labels=group.apply(lambda x:'failed' if pd.notna(x.parsed_failed) and x.parsed_failed>0 or pd.notna(x.exit_code) and x.exit_code!=0 else 'passed' if pd.notna(x.exit_code) and x.exit_code==0 and not x.pipeline_status_uncertain else 'unknown',axis=1) if len(group) else pd.Series(dtype=str)
        counts=labels.value_counts();fig.add_bar(name=source,x=['passed','failed','unknown'],y=[counts.get(v,0) for v in ['passed','failed','unknown']])
    add('tests',fig,'Test-command outcomes · agent-side and independent verifier separated')
    fig=go.Figure()
    for rid,group in t.groupby('run_id',sort=False):
        label=r.set_index('run_id').loc[rid].task_id
        fig.add_scatter(x=group.turn_number,y=group.completion_tokens,name=label,mode='lines+markers',customdata=group.evidence_id,hovertemplate='turn %{x}<br>completion %{y}<br>%{customdata}<extra></extra>')
        compact=group[group.is_compaction.eq(True)]
        if len(compact):fig.add_scatter(x=compact.turn_number,y=compact.completion_tokens,mode='markers',name=f'{label} compaction',marker=dict(symbol='diamond',size=12,color='#AA66AA'))
    add('tokens',fig,'Per-turn completion tokens · reported inference usage; compaction diamonds')
    reasons=t.finish_reason.fillna('unknown').value_counts()
    add('finish_reasons',go.Figure(go.Bar(x=reasons.index,y=reasons.values)),'Model finish reasons · trace-derived')
    if len(m.gold):
        g=m.gold[m.gold.run_id.isin(ids)]
        if len(g):
            counts=g.groupby('task_id')[['gold','agent']].sum()
            fig=go.Figure([go.Bar(x=counts.index,y=counts[col],name=col) for col in ['gold','agent']]);add('gold',fig,'Gold and agent scope · mapped functions when available, otherwise files')
    if 'gold' not in figs:figs['gold']=empty('Optional gold analysis','C04/C05 unavailable until gold patches and reliable symbol maps are supplied')
    if len(m.pairing):
        p=m.pairing[m.pairing.task_id.isin(r.task_id)&m.pairing.availability.eq('available')]
        counts=p.transition.value_counts();add('comparison',go.Figure(go.Bar(x=counts.index,y=counts.values)),'Paired official outcome transitions · descriptive; configuration review required')
        add('runtime_delta',go.Figure(go.Bar(x=p.task_id,y=p.duration_delta)),'Paired duration delta · comparison minus baseline seconds')
    else:figs['comparison']=empty('Optional paired experiments','Supply compare_dir to enable task-matched comparisons')
    return figs
