from recover_quarantine_dataset import reconstruct_target


def test_reconstructs_raw_fields_from_exact_marker_hints():
    prediction = {
        "order_name": None,
        "phone_number": None,
        "address": None,
        "items": [
            {"product": "가방", "option": "검정", "volume": 1},
            {"product": "지갑", "option": "갈색", "volume": 2},
        ],
    }

    target, errors = reconstruct_target(
        prediction, "[주문완료]\n가방 검정 1\n지갑 갈색 2"
    )

    assert errors == []
    assert target["items"] == [
        {"raw_product": "가방", "raw_option": "검정", "volume": 1},
        {"raw_product": "지갑", "raw_option": "갈색", "volume": 2},
    ]


def test_reconstruction_rejects_unexplained_marker_text():
    prediction = {
        "items": [{"product": "가방", "option": "검정", "volume": 1}]
    }

    target, errors = reconstruct_target(
        prediction, "[주문완료]\n가방 검정 1\n지갑 갈색 2"
    )

    assert target is None
    assert errors == ["marker_not_fully_consumed_by_hints"]
