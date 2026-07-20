"""주문 검수 스냅샷의 생성, 검증, XLSX/학습 정답 변환.

Streamlit과 Supabase에 의존하지 않는 순수 함수로 구성한다. 최초 모델 예측은
변경하지 않고, 사용자가 편집한 결과를 revision 스냅샷으로 저장하는 것이 목적이다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from resolver import CatalogIndex, resolve_catalog_item


COMPLETED_FILE_STATUSES = {
    "accepted",
    "corrected",
    "business_corrected",
    "no_order_confirmed",
}

AUTO_CONFIRMED_MAPPING_STATUSES = {"exact", "alias"}


def decode_json(value: Any, default=None):
    """DB의 JSON 문자열/jsonb 값을 동일한 Python 값으로 정규화한다."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default
    return value


def normalize_catalog(value: Any) -> dict[str, list[str]]:
    catalog = decode_json(value, {})
    if isinstance(catalog, list):
        return {
            str(entry["상품명"]): list(entry.get("옵션") or [])
            for entry in catalog
            if isinstance(entry, dict) and entry.get("상품명")
        }
    if isinstance(catalog, dict):
        return {
            str(product): list(options or [])
            for product, options in catalog.items()
        }
    return {}


def build_initial_snapshot(
    job_id: str,
    training_records: list[dict],
    catalog: dict[str, list[str]],
    existing_orders: list[dict] | None = None,
    *,
    filename_prefix: str = "",
) -> dict:
    """파일별 모델 예측을 검수 가능한 작업 스냅샷으로 변환한다."""
    index = CatalogIndex.build(catalog)
    orders_by_chat: dict[str, list[dict]] = {}
    for order in existing_orders or []:
        orders_by_chat.setdefault(str(order.get("chat_name") or ""), []).append(order)

    files = []
    sorted_records = sorted(
        training_records,
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("chat_filename") or "")),
    )
    for record in sorted_records:
        predicted = decode_json(record.get("predicted_json"), {}) or {}
        filename = str(record.get("chat_filename") or "")
        chat_name = _chat_name(filename, filename_prefix)
        stored_orders = orders_by_chat.get(chat_name, [])
        first_order = stored_orders[0] if stored_orders else {}
        items = []
        for position, item in enumerate(predicted.get("items") or []):
            resolved = resolve_catalog_item(
                raw_product=item.get("raw_product"),
                raw_option=item.get("raw_option"),
                volume=_positive_int_or_none(item.get("volume")),
                index=index,
            )
            review_item = {
                "row_id": f"{record.get('id')}:{position}",
                "source_position": position,
                "order_number": _matching_order_number(stored_orders, resolved),
                "raw_product": resolved.raw_product,
                "raw_option": resolved.raw_option,
                "product": resolved.product,
                "option": resolved.option,
                "volume": resolved.volume,
                "mapping_status": resolved.mapping_status,
                "mapping_reason": resolved.mapping_reason,
            }
            review_item["requires_review"] = item_requires_review(review_item)
            items.append(review_item)

        file_review = {
            "training_data_id": str(record.get("id")),
            "chat_filename": filename,
            "chat_name": chat_name,
            "review_status": "unreviewed",
            "order_name": predicted.get("order_name"),
            "phone_number": predicted.get("phone_number"),
            "address": predicted.get("address"),
            "search_address": predicted.get("search_address"),
            "zip_code": first_order.get("zip_code"),
            "no_order_confirmed": False,
            "items": items,
        }
        reasons = detect_review_reasons(file_review, record.get("chat_json"))
        file_review["review_required"] = bool(reasons)
        file_review["review_reasons"] = reasons
        file_review["source_label_hash"] = label_fingerprint(file_review)
        file_review["source_business_hash"] = business_fingerprint(file_review)
        files.append(file_review)

    return {
        "schema_version": 1,
        "job_id": str(job_id),
        "base_revision": 0,
        "files": files,
    }


def item_requires_review(item: dict) -> bool:
    """Resolver가 확정하지 못했거나 자동 보정한 item인지 판정한다."""
    return (
        item.get("mapping_status") not in AUTO_CONFIRMED_MAPPING_STATUSES
        or _positive_int_or_none(item.get("volume")) is None
    )


def detect_review_reasons(file_review: dict, chat_data: Any) -> list[str]:
    """일반 사용자에게 보여줄 명백한 불확정 사유를 반환한다."""
    reasons: list[str] = []
    items = file_review.get("items") or []
    messages = decode_json(chat_data, []) or []
    text = "\n".join(
        str(message.get("message") or "")
        if isinstance(message, dict)
        else str(message)
        for message in messages
    )
    if "[주문완료]" in text and not items:
        reasons.append("[주문완료] 메시지가 있지만 주문 항목이 추출되지 않음")
    if any(item.get("mapping_status") == "unresolved" for item in items):
        reasons.append("상품 또는 옵션을 카탈로그에 자동 매핑하지 못함")
    if any(item.get("mapping_status") in {"typo", "inferred"} for item in items):
        reasons.append("오타·축약어 자동 보정 결과 확인 필요")
    if any(_positive_int_or_none(item.get("volume")) is None for item in items):
        reasons.append("수량이 없거나 올바른 정수가 아님")

    positions_by_key: dict[tuple, list[int]] = {}
    for position, item in enumerate(items):
        key = (
            _none_if_blank(item.get("raw_product")),
            _none_if_blank(item.get("raw_option")),
            _positive_int_or_none(item.get("volume")),
        )
        positions_by_key.setdefault(key, []).append(position)
    duplicate_positions = {
        position
        for positions in positions_by_key.values()
        if len(positions) > 1
        for position in positions
    }
    if duplicate_positions:
        reasons.append("동일한 주문 항목이 중복 추출됨")
        for position in duplicate_positions:
            items[position]["requires_review"] = True
    return reasons


def ensure_review_metadata(snapshot: dict, training_records: list[dict]) -> dict:
    """이전 draft/revision에도 현재 불확정 판정 메타데이터를 채운다."""
    records = {str(record.get("id")): record for record in training_records}
    for file_review in snapshot.get("files") or []:
        for position, item in enumerate(file_review.get("items") or []):
            item.setdefault("source_position", position)
            item.setdefault("requires_review", item_requires_review(item))
        record = records.get(str(file_review.get("training_data_id")), {})
        reasons = detect_review_reasons(file_review, record.get("chat_json"))
        file_review.setdefault("review_required", bool(reasons))
        file_review.setdefault("review_reasons", reasons)
    return snapshot


def next_pending_review_file_id(snapshot: dict, current_id: str) -> str | None:
    """현재 파일 다음에 있는 미완료 보완 파일 ID를 반환한다."""
    files = [
        file_review
        for file_review in snapshot.get("files") or []
        if file_review.get("review_required")
    ]
    if not files:
        return None
    current_index = next(
        (
            index
            for index, file_review in enumerate(files)
            if file_review.get("training_data_id") == current_id
        ),
        -1,
    )
    ordered = files[current_index + 1 :] + files[: current_index + 1]
    return next(
        (
            file_review.get("training_data_id")
            for file_review in ordered
            if file_review.get("review_status") not in COMPLETED_FILE_STATUSES
        ),
        None,
    )


def label_fingerprint(file_review: dict) -> str:
    """모델이 학습할 필드만 비교하는 안정적인 해시."""
    payload = corrected_json_from_file(file_review)
    return _hash_json(payload)


def business_fingerprint(file_review: dict) -> str:
    """XLSX에 영향을 주는 canonical 필드까지 포함한 비교 해시."""
    payload = {
        "order_name": _none_if_blank(file_review.get("order_name")),
        "phone_number": _none_if_blank(file_review.get("phone_number")),
        "address": _none_if_blank(file_review.get("address")),
        "search_address": _none_if_blank(file_review.get("search_address")),
        "zip_code": _none_if_blank(file_review.get("zip_code")),
        "items": [
            {
                "raw_product": _none_if_blank(item.get("raw_product")),
                "raw_option": _none_if_blank(item.get("raw_option")),
                "product": _none_if_blank(item.get("product")),
                "option": _none_if_blank(item.get("option")),
                "volume": _positive_int_or_none(item.get("volume")),
            }
            for item in file_review.get("items") or []
        ],
    }
    return _hash_json(payload)


def classify_file_review(file_review: dict, chat_data: Any) -> str:
    """완료된 파일을 Gold 사용 가능 여부에 맞는 상태로 분류한다."""
    items = file_review.get("items") or []
    if file_review.get("no_order_confirmed") and not items:
        return "no_order_confirmed"

    source_verified = raw_values_exist_in_chat(file_review, chat_data)
    label_changed = label_fingerprint(file_review) != file_review.get("source_label_hash")
    business_changed = (
        business_fingerprint(file_review) != file_review.get("source_business_hash")
    )
    if not source_verified:
        return "business_corrected"
    if label_changed:
        return "corrected"
    if business_changed:
        return "business_corrected"
    return "accepted"


def raw_values_exist_in_chat(file_review: dict, chat_data: Any) -> bool:
    """학습 대상 값이 실제 채팅에 존재하는지 확인한다."""
    messages = decode_json(chat_data, []) or []
    text = "\n".join(
        str(message.get("message") or "")
        if isinstance(message, dict)
        else str(message)
        for message in messages
    )
    order_name = _none_if_blank(file_review.get("order_name"))
    phone = _none_if_blank(file_review.get("phone_number"))
    address = _none_if_blank(file_review.get("address"))
    if order_name and order_name not in text:
        return False
    if phone:
        phone_digits = re.sub(r"\D", "", str(phone))
        text_digits = re.sub(r"\D", "", text)
        if phone_digits not in text_digits:
            return False
    if address and _compact_whitespace(address) not in _compact_whitespace(text):
        return False
    for item in file_review.get("items") or []:
        raw_product = _none_if_blank(item.get("raw_product"))
        raw_option = _none_if_blank(item.get("raw_option"))
        if not raw_product or raw_product not in text:
            return False
        if raw_option and raw_option not in text:
            return False
    return True


def validate_file_review(
    file_review: dict,
    catalog: dict[str, list[str]],
    *,
    require_decision: bool = False,
) -> list[str]:
    errors: list[str] = []
    items = file_review.get("items") or []
    no_order = bool(file_review.get("no_order_confirmed"))

    if no_order and items:
        errors.append("주문 없음 확인과 주문 항목을 동시에 저장할 수 없습니다.")
    if require_decision and not no_order and not items:
        errors.append("주문 항목을 추가하거나 '주문 없음'을 확인해 주세요.")
    zip_code = _none_if_blank(file_review.get("zip_code"))
    if zip_code and not re.fullmatch(r"\d{5}", str(zip_code)):
        errors.append("우편번호는 숫자 5자리여야 합니다.")

    seen = set()
    for position, item in enumerate(items, start=1):
        product = _none_if_blank(item.get("product"))
        option = _none_if_blank(item.get("option"))
        volume = _positive_int_or_none(item.get("volume"))
        prefix = f"{position}번째 주문"

        if not product:
            errors.append(f"{prefix}: 상품명을 선택해 주세요.")
        elif product not in catalog:
            errors.append(f"{prefix}: 카탈로그에 없는 상품입니다: {product}")
        else:
            options = catalog.get(product, [])
            if options and option not in options:
                errors.append(f"{prefix}: '{product}'에 없는 옵션입니다: {option or '(없음)'}")
            if not options and option:
                errors.append(f"{prefix}: 옵션이 없는 상품에는 옵션을 지정할 수 없습니다.")

        if volume is None:
            errors.append(f"{prefix}: 수량은 1 이상의 정수여야 합니다.")

        key = (product, option, volume, _none_if_blank(item.get("raw_product")))
        if key in seen:
            errors.append(f"{prefix}: 동일한 주문 항목이 중복되었습니다.")
        seen.add(key)

    if require_decision and file_review.get("review_status") not in COMPLETED_FILE_STATUSES:
        errors.append("파일 검수를 완료해 주세요.")
    return errors


def validate_snapshot(snapshot: dict, catalog: dict[str, list[str]]) -> list[str]:
    errors = []
    files = snapshot.get("files") or []
    if not files:
        return ["검수할 채팅 파일이 없습니다."]
    for file_review in files:
        for message in validate_file_review(
            file_review,
            catalog,
            require_decision=bool(file_review.get("review_required")),
        ):
            errors.append(f"{file_review.get('chat_filename')}: {message}")
    return errors


def renumber_snapshot(snapshot: dict, date_prefix: str | None = None) -> dict:
    """파일 순서대로 주문번호를 재부여한 복사본을 반환한다."""
    result = copy.deepcopy(snapshot)
    prefix = date_prefix or date.today().strftime("%Y%m%d")
    sequence = 1
    for file_review in result.get("files") or []:
        items = file_review.get("items") or []
        if not items:
            continue
        order_number = f"{prefix}{sequence:03d}"
        for item in items:
            item["order_number"] = order_number
        sequence += 1
    return result


def snapshot_to_orders(snapshot: dict) -> list[dict]:
    """확정 스냅샷을 DB/XLSX용 주문 행으로 펼친다."""
    rows = []
    for file_review in snapshot.get("files") or []:
        common = {
            "chat_name": file_review.get("chat_name"),
            "order_name": _none_if_blank(file_review.get("order_name")),
            "phone_number": _none_if_blank(file_review.get("phone_number")),
            "address": _none_if_blank(file_review.get("address")),
            "search_address": _none_if_blank(file_review.get("search_address")),
            "zip_code": _none_if_blank(file_review.get("zip_code")),
        }
        for item in file_review.get("items") or []:
            rows.append(
                {
                    "order_number": item.get("order_number"),
                    "product": _none_if_blank(item.get("product")),
                    "option": _none_if_blank(item.get("option")),
                    "volume": _positive_int_or_none(item.get("volume")),
                    "raw_product": _none_if_blank(item.get("raw_product")),
                    "raw_option": _none_if_blank(item.get("raw_option")),
                    "mapping_status": item.get("mapping_status") or "manual",
                    **common,
                }
            )
    return rows


def corrected_json_from_file(file_review: dict) -> dict:
    return {
        "order_name": _none_if_blank(file_review.get("order_name")),
        "phone_number": _none_if_blank(file_review.get("phone_number")),
        "address": _none_if_blank(file_review.get("address")),
        "items": [
            {
                "raw_product": _none_if_blank(item.get("raw_product")),
                "raw_option": _none_if_blank(item.get("raw_option")),
                "volume": _positive_int_or_none(item.get("volume")),
            }
            for item in file_review.get("items") or []
        ],
    }


def build_training_labels(snapshot: dict) -> list[dict]:
    """confirm RPC에 전달할 파일별 학습 라벨 payload를 만든다."""
    return [
        {
            "training_data_id": file_review["training_data_id"],
            "label_status": (
                file_review.get("review_status", "unreviewed")
                if file_review.get("review_required")
                else "auto_accepted"
            ),
            "corrected_json": corrected_json_from_file(file_review),
        }
        for file_review in snapshot.get("files") or []
    ]


def snapshot_hash(snapshot: dict) -> str:
    return _hash_json(snapshot)


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _positive_int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 1 or str(value).strip() not in (str(number), f"{number}.0"):
        return None
    return number


def _none_if_blank(value: Any):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _compact_whitespace(value: Any) -> str:
    return re.sub(r"\s+", "", str(value))


def _chat_name(filename: str, prefix: str) -> str:
    name = unicodedata.normalize("NFC", Path(filename).stem)
    normalized_prefix = unicodedata.normalize("NFC", prefix)
    if normalized_prefix and name.startswith(normalized_prefix):
        name = name[len(normalized_prefix):]
    else:
        name = re.sub(r"_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", name)
    return name or unicodedata.normalize("NFC", Path(filename).stem)


def _matching_order_number(stored_orders: list[dict], resolved) -> str | None:
    for order in stored_orders:
        if (
            order.get("raw_product") == resolved.raw_product
            and order.get("raw_option") == resolved.raw_option
            and _positive_int_or_none(order.get("volume")) == resolved.volume
        ):
            return order.get("order_number")
    return stored_orders[0].get("order_number") if stored_orders else None
