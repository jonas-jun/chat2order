"""Build a privacy-safe inventory for the order extraction training dataset.

This first stage never rewrites or copies chat/catalog payloads.  It normalizes
schema metadata, selects one survivor per exact (filename, chat_json) group,
classifies label trust, and emits ID/hash-only audit artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


GOLD_STATUSES = {"accepted", "corrected", "no_order_confirmed"}
CHANGE_WORDS = ("변경", "취소", "말고", "대신", "수정", "바꿔")


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def parse_int(value: Any, default: int = -1) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_text(encoded)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(value: str) -> tuple[Any | None, str | None]:
    try:
        return json.loads(value), None
    except (TypeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__


def message_text(chat: Any) -> str:
    if not isinstance(chat, list):
        return ""
    return "\n".join(
        str(message.get("message") or "")
        if isinstance(message, dict)
        else str(message)
        for message in chat
    )


def prediction_schema(prediction: Any) -> tuple[str, int]:
    if isinstance(prediction, dict) and isinstance(prediction.get("items"), list):
        return "object_v2", len(prediction["items"])
    if isinstance(prediction, list) and all(isinstance(item, dict) for item in prediction):
        return "rows_v1", len(prediction)
    return "invalid", 0


def normalize_prediction(prediction: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize prediction versions without inventing missing raw labels.

    Legacy row predictions contain canonical product/option values but usually do
    not contain their source spans.  Those hints are retained for review while
    raw_product/raw_option stay null so the result cannot accidentally become a
    training target.
    """
    schema, _ = prediction_schema(prediction)
    if schema == "object_v2":
        items = []
        warnings: list[str] = []
        for position, item in enumerate(prediction.get("items") or []):
            raw_product = item.get("raw_product")
            raw_option = item.get("raw_option")
            if not raw_product:
                warnings.append(f"item_{position}_missing_raw_product")
            items.append(
                {
                    "raw_product": raw_product,
                    "raw_option": raw_option,
                    "volume": item.get("volume"),
                    "legacy_product_hint": item.get("product"),
                    "legacy_option_hint": item.get("option"),
                }
            )
        return {
            "order_name": prediction.get("order_name"),
            "phone_number": prediction.get("phone_number"),
            "address": prediction.get("address"),
            "items": items,
        }, warnings
    if schema == "rows_v1":
        rows = prediction
        first_value = lambda key: next(
            (row.get(key) for row in rows if row.get(key) not in (None, "")), None
        )
        items = [
            {
                "raw_product": row.get("raw_product"),
                "raw_option": row.get("raw_option"),
                "volume": row.get("volume"),
                "legacy_product_hint": row.get("product"),
                "legacy_option_hint": row.get("option"),
            }
            for row in rows
        ]
        warnings = ["legacy_rows_require_raw_reconstruction"]
        return {
            "order_name": first_value("order_name"),
            "phone_number": first_value("phone_number"),
            "address": first_value("address"),
            "items": items,
        }, warnings
    return None, ["invalid_prediction_schema"]


def valid_corrected_target(target: Any) -> bool:
    if not isinstance(target, dict):
        return False
    if not isinstance(target.get("items"), list):
        return False
    required = {"order_name", "phone_number", "address", "items"}
    if not required.issubset(target):
        return False
    for item in target["items"]:
        if not isinstance(item, dict):
            return False
        if not {"raw_product", "raw_option", "volume"}.issubset(item):
            return False
        volume = item.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 1:
            return False
    return True


def compact_whitespace(value: Any) -> str:
    return "".join(str(value or "").split())


def digits(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def validate_gold_target(chat: Any, target: Any) -> list[str]:
    if not valid_corrected_target(target):
        return ["invalid_corrected_schema"]

    text = message_text(chat)
    compact_text = compact_whitespace(text)
    text_digits = digits(text)
    errors: list[str] = []

    order_name = target.get("order_name")
    if order_name and str(order_name) not in text:
        errors.append("order_name_not_in_chat")
    phone = target.get("phone_number")
    if phone and digits(phone) not in text_digits:
        errors.append("phone_number_not_in_chat")
    address = target.get("address")
    if address and compact_whitespace(address) not in compact_text:
        errors.append("address_not_in_chat")

    seen: set[tuple[Any, Any, Any]] = set()
    for item in target.get("items") or []:
        raw_product = item.get("raw_product")
        raw_option = item.get("raw_option")
        volume = item.get("volume")
        if not raw_product or str(raw_product) not in text:
            errors.append("raw_product_not_in_chat")
        if raw_option and str(raw_option) not in text:
            errors.append("raw_option_not_in_chat")
        key = (raw_product, raw_option, volume)
        if key in seen:
            errors.append("duplicate_target_item")
        seen.add(key)
    return sorted(set(errors))


@dataclass(frozen=True)
class Record:
    id: str
    chat_filename_hash: str
    raw_chat_hash: str
    canonical_chat_hash: str | None
    catalog_hash: str | None
    prediction_hash: str | None
    corrected_hash: str | None
    label_status: str
    is_verified: bool
    confirmed_revision: int
    reviewed_at: str
    created_at: str
    chat_valid: bool
    catalog_valid: bool
    prediction_schema: str
    predicted_item_count: int
    corrected_valid: bool
    gold_validation_errors: tuple[str, ...]
    exact_completed_count: int
    loose_completed_count: int
    has_change_language: bool

    @property
    def exact_duplicate_key(self) -> tuple[str, str]:
        return self.chat_filename_hash, self.raw_chat_hash

    @property
    def survivor_score(self) -> tuple[Any, ...]:
        verified_gold = self.is_verified and self.label_status in GOLD_STATUSES
        return (
            verified_gold,
            self.corrected_valid,
            self.confirmed_revision,
            self.reviewed_at,
            self.created_at,
            self.id,
        )


def record_from_row(row: dict[str, str]) -> Record:
    chat, chat_error = load_json(row.get("chat_json", ""))
    catalog, catalog_error = load_json(row.get("catalog_json", ""))
    prediction, prediction_error = load_json(row.get("predicted_json", ""))
    corrected_raw = row.get("corrected_json", "").strip()
    corrected, corrected_error = (
        load_json(corrected_raw) if corrected_raw else (None, None)
    )

    schema, item_count = prediction_schema(prediction)
    text = message_text(chat)
    corrected_valid = bool(corrected_raw) and corrected_error is None and valid_corrected_target(corrected)
    gold_errors = (
        validate_gold_target(chat, corrected)
        if parse_bool(row.get("is_verified"))
        and row.get("label_status", "") in GOLD_STATUSES
        else []
    )

    return Record(
        id=row.get("id", ""),
        chat_filename_hash=sha256_text(row.get("chat_filename", "")),
        raw_chat_hash=sha256_text(row.get("chat_json", "")),
        canonical_chat_hash=(canonical_json_hash(chat) if chat_error is None else None),
        catalog_hash=(canonical_json_hash(catalog) if catalog_error is None else None),
        prediction_hash=(
            canonical_json_hash(prediction) if prediction_error is None else None
        ),
        corrected_hash=(
            canonical_json_hash(corrected)
            if corrected_raw and corrected_error is None
            else None
        ),
        label_status=row.get("label_status", "") or "unreviewed",
        is_verified=parse_bool(row.get("is_verified")),
        confirmed_revision=parse_int(row.get("confirmed_revision")),
        reviewed_at=row.get("reviewed_at", "") or "",
        created_at=row.get("created_at", "") or "",
        chat_valid=chat_error is None and isinstance(chat, list),
        catalog_valid=catalog_error is None and isinstance(catalog, (list, dict)),
        prediction_schema=(schema if prediction_error is None else "invalid_json"),
        predicted_item_count=item_count,
        corrected_valid=corrected_valid,
        gold_validation_errors=tuple(gold_errors),
        exact_completed_count=text.count("[주문완료]"),
        loose_completed_count=text.replace(" ", "").count("주문완료"),
        has_change_language=any(word in text for word in CHANGE_WORDS),
    )


def classify(record: Record) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not record.chat_valid:
        reasons.append("invalid_chat_json_or_schema")
    if not record.catalog_valid:
        reasons.append("invalid_catalog_json_or_schema")
    if record.is_verified and record.label_status in GOLD_STATUSES:
        if not record.corrected_valid:
            reasons.append("invalid_corrected_schema")
        reasons.extend(record.gold_validation_errors)
        return ("gold" if not reasons else "rejected"), sorted(set(reasons))
    if reasons:
        return "rejected", sorted(set(reasons))
    if record.label_status == "business_corrected":
        return "business-only", []
    return "silver", []


def review_priority(record: Record) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if record.exact_completed_count and record.predicted_item_count == 0:
        score += 100
        reasons.append("completed_marker_with_empty_prediction")
    if record.prediction_schema == "invalid":
        score += 90
        reasons.append("invalid_prediction_schema")
    if record.exact_completed_count > 1:
        score += 80
        reasons.append("multiple_completed_markers")
    if record.prediction_schema == "rows_v1":
        score += 60
        reasons.append("legacy_prediction_schema")
    if record.has_change_language:
        score += 50
        reasons.append("change_or_cancel_language")
    if record.predicted_item_count > 1:
        score += 40
        reasons.append("multiple_items")
    if not record.exact_completed_count and record.predicted_item_count:
        score += 30
        reasons.append("order_without_exact_completed_marker")
    score += 10
    reasons.append("unreviewed_or_auto_accepted")
    return score, reasons


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_inventory(source: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[Record] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            records.append(record_from_row(row))

    groups: dict[tuple[str, str], list[Record]] = defaultdict(list)
    for record in records:
        groups[record.exact_duplicate_key].append(record)

    survivors: list[Record] = []
    duplicate_rows: list[dict[str, Any]] = []
    for group in groups.values():
        survivor = max(group, key=lambda candidate: candidate.survivor_score)
        survivors.append(survivor)
        if len(group) > 1:
            duplicate_rows.append(
                {
                    "survivor_id": survivor.id,
                    "merged_source_ids": sorted(item.id for item in group if item.id != survivor.id),
                    "group_size": len(group),
                    "catalog_differs": len({item.catalog_hash for item in group}) > 1,
                    "prediction_differs": len({item.prediction_hash for item in group}) > 1,
                    "corrected_differs": len({item.corrected_hash for item in group}) > 1,
                    "label_statuses": sorted({item.label_status for item in group}),
                    "selection_rule": "verified_gold>corrected>revision>reviewed_at>created_at>id",
                }
            )

    survivors.sort(key=lambda item: item.id)
    duplicate_rows.sort(key=lambda item: item["survivor_id"])
    tier_counts: Counter[str] = Counter()
    schema_counts: Counter[str] = Counter()
    manifest_rows: list[dict[str, Any]] = []
    quarantine_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    gold_rows: list[dict[str, Any]] = []

    source_ids_by_key = {
        key: sorted(record.id for record in group) for key, group in groups.items()
    }
    for record in survivors:
        tier, exclusion_reasons = classify(record)
        tier_counts[tier] += 1
        schema_counts[record.prediction_schema] += 1
        example_id = sha256_text(
            f"{record.chat_filename_hash}:{record.canonical_chat_hash or record.raw_chat_hash}"
        )
        manifest = {
            "example_id": example_id,
            "survivor_id": record.id,
            "source_ids": source_ids_by_key[record.exact_duplicate_key],
            "raw_chat_hash": record.raw_chat_hash,
            "canonical_chat_hash": record.canonical_chat_hash,
            "catalog_hash": record.catalog_hash,
            "prediction_hash": record.prediction_hash,
            "corrected_hash": record.corrected_hash,
            "prediction_schema": record.prediction_schema,
            "label_status": record.label_status,
            "label_tier": tier,
            "is_verified": record.is_verified,
            "exclusion_reasons": exclusion_reasons,
        }
        manifest_rows.append(manifest)
        if tier == "rejected":
            quarantine_rows.append(
                {
                    "example_id": example_id,
                    "survivor_id": record.id,
                    "exclusion_reasons": exclusion_reasons,
                }
            )
        elif tier == "gold":
            gold_rows.append(
                {
                    "example_id": example_id,
                    "survivor_id": record.id,
                    "corrected_hash": record.corrected_hash,
                }
            )
        elif tier == "silver":
            priority, reasons = review_priority(record)
            review_rows.append(
                {
                    "example_id": example_id,
                    "survivor_id": record.id,
                    "priority": priority,
                    "reasons": reasons,
                }
            )

    review_rows.sort(key=lambda item: (-item["priority"], item["survivor_id"]))
    content_groups = Counter(
        record.canonical_chat_hash for record in survivors if record.canonical_chat_hash
    )
    residual_content_duplicates = sum(count - 1 for count in content_groups.values() if count > 1)

    summary = {
        "source": str(source),
        "source_sha256": file_sha256(source),
        "total_rows": len(records),
        "exact_duplicate_groups": sum(len(group) > 1 for group in groups.values()),
        "duplicate_rows_removed": len(records) - len(survivors),
        "survivor_rows": len(survivors),
        "residual_content_duplicates_for_grouped_split": residual_content_duplicates,
        "source_label_status_counts": dict(sorted(Counter(r.label_status for r in records).items())),
        "survivor_prediction_schema_counts": dict(sorted(schema_counts.items())),
        "survivor_label_tier_counts": dict(sorted(tier_counts.items())),
        "review_queue_rows": len(review_rows),
        "gold_candidate_rows": len(gold_rows),
        "quarantine_rows": len(quarantine_rows),
        "contains_chat_or_catalog_payloads": False,
    }

    write_json(output_dir / "summary.json", summary)
    write_jsonl(output_dir / "manifest.jsonl", manifest_rows)
    write_jsonl(output_dir / "duplicates.jsonl", duplicate_rows)
    write_jsonl(output_dir / "quarantine.jsonl", quarantine_rows)
    write_jsonl(output_dir / "review_queue.jsonl", review_rows)
    write_jsonl(output_dir / "gold_candidate_ids.jsonl", gold_rows)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_inventory(args.source, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
