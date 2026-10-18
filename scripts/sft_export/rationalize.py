#!/usr/bin/env python3
"""Generate masked-loss thoughts for every tool-call step of the trajectory data.

Usage (vLLM reachable on --base-url, e.g. through an SSH tunnel):
  python scripts/sft_export/rationalize.py sample --limit 20           # iterate on the prompt
  python scripts/sft_export/rationalize.py run                         # all steps
  python scripts/sft_export/rationalize.py apply                       # write dataset with `reasoning`

Thoughts are keyed by (row, message index) of the first assistant message of each
response group. The generator never sees the step's own tool output.
"""
import argparse
import json
import random
import re
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from prompts.rationalize_thought import USER_TEMPLATE, VERSIONS  # noqa: E402

DEFAULT_DATA = ROOT / "data/transient/sft-haiku-solved-v5-none/trajectory/train.jsonl"
OUT_DIR = ROOT / "data/transient/rationalize"
HISTORY_OBS_CHARS = 0  # tool output chars shown to the generator (0 = none); training data is untouched
MAX_HISTORY_CHARS = 60000
LEAK_WORDS = re.compile(r"\b(the output (shows|showed)|turned out|it worked|it passed|as expected|tool call|next step)\b", re.I)


def groups(messages):
    """Yield (start_idx, [message idxs]) for each assistant response group."""
    i, n = 0, len(messages)
    while i < n:
        if messages[i]["role"] == "assistant":
            j = i
            while j < n and messages[j]["role"] == "assistant":
                j += 1
            yield i, list(range(i, j))
            i = j
        else:
            i += 1


def call_text(call):
    fn = call["function"]
    args = fn["arguments"]
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    return f"{fn['name']} {json.dumps(args, ensure_ascii=False)}"


def render_step(messages, idxs):
    parts = []
    for k in idxs:
        m = messages[k]
        if (m.get("content") or "").strip():
            parts.append("engineer said: " + m["content"].strip())
        for c in m.get("tool_calls") or []:
            parts.append("action: " + call_text(c))
    return "\n".join(parts)


def render_history(messages, upto):
    out = []
    for m in messages[:upto]:
        role = m["role"]
        if role == "system":
            continue
        if role == "user":
            out.append("[task/context]\n" + m["content"].strip())
        elif role == "assistant":
            if (m.get("content") or "").strip():
                out.append("[engineer said] " + m["content"].strip())
            for c in m.get("tool_calls") or []:
                out.append("[action] " + call_text(c))
        elif role == "tool":
            c = m.get("content") or ""
            if HISTORY_OBS_CHARS == 0:
                out.append("[result omitted]")
                continue
            if len(c) > HISTORY_OBS_CHARS:
                c = c[: HISTORY_OBS_CHARS // 2] + " …[cut]… " + c[-HISTORY_OBS_CHARS // 2 :]
            out.append("[result] " + c)
    text = "\n".join(out)
    return text[-MAX_HISTORY_CHARS:]


def build_jobs(rows):
    jobs = []
    for r, row in enumerate(rows):
        msgs = row["messages"]
        for start, idxs in groups(msgs):
            if not any(msgs[k].get("tool_calls") for k in idxs):
                continue
            jobs.append({"row": r, "msg": start, "idxs": idxs})
    return jobs


def chat(base_url, model, system, user, temperature, max_tokens):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "top_p": 0.95,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.load(resp)["choices"][0]["message"]["content"] or ""


def clean(text):
    t = text.strip().strip('"').strip()
    t = re.sub(r"^(thought|thinking)\s*:\s*", "", t, flags=re.I)
    return t.strip()


BANNED = re.compile(r"\b(okay, so|i need to|logical|seems like|to confirm that|in order to|a good way|the right way)\b", re.I)


def validate(t):
    if BANNED.search(t):
        return "banned_phrase"
    if not 100 <= len(t) <= 520:
        return "length"
    if "```" in t or re.search(r"^\s*[-*#]\s", t, re.M):
        return "markdown"
    if LEAK_WORDS.search(t):
        return "leak_or_narration"
    return None


def generate(job, rows, args, system):
    msgs = rows[job["row"]]["messages"]
    user = USER_TEMPLATE.format(history=render_history(msgs, job["msg"]), step=render_step(msgs, job["idxs"]))
    last = None
    for attempt in range(args.retries + 1):
        raw = chat(args.base_url, args.model, system, user, args.temperature, args.max_tokens)
        t = clean(raw)
        why = validate(t)
        if why is None:
            return {**job, "thought": t, "attempts": attempt + 1}
        last = (t, why)
    return {**job, "thought": None, "rejected": last[0], "reason": last[1], "attempts": args.retries + 1}


def run(args):
    rows = [json.loads(l) for l in open(args.data)]
    jobs = build_jobs(rows)
    if args.limit:
        random.Random(args.seed).shuffle(jobs)
        jobs = jobs[: args.limit]
    system = VERSIONS[args.prompt_version]["system"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / (f"sample-{args.prompt_version}.jsonl" if args.cmd == "sample" else f"thoughts-{args.prompt_version}.jsonl")
    done = set()
    if args.cmd == "run" and out_path.exists():
        done = {(j["row"], j["msg"]) for j in map(json.loads, open(out_path)) if j.get("thought")}
    jobs = [j for j in jobs if (j["row"], j["msg"]) not in done]
    lock, n = threading.Lock(), [0]
    mode = "a" if args.cmd == "run" else "w"
    with open(out_path, mode) as f, ThreadPoolExecutor(args.workers) as ex:
        for res in ex.map(lambda j: generate(j, rows, args, system), jobs):
            with lock:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                n[0] += 1
                if n[0] % 50 == 0:
                    print(f"{n[0]}/{len(jobs)}", flush=True)
    print(f"wrote {out_path} ({len(jobs)} jobs)")
    if args.cmd == "sample":
        show(out_path, rows)


def show(path, rows):
    ok = bad = 0
    for line in open(path):
        r = json.loads(line)
        msgs = rows[r["row"]]["messages"]
        print("=" * 80)
        print("STEP:", render_step(msgs, r["idxs"])[:400])
        if r.get("thought"):
            ok += 1
            print("THOUGHT:", r["thought"])
        else:
            bad += 1
            print(f"REJECTED ({r['reason']}):", r.get("rejected"))
    print(f"\naccepted {ok}, rejected {bad}")


def split_at_user_turns(row):
    """Split a row at every user message after the first one.

    Gemma's template shows a thought only when its message comes after the last user
    message, so one render of a row with a mid-run user turn cannot match what the
    model saw at each step. Part j keeps messages up to the next user turn (earlier
    thoughts visible, as at generation time); later parts carry earlier assistant
    turns as context only (train=False).
    """
    msgs = row["messages"]
    cuts = [k for k, m in enumerate(msgs) if m["role"] == "user" and k > 1]
    if not cuts:
        return [row]
    ends = cuts + [len(msgs)]
    out = []
    for j, end in enumerate(ends):
        part = [dict(m) for m in msgs[:end]]
        frozen = cuts[j - 1] if j else 0
        for k in range(frozen):
            if part[k]["role"] == "assistant":
                part[k]["train"] = False
        if any(m["role"] == "assistant" and m.get("train") for m in part):
            out.append({**row, "messages": part, "part": j + 1})
    return out


def apply(args):
    rows = [json.loads(l) for l in open(args.data)]
    thoughts = {}
    for line in open(OUT_DIR / f"thoughts-{args.prompt_version}.jsonl"):
        r = json.loads(line)
        if r.get("thought"):
            thoughts[(r["row"], r["msg"])] = r["thought"]
    total = len(build_jobs(rows))
    if len(thoughts) != total:
        raise SystemExit(f"{total - len(thoughts)} tool-call steps have no thought; rerun `run` first")
    for (r, k), t in thoughts.items():
        rows[r]["messages"][k]["reasoning"] = t
    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(dst, "w") as f:
        for row in rows:
            for part in split_at_user_turns(row):
                f.write(json.dumps(part, ensure_ascii=False) + "\n")
                written += 1
    manifest_src = Path(args.data).with_name("manifest.json")
    manifest = json.loads(manifest_src.read_text()) if manifest_src.exists() else {}
    manifest.update({
        "schema_version": "gemma4-sft-v6",
        "thoughts": {"source": "gemma-4-E4B-it-qat-w4a16-ct rationalization", "prompt_version": args.prompt_version,
                     "steps": total, "loss": "masked (context only)", "result_excerpt_chars": 300},
        "retain_reasoning": True,
        "examples": written,
    })
    (dst.parent / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"thoughts attached to {len(thoughts)} of {total} tool-call steps -> {dst}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["sample", "run", "apply"])
    p.add_argument("--data", default=str(DEFAULT_DATA))
    p.add_argument("--base-url", default="http://127.0.0.1:8002/v1")
    p.add_argument("--model", default="gemma4")
    p.add_argument("--prompt-version", default="v5", choices=sorted(VERSIONS))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=160)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--out", default=str(ROOT / "data/transient/sft-haiku-solved-v6-thoughts/trajectory/train.jsonl"))
    p.add_argument("--result-chars", type=int, default=300)
    args = p.parse_args()
    global HISTORY_OBS_CHARS
    HISTORY_OBS_CHARS = args.result_chars
    if args.cmd == "sample" and not args.limit:
        args.limit = 20
    apply(args) if args.cmd == "apply" else run(args)


if __name__ == "__main__":
    main()
