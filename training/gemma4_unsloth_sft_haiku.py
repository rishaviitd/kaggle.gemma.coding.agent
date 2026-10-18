"""QLoRA SFT for google/gemma-4-E4B-it using Unsloth, on the pre-tokenized coding-agent export.

Single L4. Consumes train.jsonl rows (input_ids / attention_mask / labels) produced by
scripts/sft_export/export_sft.py; labels are NOT regenerated here.

Parameters (unchanged from the DDP/single-GPU trainers so the runs are comparable):
  1 epoch, lr 1e-4 cosine, warmup 5%, global batch 4 (per-device 1 x grad accum 4), AdamW, bf16,
  LoRA r=16 alpha=32 dropout=0.05 on language-model q/k/v/o/gate/up/down projections,
  4-bit NF4 base model.
"""
import glob
import hashlib
import json
import os
import subprocess
import sys


def install_bundled():
    # Only the datasets staged for this run. The competition's own wheels folder is
    # excluded: it contains cp312 wheels that this cp313 image cannot install.
    wheels = sorted(glob.glob("/kaggle/input/datasets/akshayggupta1/**/*.whl", recursive=True))
    assert wheels, "no staged wheels found"
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall", *wheels])
    print("installed", len(wheels), "bundled wheels", flush=True)


install_bundled()

# Unsloth must be imported before transformers/peft so its memory patches apply.
from unsloth import FastModel  # noqa: E402

import torch  # noqa: E402
import xformers.ops  # noqa: E402,F401  (cp313 xformers build, verified in the one-row test)
from datasets import load_dataset  # noqa: E402
from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments  # noqa: E402

INPUT_ROOT = "/kaggle/input"
OUTPUT_DIR = "/kaggle/working/gemma4-e4b-it-unsloth-qlora"
EXPECTED_TEMPLATE_SHA256 = "0a2c8073c878ab1da004bee933a998606537bbb62016310352c7285c3f01c5b5"
MAX_LENGTH = 32768

EPOCHS = int(os.getenv("EPOCHS", "1"))
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "1e-4"))
PER_DEVICE_BATCH = int(os.getenv("PER_DEVICE_BATCH", "1"))
GRAD_ACCUM = int(os.getenv("GRAD_ACCUM", "4"))  # global batch = 1 x 4 = 4
LORA_R = 16
LORA_ALPHA = 32
LORA_DROPOUT = 0.05


def find_model_dir():
    hits = [p for p in glob.glob(f"{INPUT_ROOT}/**/config.json", recursive=True)
            if "gemma-4-e4b-it" in p and "qat" not in p.lower()]
    if not hits:
        raise SystemExit("gemma-4-e4b-it model not mounted under /kaggle/input")
    return os.path.dirname(sorted(hits, key=len)[0])


def find_data_dir():
    hits = [os.path.dirname(p) for p in glob.glob(f"{INPUT_ROOT}/**/train.jsonl", recursive=True)
            if "unsloth-wheels-haiku-tokenized" in p]
    if not hits:
        raise SystemExit("train.jsonl not mounted under /kaggle/input")
    return hits[0]



def main():
    assert torch.cuda.is_available() and torch.cuda.is_bf16_supported(), "L4 with bf16 required"
    model_dir = find_model_dir()
    data_dir = find_data_dir()
    print("model_dir", model_dir, "data_dir", data_dir, flush=True)

    template_text = open(os.path.join(data_dir, "chat_template.jinja"), encoding="utf-8").read()
    digest = hashlib.sha256(template_text.encode("utf-8")).hexdigest()
    print("chat_template_sha256", digest, flush=True)
    if digest != EXPECTED_TEMPLATE_SHA256:
        raise SystemExit("Pinned chat template does not match the SFT export manifest")

    dataset = load_dataset("json", data_files=os.path.join(data_dir, "train.jsonl"), split="train")
    for row in dataset:
        assert len(row["input_ids"]) == len(row["labels"]) == len(row["attention_mask"])
        assert len(row["input_ids"]) <= MAX_LENGTH
        assert any(x != -100 for x in row["labels"]), "row has no supervised tokens"
    lengths = sorted(len(r["input_ids"]) for r in dataset)
    print("examples", len(dataset), "max_tokens", lengths[-1], "median_tokens", lengths[len(lengths) // 2],
          "supervised_tokens", sum(sum(x != -100 for x in r["labels"]) for r in dataset), flush=True)

    model, tokenizer = FastModel.from_pretrained(
        model_name=model_dir,
        max_seq_length=lengths[-1],
        load_in_4bit=True,
        dtype=torch.bfloat16,
    )
    # Gemma 4 loads as a processor (multimodal wrapper). Padding and the chat template live on its
    # inner text tokenizer, so the collator must receive that object, not the processor.
    text_tokenizer = getattr(tokenizer, "tokenizer", tokenizer)
    text_tokenizer.chat_template = template_text
    if text_tokenizer.pad_token_id is None:
        text_tokenizer.pad_token = text_tokenizer.eos_token

    model = FastModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",
        # Language-model projections only, same as the other trainers.
        target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)",
    )
    model.print_trainable_parameters()


    # Fail fast on the longest row before committing to the full run.
    longest = max(dataset, key=lambda r: len(r["input_ids"]))
    model.train()  # gradient checkpointing only runs in train mode
    torch.cuda.reset_peak_memory_stats()
    out = model(
        input_ids=torch.tensor([longest["input_ids"]], device="cuda"),
        attention_mask=torch.tensor([longest["attention_mask"]], device="cuda"),
        labels=torch.tensor([longest["labels"]], device="cuda"),
    )
    out.loss.backward()
    model.zero_grad(set_to_none=True)
    print("longest_row_tokens", len(longest["input_ids"]), "one-step loss", float(out.loss),
          "peak GiB", round(torch.cuda.max_memory_allocated() / 2**30, 2), flush=True)
    torch.cuda.empty_cache()

    args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=PER_DEVICE_BATCH,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LEARNING_RATE,
        lr_scheduler_type="cosine",
        # transformers 5.x removed warmup_ratio; a float in [0, 1) here is a ratio of total steps.
        warmup_steps=0.05,
        logging_steps=1,
        save_strategy="epoch",
        save_total_limit=2,
        bf16=True,
        optim="adamw_torch",
        report_to="none",
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=dataset,
        data_collator=DataCollatorForSeq2Seq(tokenizer=text_tokenizer, padding=True, label_pad_token_id=-100),
    )
    result = trainer.train()
    print("train_metrics", json.dumps(result.metrics, default=str), flush=True)

    adapter_dir = os.path.join(OUTPUT_DIR, "final_adapter")
    model.save_pretrained(adapter_dir)
    text_tokenizer.save_pretrained(adapter_dir)
    print("saved_adapter", adapter_dir, os.listdir(adapter_dir), flush=True)


if __name__ == "__main__":
    main()
