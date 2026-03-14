"""
CLI 실행 예시:
  python3 main.py --api-key <GEMINI_KEY> --catalog catalog.jsonl --chat chat1.csv chat2.csv
  python3 main.py --catalog catalog.jsonl --chat chat1.csv  # api-key는 config.yaml의 값 사용 불가, 필수 입력
"""

import argparse
import io
from pathlib import Path

import pandas as pd
import yaml

from services import (
    parse_custom_jsonl,
    parse_csv,
    extract_orders_from_chat,
    lookup_zip_code,
    format_phone_number,
    extract_timestamp,
    extract_chat_name,
)


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
                prompt_template=config["prompt"]["order_extraction"],
            )
        except RuntimeError as e:
            print(f"[ERROR] {e}")
            continue

        if extracted_data:
            chat_name = extract_chat_name(
                p.name,
                filename_prefix=(
                    config["csv"]["filename_prefix"] if p.suffix == ".csv" else ""
                ),
            )
            for order in extracted_data:
                order["time"] = ts
                order["chat_name"] = chat_name
            all_extracted_orders.extend(extracted_data)
            print(f"[INFO] {len(extracted_data)}건 추출 완료")

    if not all_extracted_orders:
        print("[WARN] 추출된 주문 데이터가 없습니다.")
        return

    df = pd.DataFrame(all_extracted_orders)
    df["phone_number"] = df["phone_number"].apply(format_phone_number)

    juso_api_key = config["juso"]["api_key"]
    if juso_api_key:
        print("[INFO] 우편번호 조회 중...")
        df["zip_code"] = df["search_address"].apply(
            lambda addr: lookup_zip_code(addr, juso_api_key)
        )

    df = df.reindex(columns=config["columns"])

    output = io.BytesIO()
    with pd.ExcelWriter(
        output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS"
    ) as writer:
        df.to_excel(writer, index=False, sheet_name=config["output"]["sheet_name"])

    Path(output_path).write_bytes(output.getvalue())
    print(f"[INFO] 완료: {output_path} ({len(df)}건)")


if __name__ == "__main__":
    main()
