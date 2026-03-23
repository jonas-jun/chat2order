import json
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
    """
    rows = [
        {
            "job_id": job_id,
            "order_number": o.get("order_number"),
            "product": o.get("product"),
            "option": o.get("option"),
            "volume": o.get("volume"),
            "chat_name": o.get("chat_name"),
            "order_name": o.get("order_name"),
            "phone_number": o.get("phone_number"),
            "address": o.get("address"),
            "search_address": o.get("search_address"),
            "zip_code": o.get("zip_code"),
            "created_at": datetime.now().isoformat(),
        }
        for o in orders
    ]
    conn.table("extracted_orders").insert(rows).execute()


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


def authenticate_user(conn: Client, user_id: str, password: str) -> str | None:
    """accounts 테이블에서 user_id/password를 검증하고, 성공 시 gemini_api_key를 반환합니다.
    인증 실패 또는 비활성 계정이면 None을 반환합니다.
    """
    response = (
        conn.table("accounts")
        .select("gemini_api_key, is_active")
        .eq("user_id", user_id)
        .eq("password", password)
        .execute()
    )
    if response.data and response.data[0].get("is_active"):
        return response.data[0]["gemini_api_key"]
    return None


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
