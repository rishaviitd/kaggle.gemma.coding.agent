"""Build a Kaggle notebook that runs one src/<name>/itr-<n> submission on the official starter.

The output folder (notebooks/<name>-itr-<n>/) holds a notebook plus kernel-metadata.json.
It is made from notebooks/starter.ipynb with four changes:
  1. Our submission files are overlaid on the official sample_submission (adapter lines dropped,
     because no adapter weights ship with the run; the starter's configs/sampling.yaml is kept).
  2. Tool-call cap, time cap and timeout come from the submission's eval_config.yaml
     (the starter hardcodes 25 calls and ignores the file).
  3. A list of task ids is run instead of one, with one model_trace.json per task.
  4. The kernel is pinned to the Python 3.12 image the wheelhouse was built for.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "notebooks" / "starter.ipynb"
ALLOWED_EXT = {".json", ".md", ".py", ".yaml", ".yml", ".txt"}
SKIP_DIRS = {"tests", "__pycache__", "adapters", "tools"}
SKIP_FILES = {"configs/sampling.yaml"}
# Same 12 train tasks the earlier Kaggle run used; all of them also exist in the itr-1 logs.
DEFAULT_TASKS = [
    "requests_7205", "rich_4070", "rich_4077", "rich_4079", "rich_4076", "rich_2943",
    "rich_3006", "rich_3454", "rich_3518", "rich_3061", "rich_3480", "rich_3934",
]
# Python 3.12 image of the kernel that completed (akshayggupta1/gemma-4-developer-agent-competition-run).
DOCKER_IMAGE = "gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461"


def replace_once(text, old, new, what):
    if text.count(old) != 1:
        raise SystemExit(f"Template changed: expected exactly one {what!r}, found {text.count(old)}.")
    return text.replace(old, new)


def collect_files(submission):
    files = {}
    for path in sorted(submission.rglob("*")):
        rel = path.relative_to(submission)
        if not path.is_file() or set(rel.parts[:-1]) & SKIP_DIRS or rel.as_posix() in SKIP_FILES:
            continue
        if path.suffix.lower() not in ALLOWED_EXT:
            raise SystemExit(f"Disallowed file extension in submission: {rel}")
        files[rel.as_posix()] = path.read_text(encoding="utf-8")
    if "agent.yaml" not in files:
        raise SystemExit(f"Missing agent.yaml in {submission}")
    return files


OVERLAY = '''# Overlay our submission ({label}) on the official sample submission.
import base64, json, re
OUR_FILES = json.loads(base64.b64decode('{payload}').decode())
for rel, text in OUR_FILES.items():
    dest = AGENT_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')
print(f'Overlaid {{len(OUR_FILES)}} files from {label}')

# Adapter weights are not available for this run: drop adapter references.
for y in [AGENT_DIR / 'agent.yaml', *sorted((AGENT_DIR / 'sub_agents').glob('*.yaml'))]:
    y.write_text(re.sub(r'(?m)^adapter:.*\\n', '', y.read_text(encoding='utf-8')), encoding='utf-8')
shutil.rmtree(AGENT_DIR / 'adapters', ignore_errors=True)

'''

RUN_LOOP = '''try:
    for idx, task in enumerate(SAMPLE_TASKS, start=1):
        print(f'[{idx}/{len(SAMPLE_TASKS)}] Evaluating {task.instance_id} ({task.repo})...')
        task_dir = LOGS_ROOT / task.instance_id
        (task_dir / 'results').mkdir(parents=True, exist_ok=True)
        trace_proxy.exchanges.clear()
        result = None
        try:
            result = run_sync(
                evaluator.evaluate_task,
                task=task,
                task_index=idx,
                total_tasks=len(SAMPLE_TASKS),
            )
        except Exception as exc:  # keep going: one bad task must not end the run
            print(f'  -> ERROR {exc!r}')
        finally:
            final = result.model_dump(mode='json', exclude={'trace'}) if result is not None else None
            trace_proxy.write_trace(
                task_dir / 'model_trace.json',
                run={'task_id': task.instance_id, 'upstream_api_base': server_instance.base_url},
                final={'result': final},
            )
        if result is None:
            predictions.append({'id': task.instance_id, 'prediction': ''})
            continue
        patch = result.agent_patch or ''
        (task_dir / 'results' / f'{task.instance_id}.json').write_text(
            result.model_dump_json(indent=2, exclude={'trace'}), encoding='utf-8'
        )
        (task_dir / 'results' / f'{task.instance_id}.patch').write_text(patch, encoding='utf-8')
        predictions.append({'id': task.instance_id, 'prediction': patch})
        print(
            f'  -> resolved={result.resolved}, exit_code={result.test_exit_code}, '
            f'patch_chars={len(patch)}, tool_calls={result.tool_calls}, '
            f'duration={result.duration_seconds:.1f}s'
        )
finally:
    trace_proxy.stop()

submission_df = pd.DataFrame(predictions, columns=['id', 'prediction'])
display(submission_df)
print('NON-EMPTY PATCHES:', sum(1 for p in predictions if p['prediction']), 'of', len(predictions))
'''



def build(submission, out_dir, kernel_id, task_ids):
    label = submission.relative_to(ROOT / "src").as_posix()
    nb = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    for cell in code:
        cell["outputs"] = []
        cell["execution_count"] = None

    def src(cell):
        return "".join(cell["source"])

    # Dataset / sample submission cell: overlay our files before the tasks are loaded.
    payload = base64.b64encode(json.dumps(collect_files(submission)).encode()).decode()
    data_cell = next(c for c in code if "SAMPLE_SUBMISSION_SRC" in src(c))
    marker = "# Load tasks from the published dataset\n"
    text = replace_once(src(data_cell), marker, OVERLAY.format(label=label, payload=payload) + marker, "tasks marker")
    data_cell["source"] = text.splitlines(keepends=True)

    # Evaluation cell: budgets from eval_config.yaml, a task list, and a per-task trace.
    run_cell = next(c for c in code if "max_tool_calls = 25" in src(c))
    text = src(run_cell)
    text = replace_once(
        text,
        "timeout_seconds = 300\n"
        "# max_tool_calls = int(eval_section.get('max_tool_calls', 50))\n"
        "max_tool_calls = 25\n"
        "# max_time_minutes = float(eval_section.get('max_time_minutes', 10.0))\n"
        "max_time_minutes = 10.0\n",
        "timeout_seconds = int(eval_section.get('timeout_seconds', 120))\n"
        "max_tool_calls = int(eval_section.get('max_tool_calls', 30))\n"
        "max_time_minutes = float(eval_section.get('max_time_minutes', 10.0))\n",
        "budget block",
    )
    start = text.index("# Select a small subset of tasks")
    end = text.index("limits, gen_constraints = build_submission_limits()")
    tasks_block = (
        f"RUN_IDS = {task_ids!r}\n"
        "by_id = {t.instance_id: t for t in tasks}\n"
        "missing = [i for i in RUN_IDS if i not in by_id]\n"
        "if missing:\n"
        "    raise ValueError(f'Not in the competition tasks: {missing}')\n"
        "SAMPLE_TASKS = [by_id[i] for i in RUN_IDS]\n"
        "LOGS_ROOT = WORKING_DIR / 'logs' / 'remote' / 'train'\n"
        "print(f'Running {len(SAMPLE_TASKS)} tasks; budget {max_tool_calls} calls / "
        "{max_time_minutes} min / timeout {timeout_seconds}s')\n"
    )
    text = text[:start] + tasks_block + text[end:]
    text = replace_once(text, "results_dir=TASK_LOGS_DIR,", "results_dir=WORKING_DIR / 'results',", "results_dir")
    loop_at = text.index("try:\n    for idx, task in enumerate(SAMPLE_TASKS")
    text = text[:loop_at] + RUN_LOOP
    run_cell["source"] = text.splitlines(keepends=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    slug = kernel_id.split("/", 1)[1]
    (out_dir / f"{slug}.ipynb").write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
    metadata = json.loads((ROOT / "notebooks" / "kernel-metadata.json").read_text())
    metadata.update({
        "id": kernel_id,
        "title": slug.replace("-", " "),
        "code_file": f"{slug}.ipynb",
        "is_private": "true",
        "docker_image": DOCKER_IMAGE,
        "docker_image_pinning_type": "original",
        "model_sources": ["google/gemma-4/Other/gemma-4-31b-it-qat-w4a16-ct/2"],
    })
    (out_dir / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return out_dir


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--submission", type=Path, required=True, help="For example src/akshay/itr-2.")
    p.add_argument("--kernel-id", required=True, help="Your Kaggle handle and slug, e.g. akshayggupta1/gemma4-akshay-itr2.")
    p.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS, help="Train task ids to run.")
    p.add_argument("--out", type=Path, help="Output folder; defaults to notebooks/<name>-<itr>.")
    args = p.parse_args()
    submission = args.submission.resolve()
    if "/" not in args.kernel_id:
        p.error("--kernel-id must look like owner/slug")
    out = args.out or ROOT / "notebooks" / "-".join(submission.relative_to(ROOT / "src").parts)
    print(build(submission, out.resolve(), args.kernel_id, args.tasks))


if __name__ == "__main__":
    sys.exit(main())

