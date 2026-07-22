"""Validate SFT JSONL schema, split isolation, and optional token lengths."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from train.config import load_config, project_path
from train.data import canonical_target_json, iter_jsonl, tokenize_sft_example, validate_example


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Do not download a tokenizer or calculate token lengths.",
    )
    return parser.parse_args()


def content_hash(example: dict[str, Any]) -> str:
    payload = json.dumps(
        example["messages"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    data_config = config["data"]
    paths = {
        "train": project_path(data_config["train_file"]),
        "validation": project_path(data_config["validation_file"]),
    }
    tokenizer = None
    if not args.schema_only:
        from transformers import AutoTokenizer

        model_config = config["model"]
        tokenizer = AutoTokenizer.from_pretrained(
            model_config["name_or_path"],
            revision=model_config.get("revision", "main"),
            trust_remote_code=bool(model_config.get("trust_remote_code", False)),
            use_fast=True,
        )

    seen_hashes: dict[str, str] = {}
    report: dict[str, Any] = {}
    for split, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        counts: Counter[str] = Counter()
        lengths: list[int] = []
        with path.open(encoding="utf-8") as handle:
            for index, example in enumerate(iter_jsonl(handle)):
                try:
                    validate_example(example)
                except ValueError as exc:
                    raise ValueError(f"{split} row {index}: {exc}") from exc
                digest = content_hash(example)
                previous = seen_hashes.setdefault(digest, split)
                if previous != split:
                    raise ValueError(
                        f"content overlap between {previous} and {split} at {split} row {index}"
                    )
                counts["rows"] += 1
                counts["items"] += len(example["target"]["items"])
                counts["empty_orders"] += not example["target"]["items"]
                canonical_target_json(example["target"])
                if tokenizer is not None:
                    encoded = tokenize_sft_example(
                        example,
                        tokenizer,
                        max_length=int(data_config["max_length"]),
                        truncation=str(data_config.get("truncation", "error")),
                        prompt_head_tokens=int(data_config.get("prompt_head_tokens", 512)),
                        chat_template_kwargs=data_config.get("chat_template_kwargs", {}),
                    )
                    lengths.append(len(encoded["input_ids"]))
        split_report: dict[str, Any] = dict(counts)
        if lengths:
            ordered = sorted(lengths)
            split_report["tokens"] = {
                "max": ordered[-1],
                "mean": round(sum(ordered) / len(ordered), 2),
                "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            }
        report[split] = split_report
    report["content_overlap"] = 0
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
