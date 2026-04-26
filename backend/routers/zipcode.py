import io
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from deps import get_config, get_current_account
from services import batch_lookup_zip_codes
from settings import settings

router = APIRouter()

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/zipcode")
async def zipcode_lookup(
    excel_file: UploadFile = File(...),
    account: dict = Depends(get_current_account),
    config: dict = Depends(get_config),
):
    if not settings.juso_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="도로명주소 API 키가 설정되지 않았습니다.",
        )

    try:
        content = await excel_file.read()
        original_name = Path(excel_file.filename or "addresses").stem
    finally:
        await excel_file.close()

    zip_df = pd.read_excel(io.BytesIO(content))
    if "주소" not in zip_df.columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'주소' 컬럼을 찾을 수 없습니다. 발견된 컬럼: {list(zip_df.columns)}",
        )

    result_series = batch_lookup_zip_codes(
        df=zip_df,
        address_col="주소",
        juso_api_key=settings.juso_api_key,
        api_key=account.get("gemini_api_key", "") or "",
        model=config["gemini"]["model"],
        temperature=config["gemini"]["temperature"],
        prompt_template=settings.prompt_address_to_search,
    )

    if "우편번호" in zip_df.columns:
        zip_df["우편번호"] = result_series
    else:
        addr_pos = zip_df.columns.get_loc("주소")
        zip_df.insert(addr_pos + 1, "우편번호", result_series)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        zip_df.to_excel(writer, index=False, sheet_name="Sheet1")
        ws = writer.sheets["Sheet1"]
        zcol = zip_df.columns.get_loc("우편번호") + 1
        for row in ws.iter_rows(
            min_row=2, max_row=ws.max_row, min_col=zcol, max_col=zcol
        ):
            row[0].number_format = "@"
    output.seek(0)

    total = len(zip_df)
    found = int((zip_df["우편번호"] != "").sum())

    return StreamingResponse(
        output,
        media_type=EXCEL_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{original_name}_우편번호.xlsx"',
            "X-Total": str(total),
            "X-Found": str(found),
        },
    )
