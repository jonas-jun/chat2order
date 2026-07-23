import pytest

from train.evaluate import (
    _item_counts,
    _mcnemar_exact,
    _score_model,
    _target_equal,
    validate_prediction,
)


def target(*items, phone="010-1234-5678", address=" 서울  중구 "):
    return {
        "order_name": "홍길동",
        "phone_number": phone,
        "address": address,
        "items": list(items),
    }


def item(product, option, volume=1):
    return {"raw_product": product, "raw_option": option, "volume": volume}


def test_semantic_exact_ignores_item_order_but_preserves_duplicates():
    gold = target(item("A", "빨강"), item("B", None), item("A", "빨강"))
    reordered = target(item("A", "빨강"), item("A", "빨강"), item("B", None))
    missing_duplicate = target(item("A", "빨강"), item("B", None))

    assert _target_equal(gold, reordered, normalized=False)
    assert not _target_equal(gold, missing_duplicate, normalized=False)
    assert _item_counts(gold, missing_duplicate, normalized=False) == (2, 0, 1)


def test_normalization_is_limited_to_documented_rules():
    gold = target(item("가방", "레드"), phone="010-1234-5678", address="서울  중구")
    formatting = target(item("가방", "레드"), phone="01012345678", address=" 서울 중구 ")
    synonym = target(item("백", "레드"), phone="01012345678", address="서울 중구")

    assert not _target_equal(gold, formatting, normalized=False)
    assert _target_equal(gold, formatting, normalized=True)
    assert not _target_equal(gold, synonym, normalized=True)


@pytest.mark.parametrize(
    "prediction",
    [
        None,
        {"order_name": None, "phone_number": None, "address": None},
        target(item("A", None, 0)),
        target({"raw_product": "A", "raw_option": None, "volume": True}),
        target({"raw_product": "A", "raw_option": None, "volume": 1, "extra": 1}),
    ],
)
def test_schema_rejects_missing_fields_nonpositive_bool_and_extra_fields(prediction):
    assert validate_prediction(prediction) is not None


def test_mcnemar_exact_uses_discordant_pairs():
    left = [True, True, False, False]
    right = [False, True, True, False]
    assert _mcnemar_exact(left, right) == (1, 1, 1.0)


def test_schema_failure_counts_all_gold_items_as_false_negatives():
    example = {"messages": [{"user": "u", "message": "m"}], "target": target(item("A", None), item("B", None))}
    aggregate, _ = _score_model(
        [example],
        [{"row": 0, "prediction": None, "latency_ms": 1, "attempts": 1}],
    )

    assert aggregate["item_micro"] == {
        "tp": 0,
        "fp": 0,
        "fn": 2,
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
    }
