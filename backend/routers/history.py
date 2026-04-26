import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from supabase import Client

from database import (
    get_catalog_by_job,
    get_jobs_by_user,
    get_orders_by_job,
)
from deps import get_current_account, get_db

router = APIRouter()


@router.get("/history")
def list_history(
    limit: int = Query(default=5, ge=1, le=50),
    account: dict = Depends(get_current_account),
    db: Client = Depends(get_db),
):
    return get_jobs_by_user(conn=db, user_id=account["user_id"], limit=limit)


@router.get("/history/{job_id}/orders")
def get_orders(
    job_id: str,
    account: dict = Depends(get_current_account),
    db: Client = Depends(get_db),
):
    orders = get_orders_by_job(conn=db, job_id=job_id)
    if not orders:
        return []
    return orders


@router.get("/history/{job_id}/catalog")
def get_catalog(
    job_id: str,
    account: dict = Depends(get_current_account),
    db: Client = Depends(get_db),
):
    catalog_json = get_catalog_by_job(conn=db, job_id=job_id)
    if not catalog_json:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 작업의 카탈로그가 저장되어 있지 않습니다.",
        )
    catalog_list = json.loads(catalog_json)
    return {item["상품명"]: item["옵션"] for item in catalog_list}
