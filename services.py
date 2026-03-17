import re
import ast
import json
import io
import unicodedata
import requests
from datetime import datetime
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import types

from models import OrderItem


def parse_custom_jsonl(
    source,
    time_after: datetime | None = None,
    time_before: datetime | None = None,
) -> list:
    """
    작은따옴표가 포함된 파이썬 dict 형태의 텍스트 라인을 파싱합니다.
    source: Streamlit UploadedFile 또는 파일 경로(str/Path)
    time_after: 이 시각 이후의 메시지만 포함합니다. (각 row의 "date" 키 기준)
    time_before: 이 시각 이전의 메시지만 포함합니다.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source.getvalue()

    lines = raw.decode("utf-8").splitlines()
    data = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed_dict = ast.literal_eval(line)
            if (time_after or time_before) and "date" in parsed_dict:
                row_date = pd.to_datetime(parsed_dict["date"])
                if time_after and row_date < time_after:
                    continue
                if time_before and row_date > time_before:
                    continue
            data.append(parsed_dict)
        except Exception as e:
            print(f"[WARN] 파싱 오류 발생 라인 건너뜀: {line[:30]}... ({e})")
    return data


def extract_orders_from_chat(
    api_key: str,
    catalog_data: list,
    chat_data: list,
    model: str,
    temperature: float,
    prompt_template: str,
) -> list | None:
    """Gemini API를 호출하여 대화에서 주문 정보를 추출합니다."""
    api_key = re.sub(r"[^\x20-\x7E]", "", api_key).strip()
    client = genai.Client(api_key=api_key)

    prompt = prompt_template.format(
        catalog=json.dumps(catalog_data, ensure_ascii=False, indent=2),
        chat=json.dumps(chat_data, ensure_ascii=False, indent=2),
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=list[OrderItem],
                temperature=temperature,
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        raise RuntimeError(
            f"Gemini API 호출 중 오류가 발생했습니다: {e}\n\n[Traceback]\n{tb}"
        ) from e


def lookup_zip_code(address: str | None, juso_api_key: str) -> str | None:
    """도로명주소 검색API로 우편번호를 조회합니다."""
    if not address or not juso_api_key:
        return None
    try:
        resp = requests.get(
            "https://business.juso.go.kr/addrlink/addrLinkApi.do",
            params={
                "confmKey": juso_api_key,
                "currentPage": 1,
                "countPerPage": 1,
                "keyword": address,
                "resultType": "json",
            },
            timeout=5,
        )
        juso_list = resp.json().get("results", {}).get("juso", [])
        if juso_list:
            return juso_list[0].get("zipNo")
    except Exception:
        pass
    return None


def format_phone_number(phone: str | None) -> str | None:
    """전화번호에서 숫자만 추출 후 010-XXXX-XXXX 형식으로 변환합니다."""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return phone


def normalize_zip_code(zip_code: object) -> str:
    """우편번호를 문자열(5자리)로 정규화합니다."""
    if zip_code is None or pd.isna(zip_code):
        return ""

    raw = str(zip_code).strip()
    if not raw:
        return ""

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw

    if len(digits) <= 5:
        return digits.zfill(5)

    return digits


def extract_chat_name(filename: str, filename_prefix: str = "") -> str | None:
    """
    파일명에서 채팅명을 추출합니다.
    - CSV: 다애모드(daae_mode)_<채팅명>.csv
    - JSONL: <채팅명>_2026-03-12-10-17-22.jsonl
    """
    name = unicodedata.normalize("NFC", Path(filename).stem)
    if filename_prefix:
        prefix = unicodedata.normalize("NFC", filename_prefix)
        if name.startswith(prefix):
            return name[len(prefix) :]
    return re.sub(r"_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", name) or None


def extract_timestamp(filename: str) -> datetime | None:
    """파일명에서 timestamp를 추출합니다. (예: 김성희_2026-03-12-10-17-22.jsonl)"""
    ts_match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})", filename)
    if ts_match:
        return datetime.strptime(ts_match.group(1), "%Y-%m-%d-%H-%M-%S")
    return None


def generate_catalog_from_csv(source) -> dict:
    """
    stk_forInOut CSV에서 상품명-옵션 매핑을 추출하여 카탈로그 dict를 생성합니다.
    반환: {"상품명A": ["옵션1", "옵션2"], "상품명B": ["단일상품"], ...}
    """
    if isinstance(source, (str, Path)):
        raw = io.BytesIO(Path(source).read_bytes())
    else:
        raw = io.BytesIO(source.getvalue())

    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            raw.seek(0)
            df = pd.read_csv(raw, encoding=encoding, encoding_errors="strict")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raw.seek(0)
        df = pd.read_csv(raw, encoding="utf-8", encoding_errors="replace")

    if "상품명" not in df.columns or "옵션내용" not in df.columns:
        raise ValueError(
            "CSV에 '상품명'과 '옵션내용' 컬럼이 필요합니다. "
            f"발견된 컬럼: {list(df.columns)}"
        )

    catalog: dict[str, list[str]] = {}
    for _, row in df.iterrows():
        product = str(row["상품명"]).strip()
        option = str(row["옵션내용"]).strip()
        if not product or product == "nan":
            continue
        if product not in catalog:
            catalog[product] = []
        if option and option != "nan" and option not in catalog[product]:
            catalog[product].append(option)

    return catalog


def parse_catalog_json(source) -> list:
    """
    {상품명: [옵션...]} 형태의 JSON 카탈로그를 파싱하여
    기존 JSONL 형식과 호환되는 list[dict] 형태로 반환합니다.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source.getvalue()

    catalog_dict = json.loads(raw.decode("utf-8"))
    return [
        {"상품명": product, "옵션": options}
        for product, options in catalog_dict.items()
    ]


def parse_csv(
    source,
    filename_prefix: str,
    exclude_messages: list,
    time_after: datetime | None = None,
    time_before: datetime | None = None,
) -> tuple[list, datetime | None]:
    """
    카카오톡 채널 CSV를 파싱하여 (messages, timestamp) 튜플을 반환합니다.
    source: Streamlit UploadedFile 또는 파일 경로(str/Path)
    time_after: 이 시각 이후의 메시지만 포함합니다.
    time_before: 이 시각 이전의 메시지만 포함합니다.
    """
    if isinstance(source, (str, Path)):
        raw = io.BytesIO(Path(source).read_bytes())
    else:
        raw = io.BytesIO(source.getvalue())

    df = pd.read_csv(raw, encoding="utf-8-sig", encoding_errors="replace")

    timestamp = None
    if "DATE" in df.columns and len(df) > 0:
        try:
            timestamp = pd.to_datetime(df.iloc[-1]["DATE"]).to_pydatetime()
        except Exception:
            pass

    if (time_after or time_before) and "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"])
        if time_after:
            df = df[df["DATE"] >= time_after]
        if time_before:
            df = df[df["DATE"] <= time_before]

    messages = []
    for _, row in df.iterrows():
        user = row.get("USER", "")
        message = re.sub(r"\s+", " ", str(row.get("MESSAGE", ""))).strip()
        if any(message.startswith(excl) for excl in exclude_messages):
            continue
        messages.append({"user": user, "message": message})

    return messages, timestamp
