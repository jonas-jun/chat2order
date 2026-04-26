from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from supabase import Client

from auth import COOKIE_NAME, create_access_token, get_current_user
from database import authenticate_user
from deps import get_db
from settings import settings

router = APIRouter()


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    monthly_extract_limit: int | None = None


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Client = Depends(get_db),
):
    account = authenticate_user(db, user_id=payload.email, password=payload.password)
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일/비밀번호가 올바르지 않거나 비활성화된 계정입니다.",
        )
    token = create_access_token(payload.email)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return LoginResponse(
        user_id=payload.email,
        monthly_extract_limit=account.get("monthly_extract_limit"),
    )


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/me")
def me(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}
