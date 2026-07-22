"""Recover high-confidence training labels from quarantined completion messages."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_training_dataset import canonical_json_hash, validate_gold_target
from resolver import CatalogIndex, resolve_catalog_item
from review_all_dataset import (
    normalize_catalog,
    recipient_name_supported,
    unexplained_marker_text,
)


BLOCKING_REASONS = {
    "no_exact_completed_marker",
    "empty_items_not_human_confirmed",
    "legacy_rows_require_raw_reconstruction",
    "meaningful_message_after_last_marker",
    "recipient_name_not_explicitly_supported",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def reconstruct_target(
    prediction: Any, marker: str
) -> tuple[dict[str, Any] | None, list[str]]:
    if not isinstance(prediction, dict) or not isinstance(prediction.get("items"), list):
        return None, ["prediction_not_object_schema"]
    if not prediction["items"]:
        return None, ["prediction_items_empty"]

    reconstructed_items: list[dict[str, Any]] = []
    errors: list[str] = []
    for position, item in enumerate(prediction["items"]):
        product = item.get("product")
        option = item.get("option")
        volume = item.get("volume")
        if not product or str(product) not in marker:
            errors.append(f"item_{position}_product_hint_not_in_marker")
        normalized_option = None
        if option not in (None, "") and str(option).lower() != "null":
            normalized_option = str(option)
            if normalized_option not in marker:
                errors.append(f"item_{position}_option_hint_not_in_marker")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 1:
            errors.append(f"item_{position}_invalid_volume")
        reconstructed_items.append(
            {
                "raw_product": str(product) if product else None,
                "raw_option": normalized_option,
                "volume": volume,
            }
        )
    if errors:
        return None, sorted(set(errors))
    if unexplained_marker_text(marker, reconstructed_items):
        return None, ["marker_not_fully_consumed_by_hints"]

    return {
        "order_name": prediction.get("order_name"),
        "phone_number": prediction.get("phone_number"),
        "address": prediction.get("address"),
        "items": reconstructed_items,
    }, []


def recover_quarantine(
    source: Path,
    quarantine_path: Path,
    existing_reviews_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    quarantine = read_jsonl(quarantine_path)
    existing_reviews = read_jsonl(existing_reviews_path)
    quarantine_by_id = {item["survivor_id"]: item for item in quarantine}
    existing_ids = {item["survivor_id"] for item in existing_reviews}
    wanted_ids = set(quarantine_by_id) | existing_ids

    rows: dict[str, dict[str, str]] = {}
    existing_hashes: set[str] = set()
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            record_id = row.get("id")
            if record_id in wanted_ids:
                rows[record_id] = row
            if record_id in existing_ids:
                existing_hashes.add(
                    canonical_json_hash(json.loads(row["chat_json"]))
                )

    decisions: list[dict[str, Any]] = []
    recovered_reviews: list[dict[str, Any]] = []
    recovered_hashes: set[str] = set()
    failure_counts: Counter[str] = Counter()
    base_eligible = 0

    for record_id in sorted(quarantine_by_id):
        quarantine_item = quarantine_by_id[record_id]
        original_reasons = set(quarantine_item.get("reasons") or [])
        reasons: list[str] = []
        if BLOCKING_REASONS & original_reasons:
            reasons.append("blocked_by_original_review_reason")
        if "item_0_missing_raw_product" not in original_reasons:
            reasons.append("not_raw_reconstruction_cohort")
        if reasons:
            for reason in reasons:
                failure_counts[reason] += 1
            decisions.append(
                {
                    "survivor_id": record_id,
                    "decision": "not_recovered",
                    "reasons": reasons,
                }
            )
            continue

        base_eligible += 1
        row = rows[record_id]
        chat = json.loads(row["chat_json"])
        prediction = json.loads(row["predicted_json"])
        catalog = json.loads(row["catalog_json"])
        marker = [
            str(message.get("message") or "")
            for message in chat
            if isinstance(message, dict)
            and "[주문완료]" in str(message.get("message") or "")
        ][-1]
        target, reconstruction_errors = reconstruct_target(prediction, marker)
        reasons.extend(reconstruction_errors)
        if target and validate_gold_target(chat, target):
            reasons.append("reconstructed_source_validation_failed")
        if target and not recipient_name_supported(chat, target):
            reasons.append("recipient_name_not_supported")
        if target:
            index = CatalogIndex.build(normalize_catalog(catalog))
            for item in target["items"]:
                resolved = resolve_catalog_item(
                    item["raw_product"],
                    item["raw_option"],
                    item["volume"],
                    index,
                )
                if resolved.mapping_status not in {"exact", "alias"}:
                    reasons.append("reconstructed_catalog_not_exact_or_alias")
                    break

        chat_hash = canonical_json_hash(chat)
        if chat_hash in existing_hashes or chat_hash in recovered_hashes:
            reasons.append("duplicate_content_of_accepted")
        if reasons:
            reasons = sorted(set(reasons))
            for reason in reasons:
                failure_counts[reason] += 1
            decisions.append(
                {
                    "survivor_id": record_id,
                    "canonical_chat_hash": chat_hash,
                    "decision": "not_recovered",
                    "reasons": reasons,
                }
            )
            continue

        recovered_hashes.add(chat_hash)
        corrections = {"items": target["items"]}
        decision = {
            "survivor_id": record_id,
            "canonical_chat_hash": chat_hash,
            "decision": "reconstructed_marker_hints",
            "reasons": [],
        }
        decisions.append(decision)
        recovered_reviews.append(
            {
                "survivor_id": record_id,
                "decision": "reconstructed_marker_hints",
                "reviewer": "marker_hint_reconstructor_v1",
                "review_scope": "last_completed_message_exact_hint_coverage",
                "corrections": corrections,
                "notes": ["raw_fields_reconstructed_from_exact_marker_substrings"],
            }
        )

    combined_reviews = existing_reviews + recovered_reviews
    summary = {
        "quarantine_rows": len(quarantine),
        "base_eligible_rows": base_eligible,
        "recovered_rows": len(recovered_reviews),
        "not_recovered_rows": len(quarantine) - len(recovered_reviews),
        "combined_accepted_reviews": len(combined_reviews),
        "failure_counts": dict(sorted(failure_counts.items())),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        ("recovery_decisions.jsonl", decisions),
        ("recovered_reviews.jsonl", recovered_reviews),
        ("combined_reviews.jsonl", combined_reviews),
        (
            "remaining_quarantine.jsonl",
            [item for item in decisions if item["decision"] == "not_recovered"],
        ),
    ):
        with (output_dir / filename).open("w", encoding="utf-8") as handle:
            for item in payload:
                handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--quarantine", type=Path, required=True)
    parser.add_argument("--existing-reviews", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = recover_quarantine(
        args.source, args.quarantine, args.existing_reviews, args.output_dir
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
