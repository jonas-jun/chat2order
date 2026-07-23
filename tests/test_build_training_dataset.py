import csv
import json

from build_training_dataset import build_inventory, normalize_prediction


FIELDNAMES = [
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


def _row(record_id, *, verified="false", status="unreviewed", corrected=""):
    return {
        "id": record_id,
        "created_at": "2026-01-01T00:00:00",
        "chat_filename": "sample.csv",
        "catalog_json": json.dumps([{"상품명": "가방", "옵션": ["검정"]}]),
        "chat_json": json.dumps(
            [
                {"user": "customer", "message": "홍길동 010-1234-5678 서울시 중구 예시로 1"},
                {"user": "seller", "message": "[주문완료] 가방 검정 1"},
            ]
        ),
        "predicted_json": json.dumps(
            {
                "order_name": "홍길동",
                "phone_number": "01012345678",
                "address": "서울시 중구 예시로 1",
                "items": [{"raw_product": "가방", "raw_option": "검정", "volume": 1}],
            }
        ),
        "is_verified": verified,
        "user_id": "",
        "job_id": "",
        "corrected_json": corrected,
        "label_status": status,
        "reviewed_at": "",
        "reviewer_id": "",
        "error_tags": "",
        "confirmed_revision": "",
    }


def _write_source(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def test_verified_gold_survives_duplicate_and_is_not_copied_to_artifacts(tmp_path):
    corrected = json.dumps(
        {
            "order_name": "홍길동",
            "phone_number": "01012345678",
            "address": "서울시 중구 예시로 1",
            "items": [{"raw_product": "가방", "raw_option": "검정", "volume": 1}],
        }
    )
    source = tmp_path / "source.csv"
    output = tmp_path / "output"
    _write_source(
        source,
        [
            _row("silver"),
            _row("gold", verified="true", status="accepted", corrected=corrected),
        ],
    )

    summary = build_inventory(source, output)

    assert summary["duplicate_rows_removed"] == 1
    assert summary["survivor_label_tier_counts"] == {"gold": 1}
    manifest = json.loads((output / "manifest.jsonl").read_text())
    assert manifest["survivor_id"] == "gold"
    all_output = "".join(path.read_text() for path in output.iterdir())
    assert "010-1234-5678" not in all_output
    assert "서울시 중구 예시로 1" not in all_output


def test_legacy_prediction_is_silver_and_prioritized_for_review(tmp_path):
    row = _row("legacy")
    row["predicted_json"] = json.dumps(
        [{"product": "가방", "option": "검정", "volume": 1}]
    )
    source = tmp_path / "source.csv"
    output = tmp_path / "output"
    _write_source(source, [row])

    summary = build_inventory(source, output)

    assert summary["survivor_prediction_schema_counts"] == {"rows_v1": 1}
    queue = json.loads((output / "review_queue.jsonl").read_text())
    assert "legacy_prediction_schema" in queue["reasons"]


def test_legacy_normalization_does_not_invent_raw_values():
    normalized, warnings = normalize_prediction(
        [
            {
                "order_name": "홍길동",
                "product": "canonical 상품",
                "option": "검정",
                "volume": 1,
            }
        ]
    )

    assert normalized["items"] == [
        {
            "raw_product": None,
            "raw_option": None,
            "volume": 1,
            "legacy_product_hint": "canonical 상품",
            "legacy_option_hint": "검정",
        }
    ]
    assert warnings == ["legacy_rows_require_raw_reconstruction"]
