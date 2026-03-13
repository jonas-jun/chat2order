import re
import ast
import json
import requests
from datetime import datetime

import streamlit as st
from google import genai
from google.genai import types

from models import OrderItem


def parse_custom_jsonl(uploaded_file) -> list:
    """작은따옴표가 포함된 파이썬 dict 형태의 텍스트 라인을 파싱합니다."""
    lines = uploaded_file.getvalue().decode("utf-8").splitlines()
    data = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            parsed_dict = ast.literal_eval(line)
            data.append(parsed_dict)
        except Exception as e:
            st.warning(f"파싱 오류 발생 라인 건너뜀: {line[:30]}... ({e})")
    return data


def extract_orders_from_chat(api_key: str, catalog_data: list, chat_data: list, model: str, temperature: float, prompt_template: str) -> list | None:
    """Gemini API를 호출하여 대화에서 주문 정보를 추출합니다."""
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
        st.error(f"Gemini API 호출 중 오류가 발생했습니다: {e}")
        return None


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


def extract_timestamp(filename: str) -> datetime | None:
    """파일명에서 timestamp를 추출합니다. (예: 김성희_2026-03-12-10-17-22.jsonl)"""
    ts_match = re.search(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})", filename)
    if ts_match:
        return datetime.strptime(ts_match.group(1), "%Y-%m-%d-%H-%M-%S")
    return None
