#!/usr/bin/env python3
"""One trajectory LoRA run on Kaggle's L4x4 kernel, the model split over all four GPUs by Unsloth.

  r=64  alpha=128  trajectory  3 epochs

The bf16 E4B weights do not leave enough room to train long rows on one 24 GB L4, so the model
is split over GPUS_PER_RUN cards with Unsloth's own placement (device_map="unsloth_balanced").
This is the first, simplest version. Other ranks and parallel runs come after it is proven.

Base: Gemma 4 E4B QAT unquantized (bf16 LoRA). The adapter is meant to be served
on the matching QAT w4a16 checkpoint. Reads wheels, JSONL, the canonical chat template
and the model from /kaggle/input only. Writes the adapter to /kaggle/working.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

INPUT_ROOT = Path("/kaggle/input")
OUTPUT_ROOT = Path("/kaggle/working/e4b-lora-parallel")
MAX_SEQ_LENGTH = 12288
EPOCHS = 3
LEARNING_RATE = 1e-4
MODEL_MARKER = "gemma-4-e4b-it-qat-q4_0-unquantized"
TEMPLATE_MARKER = "Google Gemma 4 Canonical Chat Template"
DATA_SCHEMA = "gemma4-sft-v6"  # trajectory rows with loss-masked thoughts on every tool-call step

TOTAL_GPUS = 4
GPUS_PER_RUN = 4  # 1 = no split; 2 = two runs at a time; 4 = one run on all four cards
RANKS = (64,)
SPLIT_DEVICE_MAP = "unsloth_balanced"
GPU_GROUPS = tuple(tuple(range(start, start + GPUS_PER_RUN)) for start in range(0, TOTAL_GPUS, GPUS_PER_RUN))

# Alpha is twice the rank. Runs that share a wave train at the same time.
EXPERIMENTS = tuple(
    {"mode": "trajectory", "rank": rank, "alpha": 2 * rank,
     "gpus": GPU_GROUPS[index % len(GPU_GROUPS)], "wave": index // len(GPU_GROUPS)}
    for index, rank in enumerate(RANKS)
)


def waves() -> list[list[dict]]:
    count = max(experiment["wave"] for experiment in EXPERIMENTS) + 1
    return [[e for e in EXPERIMENTS if e["wave"] == wave] for wave in range(count)]


def dataset_dir() -> Path:
    """The attached dataset that holds the wheels, the chat template and sft/trajectory."""
    hits = sorted(INPUT_ROOT.glob("**/sft/trajectory/train.jsonl"))
    usable = [hit for hit in hits if (hit.parents[2] / "chat_template.jinja").exists()
              and list(hit.parents[2].glob("unsloth-*.whl"))]
    if len(usable) != 1:
        raise RuntimeError(f"expected one Kaggle SFT dataset under /kaggle/input, found {len(usable)}")
    return usable[0].parents[2]


def load_chat_template(data_root: str) -> str:
    """The canonical Gemma 4 template shipped in the dataset.

    The model copies on Kaggle carry the June release template, which closes the
    turn between commentary and a tool call. Training always uses the canonical one.
    """
    text = (Path(data_root).parent / "chat_template.jinja").read_text()
    if TEMPLATE_MARKER not in text:
        raise RuntimeError("dataset chat_template.jinja is not the canonical Gemma 4 template")
    return text


def check_manifest(data_root: str, mode: str = "trajectory") -> dict:
    """Refuse to train on an older dataset version than the one with thoughts."""
    manifest = json.loads((Path(data_root) / mode / "manifest.json").read_text())
    if manifest.get("schema_version") != DATA_SCHEMA or not manifest.get("thoughts"):
        raise RuntimeError(f"expected {DATA_SCHEMA} data with thoughts, got {manifest.get('schema_version')}")
    rows = sum(1 for line in (Path(data_root) / mode / "train.jsonl").read_text().splitlines() if line.strip())
    if rows != manifest["examples"]:
        raise RuntimeError(f"train.jsonl has {rows} rows, manifest says {manifest['examples']}")
    return manifest


def thought_leaks(tokenizer, row: dict, encoded: dict) -> int:
    """Number of thoughts whose text appears inside the supervised tokens (must be 0)."""
    supervised = tokenizer.decode([t for t, label in zip(encoded["input_ids"], encoded["labels"]) if label != -100])
    return sum(1 for m in row["messages"]
               if m.get("train") and (m.get("reasoning") or "").strip() and m["reasoning"].strip() in supervised)


def install_bundled(dataset: Path) -> None:
    """Install the cp313 Unsloth wheels shipped in the SFT dataset.

    The competition wheelhouse is a different mount and is not installed.
    """
    wheels = sorted(str(path) for path in dataset.glob("*.whl"))
    if not any(Path(path).name.startswith("unsloth-") for path in wheels):
        raise RuntimeError(f"no Unsloth wheels in {dataset}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", *wheels])
    print("installed", len(wheels), "bundled wheels from", dataset, flush=True)


def find_model_dir() -> str:
    hits = [path for path in INPUT_ROOT.glob("**/config.json")
            if MODEL_MARKER in str(path).lower() and "assistant" not in str(path).lower()
            and (path.parent / "model.safetensors").exists()]
    if not hits:
        raise RuntimeError(f"{MODEL_MARKER} is not mounted under /kaggle/input")
    return str(sorted(hits, key=lambda path: len(str(path)))[0].parent)


def intervals_overlap(records: list[dict]) -> bool:
    """True when every run's wall clock overlaps every other run."""
    if len(records) < 2:
        return False
    start = max(record["started_at"] for record in records)
    end = min(record["finished_at"] for record in records)
    return end > start


def assert_parallel(records: list[dict], experiments: list[dict] | None = None) -> None:
    """Check that one wave ran together: the right ranks, on the right GPU groups, overlapping in time."""
    experiments = waves()[0] if experiments is None else experiments
    if len(records) != len(experiments):
        raise RuntimeError(f"expected {len(experiments)} runs, got {len(records)}")
    expected = {experiment["rank"]: list(experiment["gpus"]) for experiment in experiments}
    got = {record.get("lora_r"): list(record["gpus"]) for record in records}
    if got != expected:
        raise RuntimeError(f"runs did not take their gpus: expected {expected}, got {got}")
    flat = [gpu for record in records for gpu in record["gpus"]]
    if len(set(flat)) != len(flat):
        raise RuntimeError(f"two runs share a gpu: {flat}")
    pids = [record["pid"] for record in records]
    if len(set(pids)) != len(records):
        raise RuntimeError(f"runs did not use {len(records)} processes: {pids}")
    names = [name for record in records for name in record["device_names"]]
    if any("L4" not in name for name in names):
        raise RuntimeError(f"expected L4 devices, got {names}")
    if len(records) > 1 and not intervals_overlap(records):
        raise RuntimeError("the runs of one wave did not overlap in wall-clock time")


THOUGHT_OPENER = "<|channel>thought\n"
THOUGHT_CLOSER = "<channel|>"


def common_prefix(a: str, b: str) -> int:
    limit = min(len(a), len(b))
    index = 0
    while index < limit and a[index] == b[index]:
        index += 1
    return index


def render_row(tokenizer, row: dict) -> dict:
    """Render one chat row once and mask everything except the supervised assistant spans.

    Prefix rows have a single supervised message, ``completion``. Trajectory rows
    supervise each assistant message whose ``train`` flag is true. Tool results
    and the task text stay in the sequence with labels of -100.

    The conversation is rendered a single time and tokenized a single time with
    character offsets, so labels always match the exact tokens the model sees.
    Each supervised span runs from where the generation prompt ends to where the
    message's own render ends (including the turn or tool-response stop token).
    Gemma opens a thought channel after a tool result when thinking is on. A message
    with ``reasoning`` keeps its thought in the sequence with no loss; the loss starts
    at the closing ``<channel|>`` (and covers the opener on a first turn, where the
    model has to write it). A message without one is supervised from the opener on.
    """
    tools = row["tools"]
    enable_thinking = bool(row.get("enable_thinking", True))
    if row["mode"] == "prefix":
        completion = row["completion"]
        messages = list(row["messages"]) + (completion if isinstance(completion, list) else [completion])
    else:
        messages = list(row["messages"])

    def render(subset, generation_prompt=False):
        return tokenizer.apply_chat_template(
            subset, tools=tools, tokenize=False, add_generation_prompt=generation_prompt,
            enable_thinking=enable_thinking)

    full = render(messages)
    spans = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("train", False):
            continue
        has_thought = bool((message.get("reasoning") or message.get("reasoning_content") or "").strip())
        prompt = render(messages[:index], True)
        if prompt.endswith(THOUGHT_OPENER) and not has_thought:
            prompt = prompt[:-len(THOUGHT_OPENER)]
        start = common_prefix(prompt, full)
        end = common_prefix(render(messages[:index + 1]), full)
        if end <= start:
            raise ValueError("empty supervised span")
        if spans and start < spans[-1][1]:
            raise ValueError("supervised spans overlap")
        if not has_thought:
            spans.append((start, end))
            continue
        # The thought text is context only: no loss on it. Loss covers the opener when the
        # model has to generate it (first turn), the closing marker, and everything after
        # it (commentary, tool call, stop token).
        opener = full.find(THOUGHT_OPENER, start, end)
        closer = full.find(THOUGHT_CLOSER, start, end)
        if closer < 0 or (opener < 0 and not prompt.endswith(THOUGHT_OPENER)):
            raise ValueError("thought block not found in rendered message")
        hole_start = opener + len(THOUGHT_OPENER) if opener >= 0 else start
        if hole_start > start:
            spans.append((start, hole_start))
        spans.append((closer, end))
    if not spans:
        raise ValueError("no supervised tokens")

    encoded = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    input_ids = list(encoded["input_ids"])
    labels = []
    cursor = 0
    for token_id, (char_start, _char_end) in zip(input_ids, encoded["offset_mapping"]):
        while cursor < len(spans) and char_start >= spans[cursor][1]:
            cursor += 1
        inside = cursor < len(spans) and spans[cursor][0] <= char_start < spans[cursor][1]
        labels.append(token_id if inside else -100)
    if not any(label != -100 for label in labels):
        raise ValueError("no supervised tokens")
    if len(input_ids) > MAX_SEQ_LENGTH:
        raise ValueError(f"{len(input_ids)} tokens exceeds quality cap {MAX_SEQ_LENGTH}")
    return {"input_ids": input_ids, "attention_mask": [1] * len(input_ids), "labels": labels}


def worker(gpus: tuple[int, ...], mode: str, rank: int, alpha: int, data_root: str, model_dir: str,
           output_root: str) -> None:
    """Train one experiment on its own GPU group. Imports Unsloth only inside the child."""
    os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(gpu) for gpu in gpus)
    started_at = time.time()
    status_path = Path(output_root) / f"r{rank}.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    identity = {"gpus": list(gpus), "pid": os.getpid(), "mode": mode, "lora_r": rank, "lora_alpha": alpha,
                "lr": LEARNING_RATE, "epochs": EPOCHS, "started_at": started_at}

    import torch
    from datasets import Dataset
    from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments
    from unsloth import FastModel

    if torch.cuda.device_count() != len(gpus):
        raise RuntimeError(f"r={rank} sees {torch.cuda.device_count()} devices, expected {len(gpus)}")
    device_names = [torch.cuda.get_device_name(index) for index in range(len(gpus))]
    status_path.write_text(json.dumps({**identity, "device_names": device_names, "phase": "loaded"}) + "\n")
    print(f"PARALLEL_DEVICE gpus={list(gpus)} pid={os.getpid()} names={device_names} mode={mode} "
          f"r={rank} alpha={alpha}", flush=True)

    # Plain bf16 LoRA on the unquantized QAT weights, not QLoRA: the adapter is served on the
    # QAT w4a16 checkpoint, which is these same weights packed for vLLM. With more than one card,
    # Unsloth's own planner places consecutive layers on each card.
    load_kwargs = {"device_map": SPLIT_DEVICE_MAP} if len(gpus) > 1 else {}
    model, tokenizer = FastModel.from_pretrained(
        model_name=model_dir, max_seq_length=MAX_SEQ_LENGTH, load_in_4bit=False, load_in_16bit=True,
        dtype=torch.bfloat16, **load_kwargs)
    text_tokenizer = getattr(tokenizer, "tokenizer", tokenizer)
    text_tokenizer.chat_template = load_chat_template(data_root)
    if text_tokenizer.pad_token_id is None:
        text_tokenizer.pad_token = text_tokenizer.eos_token
    model = FastModel.get_peft_model(
        model, r=rank, lora_alpha=alpha, lora_dropout=0.05, bias="none",
        use_gradient_checkpointing="unsloth",
        target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)",
    )
    placed = sorted({str(device) for device in (getattr(model, "hf_device_map", None) or {}).values()})
    print(f"PLACEMENT r={rank} gpus={list(gpus)} devices_holding_weights={placed}", flush=True)
    if len(gpus) > 1 and len(placed) != len(gpus):
        raise RuntimeError(f"r={rank} was meant to be split over {len(gpus)} gpus but weights are on {placed}")

    rows = []
    rejected = 0
    thought_rows = leaks = 0
    source = Path(data_root) / mode / "train.jsonl"
    for line in source.read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            encoded = render_row(text_tokenizer, raw)
        except ValueError as error:
            rejected += 1
            print(f"skip r={rank} {error}", flush=True)
            continue
        if any((m.get("reasoning") or "").strip() for m in raw["messages"]):
            thought_rows += 1
            leaks += thought_leaks(text_tokenizer, raw, encoded)
        rows.append(encoded)
    if rejected:
        raise RuntimeError(f"r={rank} {rejected} rows failed to render; the audited data renders every row")
    if leaks:
        raise RuntimeError(f"r={rank} {leaks} thoughts carry loss; thought text must be context only")
    longest = max(len(row["input_ids"]) for row in rows)
    supervised = sum(sum(label != -100 for label in row["labels"]) for row in rows)
    print(f"DATA r={rank} rows={len(rows)} rows_with_thoughts={thought_rows} thought_leaks=0 "
          f"supervised_tokens={supervised} longest={longest} cap={MAX_SEQ_LENGTH}", flush=True)

    # Fail fast: one forward and backward on the longest row, so an out-of-memory error shows up
    # in the first minutes and not hours in.
    model.train()
    hardest = max(rows, key=lambda row: len(row["input_ids"]))
    batch = {key: torch.tensor([value]).to("cuda:0") for key, value in hardest.items()}
    model(**batch).loss.backward()
    model.zero_grad(set_to_none=True)
    peaks = [round(torch.cuda.max_memory_allocated(index) / 2**30, 1) for index in range(len(gpus))]
    print(f"PREFLIGHT_OK r={rank} longest_row_tokens={longest} peak_gib_per_gpu={peaks}", flush=True)
    torch.cuda.empty_cache()

    output_dir = Path(output_root) / f"{mode}-r{rank}"
    grad_accum = 4
    total_steps = math.ceil(len(rows) / grad_accum) * EPOCHS
    warmup = max(1, round(0.05 * total_steps))
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output_dir),
            num_train_epochs=EPOCHS,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=grad_accum,
            learning_rate=LEARNING_RATE,
            lr_scheduler_type="cosine",
            warmup_steps=warmup,
            logging_steps=1,
            save_strategy="epoch",
            save_only_model=True,
            bf16=True,
            optim="adamw_torch",
            report_to="none",
            remove_unused_columns=False,
            seed=3407,
        ),
        train_dataset=Dataset.from_list(rows),
        data_collator=DataCollatorForSeq2Seq(tokenizer=text_tokenizer, padding=True, label_pad_token_id=-100),
    )
    # A split model must stay one model: no DataParallel copy and one sample per step.
    if len(gpus) > 1 and not (getattr(trainer, "is_model_parallel", False) and trainer.args.n_gpu == 1):
        raise RuntimeError(f"Trainer did not treat the split model as model parallel "
                           f"(is_model_parallel={getattr(trainer, 'is_model_parallel', None)}, n_gpu={trainer.args.n_gpu})")
    print(f"TRAINER r={rank} model_parallel={getattr(trainer, 'is_model_parallel', None)} n_gpu={trainer.args.n_gpu} "
          f"steps={total_steps} warmup={warmup}", flush=True)
    result = trainer.train()
    adapter = output_dir / "final_adapter"
    model.save_pretrained(adapter, safe_serialization=True)
    text_tokenizer.save_pretrained(adapter)
    if not (adapter / "adapter_model.safetensors").exists():
        raise RuntimeError(f"no adapter_model.safetensors in {adapter}; the harness only loads safetensors")
    finished_at = time.time()
    status_path.write_text(json.dumps({
        **identity, "device_names": device_names, "finished_at": finished_at, "phase": "done",
        "rows": len(rows), "rejected": rejected, "longest_tokens": longest, "peak_gib_per_gpu": peaks,
        "devices_holding_weights": placed, "max_seq_length": MAX_SEQ_LENGTH,
        "train_loss": result.training_loss, "adapter": str(adapter),
    }) + "\n")
    print(f"DONE r={rank} loss={result.training_loss} seconds={finished_at - started_at:.1f}", flush=True)


def run_wave(wave: list[dict], data_root: str, model_dir: str, output_root: str) -> None:
    output = Path(output_root)
    processes = []
    for experiment in wave:
        processes.append(subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker",
             ",".join(str(gpu) for gpu in experiment["gpus"]), experiment["mode"],
             str(experiment["rank"]), str(experiment["alpha"]), data_root, model_dir, output_root],
        ))
    # The GPU groups must be claimed together, before any worker is allowed to finish alone.
    deadline = time.time() + 900
    while time.time() < deadline:
        ready = []
        for experiment in wave:
            path = output / f"r{experiment['rank']}.json"
            if path.exists():
                ready.append(json.loads(path.read_text()))
        if len(ready) == len(wave) and all(record.get("phase") in ("loaded", "done") for record in ready):
            print(f"PARALLEL_OK {len(wave)} workers claimed their GPU groups", flush=True)
            break
        if any(process.poll() not in (None, 0) for process in processes):
            break
        time.sleep(2)
    else:
        for process in processes:
            process.kill()
        raise RuntimeError("workers did not claim their gpus")

    codes = [process.wait() for process in processes]
    if any(code != 0 for code in codes):
        raise RuntimeError(f"a worker failed: {codes}")
    records = [json.loads((output / f"r{experiment['rank']}.json").read_text()) for experiment in wave]
    assert_parallel(records, wave)
    print(f"PARALLEL_VERIFIED wave of {len(wave)} finished: ranks {[r['lora_r'] for r in records]}", flush=True)


def launch(data_root: str, model_dir: str, output_root: str) -> None:
    Path(output_root).mkdir(parents=True, exist_ok=True)
    for number, wave in enumerate(waves(), 1):
        print(f"WAVE {number}: ranks {[e['rank'] for e in wave]} on gpus {[list(e['gpus']) for e in wave]}",
              flush=True)
        run_wave(wave, data_root, model_dir, output_root)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        _, _, gpus, mode, rank, alpha, data_root, model_dir, output_root = sys.argv
        worker(tuple(int(gpu) for gpu in gpus.split(",")), mode, int(rank), int(alpha), data_root, model_dir,
               output_root)
        return
    dataset = dataset_dir()
    install_bundled(dataset)
    model_dir = find_model_dir()
    data_root = dataset / "sft"
    load_chat_template(str(data_root))
    manifest = check_manifest(str(data_root))
    print("data_root", data_root, "model_dir", model_dir, "schema", manifest["schema_version"],
          "rows", manifest["examples"], "thought_steps", manifest["thoughts"]["steps"], flush=True)
    launch(str(data_root), model_dir, str(OUTPUT_ROOT))


if __name__ == "__main__":
    main()
