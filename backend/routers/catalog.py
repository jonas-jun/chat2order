from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from deps import get_current_account
from services import generate_catalog_from_csv

router = APIRouter()


class _BytesSource:
    def __init__(self, content: bytes):
        self._content = content

    def getvalue(self) -> bytes:
        return self._content


@router.post("/catalog/from-csv")
async def catalog_from_csv(
    csv_file: UploadFile = File(...),
    _: dict = Depends(get_current_account),
):
    try:
        content = await csv_file.read()
    finally:
        await csv_file.close()

    try:
        return generate_catalog_from_csv(_BytesSource(content))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
