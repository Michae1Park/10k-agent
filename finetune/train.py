"""V7: LoRA fine-tune of the agent model on passing trajectories (docs/FINETUNE.md).

    uv run --extra finetune python finetune/train.py [finetune/config.yaml] [--max-steps N]

Hugging Face Trainer + PEFT; the only custom parts are the two things the stock tools
can't do for this model and data:

- Masking. Each sample is rendered with the model's own chat template, exactly as vLLM
  renders a request; loss covers only what the model generated (assistant turns), not the
  prompt, the question or tool results. A conversation with a later user turn (the repair
  round) is cut into segments, so each is rendered as it was at inference time.
- Logits. Trajectories run to 40k tokens and the vocabulary is 248k, so full logits would
  take ~40 GB. The loss asks the model for logits at assistant positions only.
"""

import argparse
import json
import random
from pathlib import Path

import torch
import torch.nn.functional as F
import yaml
from peft import LoraConfig, get_peft_model
from transformers import (
    AutoModelForImageTextToText,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default="finetune/config.yaml", type=Path)
    parser.add_argument("--max-steps", type=int, default=-1, help="smoke test: stop early")
    parser.add_argument("--data", type=Path, help="override the config's data file")
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    out = Path(cfg["output_dir"])

    tokenizer = AutoTokenizer.from_pretrained(cfg["base_model"])
    records = [
        json.loads(line) for line in (args.data or Path(cfg["data"])).read_text().splitlines()
    ]
    samples, dropped = [], 0
    for record in records:
        for sample in encode(record, tokenizer, cfg["enable_thinking"]):
            if len(sample["input_ids"]) > cfg["max_length"]:
                dropped += 1
            else:
                samples.append(sample)
    if not samples:
        raise SystemExit("No training samples (all longer than max_length?)")
    random.Random(cfg["seed"]).shuffle(samples)
    n_eval = int(len(samples) * cfg["eval_fraction"])
    eval_set, train_set = samples[:n_eval], samples[n_eval:]
    targets = sum(sum(t != -100 for t in s["labels"]) for s in samples)
    print(
        f"{len(records)} trajectories -> {len(samples)} samples ({dropped} over max_length), "
        f"{len(train_set)} train / {len(eval_set)} eval, {targets:,} target tokens"
    )

    model = AutoModelForImageTextToText.from_pretrained(
        cfg["base_model"], dtype=torch.bfloat16, device_map={"": 0}
    )
    model.config.use_cache = False
    lora = cfg["lora"]
    model = get_peft_model(
        model,
        LoraConfig(
            r=lora["r"],
            lora_alpha=lora["alpha"],
            lora_dropout=lora["dropout"],
            target_modules=lora["target_modules"],
            task_type="CAUSAL_LM",
        ),
    )
    model.print_trainable_parameters()

    t = cfg["train"]
    trainer = AssistantLossTrainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=t["epochs"],
            max_steps=args.max_steps,
            per_device_train_batch_size=1,
            per_device_eval_batch_size=1,
            gradient_accumulation_steps=t["gradient_accumulation_steps"],
            learning_rate=t["learning_rate"],
            lr_scheduler_type=t["lr_scheduler"],
            warmup_steps=t["warmup_ratio"],  # a float below 1 is a ratio of total steps
            weight_decay=t["weight_decay"],
            max_grad_norm=t["max_grad_norm"],
            bf16=True,
            gradient_checkpointing=t["gradient_checkpointing"],
            gradient_checkpointing_kwargs={"use_reentrant": False},
            logging_steps=t["logging_steps"],
            eval_strategy="steps" if eval_set else "no",
            eval_steps=t["eval_steps"],
            save_steps=t["save_steps"],
            save_total_limit=t["save_total_limit"],
            report_to="none",
            remove_unused_columns=False,
            # Eval goes through compute_loss too, and keeps no logits.
            label_names=["labels"],
            prediction_loss_only=True,
            seed=cfg["seed"],
        ),
        train_dataset=train_set,
        eval_dataset=eval_set or None,
        data_collator=collate,
    )
    trainer.train()
    model.save_pretrained(out / "adapter")
    tokenizer.save_pretrained(out / "adapter")
    (out / "adapter" / "train_config.yaml").write_text(yaml.safe_dump(cfg))
    print(f"Adapter: {out / 'adapter'}")


def encode(record: dict, tokenizer, enable_thinking: bool) -> list[dict]:
    """One sample per segment (messages up to the next user turn), labels on assistant turns."""
    messages, tools = record["messages"], record.get("tools")

    def render(msgs, generation_prompt=False) -> str:
        return tokenizer.apply_chat_template(
            msgs,
            tools=tools,
            tokenize=False,
            add_generation_prompt=generation_prompt,
            enable_thinking=enable_thinking,
        )

    users = [i for i, m in enumerate(messages) if m["role"] == "user"]
    samples = []
    for n, start in enumerate(users):
        end = users[n + 1] if n + 1 < len(users) else len(messages)
        turns = [j for j in range(start + 1, end) if messages[j]["role"] == "assistant"]
        if not turns:
            continue
        text = render(messages[: turns[-1] + 1])
        spans = []
        for j in turns:
            prompt, upto = render(messages[:j], generation_prompt=True), render(messages[: j + 1])
            if not (upto.startswith(prompt) and text.startswith(upto)):
                raise ValueError(f"{record['meta']['id']}: chat template isn't prefix-stable")
            spans.append((len(prompt), len(upto)))
        encoded = tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        labels = [
            token if any(a <= begin < b for a, b in spans) else -100
            for token, (begin, _) in zip(
                encoded["input_ids"], encoded["offset_mapping"], strict=True
            )
        ]
        samples.append({"input_ids": encoded["input_ids"], "labels": labels})
    return samples


def collate(batch: list[dict]) -> dict:
    (sample,) = batch  # batch size 1: trajectories vary from 5k to 40k tokens
    # No attention mask: with one unpadded sequence it says nothing, and passing one can
    # materialize a 40k x 40k mask and push attention off the memory-efficient kernel.
    return {
        "input_ids": torch.tensor([sample["input_ids"]]),
        "labels": torch.tensor([sample["labels"]]),
    }


class AssistantLossTrainer(Trainer):
    """Cross-entropy on assistant tokens, computing logits only at those positions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The loss below is a per-sample mean, so the Trainer must divide it by the
        # accumulation steps itself (it doesn't when it thinks the model takes loss kwargs).
        self.model_accepts_loss_kwargs = False

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        labels = inputs.pop("labels")
        # Position t predicts token t + 1.
        positions = (labels[0, 1:] != -100).nonzero().squeeze(-1)
        outputs = model(**inputs, logits_to_keep=positions)
        loss = F.cross_entropy(outputs.logits[0].float(), labels[0, positions + 1])
        return (loss, outputs) if return_outputs else loss


if __name__ == "__main__":
    main()
