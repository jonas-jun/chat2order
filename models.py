from pydantic import BaseModel, Field


class ProductItem(BaseModel):
    product: str | None = Field(
        description="카탈로그에서 매핑된 상품명"
    )
    option: str | None = Field(
        description="색상, 사이즈 등의 옵션 (분리하여 작성)"
    )
    volume: int | None = Field(
        description="주문 수량 (정수)"
    )


class OrderExtractionResult(BaseModel):
    order_name: str | None = Field(
        description="주문자명. 수령인 이름이 있으면 우선 사용하고, 없으면 입금자명을 사용. 둘 다 없으면 null"
    )
    phone_number: str | None = Field(
        description="주문자 연락처"
    )
    address: str | None = Field(
        description="고객이 말한 배송지 전체 주소"
    )
    search_address: str | None = Field(
        description="우편번호 검색용 도로명주소. address에서 상세주소를 제거하고 "
        "도로명+건물번호까지만 작성 (예: 부산 북구 백양대로1050번길 16)"
    )
    items: list[ProductItem] = Field(
        description="주문이 확정된 상품 내역 리스트. 주문 건이 없으면 빈 리스트"
    )
