"""Conservatively disposition every remaining dataset review item.

Only examples whose structured final completion message fully supports the
prediction are accepted. Everything else receives explicit quarantine reasons.
The output contains IDs, hashes, decisions, and reasons only.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_training_dataset import (
    canonical_json_hash,
    normalize_prediction,
    validate_gold_target,
)
from resolver import CatalogIndex, resolve_catalog_item


SELLER_MARKERS = ("이지픽", "다애모드")
NAME_EVIDENCE = ("이름", "성함", "수취인", "받는사람", "주문자", "배송")
CHANGE_WORDS = ("변경", "취소", "말고", "대신", "수정", "바꿔", "아니고")
SAFE_SHORT_TAIL_PREFIXES = ("네", "넵", "넹", "감사", "확인", "수고")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def compact(value: Any) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]+", "", str(value or ""))


def digits(value: Any) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def normalize_catalog(value: Any) -> dict[str, list[str]]:
    if isinstance(value, list):
        return {
            str(entry["상품명"]): list(entry.get("옵션") or [])
            for entry in value
            if isinstance(entry, dict) and entry.get("상품명")
        }
    if isinstance(value, dict):
        return {str(product): list(options or []) for product, options in value.items()}
    return {}


def is_seller(user: Any) -> bool:
    return any(marker in str(user or "") for marker in SELLER_MARKERS)


def safe_tail_message(message: Any) -> bool:
    text = str(message or "").strip()
    if not text:
        return True
    normalized = compact(text)
    if any(word in text for word in CHANGE_WORDS) or any(char.isdigit() for char in text):
        return False
    if len(normalized) <= 12 and normalized.startswith(SAFE_SHORT_TAIL_PREFIXES):
        return True
    canned = (
        "주문내역확인" in normalized
        and ("송장" in normalized or "운송장" in normalized)
    )
    return canned


def unexplained_marker_text(marker: str, items: list[dict[str, Any]]) -> str:
    remainder = marker.replace("[주문완료]", "", 1)
    for item in items:
        for value in (item.get("raw_product"), item.get("raw_option")):
            if value:
                remainder = remainder.replace(str(value), "", 1)
        volume = item.get("volume")
        if volume is not None:
            remainder = re.sub(
                rf"(?<!\d){re.escape(str(volume))}(?!\d)", "", remainder, count=1
            )
    remainder = re.sub(
        r"주문\s*감사(?:합니다|드려요)?|"
        r"주문내역\s*확인(?:해주세요|부탁드려요)?|총|개",
        "",
        remainder,
    )
    return compact(remainder)


def recipient_name_supported(chat: list[Any], target: dict[str, Any]) -> bool:
    name = target.get("order_name")
    if not name:
        return True
    phone = digits(target.get("phone_number"))
    address = compact(target.get("address"))
    for message in chat:
        if not isinstance(message, dict):
            continue
        text = str(message.get("message") or "")
        if str(name) not in text:
            continue
        if any(keyword in text for keyword in NAME_EVIDENCE):
            return True
        if phone and phone in digits(text):
            return True
        if address and address in compact(text):
            return True
    return False


def classify_row(row: dict[str, str]) -> tuple[str, list[str], str]:
    chat = json.loads(row["chat_json"])
    prediction = json.loads(row["predicted_json"])
    catalog = json.loads(row["catalog_json"])
    chat_hash = canonical_json_hash(chat)
    target, warnings = normalize_prediction(prediction)
    reasons = list(warnings)
    items = (target or {}).get("items") or []
    markers = [
        (position, message)
        for position, message in enumerate(chat)
        if isinstance(message, dict)
        and "[주문완료]" in str(message.get("message") or "")
    ]

    if not items:
        reasons.append("empty_items_not_human_confirmed")
    if not markers:
        reasons.append("no_exact_completed_marker")
    if target:
        target_for_validation = {
            key: target.get(key)
            for key in ("order_name", "phone_number", "address", "items")
        }
        if validate_gold_target(chat, target_for_validation):
            reasons.append("source_field_validation_failed")

    if markers and items:
        marker_position, marker_message = markers[-1]
        marker = str(marker_message.get("message") or "")
        if any(
            not item.get("raw_product")
            or str(item.get("raw_product")) not in marker
            or (
                item.get("raw_option")
                and str(item.get("raw_option")) not in marker
            )
            for item in items
        ):
            reasons.append("items_not_fully_in_last_marker")
        if unexplained_marker_text(marker, items):
            reasons.append("unexplained_last_marker_text")
        if any(
            not safe_tail_message(message.get("message"))
            for message in chat[marker_position + 1 :]
            if isinstance(message, dict)
        ):
            reasons.append("meaningful_message_after_last_marker")

    if target and not recipient_name_supported(chat, target):
        reasons.append("recipient_name_not_explicitly_supported")

    if target and items:
        index = CatalogIndex.build(normalize_catalog(catalog))
        for item in items:
            resolved = resolve_catalog_item(
                item.get("raw_product"),
                item.get("raw_option"),
                item.get("volume"),
                index,
            )
            if resolved.mapping_status not in {"exact", "alias"}:
                reasons.append("catalog_not_exact_or_alias")
                break

    unique_reasons = sorted(set(reasons))
    return (
        "accepted_strict" if not unique_reasons else "quarantined",
        unique_reasons,
        chat_hash,
    )


def review_all(
    source: Path,
    queue_path: Path,
    existing_reviews_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    queue = read_jsonl(queue_path)
    queue_ids = {item["survivor_id"] for item in queue}
    existing_reviews = read_jsonl(existing_reviews_path)
    existing_ids = {item["survivor_id"] for item in existing_reviews}
    target_ids = queue_ids - existing_ids

    rows: dict[str, dict[str, str]] = {}
    existing_chat_hashes: set[str] = set()
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            record_id = row.get("id")
            if record_id in target_ids:
                rows[record_id] = row
            if record_id in existing_ids:
                existing_chat_hashes.add(
                    canonical_json_hash(json.loads(row["chat_json"]))
                )
    missing = sorted(target_ids - set(rows))
    if missing:
        raise ValueError(f"queued source ids not found: {missing[:10]}")

    preliminary: list[dict[str, Any]] = []
    accepted_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record_id in sorted(target_ids):
        decision, reasons, chat_hash = classify_row(rows[record_id])
        item = {
            "survivor_id": record_id,
            "canonical_chat_hash": chat_hash,
            "decision": decision,
            "reasons": reasons,
            "reviewer": "strict_full_dataset_validator_v1",
        }
        preliminary.append(item)
        if decision == "accepted_strict":
            accepted_by_hash[chat_hash].append(item)

    for chat_hash, duplicates in accepted_by_hash.items():
        ordered = sorted(duplicates, key=lambda item: item["survivor_id"])
        if chat_hash in existing_chat_hashes:
            keep = None
        else:
            keep = ordered[0]
            existing_chat_hashes.add(chat_hash)
        for item in ordered:
            if item is keep:
                continue
            item["decision"] = "excluded_duplicate_content"
            item["reasons"] = ["duplicate_content_of_accepted"]

    decisions = sorted(preliminary, key=lambda item: item["survivor_id"])
    strict_reviews = [
        {
            "survivor_id": item["survivor_id"],
            "decision": "accepted_strict",
            "reviewer": item["reviewer"],
            "review_scope": "full_chat_deterministic_evidence",
            "notes": ["all_strict_acceptance_rules_passed"],
        }
        for item in decisions
        if item["decision"] == "accepted_strict"
    ]
    combined_reviews = existing_reviews + strict_reviews
    reason_counts = Counter(
        reason for item in decisions for reason in item.get("reasons") or []
    )
    decision_counts = Counter(item["decision"] for item in decisions)
    summary = {
        "queued_rows": len(queue_ids),
        "already_reviewed_rows": len(queue_ids & existing_ids),
        "newly_dispositioned_rows": len(decisions),
        "decision_counts": dict(sorted(decision_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "combined_accepted_reviews": len(combined_reviews),
        "unreviewed_rows": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, payload in (
        ("all_decisions.jsonl", decisions),
        ("strict_accepted_reviews.jsonl", strict_reviews),
        ("combined_reviews.jsonl", combined_reviews),
        (
            "quarantine.jsonl",
            [item for item in decisions if item["decision"] == "quarantined"],
        ),
        (
            "duplicate_exclusions.jsonl",
            [
                item
                for item in decisions
                if item["decision"] == "excluded_duplicate_content"
            ],
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
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--existing-reviews", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = review_all(
        args.source, args.queue, args.existing_reviews, args.output_dir
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
