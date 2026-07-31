"""Quantize a merged BF16 model and save a reloadable 4-bit checkpoint."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import time
from typing import Any

from train.config import write_json
from train.quantization import (
    QUANTIZATION_MANIFEST,
    SUPPORTED_BACKENDS,
    ensure_no_cpu_offload,
    inventory_files,
    quantized_inventory,
    validate_quantized_inventory,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=SUPPORTED_BACKENDS, required=True)
    parser.add_argument("--group-size", type=int, default=128)
    parser.add_argument("--device-map", default="cuda:0")
    parser.add_argument("--max-shard-size", default="5GB")
    parser.add_argument(
        "--skip-input-hashes",
        action="store_true",
        help="Skip hashing the large source checkpoint (not recommended for final runs).",
    )
    return parser.parse_args()


def package_versions(names: tuple[str, ...]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def build_quantization_config(backend: str, group_size: int) -> tuple[Any, dict[str, Any]]:
    import torch
    from transformers import BitsAndBytesConfig, TorchAoConfig

    if backend == "torchao-int4":
        from torchao.quantization import Int4WeightOnlyConfig

        if group_size not in {32, 64, 128, 256}:
            raise ValueError("TorchAO group size must be one of 32, 64, 128, or 256")
        return (
            TorchAoConfig(Int4WeightOnlyConfig(group_size=group_size)),
            {
                "method": "int4_weight_only",
                "group_size": group_size,
                "weight_bits": 4,
                "activation_dtype": "bfloat16",
                "compute_dtype": "bfloat16",
                "calibration": None,
            },
        )
    if backend == "bitsandbytes-nf4":
        if group_size != 128:
            raise ValueError(
                "--group-size only applies to TorchAO; use 128 for bitsandbytes"
            )
        return (
            BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_use_double_quant=True,
            ),
            {
                "method": "nf4",
                "group_size": None,
                "weight_bits": 4,
                "double_quantization": True,
                "activation_dtype": "bfloat16",
                "compute_dtype": "bfloat16",
                "calibration": None,
            },
        )
    raise ValueError(f"unsupported quantization backend: {backend}")


def _source_weight_files(model_path: Path) -> list[Path]:
    patterns = ("*.safetensors", "*.bin", "*.index.json")
    return [path for pattern in patterns for path in model_path.glob(pattern)]


def main() -> None:
    args = parse_args()
    if not args.model.is_dir():
        raise FileNotFoundError(args.model)
    if (args.model / "adapter_config.json").is_file():
        raise ValueError(
            "quantization input must be a merged model, not a LoRA adapter; "
            "run train.merge_lora first"
        )
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {args.output_dir}")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the H100 quantization run")

    quantization_config, settings = build_quantization_config(
        args.backend, args.group_size
    )
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        dtype=torch.bfloat16,
        device_map=args.device_map,
        low_cpu_mem_usage=True,
        quantization_config=quantization_config,
    )
    ensure_no_cpu_offload(model)
    source_inventory = quantized_inventory(model)
    validate_quantized_inventory(
        source_inventory, args.backend, context="freshly quantized model"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(
        args.output_dir,
        safe_serialization=True,
        max_shard_size=args.max_shard_size,
    )
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    tokenizer.save_pretrained(args.output_dir)
    for name in ("training_manifest.json", "chat_template.jinja"):
        source = args.model / name
        destination = args.output_dir / name
        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)

    input_files = (
        []
        if args.skip_input_hashes
        else inventory_files(_source_weight_files(args.model))
    )
    manifest = {
        "schema_version": 1,
        "backend": args.backend,
        "settings": settings,
        "source_model": str(args.model.resolve()),
        "source_files": input_files,
        "source_size_bytes": sum(path.stat().st_size for path in _source_weight_files(args.model)),
        "created_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "quantization_seconds": round(time.perf_counter() - started, 3),
        "peak_gpu_memory_bytes": torch.cuda.max_memory_allocated(),
        "device_map": args.device_map,
        "python_version": platform.python_version(),
        "packages": package_versions(
            (
                "torch",
                "transformers",
                "accelerate",
                "safetensors",
                "torchao",
                "mslk-cuda",
                "bitsandbytes",
            )
        ),
        "inventory": source_inventory,
    }
    write_json(args.output_dir / QUANTIZATION_MANIFEST, manifest)
    del model
    torch.cuda.empty_cache()

    reloaded = AutoModelForCausalLM.from_pretrained(
        args.output_dir,
        dtype=torch.bfloat16,
        device_map=args.device_map,
        low_cpu_mem_usage=True,
    )
    ensure_no_cpu_offload(reloaded)
    reload_inventory = quantized_inventory(reloaded)
    validate_quantized_inventory(
        reload_inventory, args.backend, context="reloaded checkpoint"
    )
    manifest["reload_validated"] = True
    manifest["reload_inventory"] = reload_inventory
    manifest["output_files"] = inventory_files(_source_weight_files(args.output_dir))
    manifest["output_size_bytes"] = sum(
        item["size_bytes"] for item in manifest["output_files"]
    )
    write_json(args.output_dir / QUANTIZATION_MANIFEST, manifest)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "backend": args.backend,
                "output_size_bytes": manifest["output_size_bytes"],
                "reload_validated": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
