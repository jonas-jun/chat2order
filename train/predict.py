"""Run deterministic JSON extraction with a LoRA adapter or merged model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from train.data import build_user_content, iter_jsonl
from train.quantization import (
    ensure_no_cpu_offload,
    quantized_inventory,
    read_quantization_manifest,
    validate_quantized_inventory,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument(
        "--base-model",
        help="Override the adapter's base model with a local path or Hugging Face ID.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-input-length", type=int, default=4096)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--chat-template-kwargs",
        help="JSON object passed to apply_chat_template; defaults to the "
        "data.chat_template_kwargs recorded in training_manifest.json.",
    )
    return parser.parse_args()


def resolve_chat_template_kwargs(
    model_path: Path, override: str | None
) -> dict[str, Any]:
    """Prompts must be built exactly as during training (e.g. Qwen thinking off)."""

    if override is not None:
        kwargs = json.loads(override)
        if not isinstance(kwargs, dict):
            raise ValueError("--chat-template-kwargs must be a JSON object")
        return kwargs
    manifest_path = model_path / "training_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        kwargs = manifest.get("config", {}).get("data", {}).get("chat_template_kwargs")
        if isinstance(kwargs, dict):
            return kwargs
    return {}


def _portable_base_model(model_path: Path) -> tuple[str | None, str | None]:
    manifest_path = model_path / "training_manifest.json"
    if not manifest_path.is_file():
        return None, None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest.get("base_model_hub_id"), manifest.get("base_model_revision")


def load_model(model_path: Path, base_model: str | None = None) -> tuple[Any, Any]:
    adapter_path = model_path / "adapter_config.json"
    quantization_manifest = read_quantization_manifest(model_path)
    if adapter_path.is_file() and quantization_manifest is not None:
        raise ValueError("a model directory cannot be both a LoRA adapter and quantized")
    if adapter_path.is_file():
        from peft import PeftConfig, PeftModel

        adapter_config = PeftConfig.from_pretrained(model_path)
        manifest_base, manifest_revision = _portable_base_model(model_path)
        base = AutoModelForCausalLM.from_pretrained(
            base_model or manifest_base or adapter_config.base_model_name_or_path,
            revision=(
                manifest_revision
                or getattr(adapter_config, "revision", None)
                or "main"
            ),
            dtype=torch.bfloat16,
            device_map="auto",
            low_cpu_mem_usage=True,
        )
        model = PeftModel.from_pretrained(base, model_path)
    elif quantization_manifest is not None:
        if base_model is not None:
            raise ValueError("--base-model cannot be used with a quantized checkpoint")
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            dtype=torch.bfloat16,
            device_map="auto",
            low_cpu_mem_usage=True,
        )
        ensure_no_cpu_offload(model)
        inventory = quantized_inventory(model)
        validate_quantized_inventory(
            inventory,
            str(quantization_manifest["backend"]),
            context=str(model_path),
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            dtype=torch.bfloat16,
            device_map="auto",
            low_cpu_mem_usage=True,
        )
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model.eval()
    return model, tokenizer


def parse_json_output(text: str) -> tuple[dict[str, Any] | None, str | None]:
    candidate = text.strip()
    if candidate.startswith("```json"):
        candidate = candidate[7:]
    elif candidate.startswith("```"):
        candidate = candidate[3:]
    if candidate.endswith("```"):
        candidate = candidate[:-3]
    try:
        parsed = json.loads(candidate.strip())
    except json.JSONDecodeError as exc:
        return None, f"{exc.msg} at position {exc.pos}"
    if not isinstance(parsed, dict):
        return None, "prediction is not a JSON object"
    return parsed, None


@torch.inference_mode()
def predict_one(
    messages: list[dict[str, str]],
    model: Any,
    tokenizer: Any,
    *,
    max_input_length: int,
    max_new_tokens: int,
    chat_template_kwargs: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, str | None]:
    prompt = [{"role": "user", "content": build_user_content(messages)}]
    input_ids = tokenizer.apply_chat_template(
        prompt,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=False,
        **chat_template_kwargs,
    )
    if input_ids.shape[-1] > max_input_length:
        raise ValueError(
            f"input has {input_ids.shape[-1]} tokens, exceeding {max_input_length}"
        )
    device = next(model.parameters()).device
    input_ids = input_ids.to(device)
    # eos_token_id is deliberately not overridden: models like Gemma stop on
    # <end_of_turn>, which is only in generation_config, not tokenizer.eos_token.
    generated = model.generate(
        input_ids=input_ids,
        attention_mask=torch.ones_like(input_ids),
        max_new_tokens=max_new_tokens,
        do_sample=False,
        use_cache=True,
        pad_token_id=(
            tokenizer.pad_token_id
            if tokenizer.pad_token_id is not None
            else tokenizer.eos_token_id
        ),
    )
    text = tokenizer.decode(generated[0, input_ids.shape[-1] :], skip_special_tokens=True)
    prediction, error = parse_json_output(text)
    return prediction, text, error


def main() -> None:
    args = parse_args()
    chat_template_kwargs = resolve_chat_template_kwargs(
        args.model, args.chat_template_kwargs
    )
    model, tokenizer = load_model(args.model, args.base_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open(encoding="utf-8") as source, args.output.open(
        "w", encoding="utf-8"
    ) as destination:
        for index, example in enumerate(iter_jsonl(source)):
            if args.limit is not None and index >= args.limit:
                break
            messages = example.get("messages")
            if not isinstance(messages, list):
                raise ValueError(f"row {index}: messages must be a list")
            try:
                prediction, raw_prediction, error = predict_one(
                    messages,
                    model,
                    tokenizer,
                    max_input_length=args.max_input_length,
                    max_new_tokens=args.max_new_tokens,
                    chat_template_kwargs=chat_template_kwargs,
                )
            except ValueError as exc:
                prediction, raw_prediction, error = None, "", str(exc)
            destination.write(
                json.dumps(
                    {
                        "row": index,
                        "prediction": prediction,
                        "parse_error": error,
                        "raw_prediction": raw_prediction,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(f"Predictions saved to {args.output}")


if __name__ == "__main__":
    main()
