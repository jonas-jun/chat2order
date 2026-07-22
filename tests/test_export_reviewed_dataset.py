import csv
import json

from export_reviewed_dataset import (
    export_reviewed,
    pseudonymize_chat,
    target_for_review,
)


def test_export_removes_source_identity_address_phone_and_account(tmp_path):
    source = tmp_path / "source.csv"
    chat = [
        {
            "user": "실제고객닉네임",
            "message": "실제고객닉네임 입금\n홍길동\n서울시 중구 실제로 1 101동 202호\n010 1234 5678",
        },
        {
            "user": "이지픽",
            "message": "입금 계좌 1002-6228-0301\n[주문완료]\n가방 검정 1",
        },
    ]
    prediction = {
        "order_name": "홍길동",
        "phone_number": "01012345678",
        "address": "서울시 중구 실제로 1 101동 202호",
        "items": [{"raw_product": "가방", "raw_option": "검정", "volume": 1}],
    }
    fields = [
        "id",
        "created_at",
        "chat_filename",
        "catalog_json",
        "chat_json",
        "predicted_json",
        "is_verified",
        "user_id",
        "job_id",
        "corrected_json",
        "label_status",
        "reviewed_at",
        "reviewer_id",
        "error_tags",
        "confirmed_revision",
    ]
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "id": "reviewed",
                "chat_filename": "private.csv",
                "catalog_json": "[]",
                "chat_json": json.dumps(chat),
                "predicted_json": json.dumps(prediction),
            }
        )
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text(
        json.dumps(
            {
                "survivor_id": "reviewed",
                "decision": "accepted_prediction",
                "review_scope": "full_chat",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = export_reviewed(source, reviews, tmp_path / "output")

    assert summary["exported_rows"] == 1
    result = (tmp_path / "output" / "reviewed_deidentified.jsonl").read_text()
    for sensitive in (
        "홍길동",
        "실제고객닉네임",
        "서울시 중구 실제로 1",
        "01012345678",
        "010 1234 5678",
        "1002-6228-0301",
    ):
        assert sensitive not in result
    example = json.loads(result)
    assert [message["user"] for message in example["messages"]] == [
        "customer",
        "seller",
    ]
    assert example["target"]["items"] == [
        {"raw_product": "가방", "raw_option": "검정", "volume": 1}
    ]


def test_corrected_decision_uses_corrected_json():
    corrected = {
        "order_name": None,
        "phone_number": None,
        "address": None,
        "items": [{"raw_product": "교정상품", "raw_option": None, "volume": 2}],
    }
    target, warnings = target_for_review(
        {
            "predicted_json": json.dumps({"items": []}),
            "corrected_json": json.dumps(corrected),
        },
        "accepted_corrected",
    )

    assert target == corrected
    assert warnings == []


def test_manual_correction_can_clear_incorrect_depositor_name():
    target, warnings = target_for_review(
        {
            "predicted_json": json.dumps(
                {
                    "order_name": "입금자명",
                    "phone_number": None,
                    "address": None,
                    "items": [
                        {"raw_product": "가방", "raw_option": "검정", "volume": 1}
                    ],
                }
            ),
            "corrected_json": "",
        },
        "corrected_prediction",
        {"order_name": None},
    )

    assert target["order_name"] is None
    assert warnings == []


def test_phone_pattern_supports_dot_separators():
    from export_reviewed_dataset import digit_flexible_pattern

    pattern = digit_flexible_pattern("01082242017")

    assert pattern.search("연락처: 010.8224.2017")
    assert pattern.search("연락처: 010ㅡ8224ㅡ2017")


def test_single_character_alias_does_not_corrupt_normal_words():
    chat, _ = pseudonymize_chat(
        [{"user": "정", "message": "결정하셔서 알려주세요"}],
        {"order_name": None, "phone_number": None, "address": None, "items": []},
        "0" * 64,
    )

    assert chat[0] == {"user": "customer", "message": "결정하셔서 알려주세요"}


def test_reconstructed_items_clear_original_missing_raw_warning():
    target, warnings = target_for_review(
        {
            "predicted_json": json.dumps(
                {
                    "order_name": None,
                    "phone_number": None,
                    "address": None,
                    "items": [{"product": "가방", "option": "검정", "volume": 1}],
                }
            ),
            "corrected_json": "",
        },
        "reconstructed_marker_hints",
        {
            "items": [
                {"raw_product": "가방", "raw_option": "검정", "volume": 1}
            ]
        },
    )

    assert warnings == []
    assert target["items"][0]["raw_product"] == "가방"


def test_address_pattern_allows_whitespace_inside_address_tokens():
    from export_reviewed_dataset import whitespace_flexible_pattern

    pattern = whitespace_flexible_pattern("아파트 102동 901호")

    assert pattern.search("아파트 102 동 901 호")


def test_name_replacement_allows_whitespace_inside_name():
    from export_reviewed_dataset import replace_sensitive_text

    result = replace_sensitive_text(
        "곽 혜경 주문",
        {"order_name": "곽혜경", "phone_number": None, "address": None},
        {"order_name": "김가람", "phone_number": None, "address": None},
    )

    assert result == "김가람 주문"
