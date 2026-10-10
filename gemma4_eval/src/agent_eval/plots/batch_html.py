"""Small, offline companion report containing only batch rollups and a task index."""
import json
from pathlib import Path
from plotly.offline import get_plotlyjs
from plotly.utils import PlotlyJSONEncoder
from .batch import build_batch_figures
from ..exports import records


def write_batch_html(batch, path, findings=None, reviews=None):
    figures={key:figure.to_plotly_json() for key,figure in build_batch_figures(batch).items()}
    finding_runs={}
    if findings is not None and len(findings.findings):
        finding_runs={category:sorted(set(group.run_id)) for category,group in findings.findings.groupby('category')}
    data=dict(overview=batch.overview,repositories=records(batch.repositories),workflow=records(batch.workflow),budget=records(batch.budget),tools=records(batch.tool_reliability),stages=records(batch.primary_stages),findings=records(batch.finding_prevalence),finding_runs=finding_runs,cost=records(batch.cost),tasks=records(batch.tasks),quality=records(batch.quality),submission=batch.submission,gold_metrics=records(batch.gold_metrics),pairing=records(batch.pairing),modules=records(batch.modules),figures=figures)
    if reviews is not None and not reviews.empty:
        data['llm_reviews']=records(reviews)
        if findings is not None:
            reviewed_ids=set(reviews.loc[reviews.status.eq('ok'),'run_id'])
            data['llm_rule_findings_total']=int(findings.findings.run_id.isin(reviewed_ids).sum())
    serialized=json.dumps(data,cls=PlotlyJSONEncoder,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=Path(__file__).with_name('batch_template.html').read_text()
    if reviews is not None and not reviews.empty:
        from ..review.html import inject_batch
        template=inject_batch(template)
    Path(path).write_text(template.replace('__PLOTLY_JS__',get_plotlyjs()).replace('__BATCH_DATA__',serialized))
