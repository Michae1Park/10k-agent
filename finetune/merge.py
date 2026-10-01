"""Merge the V7 LoRA adapter into the base model, for vLLM (docs/FINETUNE.md).

    uv run --extra finetune python finetune/merge.py [finetune/config.yaml]

Writes <output_dir>/merged: full BF16 weights (vision tower included, so vLLM loads the
same architecture as the base) plus the base model's tokenizer, chat template and processor
files, copied unchanged so vLLM renders prompts exactly as for the base. Runs on the CPU:
the merge needs ~20 GB of RAM, no GPU.
"""

import argparse
import shutil
from pathlib import Path

import torch
import yaml
from huggingface_hub import snapshot_download
from peft import PeftModel
from transformers import AutoModelForImageTextToText

# Written by save_pretrained for the merged weights; everything else comes from the base.
OWN_FILES = {"config.json", "generation_config.json", "model.safetensors.index.json"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default="finetune/config.yaml", type=Path)
    args = parser.parse_args()
    cfg = yaml.safe_load(args.config.read_text())
    out = Path(cfg["output_dir"])

    model = AutoModelForImageTextToText.from_pretrained(cfg["base_model"], dtype=torch.bfloat16)
    model = PeftModel.from_pretrained(model, out / "adapter").merge_and_unload()
    model.save_pretrained(out / "merged", safe_serialization=True)
    # Tokenizer, chat template and processor files only (the snapshot also holds weights).
    base = Path(snapshot_download(cfg["base_model"], allow_patterns=["*.json", "*.jinja", "*.txt"]))
    for path in base.iterdir():
        if path.suffix in {".json", ".jinja", ".txt"} and path.name not in OWN_FILES:
            shutil.copy(path, out / "merged" / path.name)
    print(f"Merged model: {out / 'merged'}")


if __name__ == "__main__":
    main()
