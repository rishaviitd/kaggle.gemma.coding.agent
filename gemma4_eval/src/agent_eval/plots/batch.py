"""Batch-only charts and Jupyter view; all inputs are precomputed tables."""
import math
import pandas as pd
import plotly.graph_objects as go

GREEN='#009E73'; RED='#D55E00'; BLUE='#0072B2'; GREY='#8D9AA6'; PURPLE='#7A59A4'; ORANGE='#D58B10'


def _finish(fig,title,height=400,xaxis_title=None,yaxis_title=None):
    fig.update_layout(title=dict(text=title,font=dict(size=18)),height=height,template='plotly_white',font=dict(family='system-ui, sans-serif',size=12),margin=dict(t=65,l=90,r=40,b=65),legend=dict(orientation='h',y=-.24),xaxis_title=xaxis_title,yaxis_title=yaxis_title)
    return fig


def _empty(title,why):
    fig=go.Figure();fig.add_annotation(text=why,x=.5,y=.5,xref='paper',yref='paper',showarrow=False,font=dict(color=GREY));return _finish(fig,title)


def build_batch_figures(batch):
    b=batch;figures={};o=b.overview
    figures['outcomes']=_finish(go.Figure(go.Bar(x=['Resolved','Unresolved','Unknown'],y=[o['resolved'],o['unresolved'],o['unknown_outcomes']],marker_color=[GREEN,RED,GREY],text=[o['resolved'],o['unresolved'],o['unknown_outcomes']],textposition='outside')),'Official task resolution · attempts',yaxis_title='Attempts')
    if len(b.repositories):
        repo=b.repositories
        fig=go.Figure()
        fig.add_bar(name='Resolved',x=repo.repo,y=repo.resolved,marker_color=GREEN,customdata=repo[['known_outcomes','attempts']].values,hovertemplate='%{x}: %{y}/%{customdata[0]} known resolved<br>coverage %{customdata[0]}/%{customdata[1]}<extra></extra>')
        fig.add_bar(name='Unresolved',x=repo.repo,y=repo.unresolved,marker_color=RED)
        fig.add_bar(name='Unknown',x=repo.repo,y=repo.unknown,marker_color=GREY)
        fig.update_layout(barmode='stack');figures['repositories']=_finish(fig,'Outcomes by repository · counts',yaxis_title='Attempts')
    else:figures['repositories']=_empty('Outcomes by repository','No accepted attempts')
    w=b.workflow
    fig=go.Figure(go.Bar(x=[0 if pd.isna(value) else value for value in w.rate],y=w.indicator,orientation='h',marker_color=[GREEN,BLUE,BLUE,PURPLE,ORANGE],text=[f'{row.numerator}/{row.denominator}' for row in w.itertuples()],textposition='outside',customdata=w[['denominator_definition','evidence_kind']].values,hovertemplate='%{y}: %{text}<br>%{customdata[0]}<br>%{customdata[1]}<extra></extra>'))
    fig.update_layout(xaxis=dict(tickformat='.0%',range=[0,1.15]),yaxis=dict(autorange='reversed'))
    figures['workflow']=_finish(fig,'Independent workflow indicators · different denominators',height=425,xaxis_title='Share of stated denominator')
    figures['workflow'].update_layout(margin=dict(l=290,r=70,t=70,b=60))
    timing=b.tasks[b.tasks.first_source_edit_call.notna()] if len(b.tasks) else b.tasks
    if len(timing):
        fig=go.Figure()
        for outcome,color in [('resolved',GREEN),('unresolved',RED),('unknown',GREY)]:
            part=timing[timing.outcome.eq(outcome)]
            fig.add_histogram(name=outcome,x=part.first_source_edit_call,marker_color=color,xbins=dict(start=0,end=max(30,float(timing.first_source_edit_call.max())+1),size=5),opacity=.77)
        fig.update_layout(barmode='overlay')
        figures['first_edit']=_finish(fig,'First confirmed source edit · raw attempt index',xaxis_title='Attempt index (5-call bins)',yaxis_title='Attempts')
    else:figures['first_edit']=_empty('First confirmed source edit','No confirmed source edits recorded')
    bg=b.budget[b.budget.attempts.gt(0)]
    figures['budget']=_finish(go.Figure(go.Bar(x=bg.stage,y=bg.attempts,marker_color=[RED if x in ('pre_edit','pre_verification') else ORANGE if x=='post_edit' else GREY for x in bg.stage],customdata=bg.meaning,hovertemplate='%{x}: %{y} attempts<br>%{customdata}<extra></extra>')),'Observed budget stage · all accepted attempts',yaxis_title='Attempts')
    sub=b.submission
    top=sub.get('top_tasks',[])
    if top:
        top=top[::-1]
        figures['submission']=_finish(go.Figure(go.Bar(x=[x['submit_attempts'] for x in top],y=[x['task_id'] for x in top],orientation='h',marker_color=ORANGE,customdata=[x['outcome'] for x in top],hovertemplate='%{y}: %{x} submit attempts<br>Official outcome: %{customdata}<extra></extra>')),'Most repeated submit_patch calls',height=480,xaxis_title='Raw attempts')
    else:figures['submission']=_empty('Repeated submit_patch calls','No submission calls recorded')
    tools=b.tool_reliability[b.tool_reliability.tool.ne('submit_patch')].sort_values('attempts') if len(b.tool_reliability) else b.tool_reliability
    if len(tools):
        fig=go.Figure()
        for status,color,col in [('ok',GREEN,'ok'),('error',RED,'error'),('unknown result',GREY,'unknown_result')]:
            fig.add_bar(name=status,x=tools[col],y=tools.tool,orientation='h',marker_color=color)
        fig.update_layout(barmode='stack')
        figures['tools']=_finish(fig,'Tool execution outcomes · submit_patch shown separately',height=max(390,len(tools)*40+120),xaxis_title='Raw attempted calls')
    else:figures['tools']=_empty('Tool execution outcomes','No non-submission tool attempts recorded')
    stages=b.primary_stages
    if len(stages):
        figures['stages']=_finish(go.Figure(go.Bar(x=stages.stage,y=stages.attempts,marker_color=PURPLE,customdata=stages.denominator,hovertemplate='%{x}: %{y}/%{customdata} unresolved attempts<extra></extra>')),'Tentative primary stage · unresolved attempts only',yaxis_title='Attempts')
    else:figures['stages']=_empty('Tentative primary stage','No unresolved attempts with a stage')
    costs=b.tasks
    if len(costs):
        fig=go.Figure()
        for outcome,color in [('resolved',GREEN),('unresolved',RED),('unknown',GREY)]:
            part=costs[costs.outcome.eq(outcome)]
            fig.add_scatter(x=part.budgeted_tool_calls,y=part.duration_seconds,mode='markers',name=outcome,marker=dict(color=color,size=11,opacity=.75),text=part.task_id,customdata=part.run_id,hovertemplate='%{text}<br>Budgeted calls %{x}<br>Duration %{y:.1f}s<extra></extra>')
        figures['cost']=_finish(fig,'Budgeted calls versus duration · descriptive',xaxis_title='Budgeted tool calls',yaxis_title='Seconds')
    else:figures['cost']=_empty('Budgeted calls versus duration','No cost data')
    q=b.quality.head(10)
    if len(q):
        figures['quality']=_finish(go.Figure(go.Bar(x=q.affected_attempts[::-1],y=q.issue_type[::-1],orientation='h',marker_color=GREY,customdata=q.denominator[::-1],hovertemplate='%{y}: %{x}/%{customdata} attempts<extra></extra>')),'Data-quality flags · affected attempts',height=max(360,len(q)*38+100),xaxis_title='Attempts')
    else:figures['quality']=_empty('Data-quality flags','No quality issues recorded')
    return figures


def render_batch_report(batch):
    """Display the companion batch report in the *same* running notebook."""
    from IPython.display import HTML, display
    import ipywidgets as widgets
    figures=build_batch_figures(batch)
    o=batch.overview
    ci=o['wilson_95']
    ci_text=f'{ci[0]:.1%}–{ci[1]:.1%}' if ci[0] is not None else 'unavailable'
    rate_text=f'{o["resolved_rate"]:.1%}' if o['resolved_rate'] is not None else 'unavailable'
    header=widgets.HTML(value=f'<h2>Whole-batch report · {o["experiment_id"]}</h2><p><b>{o["resolved"]}/{o["known_outcomes"]}</b> known outcomes resolved ({rate_text}); coverage {o["known_outcomes"]}/{o["total_runs"]}; Wilson 95% interval {ci_text}. These are attempts, not unique tasks.</p>')
    sections=[('Outcomes',['outcomes','repositories'],batch.repositories),('Workflow',['workflow','first_edit'],batch.workflow),('Budget & submissions',['budget','submission'],batch.budget),('Tools',['tools'],batch.tool_reliability),('Failure stages',['stages'],batch.primary_stages),('Cost',['cost'],batch.cost),('Data quality',['quality'],batch.quality),('Optional analyses',[],batch.gold_metrics[['run_id','metric_id','value','availability','unavailable_reason','denominator']] if len(batch.gold_metrics) else batch.gold_metrics),('Task index',[],batch.tasks[['task_id','repo','outcome','primary_stage','confirmed_source_edits','post_edit_agent_test','budget_stage','budgeted_tool_calls','submit_attempts','duration_seconds','trace_path']] if len(batch.tasks) else batch.tasks)]
    outputs=[]
    for title,keys,frame in sections:
        output=widgets.Output()
        with output:
            if title=='Workflow':display(HTML('<p>Each row states its own denominator. A final test with exit code 0 is separate from official resolution.</p>'))
            if title=='Failure stages':display(HTML('<p>Rules select a tentative observable stage. Findings do not establish a semantic root cause.</p>'))
            if title=='Optional analyses':
                display(HTML('<h4>Gold-function metric availability</h4><p>Gold patches and baseline symbol maps are optional. Unavailable values are not zeros.</p>'))
                if len(batch.pairing):
                    display(HTML('<h4>Paired experiments</h4>'))
                    display(batch.pairing)
                else:display(HTML('<p>Second comparison batch not supplied; paired outcomes unavailable.</p>'))
                display(batch.modules)
            if title=='Budget & submissions':display(HTML(f'<p><b>{batch.submission["attempts"]}</b> raw submit_patch attempts; <b>{batch.submission["attempts_with_50_plus"]}</b> tasks have 50 or more. Raw attempts differ from budget-counted calls.</p>'))
            for key in keys:display(figures[key])
            display(frame)
        outputs.append(output)
    tabs=widgets.Tab(children=outputs)
    for i,(title,_,__) in enumerate(sections):tabs.set_title(i,title)
    ui=widgets.VBox([header,tabs])
    display(ui)
    return ui
