"""
CLI 실행 예시:
  python3 main.py --api-key <GEMINI_KEY> --catalog catalog.jsonl --chat chat1.csv chat2.csv
  python3 main.py --catalog catalog.jsonl --chat chat1.csv  # api-key는 config.yaml의 값 사용 불가, 필수 입력
"""

import argparse
import datetime
from pathlib import Path

import pandas as pd
import yaml

from services import (
    parse_custom_jsonl,
    parse_catalog_json,
    normalize_catalog,
    process_chat_file,
    lookup_zip_code,
    format_phone_number,
    normalize_zip_code,
)
from excel_utils import write_excel_with_text_zipcode
from resolver import CatalogIndex
from settings import get_env, load_prompt


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class FileWrapper:
    """Path를 Streamlit UploadedFile처럼 감싸는 래퍼 (services 함수 호환용)"""

    def __init__(self, path: Path):
        self.name = path.name
        self._data = path.read_bytes()

    def getvalue(self) -> bytes:
        return self._data


def main():
    parser = argparse.ArgumentParser(description="Chat2Order CLI")
    parser.add_argument("--api-key", required=True, help="Gemini API Key")
    parser.add_argument("--catalog", required=True, help="카탈로그 JSONL 파일 경로")
    parser.add_argument(
        "--chat",
        required=True,
        nargs="+",
        help="대화 파일 경로 (CSV 또는 JSONL), 여러 개 가능",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="출력 엑셀 파일명 (기본값: config.yaml의 file_name)",
    )
    parser.add_argument("--config", default="config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    config = load_config(args.config)
    order_extraction_prompt = load_prompt(config["prompts"]["order_extraction"])
    output_path = args.output or config["output"]["file_name"]

    print(f"[INFO] 카탈로그 파싱 중: {args.catalog}")
    catalog_wrapper = FileWrapper(Path(args.catalog))
    if Path(args.catalog).suffix.lower() == ".json":
        catalog_data = parse_catalog_json(catalog_wrapper)
    else:
        catalog_data = normalize_catalog(parse_custom_jsonl(catalog_wrapper))
    catalog_index = CatalogIndex.build(catalog_data)

    all_extracted_orders = []
    all_unresolved_rows = []
    today_str = datetime.date.today().strftime("%Y%m%d")
    sequence = 1

    for chat_path in args.chat:
        p = Path(chat_path)
        print(f"[INFO] 대화 파일 처리 중: {p.name}")
        wrapper = FileWrapper(p)

        try:
            result = process_chat_file(
                chat_file=wrapper,
                catalog=catalog_data,
                index=catalog_index,
                config=config,
                api_key=args.api_key,
                prompt_template=order_extraction_prompt,
                order_number=f"{today_str}{sequence:03d}",
            )
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            continue

        all_extracted_orders.extend(result.orders)
        all_unresolved_rows.extend(result.unresolved)
        if result.extracted_data and result.extracted_data.get("items"):
            sequence += 1
        print(
            f"[INFO] 확정 {len(result.orders)}건, "
            f"검토 필요 {len(result.unresolved)}건"
        )

    if not all_extracted_orders and not all_unresolved_rows:
        print("[WARN] 추출된 주문 데이터가 없습니다.")
        return

    df = pd.DataFrame(all_extracted_orders, dtype=object)
    if "phone_number" in df:
        df["phone_number"] = df["phone_number"].apply(format_phone_number)

    juso_api_key = get_env("JUSO_API_KEY")
    if juso_api_key and "search_address" in df:
        print("[INFO] 우편번호 조회 중...")
        df["zip_code"] = df["search_address"].apply(
            lambda addr: lookup_zip_code(addr, juso_api_key)
        )
        df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

    col_map = config["output_columns"]
    rename = {source: output for output, source in col_map.items() if source}
    df = df.rename(columns=rename)
    df = df.reindex(columns=list(col_map.keys()), fill_value="")

    unresolved_df = pd.DataFrame(all_unresolved_rows, dtype=object)
    if not unresolved_df.empty and "candidate_products" in unresolved_df:
        unresolved_df["candidate_products"] = unresolved_df[
            "candidate_products"
        ].apply(lambda value: ", ".join(value) if isinstance(value, list) else value)
    excel_bytes = write_excel_with_text_zipcode(
        df,
        config["output"]["sheet_name"],
        zip_col="우편번호" if "우편번호" in df else "zip_code",
        extra_sheets={"검토필요": unresolved_df} if not unresolved_df.empty else None,
    )
    Path(output_path).write_bytes(excel_bytes)
    print(f"[INFO] 완료: {output_path} ({len(df)}건)")


if __name__ == "__main__":
    main()
