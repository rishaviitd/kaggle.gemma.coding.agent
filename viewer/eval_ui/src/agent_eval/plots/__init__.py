"""Focused single-trace Jupyter presentation and separate batch renderer."""
import html
import pandas as pd
from .charts import build_figures
from .batch import render_batch_report, build_batch_figures
from ..evidence import evidence_html, read_evidence


def render_report(experiment, metrics, findings):
    """Render one selected trace at a time; aggregate views live in render_batch_report."""
    import ipywidgets as widgets
    from IPython.display import display, HTML, clear_output
    import plotly.graph_objects as go
    e,m,f=experiment,metrics,findings
    repo=widgets.Dropdown(options=['All']+sorted(e.runs.repo.dropna().unique().tolist()),description='Repository:',layout=widgets.Layout(width='310px'))
    task=widgets.Dropdown(description='Task:',layout=widgets.Layout(width='570px'))
    panel=widgets.Output()
    def options(keep=None):
        runs=e.runs if repo.value=='All' else e.runs[e.runs.repo.eq(repo.value)]
        task.options=[(f'{r.task_id} · attempt {r.attempt_id}',r.run_id) for r in runs.itertuples()]
        if keep in [value for _,value in task.options]:task.value=keep
    def render(change=None):
        rid=task.value
        with panel:
            clear_output(wait=True)
            if not rid:
                display(HTML('<p>No accepted traces match the selection.</p>'));return
            r=e.runs[e.runs.run_id.eq(rid)].iloc[0]
            c=e.tool_calls[e.tool_calls.run_id.eq(rid)].copy();results=e.tool_results[e.tool_results.run_id.eq(rid)]
            c=c.merge(results[['run_id','call_id','status','exit_code','error_type','output_excerpt','evidence_id']],on=['run_id','call_id'],how='left',suffixes=('','_result'))
            tests=e.test_evidence[e.test_evidence.run_id.eq(rid)]
            patch=e.patches[e.patches.run_id.eq(rid)].iloc[0]
            primary=f.primary[f.primary.run_id.eq(rid)]
            per_metric=m.metrics[m.metrics.run_id.eq(rid)].set_index('metric_id')
            def value(key):
                return per_metric.loc[key,'value'] if key in per_metric.index and per_metric.loc[key,'availability']!='unavailable' else None
            outcome='unknown' if pd.isna(r.resolved_nullable) else 'resolved' if r.resolved_nullable else 'unresolved'
            stage=primary.iloc[0].primary_stage if len(primary) else 'unknown'
            display(HTML(f'<h2>{html.escape(str(r.task_id))} · attempt {r.attempt_id}</h2><p><b>Official outcome:</b> {outcome} · <b>Harness:</b> {html.escape(str(r.harness_status))} · <b>Final test exit:</b> {html.escape(str(r.test_exit_code))} · <b>Budget-counted calls:</b> {html.escape(str(r.budgeted_tool_calls))} · <b>Duration:</b> {html.escape(str(r.duration_seconds))}s · <b>Primary stage:</b> {html.escape(str(stage))}</p><p><small>Source: {html.escape(str(r.trace_path))}</small></p>'))
            figs=build_figures(e,m,f,[rid])
            panes=[]
            def pane(content):
                output=widgets.Output()
                with output:content()
                panes.append(output)
            def timeline():
                display(HTML('<p>Call order only; timestamps do not measure tool duration. Select a point or evidence row to inspect source.</p>'))
                try:
                    figure=go.FigureWidget(figs['timeline'])
                    detail=widgets.HTML('<p>Select an event to inspect its source pointer.</p>')
                    def clicked(trace,points,state):
                        if points.point_inds:
                            eid=trace.customdata[points.point_inds[0]][0]
                            detail.value=evidence_html(e,[eid])
                    for trace in figure.data:trace.on_click(clicked)
                    display(figure,detail)
                except (ImportError,ValueError):
                    display(figs['timeline'])
                ordered=e.events[e.events.run_id.eq(rid)].sort_values('event_order')
                picker=widgets.Dropdown(options=[(f'{row.event_order:g} · {row.kind} · turn {row.turn_number}',row.evidence_id) for row in ordered.itertuples()],description='Evidence:',layout=widgets.Layout(width='90%'))
                detail=widgets.HTML()
                def refresh(change=None):
                    detail.value=evidence_html(e,[picker.value]) if picker.value else ''
                picker.observe(refresh,names='value');refresh()
                button=widgets.Button(description='Load larger redacted source excerpt')
                def full(_):
                    if picker.value:detail.value='<pre>'+html.escape(read_evidence(e,picker.value))+'</pre>'
                button.on_click(full);display(picker,button,detail)
            pane(timeline)
            def tool_history():
                display(figs['tools']);display(figs['budgets'])
                if len(c):display(c[['sequence_index','turn_number','name','requested_path','command_category','status','exit_code','error_type','schema_issues','result_linked','evidence_id','evidence_id_result']].sort_values('sequence_index'))
                display(HTML('<p>Tool status describes execution. A command can run successfully and still exit nonzero.</p>'))
            pane(tool_history)
            def verify():
                post=value('C08')
                display(HTML(f'<p><b>Confirmed source edits:</b> {html.escape(str(value("confirmed_source_edits")))} · <b>Agent tests after final confirmed edit:</b> {"unavailable" if post is None else "recorded" if post else "absent"}. Independent final verification is separate.</p>'))
                for source in ['agent-side','external']:
                    display(HTML('<h4>'+('Agent-side tests and reproductions' if source=='agent-side' else 'Independent final verifier')+'</h4>'))
                    for row in tests[tests.source.eq(source)].itertuples():
                        display(HTML('<details><summary>Turn '+html.escape(str(row.turn_number))+' · exit '+html.escape(str(row.exit_code))+'</summary><pre>'+html.escape(str(row.output_excerpt))+'</pre>'+evidence_html(e,[row.evidence_id])+'</details>'))
                    if not len(tests[tests.source.eq(source)]):display(HTML('<p>No evidence recorded.</p>'))
            pane(verify)
            def tokens():
                display(figs['tokens'])
                turns=e.turns[e.turns.run_id.eq(rid)]
                display(turns[['turn_number','finish_reason','prompt_tokens','completion_tokens','reasoning_tokens','is_compaction','compaction_confidence','evidence_id']])
            pane(tokens)
            def final_patch():
                display(HTML('<p>Files: '+html.escape(', '.join(patch.modified_files) or 'none')+' · added '+str(patch.added_lines)+' · deleted '+str(patch.deleted_lines)+' · apply status '+html.escape(str(patch.apply_status))+'</p><pre>'+html.escape(str(patch.patch_excerpt))+'</pre>'))
            pane(final_patch)
            def find():
                selection=f.findings[f.findings.run_id.eq(rid)]
                if not len(selection):display(HTML('<p>No rule-based findings recorded.</p>'))
                for row in selection.itertuples():
                    display(HTML('<details><summary>'+html.escape(row.headline)+' · '+html.escape(row.confidence)+' · '+html.escape(row.observation)+'</summary><p>'+html.escape(row.suggested_intervention)+'</p>'+evidence_html(e,row.evidence_ids)+'</details>'))
            pane(find)
            def quality():
                issues=e.quality_issues[e.quality_issues.run_id.eq(rid)]
                display(issues[['issue_type','field_path','description','evidence_id']])
                if not len(issues):display(HTML('<p>No parser warnings for this attempt.</p>'))
            pane(quality)
            tabs=widgets.Tab(children=panes)
            for index,title in enumerate(['Timeline','Tools & budget','Verification','Tokens & context','Final patch','Findings','Data quality']):tabs.set_title(index,title)
            display(tabs)
    def change_repo(change):
        options(task.value);render()
    repo.observe(change_repo,names='value');task.observe(render,names='value')
    options()
    ui=widgets.VBox([widgets.HTML('<h2>Single-agent trace explorer</h2><p>Select one attempt. Whole-batch charts appear in the separate batch report below.</p>'),widgets.HBox([repo,task]),panel])
    display(ui);render()
    return ui
