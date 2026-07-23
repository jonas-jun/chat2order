"""Export manually reviewed examples with deterministic local pseudonymization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from build_training_dataset import (
    file_sha256,
    normalize_prediction,
    sha256_text,
    validate_gold_target,
)


SELLER_MARKERS = ("이지픽", "다애모드")
SYNTHETIC_NAMES = ("김가람", "이도윤", "박서윤", "최하린", "정민준", "한지우")
ACCOUNT_PATTERN = re.compile(r"(?<!\d)(?:\d[\s-]*){8,}(?!\d)")
ACCEPTED_DECISIONS = {
    "accepted_prediction",
    "corrected_prediction",
    "accepted_corrected",
    "accepted_strict",
    "reconstructed_marker_hints",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def stable_number(example_id: str, offset: int, modulus: int) -> int:
    return int(example_id[offset : offset + 8], 16) % modulus


def synthetic_identity(example_id: str, target: dict[str, Any]) -> dict[str, str | None]:
    name = (
        SYNTHETIC_NAMES[stable_number(example_id, 0, len(SYNTHETIC_NAMES))]
        if target.get("order_name")
        else None
    )
    phone = (
        f"010-0000-{stable_number(example_id, 8, 10000):04d}"
        if target.get("phone_number")
        else None
    )
    address = None
    if target.get("address"):
        road = stable_number(example_id, 16, 900) + 1
        building = stable_number(example_id, 24, 800) + 101
        unit = stable_number(example_id, 32, 8000) + 1001
        address = f"서울특별시 중구 학습로 {road} 예시아파트 {building}동 {unit}호"
    return {"order_name": name, "phone_number": phone, "address": address}


def whitespace_flexible_pattern(value: str) -> re.Pattern[str]:
    characters = [character for character in value if not character.isspace()]
    return re.compile(r"\s*".join(re.escape(character) for character in characters))


def digit_flexible_pattern(value: str) -> re.Pattern[str] | None:
    number = "".join(character for character in value if character.isdigit())
    if not number:
        return None
    return re.compile(
        r"[^0-9]{0,3}".join(re.escape(character) for character in number)
    )


def replace_sensitive_text(
    text: str,
    original: dict[str, Any],
    synthetic: dict[str, str | None],
    *,
    scrub_accounts: bool = False,
) -> str:
    placeholders = {
        "order_name": "__DATASET_NAME__",
        "phone_number": "__DATASET_PHONE__",
        "address": "__DATASET_ADDRESS__",
    }
    result = text
    address = original.get("address")
    if address:
        result = whitespace_flexible_pattern(str(address)).sub(
            placeholders["address"], result
        )
    phone = original.get("phone_number")
    if phone:
        pattern = digit_flexible_pattern(str(phone))
        if pattern:
            result = pattern.sub(placeholders["phone_number"], result)
    order_name = original.get("order_name")
    if order_name:
        result = whitespace_flexible_pattern(str(order_name)).sub(
            placeholders["order_name"], result
        )

    if scrub_accounts:
        result = ACCOUNT_PATTERN.sub("<ACCOUNT_NUMBER>", result)
    for key, placeholder in placeholders.items():
        if synthetic.get(key):
            result = result.replace(placeholder, str(synthetic[key]))
    return result


def pseudonymize_chat(
    chat: list[Any],
    target: dict[str, Any],
    example_id: str,
    additional_sensitive: dict[str, Any] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    synthetic = synthetic_identity(example_id, target)
    extra_sensitive = {
        key: (additional_sensitive or {}).get(key)
        if (additional_sensitive or {}).get(key) != target.get(key)
        else None
        for key in ("order_name", "phone_number", "address")
    }
    secondary_synthetic = synthetic_identity(
        sha256_text(f"secondary:{example_id}"), extra_sensitive
    )
    customer_aliases = {
        str(message.get("user") or "")
        for message in chat
        if isinstance(message, dict)
        and message.get("user")
        and len(str(message.get("user"))) >= 2
        and not any(
            marker in str(message.get("user")) for marker in SELLER_MARKERS
        )
    }
    alias_replacement = (
        secondary_synthetic.get("order_name")
        or SYNTHETIC_NAMES[
            stable_number(example_id, 40, len(SYNTHETIC_NAMES))
        ]
    )
    transformed: list[Any] = []
    for message in chat:
        if not isinstance(message, dict):
            transformed.append(message)
            continue
        copied = dict(message)
        user = str(copied.get("user") or "")
        copied["user"] = (
            "seller" if any(marker in user for marker in SELLER_MARKERS) else "customer"
        )
        if "message" in copied:
            transformed_text = replace_sensitive_text(
                str(copied.get("message") or ""),
                target,
                synthetic,
            )
            if any(extra_sensitive.values()):
                transformed_text = replace_sensitive_text(
                    transformed_text, extra_sensitive, secondary_synthetic
                )
            for alias in sorted(customer_aliases, key=len, reverse=True):
                transformed_text = transformed_text.replace(
                    alias, str(alias_replacement)
                )
            protected_phones = [
                phone
                for phone in (
                    synthetic.get("phone_number"),
                    secondary_synthetic.get("phone_number"),
                )
                if phone
            ]
            for position, phone in enumerate(protected_phones):
                transformed_text = transformed_text.replace(
                    str(phone), f"__PROTECTED_PHONE_{position}__"
                )
            transformed_text = ACCOUNT_PATTERN.sub(
                "<ACCOUNT_NUMBER>", transformed_text
            )
            for position, phone in enumerate(protected_phones):
                transformed_text = transformed_text.replace(
                    f"__PROTECTED_PHONE_{position}__", str(phone)
                )
            copied["message"] = transformed_text
        transformed.append(copied)

    transformed_target = {
        "order_name": synthetic["order_name"],
        "phone_number": synthetic["phone_number"],
        "address": synthetic["address"],
        "items": [
            {
                "raw_product": item.get("raw_product"),
                "raw_option": item.get("raw_option"),
                "volume": item.get("volume"),
            }
            for item in target.get("items") or []
        ],
    }
    return transformed, transformed_target


def target_from_prediction(prediction: Any) -> tuple[dict[str, Any] | None, list[str]]:
    normalized, warnings = normalize_prediction(prediction)
    if normalized is None:
        return None, warnings
    target = {
        "order_name": normalized.get("order_name"),
        "phone_number": normalized.get("phone_number"),
        "address": normalized.get("address"),
        "items": [
            {
                "raw_product": item.get("raw_product"),
                "raw_option": item.get("raw_option"),
                "volume": item.get("volume"),
            }
            for item in normalized.get("items") or []
        ],
    }
    return target, warnings


def target_for_review(
    row: dict[str, str], decision: str, corrections: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, list[str]]:
    if decision in {
        "accepted_prediction",
        "corrected_prediction",
        "accepted_strict",
        "reconstructed_marker_hints",
    }:
        target, warnings = target_from_prediction(json.loads(row["predicted_json"]))
        if target is None:
            return target, warnings
        if decision in {"accepted_prediction", "accepted_strict"}:
            return target, warnings
        allowed_fields = {"order_name", "phone_number", "address", "items"}
        unknown = set(corrections or {}) - allowed_fields
        if unknown:
            return None, [f"unsupported_correction_fields:{','.join(sorted(unknown))}"]
        target.update(corrections or {})
        if "items" in (corrections or {}):
            warnings = [
                warning
                for warning in warnings
                if not warning.endswith("_missing_raw_product")
            ]
        return target, warnings
    if decision == "accepted_corrected":
        corrected = row.get("corrected_json", "").strip()
        if not corrected:
            return None, ["missing_corrected_json"]
        try:
            return json.loads(corrected), []
        except json.JSONDecodeError:
            return None, ["invalid_corrected_json"]
    return None, ["unsupported_review_decision"]


def export_reviewed(
    source: Path,
    reviews_path: Path,
    output_dir: Path,
    review_queue_path: Path | None = None,
) -> dict[str, Any]:
    reviews = read_jsonl(reviews_path)
    accepted = {
        review["survivor_id"]: review
        for review in reviews
        if review.get("decision") in ACCEPTED_DECISIONS
    }
    if len(accepted) != sum(
        review.get("decision") in ACCEPTED_DECISIONS for review in reviews
    ):
        raise ValueError("duplicate accepted survivor_id in manual reviews")

    source_rows: dict[str, dict[str, str]] = {}
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("id") in accepted:
                source_rows[row["id"]] = row
    missing = sorted(set(accepted) - set(source_rows))
    if missing:
        raise ValueError(f"reviewed source ids not found: {missing}")

    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash = file_sha256(source)
    examples: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for survivor_id in sorted(accepted):
        row = source_rows[survivor_id]
        chat = json.loads(row["chat_json"])
        decision = accepted[survivor_id]["decision"]
        target, warnings = target_for_review(
            row, decision, accepted[survivor_id].get("corrections")
        )
        if target is None or warnings:
            raise ValueError(f"{survivor_id}: prediction cannot be exported: {warnings}")
        original_errors = validate_gold_target(chat, target)
        if original_errors:
            raise ValueError(f"{survivor_id}: original validation failed: {original_errors}")

        privacy_target, _ = target_from_prediction(json.loads(row["predicted_json"]))
        example_id = sha256_text(f"reviewed:{survivor_id}:{source_hash}")
        transformed_chat, transformed_target = pseudonymize_chat(
            chat, target, example_id, privacy_target
        )
        transformed_errors = validate_gold_target(transformed_chat, transformed_target)
        if transformed_errors:
            raise ValueError(
                f"{survivor_id}: pseudonymized validation failed: {transformed_errors}"
            )
        serialized = json.dumps(
            {"messages": transformed_chat, "target": transformed_target},
            ensure_ascii=False,
            sort_keys=True,
        )
        sensitive_values = [
            target.get(key) for key in ("order_name", "phone_number", "address")
        ]
        if privacy_target:
            sensitive_values.extend(
                privacy_target.get(key)
                for key in ("order_name", "phone_number", "address")
            )
        for sensitive in sensitive_values:
            if sensitive and str(sensitive) in serialized:
                raise ValueError(f"{survivor_id}: original sensitive value remains")
        examples.append({"messages": transformed_chat, "target": transformed_target})
        audit_rows.append(
            {
                "example_id": example_id,
                "survivor_id": survivor_id,
                "decision": accepted[survivor_id]["decision"],
                "review_scope": accepted[survivor_id].get("review_scope"),
                "canonical_chat_hash": hashlib.sha256(
                    json.dumps(
                        chat,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "pseudonymization_version": 1,
                "target_hash": hashlib.sha256(
                    json.dumps(
                        transformed_target,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )

    with (output_dir / "reviewed_deidentified.jsonl").open(
        "w", encoding="utf-8"
    ) as handle:
        for example in examples:
            handle.write(json.dumps(example, ensure_ascii=False, sort_keys=True) + "\n")
    with (output_dir / "export_manifest.jsonl").open("w", encoding="utf-8") as handle:
        for audit in audit_rows:
            handle.write(json.dumps(audit, ensure_ascii=False, sort_keys=True) + "\n")
    remaining_review_rows: int | None = None
    if review_queue_path:
        queue = read_jsonl(review_queue_path)
        remaining = [
            item for item in queue if item.get("survivor_id") not in accepted
        ]
        remaining_review_rows = len(remaining)
        with (output_dir / "remaining_review_queue.jsonl").open(
            "w", encoding="utf-8"
        ) as handle:
            for item in remaining:
                handle.write(
                    json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                )
    summary = {
        "source_sha256": source_hash,
        "manual_review_rows": len(reviews),
        "exported_rows": len(examples),
        "pseudonymization_version": 1,
        "split_created": False,
        "remaining_review_rows": remaining_review_rows,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-queue", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = export_reviewed(
        args.source, args.reviews, args.output_dir, args.review_queue
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
