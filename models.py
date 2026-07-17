from typing import Literal

from pydantic import BaseModel, Field


class ProductItem(BaseModel):
    raw_product: str | None = Field(
        description="대화 원문에 적힌 상품 표현을 그대로 옮긴 값 (매핑하지 말고 원문 그대로)"
    )
    raw_option: str | None = Field(
        description="대화 원문에 적힌 옵션(색상, 사이즈 등) 표현을 그대로 옮긴 값"
    )
    product: str | None = Field(
        description="카탈로그에서 매핑된 상품명 (참고용 힌트. 최종 확정값이 아니며 "
        "애플리케이션에서 별도로 재검증함)"
    )
    option: str | None = Field(
        description="카탈로그에서 매핑된 옵션 (참고용 힌트. 최종 확정값이 아니며 "
        "애플리케이션에서 별도로 재검증함)"
    )
    volume: int | None = Field(description="주문 수량 (정수)")


class ResolvedProductItem(BaseModel):
    """CatalogResolver가 raw_product/raw_option을 카탈로그와 대조해 확정한 결과.

    LLM 구조화 출력 스키마가 아니라 애플리케이션 내부에서만 사용하는 모델.
    """

    raw_product: str | None
    raw_option: str | None
    product: str | None
    option: str | None
    volume: int | None
    mapping_status: Literal["exact", "alias", "typo", "inferred", "unresolved"]
    mapping_reason: str | None = None
    candidate_products: list[str] = Field(default_factory=list)


class OrderExtractionResult(BaseModel):
    order_name: str | None = Field(
        description="주문자명. 수령인 이름이 있으면 우선 사용하고, 없으면 입금자명을 사용. 둘 다 없으면 null"
    )
    phone_number: str | None = Field(description="주문자 연락처")
    address: str | None = Field(description="고객이 말한 배송지 전체 주소")
    search_address: str | None = Field(
        description="우편번호 검색용 도로명주소. address에서 상세주소를 제거하고 "
        "도로명+건물번호까지만 작성 (예: 부산 북구 백양대로1050번길 16)"
    )
    items: list[ProductItem] = Field(
        description="주문이 확정된 상품 내역 리스트. 주문 건이 없으면 빈 리스트"
    )
