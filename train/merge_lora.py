"""Merge a trained LoRA adapter into its base model for deployment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
import time

import torch
from peft import PeftConfig, PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from train.config import write_json
from train.quantization import inventory_files, parameter_dtype_inventory


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

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
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
    parameter_inventory = parameter_dtype_inventory(merged)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(args.output_dir, safe_serialization=True, max_shard_size="5GB")
    tokenizer = AutoTokenizer.from_pretrained(args.adapter)
    tokenizer.save_pretrained(args.output_dir)
    if manifest_path.is_file():
        shutil.copy2(manifest_path, args.output_dir / "training_manifest.json")
    output_weight_paths = [
        path
        for pattern in ("*.safetensors", "*.bin", "*.index.json")
        for path in args.output_dir.glob(pattern)
    ]
    adapter_weight_paths = list(args.adapter.glob("*.safetensors"))
    write_json(
        args.output_dir / "merge_manifest.json",
        {
            "schema_version": 1,
            "adapter": str(args.adapter.resolve()),
            "adapter_files": inventory_files(adapter_weight_paths),
            "base_model": (
                args.base_model
                or manifest.get("base_model_hub_id")
                or adapter_config.base_model_name_or_path
            ),
            "base_revision": (
                manifest.get("base_model_revision")
                or getattr(adapter_config, "revision", None)
                or "main"
            ),
            "device_map": args.device_map,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "merge_seconds": round(time.perf_counter() - started, 3),
            "peak_gpu_memory_bytes": (
                torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None
            ),
            "parameter_inventory": parameter_inventory,
            "output_files": inventory_files(output_weight_paths),
            "output_size_bytes": sum(
                path.stat().st_size for path in output_weight_paths
            ),
        },
    )
    print(f"Merged model saved to {args.output_dir}")


if __name__ == "__main__":
    main()
