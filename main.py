"""
CLI 실행 예시:
  python3 main.py --api-key <GEMINI_KEY> --catalog catalog.jsonl --chat chat1.csv chat2.csv
  python3 main.py --catalog catalog.jsonl --chat chat1.csv  # api-key는 config.yaml의 값 사용 불가, 필수 입력
"""

import argparse
import io
import tomllib
from pathlib import Path

import pandas as pd
import yaml

from services import (
    parse_custom_jsonl,
    parse_csv,
    extract_orders_from_chat,
    lookup_zip_code,
    format_phone_number,
    normalize_zip_code,
    extract_timestamp,
    extract_chat_name,
)


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_secrets(path: str = ".streamlit/secrets.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


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
    secrets = load_secrets()
    output_path = args.output or config["output"]["file_name"]

    print(f"[INFO] 카탈로그 파싱 중: {args.catalog}")
    catalog_data = parse_custom_jsonl(FileWrapper(Path(args.catalog)))

    all_extracted_orders = []

    for chat_path in args.chat:
        p = Path(chat_path)
        print(f"[INFO] 대화 파일 처리 중: {p.name}")
        wrapper = FileWrapper(p)

        if p.suffix == ".csv":
            chat_data, ts = parse_csv(
                wrapper,
                filename_prefix=config["csv"]["filename_prefix"],
                exclude_messages=config["csv"]["exclude_messages"],
            )
        else:
            chat_data = parse_custom_jsonl(wrapper)
            ts = extract_timestamp(p.name)

        try:
            extracted_data = extract_orders_from_chat(
                args.api_key,
                catalog_data,
                chat_data,
                model=config["gemini"]["model"],
                temperature=config["gemini"]["temperature"],
                prompt_template=secrets["prompt"]["order_extraction"],
            )
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            continue

        if extracted_data:
            items = extracted_data.get("items", [])
            if items:
                chat_name = extract_chat_name(
                    p.name,
                    filename_prefix=(
                        config["csv"]["filename_prefix"] if p.suffix == ".csv" else ""
                    ),
                )
                for item in items:
                    row = {
                        **item,
                        "order_name": extracted_data.get("order_name"),
                        "phone_number": extracted_data.get("phone_number"),
                        "address": extracted_data.get("address"),
                        "search_address": extracted_data.get("search_address"),
                        "time": ts,
                        "chat_name": chat_name,
                    }
                    all_extracted_orders.append(row)
                print(f"[INFO] {len(items)}건 추출 완료")

    if not all_extracted_orders:
        print("[WARN] 추출된 주문 데이터가 없습니다.")
        return

    df = pd.DataFrame(all_extracted_orders)
    df["phone_number"] = df["phone_number"].apply(format_phone_number)
    if "zip_code" in df.columns:
        df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

    juso_api_key = config["juso"]["api_key"]
    if juso_api_key:
        print("[INFO] 우편번호 조회 중...")
        df["zip_code"] = df["search_address"].apply(
            lambda addr: lookup_zip_code(addr, juso_api_key)
        )
        df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

    df = df.reindex(columns=config["columns"])

    output = io.BytesIO()
    with pd.ExcelWriter(
        output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS"
    ) as writer:
        df.to_excel(writer, index=False, sheet_name=config["output"]["sheet_name"])
        worksheet = writer.sheets[config["output"]["sheet_name"]]
        for zip_col_name in ("우편번호", "zip_code"):
            if zip_col_name in df.columns:
                zip_col_idx = df.columns.get_loc(zip_col_name) + 1
                for row in worksheet.iter_rows(
                    min_row=2,
                    max_row=worksheet.max_row,
                    min_col=zip_col_idx,
                    max_col=zip_col_idx,
                ):
                    row[0].number_format = "@"
                break

    Path(output_path).write_bytes(output.getvalue())
    print(f"[INFO] 완료: {output_path} ({len(df)}건)")


if __name__ == "__main__":
    main()
