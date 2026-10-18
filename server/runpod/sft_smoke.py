"""One-step LoRA smoke test for Gemma 4 E4B.

Loads unsloth/gemma-4-E4B-it in 4-bit, attaches a rank-64 LoRA, and trains
one step on the first assistant turn of data/transient/sft_example_requests_7427.json.
Run this on the A100 pod after Unsloth is installed. It does not use the QAT
w4a16 checkpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

from unsloth import FastModel
from unsloth.chat_templates import get_chat_template
from trl import SFTConfig, SFTTrainer

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "data/transient/sft_example_requests_7427.json"
OUTPUT = Path("/workspace/checkpoints/e4b-lora-r64-smoke")

MODEL_ID = "unsloth/gemma-4-E4B-it"
RANK = 64
MAX_LENGTH = 2048


def first_turn(path: Path) -> dict:
    row = json.loads(path.read_text())
    messages = [row["messages"][0], row["messages"][1], row["messages"][2]]
    return {"messages": messages, "tools": row["tools"]}


def main() -> None:
    model, tokenizer = FastModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_LENGTH,
        load_in_4bit=True,
        full_finetuning=False,
    )
    model = FastModel.get_peft_model(
        model,
        finetune_vision_layers=False,
        finetune_language_layers=True,
        finetune_attention_modules=True,
        finetune_mlp_modules=True,
        r=RANK,
        lora_alpha=RANK,
        lora_dropout=0,
        bias="none",
        random_state=3407,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    sample = first_turn(EXAMPLE)
    text = tokenizer.apply_chat_template(
        sample["messages"],
        tools=sample["tools"],
        tokenize=False,
        add_generation_prompt=False,
        enable_thinking=False,
    )
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=[{"text": text}],
        args=SFTConfig(
            output_dir=str(OUTPUT),
            max_steps=1,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=1,
            learning_rate=2e-4,
            logging_steps=1,
            max_length=MAX_LENGTH,
            dataset_text_field="text",
            report_to="none",
            seed=3407,
        ),
    )
    result = trainer.train()
    trainer.save_model(str(OUTPUT))
    tokenizer.save_pretrained(str(OUTPUT))
    print(f"SMOKE_OK loss={result.training_loss} saved={OUTPUT}")


if __name__ == "__main__":
    main()
