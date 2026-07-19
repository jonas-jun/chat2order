import re
import ast
import json
import io
import unicodedata
import requests
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
from google import genai
from google.genai import types

from models import OrderExtractionResult, ResolvedProductItem
from resolver import CatalogIndex, resolve_catalog_item


Catalog = dict[str, list[str]]


@dataclass
class ExtractionResult:
    """파일 하나의 추출 결과. UI와 저장소 계층에 의존하지 않는다."""

    orders: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    raw_file: dict = field(default_factory=dict)
    chat_data: list[dict] = field(default_factory=list)
    extracted_data: dict | None = None


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
    catalog_data: Catalog,
    chat_data: list,
    model: str,
    temperature: float,
    prompt_template: str,
) -> dict | None:
    """Gemini API를 호출하여 대화에서 주문 정보를 추출합니다."""
    client = _gemini_client(api_key)

    prompt = prompt_template.format(
        catalog=json.dumps(catalog_to_list(catalog_data), ensure_ascii=False, indent=2),
        chat=json.dumps(chat_data, ensure_ascii=False, indent=2),
    )

    try:
        return _generate_json(
            client=client,
            model=model,
            prompt=prompt,
            schema=OrderExtractionResult,
            temperature=temperature,
        )
    except Exception as e:
        import traceback

        tb = traceback.format_exc()
        raise RuntimeError(
            f"Gemini API 호출 중 오류가 발생했습니다: {e}\n\n[Traceback]\n{tb}"
        ) from e


def resolve_extracted_items(
    items: list[dict],
    index: CatalogIndex,
) -> list[ResolvedProductItem]:
    """LLM이 추출한 items(raw_product/raw_option 포함)를 CatalogResolver로 확정한다.

    LLM이 반환한 product/option은 힌트일 뿐 신뢰하지 않고, 항상 이 함수를 거쳐
    카탈로그와 대조한 결과만 저장 대상으로 사용한다. `[주문완료]` 유무와 무관하게
    항상 적용한다.
    """
    return [
        resolve_catalog_item(
            raw_product=item.get("raw_product"),
            raw_option=item.get("raw_option"),
            volume=item.get("volume"),
            index=index,
        )
        for item in items
    ]


def _gemini_client(api_key: str):
    """API 키를 정제하고 Gemini 클라이언트를 생성한다."""
    clean_key = re.sub(r"[^\x20-\x7E]", "", api_key).strip()
    return genai.Client(api_key=clean_key)


def _generate_json(client, model: str, prompt: str, schema, temperature: float):
    """공통 JSON structured output 호출."""
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        ),
    )
    return json.loads(response.text)


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
    if phone is None or pd.isna(phone):
        return None
    phone = str(phone)
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


def extract_search_address(
    api_key: str,
    address: str,
    model: str,
    temperature: float,
    prompt_template: str,
) -> str | None:
    """Gemini로 단일 주소에서 우편번호 검색용 도로명주소를 추출합니다."""
    client = _gemini_client(api_key)

    prompt = prompt_template.format(address=address)

    try:
        result = _generate_json(
            client=client,
            model=model,
            prompt=prompt,
            schema=str | None,
            temperature=temperature,
        )
        return result if isinstance(result, str) and result.strip() else None
    except Exception:
        return None


def batch_lookup_zip_codes(
    df: pd.DataFrame,
    address_col: str,
    juso_api_key: str,
    api_key: str = "",
    model: str = "",
    temperature: float = 0.1,
    prompt_template: str = "",
    progress_callback=None,
) -> pd.Series:
    """
    주소 컬럼으로 우편번호를 일괄 조회합니다.
    동일 주소는 한 번만 조회하고 결과를 재사용합니다.
    1차: 도로명주소 API 직접 조회
    2차(실패 건): Gemini로 주소 정제 후 재조회
    """
    addr_to_zip: dict[str, str] = {}
    unique_addrs = []
    for addr in df[address_col]:
        addr_str = str(addr).strip() if pd.notna(addr) else ""
        if addr_str and addr_str not in addr_to_zip:
            addr_to_zip[addr_str] = ""
            unique_addrs.append(addr_str)

    total = len(unique_addrs)
    use_gemini = bool(api_key and prompt_template)

    for i, addr in enumerate(unique_addrs):
        result = lookup_zip_code(addr, juso_api_key)
        if not result and use_gemini:
            search_addr = extract_search_address(
                api_key,
                addr,
                model,
                temperature,
                prompt_template,
            )
            if search_addr:
                result = lookup_zip_code(search_addr, juso_api_key)
        if result:
            addr_to_zip[addr] = normalize_zip_code(result)
        if progress_callback:
            progress_callback(i, total)

    return df[address_col].apply(
        lambda a: addr_to_zip.get(str(a).strip(), "") if pd.notna(a) else ""
    )


def extract_chat_name(filename: str, filename_prefix: str = "", strip_emoji: bool = False) -> str | None:
    """
    파일명에서 채팅명을 추출합니다.
    - CSV: 이지픽_<채팅명>.csv
    - JSONL: <채팅명>_2026-03-12-10-17-22.jsonl
    strip_emoji=True 시 이모지를 제거하여 반환합니다.
    """
    import emoji as emoji_lib

    name = unicodedata.normalize("NFC", Path(filename).stem)
    if filename_prefix:
        prefix = unicodedata.normalize("NFC", filename_prefix)
        if name.startswith(prefix):
            name = name[len(prefix):]
    else:
        name = re.sub(r"_\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", name)
    if strip_emoji:
        name = emoji_lib.replace_emoji(name, replace="").strip()
    return name or None


def extract_timestamp(filename: str) -> datetime | None:
    """[레거시] JSONL 파일명 기반 timestamp 추출. 신 CSV 포맷에서는 parse_csv()의 반환값 사용."""
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


def parse_catalog_json(source) -> Catalog:
    """
    {상품명: [옵션...]} 형태의 JSON 카탈로그를 내부 표준 dict로 반환합니다.
    """
    if isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raw = source.getvalue()

    catalog = json.loads(raw.decode("utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("카탈로그는 {상품명: [옵션...]} 형태의 JSON 객체여야 합니다.")
    return {str(product): list(options or []) for product, options in catalog.items()}


def catalog_to_list(catalog: Catalog) -> list[dict]:
    """LLM 프롬프트 등 레거시 외부 경계에서만 사용하는 직렬화 형식."""
    return [
        {"상품명": product, "옵션": options}
        for product, options in catalog.items()
    ]


def normalize_catalog(catalog_data: Catalog | list[dict]) -> Catalog:
    """dict 표준 형식과 기존 list 형식을 내부 표준 dict로 정규화한다."""
    if isinstance(catalog_data, dict):
        return {
            str(product): list(options or [])
            for product, options in catalog_data.items()
        }
    return {
        entry["상품명"]: list(entry.get("옵션") or [])
        for entry in catalog_data
        if entry.get("상품명")
    }


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
        raw_message = str(row.get("MESSAGE", ""))
        lines = [
            re.sub(r"[^\S\r\n]+", " ", line).strip()
            for line in raw_message.splitlines()
        ]
        message = "\n".join(line for line in lines if line)
        if any(excl in message for excl in exclude_messages):
            continue
        messages.append({"user": user, "message": message})

    return messages, timestamp


def process_chat_file(
    chat_file,
    catalog: Catalog,
    index: CatalogIndex,
    config: dict,
    api_key: str,
    prompt_template: str,
    *,
    display_name: str | None = None,
    time_after: datetime | None = None,
    time_before: datetime | None = None,
    order_number: str | None = None,
) -> ExtractionResult:
    """대화 파일 하나를 파싱하고 추출·resolve·row 조립까지 수행한다.

    Streamlit 및 DB 호출을 포함하지 않는다. 호출자는 반환된 원본/추출 결과를
    필요에 따라 저장하고 사용자에게 진행 상태를 표시한다.
    """
    filename = display_name or getattr(chat_file, "name", str(chat_file))
    suffix = Path(filename).suffix.lower()
    filename_prefix = config.get("csv", {}).get("filename_prefix", "")

    if suffix == ".csv":
        chat_data, timestamp = parse_csv(
            chat_file,
            filename_prefix=filename_prefix,
            exclude_messages=config.get("csv", {}).get("exclude_messages", []),
            time_after=time_after,
            time_before=time_before,
        )
        if isinstance(chat_file, (str, Path)):
            raw_bytes = Path(chat_file).read_bytes()
        else:
            raw_bytes = chat_file.getvalue()
        raw_content = raw_bytes.decode("utf-8-sig", errors="replace")
    else:
        chat_data = parse_custom_jsonl(
            chat_file, time_after=time_after, time_before=time_before
        )
        timestamp = extract_timestamp(filename)
        raw_content = ""

    chat_name = extract_chat_name(
        filename,
        filename_prefix=filename_prefix if suffix == ".csv" else "",
    )
    raw_file = {
        "filename": filename,
        "chat_name": chat_name,
        "content": raw_content,
        "message_count": len(chat_data),
    }

    extracted_data = extract_orders_from_chat(
        api_key=api_key,
        catalog_data=catalog,
        chat_data=chat_data,
        model=config["gemini"]["model"],
        temperature=config["gemini"]["temperature"],
        prompt_template=prompt_template,
    )
    result = ExtractionResult(
        raw_file=raw_file,
        chat_data=chat_data,
        extracted_data=extracted_data,
    )
    if not extracted_data:
        return result

    common = {
        "order_name": extracted_data.get("order_name"),
        "phone_number": extracted_data.get("phone_number"),
        "address": extracted_data.get("address"),
    }
    for resolved in resolve_extracted_items(extracted_data.get("items", []), index):
        if resolved.mapping_status == "unresolved":
            result.unresolved.append(
                {
                    "chat_name": chat_name,
                    "raw_product": resolved.raw_product,
                    "raw_option": resolved.raw_option,
                    "volume": resolved.volume,
                    "candidate_products": resolved.candidate_products,
                    "mapping_reason": resolved.mapping_reason,
                    **common,
                }
            )
            continue
        result.orders.append(
            {
                "product": resolved.product,
                "option": resolved.option,
                "volume": resolved.volume,
                "raw_product": resolved.raw_product,
                "raw_option": resolved.raw_option,
                "mapping_status": resolved.mapping_status,
                **common,
                "search_address": extracted_data.get("search_address"),
                "time": timestamp,
                "chat_name": chat_name,
                "live_time": time_after,
                "order_number": order_number,
            }
        )
    return result


def search_keyword_in_raw_csv(
    content: str, keyword: str, live_date: str | None = None
) -> list[dict]:
    """원본 CSV 텍스트의 MESSAGE 컬럼에서 keyword를 단순 부분 매칭으로 검색합니다.

    반환: 매칭된 메시지 [{"user", "message", "date"}], 매칭이 없으면 빈 리스트.
    - MESSAGE 컬럼만 대상으로 하며, exclude_messages/공백정규화는 적용하지 않습니다(raw 기준).
    - 매칭 판정은 `keyword in message` (대소문자/공백 정규화·퍼지·단어경계 없음).
    - live_date("YYYY-MM-DD")가 주어지면 해당 날짜의 메시지만 검색합니다.
    - 빈 키워드이거나 MESSAGE 컬럼이 없으면 빈 리스트를 반환합니다.
    """
    if not keyword:
        return []

    df = pd.read_csv(io.StringIO(content), encoding_errors="replace")
    if "MESSAGE" not in df.columns:
        return []

    if live_date and "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        df = df[df["DATE"].dt.strftime("%Y-%m-%d") == live_date]

    matches = []
    for _, row in df.iterrows():
        message = str(row.get("MESSAGE", ""))
        if keyword in message:
            matches.append(
                {
                    "user": row.get("USER", ""),
                    "message": message,
                    "date": row.get("DATE", ""),
                }
            )
    return matches
