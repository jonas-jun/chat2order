"""로그인 상태를 쿠키에 영속화하기 위한 서명 토큰 유틸리티.

Streamlit의 ``session_state``는 웹소켓 세션에 종속되어 세션이 새로 만들어지면
사라진다. 로그인 성공 시 서명 토큰을 쿠키에 저장하고, 세션이 리셋되면 쿠키의
토큰을 검증해 로그인을 복원한다. 서명은 표준 라이브러리 ``hmac``만 사용한다.
"""

import base64
import hashlib
import hmac
import json
import time


COOKIE_NAME = "c2o_auth"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7일


def issue_token(user_id: str, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """user_id와 만료시각을 담아 서명한 토큰을 발급한다."""
    payload = {"sub": user_id, "exp": int(time.time()) + ttl_seconds}
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_token(token: str, secret: str) -> str | None:
    """토큰의 서명과 만료를 검증하고, 유효하면 user_id를 반환한다."""
    if not token or not secret:
        return None
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(payload_b64, secret)):
        return None
    try:
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


def _sign(payload_b64: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
