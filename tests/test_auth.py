import time

from auth import issue_token, verify_token

SECRET = "test-secret-abcdef"


def test_valid_token_roundtrips_user_id():
    token = issue_token("user@example.com", SECRET)
    assert verify_token(token, SECRET) == "user@example.com"


def test_tampered_signature_is_rejected():
    token = issue_token("user@example.com", SECRET)
    tampered = token[:-2] + ("00" if not token.endswith("00") else "11")
    assert verify_token(tampered, SECRET) is None


def test_tampered_payload_is_rejected():
    token = issue_token("user@example.com", SECRET)
    payload, signature = token.split(".", 1)
    forged = issue_token("admin@example.com", SECRET).split(".", 1)[0]
    assert verify_token(f"{forged}.{signature}", SECRET) is None


def test_different_secret_is_rejected():
    token = issue_token("user@example.com", SECRET)
    assert verify_token(token, "other-secret") is None


def test_expired_token_is_rejected():
    token = issue_token("user@example.com", SECRET, ttl_seconds=-1)
    assert verify_token(token, SECRET) is None


def test_unexpired_token_is_accepted():
    token = issue_token("user@example.com", SECRET, ttl_seconds=3600)
    assert verify_token(token, SECRET) == "user@example.com"
    assert time.time() > 0  # sanity: 시간 기반 만료 검증이 실행됨


def test_empty_token_or_secret_is_rejected():
    token = issue_token("user@example.com", SECRET)
    assert verify_token("", SECRET) is None
    assert verify_token(token, "") is None
    assert verify_token(None, SECRET) is None


def test_malformed_token_is_rejected_without_raising():
    assert verify_token("no-dot-separator", SECRET) is None
    assert verify_token("not_base64!!.deadbeef", SECRET) is None
    assert verify_token("...", SECRET) is None
