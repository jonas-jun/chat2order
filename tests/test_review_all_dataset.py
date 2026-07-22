import json

from review_all_dataset import (
    recipient_name_supported,
    safe_tail_message,
    unexplained_marker_text,
)


def test_marker_coverage_has_no_residual_for_complete_items():
    marker = "[주문완료]\n가방 검정 1\n지갑 갈색 2"
    items = [
        {"raw_product": "가방", "raw_option": "검정", "volume": 1},
        {"raw_product": "지갑", "raw_option": "갈색", "volume": 2},
    ]

    assert unexplained_marker_text(marker, items) == ""


def test_marker_coverage_detects_unexplained_item():
    marker = "[주문완료]\n가방 검정 1\n지갑 갈색 2"
    items = [{"raw_product": "가방", "raw_option": "검정", "volume": 1}]

    assert unexplained_marker_text(marker, items) == "지갑갈색2"


def test_depositor_name_without_recipient_evidence_is_not_supported():
    target = {
        "order_name": "홍길동",
        "phone_number": None,
        "address": None,
    }
    chat = [{"user": "customer", "message": "홍길동 입금완료"}]

    assert not recipient_name_supported(chat, target)


def test_tail_accepts_acknowledgement_but_rejects_change():
    assert safe_tail_message("네 감사합니다")
    assert not safe_tail_message("블랙으로 변경해주세요")
    assert not safe_tail_message("수량 2개입니다")
