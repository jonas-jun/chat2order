import pytest

from resolver import CatalogIndex, resolve_catalog_item

CATALOG = {
    "드래곤백": [
        "그린",
        "다크블루",
        "레드",
        "진브라운",
        "블랙",
    ],
    "드래곤 트리플백": ["다크브라운", "카멜"],
    "드래곤원통프리미엄": ["블랙", "베이지"],
    "꽈배기니트 반팔": ["네이비", "화이트", "블랙"],
    "심플머그컵": ["단일상품"],
}


@pytest.fixture
def index():
    return CatalogIndex.build(CATALOG)


def test_exact_match_priority(index):
    resolved = resolve_catalog_item("드래곤백", "레드", 1, index)
    assert resolved.product == "드래곤백"
    assert resolved.option == "레드"
    assert resolved.mapping_status == "exact"


def test_abbreviated_product_with_option_combination(index):
    resolved = resolve_catalog_item("드래곤", "진브라운", 1, index)
    assert resolved.product == "드래곤백"
    assert resolved.option == "진브라운"
    assert resolved.mapping_status == "inferred"


def test_one_char_typo_product(index):
    resolved = resolve_catalog_item("꽈배니 니트 반팔", "네이비", 1, index)
    assert resolved.product == "꽈배기니트 반팔"
    assert resolved.option == "네이비"
    assert resolved.mapping_status == "typo"


def test_long_product_name_exact_match_kept(index):
    resolved = resolve_catalog_item("드래곤 트리플백", "다크브라운", 1, index)
    assert resolved.product == "드래곤 트리플백"
    assert resolved.option == "다크브라운"
    assert resolved.mapping_status == "exact"


def test_invalid_product_option_combo_blocked(index):
    resolved = resolve_catalog_item("드래곤 트리플백", "레드", 1, index)
    assert resolved.mapping_status == "unresolved"


def test_ambiguous_abbreviation_blocked(index):
    resolved = resolve_catalog_item("드래곤", "블랙", 1, index)
    assert resolved.mapping_status == "unresolved"
    assert set(resolved.candidate_products) >= {"드래곤백", "드래곤원통프리미엄"}


def test_whitespace_and_hyphen_normalized(index):
    resolved = resolve_catalog_item("드래곤 백", "레드", 1, index)
    assert resolved.product == "드래곤백"
    assert resolved.mapping_status == "exact"


def test_option_one_char_typo_unique_candidate(index):
    resolved = resolve_catalog_item("드래곤백", "진브라온", 1, index)
    assert resolved.product == "드래곤백"
    assert resolved.option == "진브라운"
    assert resolved.mapping_status == "typo"


def test_option_one_char_typo_ambiguous_blocked():
    catalog = {"테스트상품": ["가나다", "가나라", "마바사"]}
    index = CatalogIndex.build(catalog)
    resolved = resolve_catalog_item("테스트상품", "가나차", 1, index)
    assert resolved.mapping_status == "unresolved"


def test_single_product_without_real_option(index):
    resolved = resolve_catalog_item("심플머그컵", None, 2, index)
    assert resolved.product == "심플머그컵"
    assert resolved.option == "단일상품"
    assert resolved.mapping_status == "exact"


def test_no_matching_product_at_all(index):
    resolved = resolve_catalog_item("존재하지않는상품명입니다", "아무옵션", 1, index)
    assert resolved.mapping_status == "unresolved"


def test_exact_product_wrong_option_kept_as_unresolved_not_switched(index):
    """정확 일치 상품이 있으면 옵션이 안 맞아도 다른 상품으로 바꾸지 않는다."""
    resolved = resolve_catalog_item("드래곤백", "카멜", 1, index)
    assert resolved.product == "드래곤백"
    assert resolved.mapping_status == "unresolved"
