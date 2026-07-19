import io

import openpyxl
import pandas as pd

import services
from excel_utils import write_excel_with_text_zipcode
from resolver import CatalogIndex


CATALOG = {
    "드래곤백": ["레드", "진브라운"],
    "드래곤 트리플백": ["카멜"],
}
CONFIG = {
    "csv": {"filename_prefix": "이지픽_", "exclude_messages": []},
    "gemini": {"model": "test-model", "temperature": 0.1},
}


class UploadedFile:
    name = "이지픽_고객.csv"

    def __init__(self):
        self.data = (
            "DATE,USER,MESSAGE\n"
            '2026-07-18 10:00:00,customer,"드래곤백 레드 1"\n'
        ).encode("utf-8-sig")

    def getvalue(self):
        return self.data


def test_process_chat_file_builds_resolved_and_unresolved_rows(monkeypatch):
    monkeypatch.setattr(
        services,
        "extract_orders_from_chat",
        lambda **kwargs: {
            "order_name": "홍길동",
            "phone_number": "01012345678",
            "address": "서울시 중구",
            "search_address": "서울 중구 세종대로 1",
            "items": [
                {
                    "raw_product": "드래곤백",
                    "raw_option": "레드",
                    "product": "잘못된 힌트",
                    "option": "카멜",
                    "volume": 2,
                },
                {
                    "raw_product": "드래곤 트리플백",
                    "raw_option": "레드",
                    "product": None,
                    "option": None,
                    "volume": 1,
                },
            ],
        },
    )
    index = CatalogIndex.build(CATALOG)

    result = services.process_chat_file(
        UploadedFile(),
        CATALOG,
        index,
        CONFIG,
        "api-key",
        "prompt",
        order_number="20260718001",
    )

    assert len(result.orders) == 1
    assert result.orders[0]["product"] == "드래곤백"
    assert result.orders[0]["option"] == "레드"
    assert result.orders[0]["order_number"] == "20260718001"
    assert result.orders[0]["chat_name"] == "고객"
    assert len(result.unresolved) == 1
    assert result.unresolved[0]["raw_product"] == "드래곤 트리플백"
    assert result.raw_file["message_count"] == 1
    assert result.raw_file["filename"] == "이지픽_고객.csv"


def test_process_chat_file_reuses_prebuilt_catalog_index(monkeypatch):
    monkeypatch.setattr(
        services,
        "extract_orders_from_chat",
        lambda **kwargs: {"items": [], "order_name": None, "phone_number": None,
                          "address": None, "search_address": None},
    )
    index = CatalogIndex.build(CATALOG)

    def fail_if_rebuilt(*args, **kwargs):
        raise AssertionError("CatalogIndex.build must not run inside the file pipeline")

    monkeypatch.setattr(CatalogIndex, "build", fail_if_rebuilt)
    services.process_chat_file(
        UploadedFile(), CATALOG, index, CONFIG, "api-key", "prompt"
    )
    services.process_chat_file(
        UploadedFile(), CATALOG, index, CONFIG, "api-key", "prompt"
    )


def test_excel_zipcode_column_is_text_and_keeps_leading_zero():
    frame = pd.DataFrame({"주소": ["서울"], "우편번호": ["01234"]})
    excel = write_excel_with_text_zipcode(frame, "주문내역")
    workbook = openpyxl.load_workbook(io.BytesIO(excel))
    cell = workbook["주문내역"]["B2"]

    assert cell.value == "01234"
    assert cell.number_format == "@"
