"""Pure data validation and prompt formatting used by training and inference."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Sequence


TARGET_FIELDS = {"order_name", "phone_number", "address", "items"}
ITEM_FIELDS = {"raw_product", "raw_option", "volume"}

INSTRUCTION = """당신은 쇼핑몰 메신저 대화에서 최종 확정 주문을 추출하는 시스템입니다.

반드시 설명이나 Markdown 없이 아래 스키마의 JSON 객체 하나만 출력하세요.
{"order_name":string|null,"phone_number":string|null,"address":string|null,"items":[{"raw_product":string|null,"raw_option":string|null,"volume":integer|null}]}

규칙:
1. [주문완료]가 여러 번이면 가장 마지막 [주문완료]의 상품, 옵션, 수량만 items로 추출합니다.
2. [주문완료]가 없으면 대화 전체에서 입금 완료 등으로 최종 확정된 주문만 추출합니다.
3. 주문 변경과 취소를 반영하고, 상담이나 가격 문의만 한 상품은 제외합니다.
4. order_name은 배송받을 실제 이름이 대화에 명시된 경우만 적습니다. 입금자명이나 채팅 표시명을 대신 쓰지 않습니다.
5. 이름, 전화번호, 주소는 대화 전체에서 찾고, 알 수 없는 값은 null로 둡니다.
6. raw_product와 raw_option은 카탈로그명으로 매핑하지 말고 대화에 적힌 표현을 그대로 옮깁니다.
7. 수량은 양의 정수로 적습니다. 확정 주문이 없으면 items는 빈 배열입니다."""


def validate_example(example: Mapping[str, Any]) -> None:
    """Raise ValueError when an exported SFT row violates the expected contract."""

    unknown = set(example) - {"messages", "target"}
    missing = {"messages", "target"} - set(example)
    if missing or unknown:
        raise ValueError(
            f"example fields mismatch (missing={sorted(missing)}, unknown={sorted(unknown)})"
        )

    messages = example["messages"]
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object")
        if not isinstance(message.get("user"), str):
            raise ValueError(f"messages[{index}].user must be a string")
        if not isinstance(message.get("message"), str):
            raise ValueError(f"messages[{index}].message must be a string")

    target = example["target"]
    if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
        raise ValueError("target must contain exactly order_name, phone_number, address, items")
    for field in ("order_name", "phone_number", "address"):
        if target[field] is not None and not isinstance(target[field], str):
            raise ValueError(f"target.{field} must be a string or null")
    if not isinstance(target["items"], list):
        raise ValueError("target.items must be a list")
    for index, item in enumerate(target["items"]):
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise ValueError(
                f"target.items[{index}] must contain exactly raw_product, raw_option, volume"
            )
        for field in ("raw_product", "raw_option"):
            if item[field] is not None and not isinstance(item[field], str):
                raise ValueError(f"target.items[{index}].{field} must be a string or null")
        volume = item["volume"]
        if volume is not None and (
            isinstance(volume, bool) or not isinstance(volume, int) or volume <= 0
        ):
            raise ValueError(f"target.items[{index}].volume must be a positive integer or null")


def canonical_target_json(target: Mapping[str, Any]) -> str:
    """Serialize the target deterministically with its semantic field order."""

    ordered = {
        "order_name": target["order_name"],
        "phone_number": target["phone_number"],
        "address": target["address"],
        "items": [
            {
                "raw_product": item["raw_product"],
                "raw_option": item["raw_option"],
                "volume": item["volume"],
            }
            for item in target["items"]
        ],
    }
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))


def build_user_content(messages: Sequence[Mapping[str, Any]]) -> str:
    """Build one portable user turn; Gemma does not require a system-role turn."""

    chat_json = json.dumps(
        [{"user": row["user"], "message": row["message"]} for row in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{INSTRUCTION}\n\n[고객 대화 내역]\n{chat_json}"


def chat_messages(example: Mapping[str, Any], *, include_answer: bool) -> list[dict[str, str]]:
    """Convert an exported row to the model's standard role/content chat format."""

    turns = [{"role": "user", "content": build_user_content(example["messages"])}]
    if include_answer:
        turns.append(
            {"role": "assistant", "content": canonical_target_json(example["target"])}
        )
    return turns


def tokenize_sft_example(
    example: Mapping[str, Any],
    tokenizer: Any,
    *,
    max_length: int,
    truncation: str = "error",
    prompt_head_tokens: int = 512,
    chat_template_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, list[int]]:
    """Tokenize one row and mask every prompt token from the language-model loss."""

    validate_example(example)
    template_kwargs = dict(chat_template_kwargs or {})
    prompt_ids = tokenizer.apply_chat_template(
        chat_messages(example, include_answer=False),
        tokenize=True,
        add_generation_prompt=True,
        return_dict=False,
        **template_kwargs,
    )
    full_ids = tokenizer.apply_chat_template(
        chat_messages(example, include_answer=True),
        tokenize=True,
        add_generation_prompt=False,
        return_dict=False,
        **template_kwargs,
    )
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError(
            "the tokenizer chat template does not produce a stable assistant prefix"
        )

    answer_ids = full_ids[len(prompt_ids) :]
    if not answer_ids:
        raise ValueError("assistant target produced no tokens")
    if len(answer_ids) >= max_length:
        raise ValueError(
            f"assistant target has {len(answer_ids)} tokens, exceeding max_length={max_length}"
        )

    if len(full_ids) > max_length:
        if truncation == "error":
            raise ValueError(
                f"example has {len(full_ids)} tokens, exceeding max_length={max_length}; "
                "increase max_length or set data.truncation=keep_ends"
            )
        if truncation != "keep_ends":
            raise ValueError("truncation must be 'error' or 'keep_ends'")
        prompt_budget = max_length - len(answer_ids)
        head_size = min(prompt_head_tokens, prompt_budget)
        tail_size = prompt_budget - head_size
        prompt_ids = prompt_ids[:head_size] + (prompt_ids[-tail_size:] if tail_size else [])

    input_ids = prompt_ids + answer_ids
    return {
        "input_ids": input_ids,
        "attention_mask": [1] * len(input_ids),
        "labels": [-100] * len(prompt_ids) + answer_ids,
    }


def iter_jsonl(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    """Parse non-empty JSONL lines with useful one-based line errors."""

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"line {line_number}: row must be a JSON object")
        yield row
