import json

import pytest

from train.data import (
    build_user_content,
    canonical_target_json,
    tokenize_sft_example,
    validate_example,
)


def example():
    return {
        "messages": [
            {"user": "customer", "message": "가방 빨강 2개 [주문완료]"},
            {"user": "customer", "message": "김가람 010-0000-1234 학습로 1"},
        ],
        "target": {
            "order_name": "김가람",
            "phone_number": "010-0000-1234",
            "address": "학습로 1",
            "items": [{"raw_product": "가방", "raw_option": "빨강", "volume": 2}],
        },
    }


class FakeTokenizer:
    def apply_chat_template(self, messages, *, add_generation_prompt, **kwargs):
        assert kwargs["tokenize"] is True
        assert kwargs["return_dict"] is False
        prompt = [1, 2, 3, 4]
        if add_generation_prompt:
            return prompt
        assert messages[-1]["role"] == "assistant"
        return prompt + [5, 6, 7]


def test_target_json_has_stable_schema_order():
    result = canonical_target_json(example()["target"])
    assert list(json.loads(result)) == ["order_name", "phone_number", "address", "items"]
    assert "가방" in result


def test_prompt_contains_chat_but_not_target_mapping_fields():
    result = build_user_content(example()["messages"])
    assert "[주문완료]" in result
    assert "raw_product" in result  # output schema instruction
    assert '"user":"customer"' in result


def test_only_assistant_tokens_receive_loss():
    encoded = tokenize_sft_example(example(), FakeTokenizer(), max_length=20)
    assert encoded["input_ids"] == [1, 2, 3, 4, 5, 6, 7]
    assert encoded["labels"] == [-100, -100, -100, -100, 5, 6, 7]


def test_overlong_rows_fail_instead_of_silent_target_truncation():
    with pytest.raises(ValueError, match="exceeding max_length"):
        tokenize_sft_example(example(), FakeTokenizer(), max_length=6)


def test_invalid_volume_is_rejected():
    row = example()
    row["target"]["items"][0]["volume"] = 0
    with pytest.raises(ValueError, match="positive integer"):
        validate_example(row)
