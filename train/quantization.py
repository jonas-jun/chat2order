"""Pure helpers shared by quantization, loading, and tests."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable


QUANTIZATION_MANIFEST = "quantization_manifest.json"
SUPPORTED_BACKENDS = ("torchao-int4", "bitsandbytes-nf4")


def read_quantization_manifest(model_path: Path) -> dict[str, Any] | None:
    path = model_path / QUANTIZATION_MANIFEST
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    backend = value.get("backend")
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported quantization backend in {path}: {backend!r}")
    return value


def quantized_inventory(model: Any) -> dict[str, Any]:
    """Return backend-agnostic evidence that weights are actually quantized."""

    module_types: Counter[str] = Counter()
    quantized_modules: list[str] = []
    for name, module in model.named_modules():
        type_name = type(module).__name__
        module_types[type_name] += 1
        lowered = type_name.lower()
        if "linear4bit" in lowered or "weightonlyint4" in lowered:
            quantized_modules.append(name)

    parameter_types: Counter[str] = Counter()
    quantized_parameters: list[str] = []
    for name, parameter in model.named_parameters():
        type_name = type(parameter).__name__
        parameter_types[type_name] += 1
        text = f"{type_name} {getattr(parameter, 'dtype', '')}".lower()
        if any(token in text for token in ("params4bit", "affinequantized", "uint4", "int4")):
            quantized_parameters.append(name)

    config = getattr(model, "config", None)
    quantization_config = getattr(config, "quantization_config", None)
    if hasattr(quantization_config, "to_dict"):
        quantization_config = quantization_config.to_dict()
    elif quantization_config is not None and not isinstance(quantization_config, dict):
        quantization_config = str(quantization_config)

    return {
        "quantized_module_count": len(quantized_modules),
        "quantized_parameter_count": len(quantized_parameters),
        "quantized_modules": quantized_modules,
        "quantized_parameters": quantized_parameters,
        "module_type_counts": dict(sorted(module_types.items())),
        "parameter_type_counts": dict(sorted(parameter_types.items())),
        "config_quantization": quantization_config,
    }


def validate_quantized_inventory(
    inventory: dict[str, Any], backend: str, *, context: str = "model"
) -> None:
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"unsupported quantization backend: {backend}")
    module_count = int(inventory.get("quantized_module_count", 0))
    parameter_count = int(inventory.get("quantized_parameter_count", 0))
    config = inventory.get("config_quantization")
    if module_count <= 0 and parameter_count <= 0:
        raise RuntimeError(
            f"{context} declares {backend}, but no 4-bit module or parameter was found"
        )
    if config is None:
        raise RuntimeError(
            f"{context} contains 4-bit weights, but config.quantization_config is missing"
        )


def ensure_no_cpu_offload(model: Any) -> None:
    device_map = getattr(model, "hf_device_map", None)
    if not isinstance(device_map, dict):
        return
    offloaded = {
        name: str(device)
        for name, device in device_map.items()
        if str(device).lower() == "cpu" or str(device).lower().startswith("disk")
    }
    if offloaded:
        preview = list(offloaded.items())[:10]
        raise RuntimeError(f"CPU/disk offload is not allowed: {preview}")


def parameter_dtype_inventory(model: Any) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    trainable: Counter[str] = Counter()
    for parameter in model.parameters():
        dtype = str(getattr(parameter, "dtype", "unknown"))
        count = int(parameter.numel())
        totals[dtype] += count
        if bool(getattr(parameter, "requires_grad", False)):
            trainable[dtype] += count
    return {
        "total_parameters": sum(totals.values()),
        "trainable_parameters": sum(trainable.values()),
        "parameters_by_dtype": dict(sorted(totals.items())),
        "trainable_parameters_by_dtype": dict(sorted(trainable.items())),
    }


def inventory_files(paths: Iterable[Path]) -> list[dict[str, Any]]:
    from train.config import file_sha256

    records = []
    for path in sorted(paths):
        if path.is_file():
            records.append(
                {
                    "name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": file_sha256(path),
                }
            )
    return records
