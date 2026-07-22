"""Create deterministic, content-grouped train/validation/test JSONL splits."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


TRAIN_ONLY_DECISIONS = {"reconstructed_marker_hints"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def choose_split(content_hash: str, seed: str) -> str:
    bucket = int(
        hashlib.sha256(f"{seed}:{content_hash}".encode("utf-8")).hexdigest()[:8],
        16,
    ) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def split_dataset(
    examples_path: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    seed: str = "chat2order-v1",
) -> dict[str, Any]:
    examples = read_jsonl(examples_path)
    manifest = read_jsonl(manifest_path)
    if len(examples) != len(manifest):
        raise ValueError("example and manifest row counts differ")

    grouped_splits: dict[str, str] = {}
    rows_by_split: dict[str, list[dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    split_manifest: list[dict[str, Any]] = []
    for example, audit in zip(examples, manifest):
        content_hash = audit.get("canonical_chat_hash")
        if not content_hash:
            raise ValueError("manifest row missing canonical_chat_hash")
        desired_split = (
            "train"
            if audit.get("decision") in TRAIN_ONLY_DECISIONS
            else choose_split(content_hash, seed)
        )
        split = grouped_splits.setdefault(content_hash, desired_split)
        rows_by_split[split].append(example)
        split_manifest.append({**audit, "split": split, "split_seed": seed})

    output_dir.mkdir(parents=True, exist_ok=True)
    for split, rows in rows_by_split.items():
        with (output_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with (output_dir / "split_manifest.jsonl").open("w", encoding="utf-8") as handle:
        for row in split_manifest:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    split_counts = {split: len(rows) for split, rows in rows_by_split.items()}
    decision_by_split = Counter(
        (row["split"], row.get("decision")) for row in split_manifest
    )
    summary = {
        "total_rows": len(examples),
        "split_seed": seed,
        "split_counts": split_counts,
        "decision_counts_by_split": {
            f"{split}:{decision}": count
            for (split, decision), count in sorted(decision_by_split.items())
        },
        "content_group_overlap": 0,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", default="chat2order-v1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = split_dataset(
        args.examples, args.manifest, args.output_dir, seed=args.seed
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
