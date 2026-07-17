"""이슈 #61 회귀 테스트: 2026-07-12 '드래곤백' 오매핑 재발 방지.

실제 사고 사례:
  드래곤백 레드 1      -> (오매핑됨) 드래곤 트리플백 / 레드   (카탈로그에 없는 조합)
  드래곤 진브라운 1    -> (오매핑됨) 드래곤 트리플백 / 다크브라운

resolver를 거치면 항상 올바른 상품/옵션으로 확정되어야 한다.
"""

from resolver import CatalogIndex, resolve_catalog_item
from services import resolve_extracted_items

CATALOG = {
    "드래곤백": [
        "그린",
        "다크블루",
        "레드",
        "옐로우",
        "진브라운",
        "블랙",
    ],
    "드래곤 트리플백": ["다크브라운", "카멜"],
    "드래곤원통프리미엄": ["블랙", "베이지"],
}

CATALOG_LIST = [
    {"상품명": product, "옵션": options} for product, options in CATALOG.items()
]


def test_dragonbag_red_always_resolves_correctly():
    index = CatalogIndex.build(CATALOG)
    for _ in range(20):
        resolved = resolve_catalog_item("드래곤백", "레드", 1, index)
        assert resolved.product == "드래곤백"
        assert resolved.option == "레드"
        assert resolved.mapping_status == "exact"


def test_dragon_jinbrown_always_resolves_to_dragonbag():
    index = CatalogIndex.build(CATALOG)
    for _ in range(20):
        resolved = resolve_catalog_item("드래곤", "진브라운", 1, index)
        assert resolved.product == "드래곤백"
        assert resolved.option == "진브라운"
        assert resolved.mapping_status == "inferred"


def test_never_mismaps_to_triple_bag_or_cylinder_premium():
    index = CatalogIndex.build(CATALOG)
    for raw_product, raw_option in [("드래곤백", "레드"), ("드래곤", "진브라운")]:
        resolved = resolve_catalog_item(raw_product, raw_option, 1, index)
        assert resolved.product not in ("드래곤 트리플백", "드래곤원통프리미엄")


def test_nonexistent_product_option_combo_not_saved():
    index = CatalogIndex.build(CATALOG)
    resolved = resolve_catalog_item("드래곤 트리플백", "레드", 1, index)
    assert resolved.mapping_status == "unresolved"
    assert resolved.product is None or resolved.option is None


def test_full_pipeline_via_resolve_extracted_items():
    """LLM이 [주문완료] 두 행을 raw_product/raw_option으로 추출했다고 가정하고,
    services.resolve_extracted_items()가 CatalogIndex 생성부터 최종 확정까지
    올바르게 연결하는지 확인한다."""
    llm_items = [
        {
            "raw_product": "드래곤백",
            "raw_option": "레드",
            "product": "드래곤 트리플백",  # LLM 힌트가 틀려도 무시되어야 함
            "option": "레드",
            "volume": 1,
        },
        {
            "raw_product": "드래곤",
            "raw_option": "진브라운",
            "product": "드래곤 트리플백",
            "option": "다크브라운",
            "volume": 1,
        },
    ]

    resolved = resolve_extracted_items(llm_items, CATALOG_LIST)

    assert resolved[0].product == "드래곤백"
    assert resolved[0].option == "레드"
    assert resolved[0].mapping_status == "exact"

    assert resolved[1].product == "드래곤백"
    assert resolved[1].option == "진브라운"
    assert resolved[1].mapping_status == "inferred"


def test_merged_rows_from_llm_segmentation_failure_are_blocked():
    """LLM이 줄바꿈 없는 원문에서 행 분리를 실패해 두 주문을 하나로 합쳐도,
    resolver가 unresolved로 막아 잘못된 데이터가 저장되지 않는지 확인한다."""
    index = CatalogIndex.build(CATALOG)
    resolved = resolve_catalog_item(
        "드래곤백 레드 1 드래곤", "진브라운", 1, index
    )
    assert resolved.mapping_status == "unresolved"
