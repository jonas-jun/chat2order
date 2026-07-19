"""주문 원문(raw_product/raw_option)을 카탈로그와 대조해 결정적으로 매핑하는 resolver.

Streamlit이나 Gemini 클라이언트에 의존하지 않는 순수 함수/클래스로 구성한다.
LLM은 원문 추출만 담당하고, 최종 상품-옵션 확정은 이 모듈이 담당한다 (issue #61).
"""

import re
import unicodedata
from dataclasses import dataclass, field

from models import ResolvedProductItem

SINGLE_OPTION_VALUE = "단일상품"

# 운영 중 확인된 축약어/표기 차이를 등록하는 명시적 별칭 사전.
# 색상 등 의미가 다른 동의어(화이트→아이보리 등)는 유사도로 자동 처리하지 않고
# 반드시 이 사전에 명시적으로 등록해야 한다.
PRODUCT_ALIASES: dict[str, str] = {
    # "정규화된 원문": "카탈로그의 실제 상품명"
}

OPTION_ALIASES: dict[tuple[str, str], str] = {
    # ("상품명", "정규화된 원문 옵션"): "카탈로그의 실제 옵션"
}

# 편집 거리 1의 상대적 영향이 큰 짧은 이름은 오타 자동 확정 대상에서 제외한다.
SHORT_NAME_LEN_THRESHOLD = 3

_STATUS_RANK = {"exact": 0, "alias": 1, "typo": 2, "inferred": 3}


def normalize_catalog_text(value: str) -> str:
    """의미를 바꾸지 않는 표기 차이(유니코드, 대소문자, 공백, 단순 구분자)만 제거한다."""
    value = unicodedata.normalize("NFKC", value)
    value = value.casefold()
    return re.sub(r"[\s,_\-/]+", "", value)


def _levenshtein_leq_one(a: str, b: str) -> bool:
    """a, b의 편집 거리가 1 이하인지(삽입/삭제/변경 1회) 판정한다."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = sum(1 for x, y in zip(a, b) if x != y)
        return diffs == 1

    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        else:
            if skipped:
                return False
            skipped = True
            j += 1
    return True


@dataclass
class CatalogIndex:
    catalog: dict[str, list[str]]
    product_by_norm: dict[str, str] = field(default_factory=dict)
    # product -> {normalized_option: canonical_option}
    option_by_norm: dict[str, dict[str, str]] = field(default_factory=dict)
    # normalized_option -> [products containing that option]
    products_by_option_norm: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, catalog: dict[str, list[str]]) -> "CatalogIndex":
        index = cls(catalog=catalog)
        for product, options in catalog.items():
            norm_product = normalize_catalog_text(product)
            index.product_by_norm[norm_product] = product

            option_map: dict[str, str] = {}
            for option in options:
                norm_option = normalize_catalog_text(option)
                option_map[norm_option] = option
                index.products_by_option_norm.setdefault(norm_option, [])
                if product not in index.products_by_option_norm[norm_option]:
                    index.products_by_option_norm[norm_option].append(product)
            index.option_by_norm[product] = option_map
        return index


def _resolve_product(
    norm_product: str,
    norm_option: str | None,
    index: CatalogIndex,
) -> tuple[str | None, str | None, str | None, list[str]]:
    """상품명을 우선순위에 따라 매핑한다.

    반환: (product, status, reason, candidate_products)
    product가 None이면 미확정이며 reason/candidate_products에 근거가 담긴다.
    """
    # 1. 정확 일치
    if norm_product in index.product_by_norm:
        return index.product_by_norm[norm_product], "exact", None, []

    # 2. 명시적 별칭
    aliased = PRODUCT_ALIASES.get(norm_product)
    if aliased and aliased in index.catalog:
        return aliased, "alias", None, []

    # 3. 한 글자 오타 (정확 일치·별칭이 없을 때만)
    if len(norm_product) > SHORT_NAME_LEN_THRESHOLD:
        typo_candidates = [
            canonical
            for norm, canonical in index.product_by_norm.items()
            if len(norm) > SHORT_NAME_LEN_THRESHOLD
            and _levenshtein_leq_one(norm_product, norm)
        ]
        if len(typo_candidates) == 1:
            return typo_candidates[0], "typo", None, []
        if len(typo_candidates) > 1:
            if norm_option:
                narrowed = [
                    c
                    for c in typo_candidates
                    if norm_option in index.option_by_norm.get(c, {})
                ]
                if len(narrowed) == 1:
                    return narrowed[0], "typo", None, []
            return (
                None,
                None,
                "상품명 한 글자 오타 후보가 여러 개",
                typo_candidates,
            )

    # 4. 축약 상품명 + 옵션 결합 (원문 상품명이 카탈로그 상품명에 포함되는 경우)
    substring_candidates = [
        canonical
        for norm, canonical in index.product_by_norm.items()
        if norm_product and norm_product in norm
    ]
    if substring_candidates:
        if norm_option:
            narrowed = [
                c
                for c in substring_candidates
                if norm_option in index.option_by_norm.get(c, {})
            ]
            if len(narrowed) == 1:
                return narrowed[0], "inferred", None, []
            if len(narrowed) > 1:
                return (
                    None,
                    None,
                    "축약 상품명과 옵션이 모두 일치하는 후보가 여러 개",
                    narrowed,
                )
            return (
                None,
                None,
                "축약 상품명 후보 중 원문 옵션과 일치하는 후보가 없음",
                substring_candidates,
            )
        return (
            None,
            None,
            "축약 상품명 후보가 여러 개이며 옵션 정보로 좁힐 수 없음",
            substring_candidates,
        )

    return None, None, "일치하는 상품 후보를 찾지 못함", []


def _resolve_option(
    product: str,
    norm_option: str | None,
    index: CatalogIndex,
) -> tuple[str | None, str | None, str | None]:
    """상품이 확정된 뒤, 해당 상품의 옵션 목록 안에서만 옵션을 결정한다.

    반환: (option, status, reason)
    """
    options = index.catalog.get(product, [])

    # 단일상품처럼 실질적인 옵션이 없는 경우
    if not options:
        return None, "exact", None
    if options == [SINGLE_OPTION_VALUE]:
        return SINGLE_OPTION_VALUE, "exact", None

    if not norm_option:
        return None, None, f"'{product}'의 옵션을 원문에서 확인할 수 없음"

    option_map = index.option_by_norm.get(product, {})

    # 1. 정규화 후 정확 일치
    if norm_option in option_map:
        return option_map[norm_option], "exact", None

    # 2. 상품별 명시적 옵션 별칭
    aliased = OPTION_ALIASES.get((product, norm_option))
    if aliased and aliased in options:
        return aliased, "alias", None

    # 3. 한 글자 오타이며 유일한 후보
    if len(norm_option) > SHORT_NAME_LEN_THRESHOLD:
        typo_candidates = [
            canonical
            for norm, canonical in option_map.items()
            if len(norm) > SHORT_NAME_LEN_THRESHOLD
            and _levenshtein_leq_one(norm_option, norm)
        ]
        if len(typo_candidates) == 1:
            return typo_candidates[0], "typo", None
        if len(typo_candidates) > 1:
            return None, None, f"'{product}'에서 옵션 한 글자 오타 후보가 여러 개"

    return None, None, f"'{product}'에 해당 옵션이 없음"


def resolve_catalog_item(
    raw_product: str | None,
    raw_option: str | None,
    volume: int | None,
    index: CatalogIndex,
) -> ResolvedProductItem:
    """raw_product/raw_option을 카탈로그와 대조해 최종 product/option을 결정한다."""
    if not raw_product or not raw_product.strip():
        return ResolvedProductItem(
            raw_product=raw_product,
            raw_option=raw_option,
            product=None,
            option=None,
            volume=volume,
            mapping_status="unresolved",
            mapping_reason="원문 상품명이 비어 있음",
        )

    norm_product = normalize_catalog_text(raw_product)
    norm_option = normalize_catalog_text(raw_option) if raw_option else None

    product, product_status, product_reason, candidates = _resolve_product(
        norm_product, norm_option, index
    )
    if product is None:
        return ResolvedProductItem(
            raw_product=raw_product,
            raw_option=raw_option,
            product=None,
            option=None,
            volume=volume,
            mapping_status="unresolved",
            mapping_reason=product_reason,
            candidate_products=candidates,
        )

    option, option_status, option_reason = _resolve_option(product, norm_option, index)
    if option is None and index.catalog.get(product):
        # 정확 일치 상품이 존재하면 옵션이 안 맞아도 다른 상품으로 바꾸지 않고
        # 상품은 유지한 채 옵션 불일치로 표시한다.
        return ResolvedProductItem(
            raw_product=raw_product,
            raw_option=raw_option,
            product=product,
            option=None,
            volume=volume,
            mapping_status="unresolved",
            mapping_reason=option_reason,
            candidate_products=[product],
        )

    final_status = (
        product_status
        if _STATUS_RANK[product_status] >= _STATUS_RANK[option_status]
        else option_status
    )

    resolved = ResolvedProductItem(
        raw_product=raw_product,
        raw_option=raw_option,
        product=product,
        option=option,
        volume=volume,
        mapping_status=final_status,
        mapping_reason=product_reason or option_reason,
    )

    # 저장 전 불변 조건 재검증: 위반 시 unresolved로 강등한다.
    catalog_options = index.catalog.get(resolved.product, [])
    option_valid = resolved.option in catalog_options or (
        not catalog_options and resolved.option is None
    )
    if resolved.product not in index.catalog or not option_valid:
        resolved.mapping_status = "unresolved"
        resolved.mapping_reason = "최종 상품-옵션 조합이 카탈로그에 존재하지 않음"
        resolved.candidate_products = [resolved.product]

    return resolved
