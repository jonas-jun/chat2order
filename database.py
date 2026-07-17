import json
import math
from datetime import datetime

from supabase import create_client, Client


def get_connection(url: str, key: str) -> Client:
    """Supabase 클라이언트를 생성하여 반환합니다."""
    return create_client(url, key)


def create_extraction_job(
    conn: Client,
    user_id: str,
    title: str,
    live_start_time: datetime,
    model: str,
) -> str:
    """extraction_jobs 테이블에 새 작업을 생성하고 job_id를 반환합니다."""
    result = (
        conn.table("extraction_jobs")
        .insert(
            {
                "user_id": user_id,
                "title": title,
                "live_start_time": live_start_time.isoformat(),
                "model": model,
                "total_orders": 0,
                "created_at": datetime.now().isoformat(),
            }
        )
        .execute()
    )
    return result.data[0]["id"]


def update_extraction_job_total(
    conn: Client,
    job_id: str,
    total_orders: int,
) -> None:
    """extraction_jobs의 total_orders를 업데이트합니다."""
    conn.table("extraction_jobs").update({"total_orders": total_orders}).eq(
        "id", job_id
    ).execute()


def save_extracted_orders(
    conn: Client,
    job_id: str,
    orders: list[dict],
) -> None:
    """extracted_orders 테이블에 주문 row들을 일괄 삽입합니다.

    orders 리스트의 각 dict는 다음 키를 포함해야 합니다:
    order_number, product, option, volume, chat_name,
    order_name, phone_number, address, search_address, zip_code
    raw_product, raw_option, mapping_status는 선택 키이며 CatalogResolver가
    확정한 건(exact/alias/typo/inferred)만 이 함수로 저장해야 합니다.
    unresolved 건은 save_unresolved_items()로 별도 저장합니다.
    """
    def clean(value):
        """pandas가 결측치를 float NaN으로 채운 경우 JSON 직렬화가 가능하도록 None으로 변환합니다."""
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    rows = [
        {
            "job_id": job_id,
            "order_number": clean(o.get("order_number")),
            "product": clean(o.get("product")),
            "option": clean(o.get("option")),
            "volume": clean(o.get("volume")),
            "chat_name": clean(o.get("chat_name")),
            "order_name": clean(o.get("order_name")),
            "phone_number": clean(o.get("phone_number")),
            "address": clean(o.get("address")),
            "search_address": clean(o.get("search_address")),
            "zip_code": clean(o.get("zip_code")),
            "raw_product": clean(o.get("raw_product")),
            "raw_option": clean(o.get("raw_option")),
            "mapping_status": clean(o.get("mapping_status")),
            "created_at": datetime.now().isoformat(),
        }
        for o in orders
    ]
    conn.table("extracted_orders").insert(rows).execute()


def save_unresolved_items(
    conn: Client,
    job_id: str,
    items: list[dict],
) -> None:
    """unresolved_items 테이블에 resolver가 확정하지 못한 항목들을 일괄 삽입합니다.

    items 리스트의 각 dict는 다음 키를 포함해야 합니다:
    chat_name, raw_product, raw_option, volume, candidate_products,
    mapping_reason, order_name, phone_number, address
    """
    if not items:
        return

    def clean(value):
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    rows = [
        {
            "job_id": job_id,
            "chat_name": clean(i.get("chat_name")),
            "raw_product": clean(i.get("raw_product")),
            "raw_option": clean(i.get("raw_option")),
            "volume": clean(i.get("volume")),
            "candidate_products": i.get("candidate_products") or [],
            "mapping_reason": clean(i.get("mapping_reason")),
            "order_name": clean(i.get("order_name")),
            "phone_number": clean(i.get("phone_number")),
            "address": clean(i.get("address")),
            "created_at": datetime.now().isoformat(),
        }
        for i in items
    ]
    conn.table("unresolved_items").insert(rows).execute()


def save_training_record(
    conn: Client,
    job_id: str,
    user_id: str,
    chat_filename: str,
    catalog_data: list,
    chat_data: list,
    predicted_json: list | dict,
) -> str:
    """학습 데이터 레코드를 Supabase에 저장하고 id를 반환합니다."""
    result = (
        conn.table("training_data")
        .insert(
            {
                "job_id": job_id,
                "user_id": user_id,
                "chat_filename": chat_filename,
                "catalog_json": json.dumps(catalog_data, ensure_ascii=False),
                "chat_json": json.dumps(chat_data, ensure_ascii=False),
                "predicted_json": json.dumps(predicted_json, ensure_ascii=False),
                "is_verified": False,
                "created_at": datetime.now().isoformat(),
            }
        )
        .execute()
    )
    return result.data[0]["id"]


def get_catalog_by_job(conn: Client, job_id: str) -> str | None:
    """training_data 테이블에서 job_id에 해당하는 catalog_json 문자열을 반환합니다.

    하나의 job_id에 채팅 파일 수만큼 여러 레코드가 존재할 수 있으나,
    동일 작업 내 모든 레코드는 같은 카탈로그를 공유하므로 LIMIT 1로 조회합니다.
    데이터가 없으면 None을 반환합니다.
    """
    result = (
        conn.table("training_data")
        .select("catalog_json")
        .eq("job_id", job_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["catalog_json"]
    return None


def authenticate_user(conn: Client, user_id: str, password: str) -> dict | None:
    """accounts 테이블에서 user_id/password를 검증하고, 성공 시 계정 정보 dict를 반환합니다.
    반환 키: gemini_api_key, monthly_extract_limit (None이면 무제한)
    인증 실패 또는 비활성 계정이면 None을 반환합니다.
    """
    response = (
        conn.table("accounts")
        .select("gemini_api_key, is_active, monthly_extract_limit")
        .eq("user_id", user_id)
        .eq("password", password)
        .execute()
    )
    if response.data and response.data[0].get("is_active"):
        row = response.data[0]
        return {
            "gemini_api_key": row["gemini_api_key"],
            "monthly_extract_limit": row.get("monthly_extract_limit"),
        }
    return None


def save_extract_call_log(
    conn: Client,
    user_id: str,
    job_id: str,
    chat_filename: str,
) -> None:
    """extract_call_logs 테이블에 API 호출 1건을 기록합니다."""
    conn.table("extract_call_logs").insert(
        {
            "user_id": user_id,
            "job_id": job_id,
            "chat_filename": chat_filename,
        }
    ).execute()


def get_monthly_api_call_count(conn: Client, user_id: str) -> int:
    """이번 달(1일~말일) 동안 user_id의 API 호출 횟수를 반환합니다."""
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1).isoformat()
    result = (
        conn.table("extract_call_logs")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .gte("called_at", month_start)
        .execute()
    )
    return result.count if result.count is not None else 0


def get_jobs_by_user(
    conn: Client,
    user_id: str,
    limit: int = 5,
) -> list[dict]:
    """유저의 추출 작업 목록을 최신순으로 최대 limit개 반환합니다."""
    result = (
        conn.table("extraction_jobs")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


def get_orders_by_job(
    conn: Client,
    job_id: str,
) -> list[dict]:
    """job_id에 해당하는 주문 row들을 반환합니다."""
    result = (
        conn.table("extracted_orders")
        .select("*")
        .eq("job_id", job_id)
        .order("order_number")
        .execute()
    )
    return result.data


def save_raw_chat_files(
    conn: Client,
    job_id: str,
    user_id: str,
    files: list[dict],
) -> None:
    """raw_chat_files 테이블에 원본 CSV row들을 일괄 삽입합니다.

    files의 각 dict는 다음 키를 포함해야 합니다:
    filename, chat_name, content, message_count
    """
    if not files:
        return
    rows = [
        {
            "job_id": job_id,
            "user_id": user_id,
            "filename": f.get("filename"),
            "chat_name": f.get("chat_name"),
            "content": f.get("content"),
            "message_count": f.get("message_count"),
            "created_at": datetime.now().isoformat(),
        }
        for f in files
    ]
    conn.table("raw_chat_files").insert(rows).execute()


def get_raw_files_by_job(
    conn: Client,
    job_id: str,
) -> list[dict]:
    """job_id에 해당하는 원본 CSV 파일 목록을 반환합니다.
    반환 키: id, filename, chat_name, content, message_count
    """
    result = (
        conn.table("raw_chat_files")
        .select("id, filename, chat_name, content, message_count")
        .eq("job_id", job_id)
        .order("filename")
        .execute()
    )
    return result.data
