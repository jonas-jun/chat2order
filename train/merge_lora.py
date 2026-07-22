"""Merge a trained LoRA adapter into its base model for deployment."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from peft import PeftConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument(
        "--base-model",
        help="Override the adapter's base model with a local path or Hugging Face ID.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device-map", default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not (args.adapter / "adapter_config.json").is_file():
        raise FileNotFoundError(args.adapter / "adapter_config.json")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {args.output_dir}")

    adapter_config = PeftConfig.from_pretrained(args.adapter)
    manifest_path = args.adapter / "training_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model
        or manifest.get("base_model_hub_id")
        or adapter_config.base_model_name_or_path,
        revision=(
            manifest.get("base_model_revision")
            or getattr(adapter_config, "revision", None)
            or "main"
        ),
        dtype=torch.bfloat16,
        device_map=args.device_map,
        low_cpu_mem_usage=True,
    )
    model = PeftModel.from_pretrained(base_model, args.adapter)
    merged = model.merge_and_unload(safe_merge=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(args.output_dir, safe_serialization=True, max_shard_size="5GB")
    tokenizer = AutoTokenizer.from_pretrained(args.adapter)
    tokenizer.save_pretrained(args.output_dir)
    if manifest_path.is_file():
        shutil.copy2(manifest_path, args.output_dir / "training_manifest.json")
    print(f"Merged model saved to {args.output_dir}")


if __name__ == "__main__":
    main()
