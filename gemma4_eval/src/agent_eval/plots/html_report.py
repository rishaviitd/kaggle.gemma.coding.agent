"""Browser-only filtering over normalized tables; bundled Plotly, no CDN."""
import json
from pathlib import Path
from plotly.offline import get_plotlyjs


def write_html(e,m,f,path):
    from ..exports import records
    payload={name:records(getattr(e,name)) for name in ['runs','turns','tool_calls','tool_results','events','patches','test_evidence','quality_issues','evidence','intake']}
    payload.update(metrics=records(m.metrics),findings=records(f.findings),primary=records(f.primary),pairing=records(m.pairing),gold=records(m.gold),modules=records(m.modules),summary=m.summary)
    if not m.reviews.empty:
        payload['llm_reviews']=records(m.reviews)
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    template=Path(__file__).with_name('report_template.html').read_text()
    if not m.reviews.empty:
        from ..review.html import inject_single
        template=inject_single(template)
    Path(path).write_text(template.replace('__PLOTLY_JS__',get_plotlyjs()).replace('__PAYLOAD__',data))
