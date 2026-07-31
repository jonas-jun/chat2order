"""월 사용량 집계 경계(KST) 회귀 테스트.

배포 서버의 로컬 타임존이 UTC라서 KST 1일 00:00~09:00 구간에 지난달로 판정되던
버그(2026-08-01 발생)를 막는다. 서버 타임존과 무관하게 항상 KST 경계를 쓰는지
검증한다.
"""

from datetime import datetime, timedelta, timezone

from settings import KST, current_month_key, kst_month_range


UTC = timezone.utc


def test_kst_month_start_has_kst_offset():
    """경계 문자열에 +09:00이 붙어야 DB가 UTC로 해석해도 밀리지 않는다."""
    month_start, next_month = kst_month_range(datetime(2026, 8, 15, tzinfo=KST))
    assert month_start.utcoffset() == timedelta(hours=9)
    assert month_start.isoformat() == "2026-08-01T00:00:00+09:00"
    assert next_month.isoformat() == "2026-09-01T00:00:00+09:00"


def test_utc_still_july_is_counted_as_august():
    """버그 재현 시점: KST 8/1 07:28 == UTC 7/31 22:28.

    수정 전 코드는 UTC 기준으로 7월이라 판정해 사용량이 리셋되지 않았다.
    """
    utc_now = datetime(2026, 7, 31, 22, 28, tzinfo=UTC)
    month_start, next_month = kst_month_range(utc_now)
    assert (month_start.year, month_start.month) == (2026, 8)
    assert (next_month.year, next_month.month) == (2026, 9)
    assert current_month_key(utc_now) == "2026-08"


def test_kst_month_first_day_before_nine_am_is_new_month():
    """KST 1일 00:00 정각도 새 달로 판정해야 한다."""
    assert current_month_key(datetime(2026, 8, 1, 0, 0, tzinfo=KST)) == "2026-08"
    assert current_month_key(datetime(2026, 8, 1, 8, 59, tzinfo=KST)) == "2026-08"


def test_kst_month_last_moment_is_previous_month():
    """KST 말일 23:59는 아직 그 달이어야 한다(경계 반대편)."""
    assert current_month_key(datetime(2026, 7, 31, 23, 59, tzinfo=KST)) == "2026-07"
    month_start, next_month = kst_month_range(
        datetime(2026, 7, 31, 23, 59, tzinfo=KST)
    )
    assert month_start.isoformat() == "2026-07-01T00:00:00+09:00"
    assert next_month.isoformat() == "2026-08-01T00:00:00+09:00"


def test_december_rolls_over_to_next_year():
    """12월의 다음 달 경계는 익년 1월이어야 한다."""
    month_start, next_month = kst_month_range(datetime(2026, 12, 9, tzinfo=KST))
    assert month_start.isoformat() == "2026-12-01T00:00:00+09:00"
    assert next_month.isoformat() == "2027-01-01T00:00:00+09:00"


def test_range_is_half_open_and_covers_whole_month():
    """[month_start, next_month) 구간이 그 달 전체를 빠짐없이 덮어야 한다."""
    month_start, next_month = kst_month_range(datetime(2026, 8, 20, tzinfo=KST))
    first_call = datetime(2026, 8, 1, 0, 0, tzinfo=KST)
    last_call = datetime(2026, 8, 31, 23, 59, 59, tzinfo=KST)
    previous_month_call = datetime(2026, 7, 31, 23, 59, 59, tzinfo=KST)
    next_month_call = datetime(2026, 9, 1, 0, 0, tzinfo=KST)

    assert month_start <= first_call < next_month
    assert month_start <= last_call < next_month
    assert not month_start <= previous_month_call < next_month
    assert not month_start <= next_month_call < next_month


def test_result_is_independent_of_server_local_timezone():
    """같은 시점이면 어떤 타임존 표현으로 주어져도 결과가 같아야 한다."""
    instant_in_utc = datetime(2026, 7, 31, 22, 28, tzinfo=UTC)
    instant_in_kst = instant_in_utc.astimezone(KST)
    instant_in_est = instant_in_utc.astimezone(timezone(timedelta(hours=-5)))

    assert (
        kst_month_range(instant_in_utc)
        == kst_month_range(instant_in_kst)
        == kst_month_range(instant_in_est)
    )
