"""Run and score the fixed Chat2Order held-out model comparison."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import subprocess
import time
from typing import Any, Mapping
import unicodedata

from train.config import file_sha256, write_json
from train.data import ITEM_FIELDS, TARGET_FIELDS, build_user_content, iter_jsonl


EXPECTED_TEST_SHA256 = "7ae3bdf5a1151a58b4315214ef68d424eef14f27b2dd6ac11ad00989baa19841"
EXPECTED_TEST_ROWS = 35
SCALAR_FIELDS = ("order_name", "phone_number", "address")
ITEM_FIELD_ORDER = ("raw_product", "raw_option", "volume")

TARGET_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["order_name", "phone_number", "address", "items"],
    "properties": {
        "order_name": {"type": ["string", "null"]},
        "phone_number": {"type": ["string", "null"]},
        "address": {"type": ["string", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["raw_product", "raw_option", "volume"],
                "properties": {
                    "raw_product": {"type": ["string", "null"]},
                    "raw_option": {"type": ["string", "null"]},
                    "volume": {"type": ["integer", "null"], "minimum": 1},
                },
            },
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("local", help="Run one local adapter or merged model")
    _add_prediction_args(local)
    local.add_argument("--model", type=Path, required=True)
    local.add_argument("--base-model")

    gemini = subparsers.add_parser("gemini", help="Run Gemini structured extraction")
    _add_prediction_args(gemini)
    gemini.add_argument("--model", default="gemini-3.5-flash")
    gemini.add_argument("--api-key-env", default="GEMINI_API_KEY")
    gemini.add_argument("--thinking-level", default="minimal")
    gemini.add_argument("--max-attempts", type=int, default=3)

    score = subparsers.add_parser("score", help="Score aligned prediction JSONL files")
    score.add_argument("--input", type=Path, required=True)
    score.add_argument("--prediction", action="append", required=True, metavar="ID=PATH")
    score.add_argument("--output-dir", type=Path, required=True)
    score.add_argument("--bootstrap-samples", type=int, default=10_000)

    examples = subparsers.add_parser(
        "examples", help="Export selected input/gold/prediction rows as Markdown"
    )
    examples.add_argument("--input", type=Path, required=True)
    examples.add_argument("--prediction", action="append", required=True, metavar="ID=PATH")
    examples.add_argument("--rows", required=True, help="Comma-separated zero-based rows")
    examples.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _add_prediction_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-input-length", type=int, default=4096)
    parser.add_argument("--max-new-tokens", type=int, default=512)


def example_id(messages: Any) -> str:
    canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def validate_prediction(value: Any) -> str | None:
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        return "prediction must contain exactly order_name, phone_number, address, items"
    for field in SCALAR_FIELDS:
        if value[field] is not None and not isinstance(value[field], str):
            return f"{field} must be a string or null"
    if not isinstance(value["items"], list):
        return "items must be an array"
    for index, item in enumerate(value["items"]):
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            return f"items[{index}] has invalid fields"
        for field in ("raw_product", "raw_option"):
            if item[field] is not None and not isinstance(item[field], str):
                return f"items[{index}].{field} must be a string or null"
        volume = item["volume"]
        if volume is not None and (
            isinstance(volume, bool) or not isinstance(volume, int) or volume <= 0
        ):
            return f"items[{index}].volume must be a positive integer or null"
    return None


def _load_examples(path: Path, *, enforce_fixed_test: bool = True) -> list[dict[str, Any]]:
    if enforce_fixed_test and file_sha256(path) != EXPECTED_TEST_SHA256:
        raise ValueError(f"test SHA-256 mismatch: {file_sha256(path)}")
    with path.open(encoding="utf-8") as handle:
        examples = list(iter_jsonl(handle))
    if enforce_fixed_test and len(examples) != EXPECTED_TEST_ROWS:
        raise ValueError(f"expected {EXPECTED_TEST_ROWS} test rows, found {len(examples)}")
    return examples


def run_local(args: argparse.Namespace) -> None:
    import torch
    from train.predict import load_model, predict_one, resolve_chat_template_kwargs

    examples = _load_examples(args.input, enforce_fixed_test=args.limit is None)
    template_kwargs = resolve_chat_template_kwargs(args.model, None)
    load_started = time.perf_counter()
    model, tokenizer = load_model(args.model, args.base_model)
    load_ms = round((time.perf_counter() - load_started) * 1000, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    started = datetime.now(timezone.utc).isoformat()
    with args.output.open("w", encoding="utf-8") as destination:
        for row, example in enumerate(examples[: args.limit]):
            messages = example["messages"]
            prompt = [{"role": "user", "content": build_user_content(messages)}]
            input_ids = tokenizer.apply_chat_template(
                prompt,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=False,
                **template_kwargs,
            )
            before = time.perf_counter()
            try:
                prediction, raw, parse_error = predict_one(
                    messages,
                    model,
                    tokenizer,
                    max_input_length=args.max_input_length,
                    max_new_tokens=args.max_new_tokens,
                    chat_template_kwargs=template_kwargs,
                )
                error = None
            except (RuntimeError, ValueError) as exc:
                prediction, raw, parse_error, error = None, "", None, str(exc)
            latency_ms = round((time.perf_counter() - before) * 1000, 3)
            record = {
                "example_id": example_id(messages),
                "row": row,
                "prediction": prediction,
                "raw_prediction": raw,
                "parse_error": parse_error,
                "schema_error": validate_prediction(prediction) if prediction is not None else None,
                "error": error,
                "input_tokens": len(input_ids),
                "output_tokens": len(tokenizer.encode(raw, add_special_tokens=False)),
                "latency_ms": latency_ms,
                "attempts": 1,
            }
            destination.write(json.dumps(record, ensure_ascii=False) + "\n")
            destination.flush()
            print(f"row={row} latency_ms={latency_ms} error={error or parse_error or record['schema_error']}", flush=True)
    peak = torch.cuda.max_memory_allocated() if torch.cuda.is_available() else None
    write_json(
        args.output.with_suffix(".meta.json"),
        {
            "backend": "local",
            "model": str(args.model),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "model_load_ms": load_ms,
            "peak_gpu_memory_bytes": peak,
            "max_input_length": args.max_input_length,
            "max_new_tokens": args.max_new_tokens,
            "chat_template_kwargs": template_kwargs,
        },
    )


def _usage(response: Any, *names: str) -> int | None:
    usage = getattr(response, "usage_metadata", None)
    for name in names:
        value = getattr(usage, name, None) if usage is not None else None
        if value is not None:
            return int(value)
    return None


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or (isinstance(status, int) and status >= 500):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("429", "timeout", "timed out", "503", "502", "500"))


def run_gemini(args: argparse.Namespace) -> None:
    from google import genai
    from google.genai import types

    key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        raise ValueError(f"environment variable {args.api_key_env} is empty")
    examples = _load_examples(args.input, enforce_fixed_test=args.limit is None)
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=120_000))
    thinking_level = types.ThinkingLevel(args.thinking_level.upper())
    config = types.GenerateContentConfig(
        max_output_tokens=args.max_new_tokens,
        response_mime_type="application/json",
        response_json_schema=TARGET_JSON_SCHEMA,
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    versions: set[str] = set()
    started = datetime.now(timezone.utc).isoformat()
    with args.output.open("w", encoding="utf-8") as destination:
        for row, example in enumerate(examples[: args.limit]):
            raw = ""
            prediction = None
            parse_error = None
            error = None
            response = None
            attempts = 0
            before = time.perf_counter()
            while attempts < args.max_attempts:
                attempts += 1
                try:
                    response = client.models.generate_content(
                        model=args.model,
                        contents=build_user_content(example["messages"]),
                        config=config,
                    )
                    raw = response.text or ""
                    try:
                        prediction = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        parse_error = f"{exc.msg} at position {exc.pos}"
                    break
                except Exception as exc:  # SDK wraps transport/API failures in several types.
                    error = f"{type(exc).__name__}: {exc}"
                    if not _retryable(exc) or attempts >= args.max_attempts:
                        break
                    time.sleep(2 ** (attempts - 1))
            latency_ms = round((time.perf_counter() - before) * 1000, 3)
            model_version = getattr(response, "model_version", None) if response else None
            if model_version:
                versions.add(model_version)
            finish_reason = None
            if response and getattr(response, "candidates", None):
                finish_reason = str(getattr(response.candidates[0], "finish_reason", None))
            record = {
                "example_id": example_id(example["messages"]),
                "row": row,
                "prediction": prediction,
                "raw_prediction": raw,
                "parse_error": parse_error,
                "schema_error": validate_prediction(prediction) if prediction is not None else None,
                "error": error,
                "input_tokens": _usage(response, "prompt_token_count") if response else None,
                "output_tokens": _usage(response, "candidates_token_count") if response else None,
                "thought_tokens": _usage(response, "thoughts_token_count") if response else None,
                "total_tokens": _usage(response, "total_token_count") if response else None,
                "latency_ms": latency_ms,
                "attempts": attempts,
                "model_version": model_version,
                "finish_reason": finish_reason,
            }
            destination.write(json.dumps(record, ensure_ascii=False) + "\n")
            destination.flush()
            print(f"row={row} latency_ms={latency_ms} attempts={attempts} error={error or parse_error or record['schema_error']}", flush=True)
    write_json(
        args.output.with_suffix(".meta.json"),
        {
            "backend": "gemini",
            "requested_model": args.model,
            "model_versions": sorted(versions),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "thinking_level": args.thinking_level,
            "max_new_tokens": args.max_new_tokens,
            "sdk_version": importlib.metadata.version("google-genai"),
        },
    )


def _normalize(value: Any, field: str) -> Any:
    if not isinstance(value, str):
        return value
    value = unicodedata.normalize("NFC", value).strip()
    if field == "phone_number":
        return "".join(character for character in value if character.isdigit())
    if field == "address":
        return " ".join(value.split())
    return value


def _item_tuple(item: Mapping[str, Any], normalized: bool) -> tuple[Any, Any, Any]:
    return tuple(
        _normalize(item[field], field) if normalized else item[field]
        for field in ITEM_FIELD_ORDER
    )  # type: ignore[return-value]


def _target_equal(gold: Mapping[str, Any], prediction: Mapping[str, Any], normalized: bool) -> bool:
    if normalized:
        scalars = all(
            _normalize(gold[field], field) == _normalize(prediction[field], field)
            for field in SCALAR_FIELDS
        )
    else:
        scalars = all(gold[field] == prediction[field] for field in SCALAR_FIELDS)
    return scalars and Counter(_item_tuple(x, normalized) for x in gold["items"]) == Counter(
        _item_tuple(x, normalized) for x in prediction["items"]
    )


def _item_counts(gold: Mapping[str, Any], prediction: Mapping[str, Any], normalized: bool) -> tuple[int, int, int]:
    gold_items = Counter(_item_tuple(x, normalized) for x in gold["items"])
    predicted_items = Counter(_item_tuple(x, normalized) for x in prediction["items"])
    tp = sum((gold_items & predicted_items).values())
    return tp, sum(predicted_items.values()) - tp, sum(gold_items.values()) - tp


def _wilson(successes: int, total: int) -> list[float]:
    if not total:
        return [0.0, 0.0]
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [center - margin, center + margin]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _read_predictions(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return list(iter_jsonl(handle))


def _score_model(examples: list[dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if len(rows) != len(examples):
        raise ValueError(f"prediction row count {len(rows)} != test row count {len(examples)}")
    schema_success = strict_success = normalized_success = 0
    strict_counts = [0, 0, 0]
    normalized_counts = [0, 0, 0]
    field_hits = {field: 0 for field in SCALAR_FIELDS}
    normalized_field_hits = {field: 0 for field in SCALAR_FIELDS}
    details = []
    for index, (example, row) in enumerate(zip(examples, rows)):
        expected_id = example_id(example["messages"])
        if row.get("row") != index or row.get("example_id", expected_id) != expected_id:
            raise ValueError(f"unaligned prediction at row {index}")
        prediction = row.get("prediction")
        schema_error = validate_prediction(prediction)
        strict = normalized = False
        if schema_error is None:
            schema_success += 1
            gold = example["target"]
            strict = _target_equal(gold, prediction, False)
            normalized = _target_equal(gold, prediction, True)
            strict_success += int(strict)
            normalized_success += int(normalized)
            for field in SCALAR_FIELDS:
                field_hits[field] += int(gold[field] == prediction[field])
                normalized_field_hits[field] += int(
                    _normalize(gold[field], field) == _normalize(prediction[field], field)
                )
            for destination, normalized_flag in ((strict_counts, False), (normalized_counts, True)):
                counts = _item_counts(gold, prediction, normalized_flag)
                for position, value in enumerate(counts):
                    destination[position] += value
        else:
            # An unusable row misses every gold item; excluding it would make a
            # schema-unstable model's item recall look artificially perfect.
            strict_counts[2] += len(example["target"]["items"])
            normalized_counts[2] += len(example["target"]["items"])
        details.append({
            "row": index,
            "example_id": expected_id,
            "schema_success": schema_error is None,
            "semantic_exact": strict,
            "normalized_semantic_exact": normalized,
            "schema_error": schema_error,
        })
    total = len(examples)

    def item_metrics(counts: list[int]) -> dict[str, float | int]:
        tp, fp, fn = counts
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}

    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    aggregate = {
        "rows": total,
        "schema_success": schema_success,
        "schema_success_rate": schema_success / total,
        "semantic_exact": strict_success,
        "semantic_exact_rate": strict_success / total,
        "semantic_exact_wilson_95": _wilson(strict_success, total),
        "normalized_semantic_exact": normalized_success,
        "normalized_semantic_exact_rate": normalized_success / total,
        "item_micro": item_metrics(strict_counts),
        "normalized_item_micro": item_metrics(normalized_counts),
        "field_accuracy": {field: field_hits[field] / total for field in SCALAR_FIELDS},
        "normalized_field_accuracy": {field: normalized_field_hits[field] / total for field in SCALAR_FIELDS},
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            "p90": _percentile(latencies, 0.90),
            "p95": _percentile(latencies, 0.95),
        },
        "input_tokens": sum(row.get("input_tokens") or 0 for row in rows),
        "output_tokens": sum(row.get("output_tokens") or 0 for row in rows),
        "thought_tokens": sum(row.get("thought_tokens") or 0 for row in rows),
        "attempts": sum(row.get("attempts") or 0 for row in rows),
    }
    return aggregate, details


def _bootstrap_difference(a: list[bool], b: list[bool], samples: int) -> list[float]:
    rng = random.Random(42)
    differences = []
    for _ in range(samples):
        indices = [rng.randrange(len(a)) for _ in a]
        differences.append(sum(int(a[i]) - int(b[i]) for i in indices) / len(indices))
    return [_percentile(differences, 0.025) or 0.0, _percentile(differences, 0.975) or 0.0]


def _mcnemar_exact(a: list[bool], b: list[bool]) -> tuple[int, int, float]:
    a_only = sum(x and not y for x, y in zip(a, b))
    b_only = sum(y and not x for x, y in zip(a, b))
    n = a_only + b_only
    if not n:
        return a_only, b_only, 1.0
    tail = sum(math.comb(n, k) for k in range(0, min(a_only, b_only) + 1)) / (2**n)
    return a_only, b_only, min(1.0, 2 * tail)


def _manifest(input_path: Path, prediction_paths: Mapping[str, Path]) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout)
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    versions = {}
    for package in ("torch", "transformers", "peft", "google-genai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "test_file": str(input_path),
        "test_sha256": file_sha256(input_path),
        "test_rows": EXPECTED_TEST_ROWS,
        "predictions": {name: {"path": str(path), "sha256": file_sha256(path)} for name, path in prediction_paths.items()},
        "git_commit": commit,
        "git_dirty": dirty,
        "python": platform.python_version(),
        "packages": versions,
    }


def run_score(args: argparse.Namespace) -> None:
    examples = _load_examples(args.input)
    paths: dict[str, Path] = {}
    for specification in args.prediction:
        if "=" not in specification:
            raise ValueError("--prediction must be ID=PATH")
        name, path = specification.split("=", 1)
        if not name or name in paths:
            raise ValueError(f"invalid or duplicate prediction ID: {name!r}")
        paths[name] = Path(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    aggregates = {}
    details_by_model = {}
    for name, path in paths.items():
        aggregate, details = _score_model(examples, _read_predictions(path))
        aggregates[name] = aggregate
        details_by_model[name] = details
    pairwise = {}
    names = list(paths)
    raw_p_values = []
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            left_values = [row["semantic_exact"] for row in details_by_model[left]]
            right_values = [row["semantic_exact"] for row in details_by_model[right]]
            left_only, right_only, p_value = _mcnemar_exact(left_values, right_values)
            key = f"{left}__vs__{right}"
            pairwise[key] = {
                "left_wins": left_only,
                "right_wins": right_only,
                "ties": len(examples) - left_only - right_only,
                "rate_difference": (sum(left_values) - sum(right_values)) / len(examples),
                "bootstrap_95": _bootstrap_difference(left_values, right_values, args.bootstrap_samples),
                "mcnemar_exact_p": p_value,
            }
            raw_p_values.append((key, p_value))
    # Holm step-down adjusted p-values, monotonic in sorted raw-p order.
    running = 0.0
    for rank, (key, p_value) in enumerate(sorted(raw_p_values, key=lambda item: item[1])):
        adjusted = min(1.0, p_value * (len(raw_p_values) - rank))
        running = max(running, adjusted)
        pairwise[key]["mcnemar_holm_p"] = running
    per_example = []
    for index in range(len(examples)):
        per_example.append({
            "row": index,
            "example_id": example_id(examples[index]["messages"]),
            "models": {name: details_by_model[name][index] for name in names},
        })
    write_json(args.output_dir / "aggregate.json", aggregates)
    write_json(args.output_dir / "pairwise.json", pairwise)
    write_json(args.output_dir / "manifest.json", _manifest(args.input, paths))
    with (args.output_dir / "per_example.jsonl").open("w", encoding="utf-8") as handle:
        for row in per_example:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    lines = [
        "# Chat2Order held-out 평가 결과",
        "",
        f"- Test set: {len(examples)} rows (`{file_sha256(args.input)}`)",
        "- 대표 품질 지표: strict semantic exact match",
        "",
        "| 모델 | Schema | Exact | Normalized exact | Item F1 | Median latency |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in names:
        value = aggregates[name]
        latency = value["latency_ms"]["median"]
        lines.append(
            f"| {name} | {value['schema_success']}/{value['rows']} | "
            f"{value['semantic_exact']}/{value['rows']} ({value['semantic_exact_rate']:.1%}) | "
            f"{value['normalized_semantic_exact']}/{value['rows']} ({value['normalized_semantic_exact_rate']:.1%}) | "
            f"{value['item_micro']['f1']:.3f} | {latency:.1f} ms |"
        )
    (args.output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Scores saved to {args.output_dir}")


def run_examples(args: argparse.Namespace) -> None:
    examples = _load_examples(args.input)
    selected_rows = [int(value.strip()) for value in args.rows.split(",") if value.strip()]
    if not selected_rows or len(selected_rows) != len(set(selected_rows)):
        raise ValueError("--rows must contain unique row numbers")
    if min(selected_rows) < 0 or max(selected_rows) >= len(examples):
        raise ValueError("--rows contains an out-of-range row")
    predictions: dict[str, list[dict[str, Any]]] = {}
    for specification in args.prediction:
        if "=" not in specification:
            raise ValueError("--prediction must be ID=PATH")
        name, path = specification.split("=", 1)
        if not name or name in predictions:
            raise ValueError(f"invalid or duplicate prediction ID: {name!r}")
        predictions[name] = _read_predictions(Path(path))
        if len(predictions[name]) != len(examples):
            raise ValueError(f"{name}: prediction row count mismatch")
    lines = [
        "# Gemma 4 E4B vs Gemini 3.5 Flash 입출력 예시",
        "",
        "입력은 privacy-safe test set의 가명화 대화다. `gold`는 검수 정답이며 모델 입력에는 포함되지 않았다.",
        "",
    ]
    for row in selected_rows:
        example = examples[row]
        lines.extend(
            [
                f"## Row {row}",
                "",
                "### Input conversation",
                "",
                "```json",
                json.dumps(example["messages"], ensure_ascii=False, indent=2),
                "```",
                "",
                "### Gold output",
                "",
                "```json",
                json.dumps(example["target"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
        for name, rows in predictions.items():
            lines.extend(
                [
                    f"### {name} output",
                    "",
                    "```json",
                    json.dumps(rows[row].get("prediction"), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Examples saved to {args.output}")


def main() -> None:
    args = parse_args()
    if hasattr(args, "limit") and args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")
    if args.command == "local":
        run_local(args)
    elif args.command == "gemini":
        run_gemini(args)
    elif args.command == "score":
        run_score(args)
    else:
        run_examples(args)


if __name__ == "__main__":
    main()
