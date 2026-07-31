from pathlib import Path

import pytest

from train.quantization import (
    ensure_no_cpu_offload,
    parameter_dtype_inventory,
    quantized_inventory,
    read_quantization_manifest,
    validate_quantized_inventory,
)


class Config:
    quantization_config = {"quant_method": "bitsandbytes", "load_in_4bit": True}


class Linear4bit:
    pass


class Params4bit:
    dtype = "uint8"


class FakeQuantizedModel:
    config = Config()

    def named_modules(self):
        return [("", self), ("model.layer", Linear4bit())]

    def named_parameters(self):
        return [("model.layer.weight", Params4bit())]

    def parameters(self):
        return [FakeParameter(10, "torch.bfloat16"), FakeParameter(2, "torch.float32", True)]


class FakeParameter:
    def __init__(self, count, dtype, requires_grad=False):
        self.count = count
        self.dtype = dtype
        self.requires_grad = requires_grad

    def numel(self):
        return self.count


def test_quantized_inventory_finds_4bit_evidence() -> None:
    inventory = quantized_inventory(FakeQuantizedModel())

    assert inventory["quantized_module_count"] == 1
    assert inventory["quantized_parameter_count"] == 1
    validate_quantized_inventory(inventory, "bitsandbytes-nf4")


def test_quantized_inventory_rejects_silent_bf16_fallback() -> None:
    inventory = {
        "quantized_module_count": 0,
        "quantized_parameter_count": 0,
        "config_quantization": {"load_in_4bit": True},
    }

    with pytest.raises(RuntimeError, match="no 4-bit"):
        validate_quantized_inventory(inventory, "bitsandbytes-nf4")


def test_quantization_manifest_rejects_unknown_backend(tmp_path: Path) -> None:
    (tmp_path / "quantization_manifest.json").write_text(
        '{"backend": "unknown"}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unsupported quantization backend"):
        read_quantization_manifest(tmp_path)


def test_cpu_offload_is_rejected() -> None:
    model = FakeQuantizedModel()
    model.hf_device_map = {"model.layer": "cuda:0", "lm_head": "cpu"}

    with pytest.raises(RuntimeError, match="CPU/disk offload"):
        ensure_no_cpu_offload(model)


def test_parameter_dtype_inventory_counts_parameters() -> None:
    inventory = parameter_dtype_inventory(FakeQuantizedModel())

    assert inventory == {
        "total_parameters": 12,
        "trainable_parameters": 2,
        "parameters_by_dtype": {"torch.bfloat16": 10, "torch.float32": 2},
        "trainable_parameters_by_dtype": {"torch.float32": 2},
    }
