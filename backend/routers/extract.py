import datetime as dt
import io
import logging

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from supabase import Client

from database import (
    create_extraction_job,
    get_monthly_api_call_count,
    save_extract_call_log,
    save_extracted_orders,
    save_training_record,
    update_extraction_job_total,
)
from deps import get_config, get_current_account, get_db
from services import (
    extract_chat_name,
    extract_orders_from_chat,
    format_phone_number,
    lookup_zip_code,
    normalize_zip_code,
    parse_catalog_json,
    parse_csv,
)
from settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _BytesSource:
    """services.py의 함수들이 기대하는 .getvalue() 인터페이스를 bytes에 어댑팅."""

    def __init__(self, content: bytes):
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


@router.post("/extract")
async def extract_orders(
    catalog: UploadFile = File(...),
    chats: list[UploadFile] = File(...),
    time_after: dt.datetime = Form(...),
    time_before: dt.datetime = Form(...),
    account: dict = Depends(get_current_account),
    db: Client = Depends(get_db),
    config: dict = Depends(get_config),
):
    api_key = account.get("gemini_api_key")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="API Key가 할당되지 않았습니다. 관리자에게 문의하세요.",
        )
    if not settings.prompt_order_extraction:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="주문 추출 프롬프트가 설정되지 않았습니다.",
        )

    try:
        catalog_bytes = await catalog.read()
        chat_payloads = [
            (f.filename, await f.read()) for f in chats
        ]
    finally:
        await catalog.close()
        for f in chats:
            await f.close()

    monthly_limit = account.get("monthly_extract_limit")
    files_to_process = chat_payloads
    if monthly_limit is not None:
        used = get_monthly_api_call_count(db, account["user_id"])
        remaining = monthly_limit - used
        if remaining <= 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"이번 달 API 호출 한도({monthly_limit}회)를 모두 사용했습니다.",
            )
        if len(chat_payloads) > remaining:
            files_to_process = chat_payloads[:remaining]

    catalog_data = parse_catalog_json(_BytesSource(catalog_bytes))

    job_id = create_extraction_job(
        conn=db,
        user_id=account["user_id"],
        title=dt.datetime.now().strftime("%Y%m%d_%H%M%S"),
        live_start_time=time_after,
        model=config["gemini"]["model"],
    )

    all_orders: list[dict] = []
    today_str = dt.date.today().strftime("%Y%m%d")
    seq = 1
    for filename, content in files_to_process:
        chat_data, ts = parse_csv(
            _BytesSource(content),
            filename_prefix=config["csv"]["filename_prefix"],
            exclude_messages=config["csv"]["exclude_messages"],
            time_after=time_after,
            time_before=time_before,
        )

        try:
            extracted = extract_orders_from_chat(
                api_key,
                catalog_data,
                chat_data,
                model=config["gemini"]["model"],
                temperature=config["gemini"]["temperature"],
                prompt_template=settings.prompt_order_extraction,
            )
            save_extract_call_log(
                conn=db,
                user_id=account["user_id"],
                job_id=job_id,
                chat_filename=filename,
            )
        except RuntimeError:
            logger.exception("Gemini 추출 실패: %s", filename)
            continue

        if not extracted:
            continue

        save_training_record(
            conn=db,
            job_id=job_id,
            user_id=account["user_id"],
            chat_filename=filename,
            catalog_data=catalog_data,
            chat_data=chat_data,
            predicted_json=extracted,
        )

        items = extracted.get("items", [])
        if not items:
            continue
        chat_name = extract_chat_name(
            filename, filename_prefix=config["csv"]["filename_prefix"]
        )
        order_number = f"{today_str}{seq:03d}"
        for item in items:
            all_orders.append(
                {
                    **item,
                    "order_name": extracted.get("order_name"),
                    "phone_number": extracted.get("phone_number"),
                    "address": extracted.get("address"),
                    "search_address": extracted.get("search_address"),
                    "time": ts,
                    "chat_name": chat_name,
                    "live_time": time_after,
                    "order_number": order_number,
                }
            )
        seq += 1

    if not all_orders:
        update_extraction_job_total(conn=db, job_id=job_id, total_orders=0)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="추출된 데이터가 없습니다. 원본 데이터나 API 상태를 확인해 주세요.",
        )

    df = pd.DataFrame(all_orders)
    df["phone_number"] = df["phone_number"].apply(format_phone_number)
    if "zip_code" in df.columns:
        df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

    if settings.juso_api_key:
        df["zip_code"] = df["search_address"].apply(
            lambda addr: lookup_zip_code(addr, settings.juso_api_key)
        )
        df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

    save_extracted_orders(conn=db, job_id=job_id, orders=df.to_dict("records"))
    update_extraction_job_total(conn=db, job_id=job_id, total_orders=len(df))

    col_map = config["output_columns"]
    rename = {v: k for k, v in col_map.items() if v}
    df = df.rename(columns=rename)
    df = df.reindex(columns=list(col_map.keys()), fill_value="")

    output = io.BytesIO()
    with pd.ExcelWriter(
        output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS"
    ) as writer:
        df.to_excel(writer, index=False, sheet_name=config["output"]["sheet_name"])
        ws = writer.sheets[config["output"]["sheet_name"]]
        if "우편번호" in df.columns:
            zip_col_idx = df.columns.get_loc("우편번호") + 1
            for row in ws.iter_rows(
                min_row=2,
                max_row=ws.max_row,
                min_col=zip_col_idx,
                max_col=zip_col_idx,
            ):
                row[0].number_format = "@"
    output.seek(0)

    return StreamingResponse(
        output,
        media_type=EXCEL_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{config["output"]["file_name"]}"',
            "X-Job-Id": str(job_id),
            "X-Total-Orders": str(len(df)),
        },
    )
