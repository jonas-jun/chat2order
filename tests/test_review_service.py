import copy

from review_service import (
    build_initial_snapshot,
    build_training_labels,
    classify_file_review,
    detect_review_reasons,
    item_requires_review,
    next_pending_review_file_id,
    renumber_snapshot,
    snapshot_to_orders,
    validate_file_review,
    validate_snapshot,
)


CATALOG = {
    "드래곤백": ["레드", "진브라운"],
    "드래곤 트리플백": ["카멜"],
}

TRAINING_RECORDS = [
    {
        "id": "td-1",
        "chat_filename": "이지픽_고객A.csv",
        "chat_json": [
            {"user": "customer", "message": "[주문완료] 드래곤백 레드 2"},
            {
                "user": "customer",
                "message": "홍길동 010-1234-5678 서울 중구 세종대로 1",
            },
        ],
        "predicted_json": {
            "order_name": "홍길동",
            "phone_number": "01012345678",
            "address": "서울 중구 세종대로 1",
            "search_address": "서울 중구 세종대로 1",
            "items": [
                {
                    "raw_product": "드래곤백",
                    "raw_option": "레드",
                    "volume": 2,
                }
            ],
        },
        "created_at": "2026-07-20T10:00:00",
    },
    {
        "id": "td-2",
        "chat_filename": "이지픽_고객B.csv",
        "chat_json": [{"user": "customer", "message": "가격만 알려주세요"}],
        "predicted_json": {
            "order_name": None,
            "phone_number": None,
            "address": None,
            "search_address": None,
            "items": [],
        },
        "created_at": "2026-07-20T10:01:00",
    },
]


def _snapshot():
    return build_initial_snapshot(
        "job-1",
        TRAINING_RECORDS,
        CATALOG,
        existing_orders=[
            {
                "chat_name": "고객A",
                "raw_product": "드래곤백",
                "raw_option": "레드",
                "volume": 2,
                "order_number": "20260720001",
                "zip_code": "01234",
            }
        ],
        filename_prefix="이지픽_",
    )


def test_build_initial_snapshot_resolves_items_and_preserves_zip_code():
    snapshot = _snapshot()
    first = snapshot["files"][0]

    assert first["chat_name"] == "고객A"
    assert first["zip_code"] == "01234"
    assert first["items"][0]["product"] == "드래곤백"
    assert first["items"][0]["option"] == "레드"
    assert first["review_status"] == "unreviewed"
    assert first["review_required"] is False
    assert first["items"][0]["requires_review"] is False


def test_accept_and_no_order_create_gold_labels():
    snapshot = _snapshot()
    first, second = snapshot["files"]
    first["review_status"] = classify_file_review(
        first, TRAINING_RECORDS[0]["chat_json"]
    )
    second["no_order_confirmed"] = True
    second["review_status"] = classify_file_review(
        second, TRAINING_RECORDS[1]["chat_json"]
    )

    assert first["review_status"] == "accepted"
    assert second["review_status"] == "no_order_confirmed"
    assert validate_snapshot(snapshot, CATALOG) == []
    labels = build_training_labels(snapshot)
    assert labels[0]["label_status"] == "auto_accepted"
    assert labels[0]["corrected_json"]["items"][0] == {
        "raw_product": "드래곤백",
        "raw_option": "레드",
        "volume": 2,
    }
    assert labels[1]["corrected_json"]["items"] == []


def test_inferred_and_unresolved_items_require_review():
    assert item_requires_review({"mapping_status": "inferred", "volume": 1})
    assert item_requires_review({"mapping_status": "unresolved", "volume": 1})
    assert not item_requires_review({"mapping_status": "exact", "volume": 1})


def test_order_complete_without_items_requires_review():
    file_review = {"items": []}
    reasons = detect_review_reasons(
        file_review, [{"message": "[주문완료] 드래곤백 레드 1"}]
    )

    assert any("추출되지 않음" in reason for reason in reasons)


def test_only_review_required_files_need_a_decision():
    snapshot = _snapshot()
    # 두 파일 모두 일반 자동확정/일반 no-order이므로 사용자 확인이 필요하지 않다.
    assert validate_snapshot(snapshot, CATALOG) == []


def test_next_pending_review_file_skips_completed_files():
    snapshot = {
        "files": [
            {
                "training_data_id": "a",
                "review_required": True,
                "review_status": "accepted",
            },
            {
                "training_data_id": "b",
                "review_required": True,
                "review_status": "corrected",
            },
            {
                "training_data_id": "c",
                "review_required": True,
                "review_status": "unreviewed",
            },
        ]
    }

    assert next_pending_review_file_id(snapshot, "a") == "c"
    snapshot["files"][2]["review_status"] = "accepted"
    assert next_pending_review_file_id(snapshot, "a") is None


def test_changed_raw_value_is_corrected_when_it_exists_in_chat():
    snapshot = _snapshot()
    file_review = snapshot["files"][0]
    file_review["items"][0]["raw_option"] = "진브라운"
    chat = [
        {"message": "[주문완료] 드래곤백 진브라운 2"},
        {"message": "홍길동 01012345678 서울 중구 세종대로 1"},
    ]

    assert classify_file_review(file_review, chat) == "corrected"


def test_canonical_only_change_is_not_used_as_gold():
    snapshot = _snapshot()
    file_review = snapshot["files"][0]
    file_review["items"][0]["product"] = "드래곤 트리플백"
    file_review["items"][0]["option"] = "카멜"

    assert (
        classify_file_review(file_review, TRAINING_RECORDS[0]["chat_json"])
        == "business_corrected"
    )


def test_validation_rejects_invalid_catalog_option_and_volume():
    file_review = copy.deepcopy(_snapshot()["files"][0])
    file_review["items"][0]["option"] = "없는색"
    file_review["items"][0]["volume"] = 0

    errors = validate_file_review(file_review, CATALOG)
    assert any("없는 옵션" in error for error in errors)
    assert any("1 이상의 정수" in error for error in errors)


def test_validation_rejects_invalid_zip_code():
    file_review = copy.deepcopy(_snapshot()["files"][0])
    file_review["zip_code"] = "123"

    assert "우편번호는 숫자 5자리여야 합니다." in validate_file_review(
        file_review, CATALOG
    )


def test_renumber_and_flatten_orders_by_file():
    snapshot = _snapshot()
    first, second = snapshot["files"]
    first["review_status"] = "accepted"
    second["items"] = [
        {
            "row_id": "new",
            "raw_product": "드래곤 트리플백",
            "raw_option": "카멜",
            "product": "드래곤 트리플백",
            "option": "카멜",
            "volume": 1,
            "mapping_status": "manual",
        }
    ]
    second["review_status"] = "corrected"

    numbered = renumber_snapshot(snapshot, "20260720")
    rows = snapshot_to_orders(numbered)
    assert [row["order_number"] for row in rows] == [
        "20260720001",
        "20260720002",
    ]
    assert rows[1]["chat_name"] == "고객B"
