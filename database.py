import json
from datetime import datetime

from supabase import create_client, Client


def get_connection(url: str, key: str) -> Client:
    """Supabase 클라이언트를 생성하여 반환합니다."""
    return create_client(url, key)


def save_training_record(
    conn: Client,
    user_email: str,
    chat_filename: str,
    model_name: str,
    catalog_data: list,
    chat_data: list,
    response_json: list | dict,
) -> str:
    """학습 데이터 레코드를 Supabase에 저장하고 id를 반환합니다."""
    result = (
        conn.table("training_data")
        .insert(
            {
                "created_at": datetime.now().isoformat(),
                "user": user_email,
                "chat_filename": chat_filename,
                "model_name": model_name,
                "catalog_json": json.dumps(catalog_data, ensure_ascii=False),
                "chat_json": json.dumps(chat_data, ensure_ascii=False),
                "response_json": json.dumps(response_json, ensure_ascii=False),
                "is_verified": False,
            }
        )
        .execute()
    )
    return result.data[0]["id"]
