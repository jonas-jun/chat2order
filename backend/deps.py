from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import Depends, HTTPException, status
from supabase import Client

from auth import get_current_user
from database import get_connection
from settings import settings


@lru_cache(maxsize=1)
def get_db() -> Client:
    return get_connection(settings.supabase_url, settings.supabase_key)


@lru_cache(maxsize=1)
def get_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_current_account(
    user_id: str = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> dict:
    response = (
        db.table("accounts")
        .select("gemini_api_key, is_active, monthly_extract_limit")
        .eq("user_id", user_id)
        .execute()
    )
    if not response.data or not response.data[0].get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account inactive or not found",
        )
    row = response.data[0]
    return {
        "user_id": user_id,
        "gemini_api_key": row.get("gemini_api_key"),
        "monthly_extract_limit": row.get("monthly_extract_limit"),
    }
