from pydantic import BaseModel


class OrderItem(BaseModel):
    name: str | None
    phone_number: str | None
    address: str | None
    search_address: str | None
    product: str | None
    option: str | None
    volume: int | None
