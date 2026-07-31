"""Benchmark local generation latency, token throughput, and GPU memory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Iterable

from train.config import write_json
from train.data import build_user_content, iter_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--base-model")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-input-length", type=int, default=4096)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    return parser.parse_args()


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def distribution(values: Iterable[float]) -> dict[str, float | None]:
    data = [float(value) for value in values]
    if not data:
        return {"median": None, "p90": None, "p95": None, "p99": None}
    return {
        "median": statistics.median(data),
        "p90": percentile(data, 0.90),
        "p95": percentile(data, 0.95),
        "p99": percentile(data, 0.99),
    }


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record.get("error") is None]
    total_generation_ms = sum(
        float(record["generation_ms"]) for record in successful
    )
    output_tokens = sum(int(record["output_tokens"]) for record in successful)
    return {
        "requests": len(records),
        "successful_requests": len(successful),
        "error_rate": (
            (len(records) - len(successful)) / len(records) if records else 0.0
        ),
        "input_tokens": sum(int(record["input_tokens"]) for record in successful),
        "output_tokens": output_tokens,
        "tokenization_ms": distribution(
            record["tokenization_ms"] for record in successful
        ),
        "ttft_ms": distribution(
            record["ttft_ms"]
            for record in successful
            if record.get("ttft_ms") is not None
        ),
        "inter_token_ms": distribution(
            value
            for record in successful
            for value in record.get("inter_token_ms", [])
        ),
        "generation_ms": distribution(
            record["generation_ms"] for record in successful
        ),
        "end_to_end_ms": distribution(
            record["end_to_end_ms"] for record in successful
        ),
        "output_tokens_per_second": (
            output_tokens / (total_generation_ms / 1000)
            if total_generation_ms > 0
            else None
        ),
    }


def _example_id(messages: Any) -> str:
    canonical = json.dumps(
        messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _prepare_input(
    messages: list[dict[str, str]],
    tokenizer: Any,
    chat_template_kwargs: dict[str, Any],
    max_input_length: int,
    device: Any,
) -> tuple[Any, float]:
    prompt = [{"role": "user", "content": build_user_content(messages)}]
    started = time.perf_counter()
    input_ids = tokenizer.apply_chat_template(
        prompt,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=False,
        **chat_template_kwargs,
    )
    tokenization_ms = (time.perf_counter() - started) * 1000
    if input_ids.shape[-1] > max_input_length:
        raise ValueError(
            f"input has {input_ids.shape[-1]} tokens, exceeding {max_input_length}"
        )
    return input_ids.to(device), tokenization_ms


def _generation_kwargs(tokenizer: Any, max_new_tokens: int) -> dict[str, Any]:
    return {
        "max_new_tokens": max_new_tokens,
        "do_sample": False,
        "use_cache": True,
        "pad_token_id": (
            tokenizer.pad_token_id
            if tokenizer.pad_token_id is not None
            else tokenizer.eos_token_id
        ),
    }


def _warm_up(
    examples: list[dict[str, Any]],
    model: Any,
    tokenizer: Any,
    chat_template_kwargs: dict[str, Any],
    *,
    count: int,
    max_input_length: int,
    max_new_tokens: int,
) -> None:
    import torch

    if count <= 0:
        return
    device = next(model.parameters()).device
    with torch.inference_mode():
        for index in range(count):
            messages = examples[index % len(examples)]["messages"]
            input_ids, _ = _prepare_input(
                messages,
                tokenizer,
                chat_template_kwargs,
                max_input_length,
                device,
            )
            model.generate(
                input_ids=input_ids,
                attention_mask=torch.ones_like(input_ids),
                **_generation_kwargs(tokenizer, min(max_new_tokens, 32)),
            )
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _measure_one(
    messages: list[dict[str, str]],
    model: Any,
    tokenizer: Any,
    chat_template_kwargs: dict[str, Any],
    *,
    max_input_length: int,
    max_new_tokens: int,
) -> dict[str, Any]:
    import torch
    from transformers.generation.streamers import BaseStreamer

    device = next(model.parameters()).device
    request_started = time.perf_counter()
    input_ids, tokenization_ms = _prepare_input(
        messages,
        tokenizer,
        chat_template_kwargs,
        max_input_length,
        device,
    )

    use_cuda_events = torch.cuda.is_available() and str(device).startswith("cuda")

    class TokenTimingStreamer(BaseStreamer):
        def __init__(self) -> None:
            self.seen_prompt = False
            self.events: list[Any] = []
            self.host_times: list[float] = []

        def put(self, value: Any) -> None:
            if not self.seen_prompt:
                self.seen_prompt = True
                return
            count = int(value.shape[-1]) if hasattr(value, "shape") else 1
            for _ in range(count):
                if use_cuda_events:
                    event = torch.cuda.Event(enable_timing=True)
                    event.record()
                    self.events.append(event)
                else:
                    self.host_times.append(time.perf_counter())

        def end(self) -> None:
            return None

    streamer = TokenTimingStreamer()
    if use_cuda_events:
        torch.cuda.synchronize()
        start_event = torch.cuda.Event(enable_timing=True)
        end_event = torch.cuda.Event(enable_timing=True)
        start_event.record()
    generation_started = time.perf_counter()
    with torch.inference_mode():
        generated = model.generate(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            streamer=streamer,
            **_generation_kwargs(tokenizer, max_new_tokens),
        )
    if use_cuda_events:
        end_event.record()
        torch.cuda.synchronize()
        generation_ms = float(start_event.elapsed_time(end_event))
        token_offsets = [
            float(start_event.elapsed_time(event)) for event in streamer.events
        ]
    else:
        generation_ms = (time.perf_counter() - generation_started) * 1000
        token_offsets = [
            (timestamp - generation_started) * 1000
            for timestamp in streamer.host_times
        ]

    output_ids = generated[0, input_ids.shape[-1] :]
    text = tokenizer.decode(output_ids, skip_special_tokens=True)
    end_to_end_ms = (time.perf_counter() - request_started) * 1000
    return {
        "input_tokens": int(input_ids.shape[-1]),
        "output_tokens": int(output_ids.shape[-1]),
        "tokenization_ms": round(tokenization_ms, 3),
        "ttft_ms": round(token_offsets[0], 3) if token_offsets else None,
        "inter_token_ms": [
            round(current - previous, 3)
            for previous, current in zip(token_offsets, token_offsets[1:])
        ],
        "generation_ms": round(generation_ms, 3),
        "end_to_end_ms": round(end_to_end_ms, 3),
        "raw_prediction_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "error": None,
    }


def main() -> None:
    args = parse_args()

    import torch
    from train.predict import load_model, resolve_chat_template_kwargs

    if args.warmup < 0 or args.repeats <= 0:
        raise ValueError("--warmup must be non-negative and --repeats must be positive")
    with args.input.open(encoding="utf-8") as handle:
        examples = list(iter_jsonl(handle))
    if args.limit is not None:
        examples = examples[: args.limit]
    if not examples:
        raise ValueError("benchmark input contains no examples")

    started_at = datetime.now(timezone.utc).isoformat()
    load_started = time.perf_counter()
    model, tokenizer = load_model(args.model, args.base_model)
    load_ms = (time.perf_counter() - load_started) * 1000
    template_kwargs = resolve_chat_template_kwargs(args.model, None)
    _warm_up(
        examples,
        model,
        tokenizer,
        template_kwargs,
        count=args.warmup,
        max_input_length=args.max_input_length,
        max_new_tokens=args.max_new_tokens,
    )
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    records: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        for row, example in enumerate(examples):
            base = {
                "repeat": repeat,
                "row": row,
                "example_id": _example_id(example["messages"]),
            }
            try:
                measurement = _measure_one(
                    example["messages"],
                    model,
                    tokenizer,
                    template_kwargs,
                    max_input_length=args.max_input_length,
                    max_new_tokens=args.max_new_tokens,
                )
            except (RuntimeError, ValueError) as exc:
                measurement = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tokenization_ms": 0.0,
                    "ttft_ms": None,
                    "inter_token_ms": [],
                    "generation_ms": 0.0,
                    "end_to_end_ms": 0.0,
                    "raw_prediction_sha256": None,
                    "error": str(exc),
                }
            record = {**base, **measurement}
            records.append(record)
            print(
                f"repeat={repeat} row={row} generation_ms={record['generation_ms']} "
                f"error={record['error']}",
                flush=True,
            )

    result = {
        "model": str(args.model),
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "model_load_ms": round(load_ms, 3),
        "warmup_requests": args.warmup,
        "repeats": args.repeats,
        "max_input_length": args.max_input_length,
        "max_new_tokens": args.max_new_tokens,
        "chat_template_kwargs": template_kwargs,
        "peak_gpu_memory_allocated_bytes": (
            torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None
        ),
        "peak_gpu_memory_reserved_bytes": (
            torch.cuda.max_memory_reserved() if torch.cuda.is_available() else None
        ),
        "aggregate": summarize_records(records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, result)
    print(f"Benchmark saved to {args.output}")


if __name__ == "__main__":
    main()
