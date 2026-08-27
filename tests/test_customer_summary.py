"""구매자별 주문 집계 회귀 테스트.

실 데이터에서 확인된 함정 3가지를 고정한다.
1. ``order_number`` 는 전역 유일하지 않다(추출일 접두 + job 단위 리셋) → 주문 단위 키로 쓰면 안 된다.
2. 같은 채팅 파일을 여러 번 추출한 job 이 존재한다 → 수량이 배로 부풀려진다.
3. 저장된 시각은 KST 벽시계에 ``+00:00`` 라벨이 붙은 값이다 → tz 변환하면 9시간 밀린다.
"""

import pandas as pd
import pytest

from crm.customer_summary import (
    UNKNOWN_NAME,
    build_buyer_product_summary,
    build_buyer_summary,
    build_meta_sheet,
    build_phone_summary,
    build_sheets,
    dedupe_reextractions,
    normalize_name,
    prepare_orders,
    to_kst_naive,
)


def make_job(job_id, user_id="seller", live="2026-06-02T06:00:00+00:00"):
    return {"id": job_id, "user_id": user_id, "live_start_time": live}


def make_order(job_id, chat, name, product, option="단일", volume=1, **kw):
    row = {
        "id": kw.pop("id", f"{job_id}-{chat}-{product}-{option}"),
        "job_id": job_id,
        "order_number": kw.pop("order_number", "20260602001"),
        "chat_name": chat,
        "order_name": name,
        "product": product,
        "option": option,
        "volume": volume,
        "phone_number": kw.pop("phone_number", "010-0000-0001"),
        "address": kw.pop("address", "서울시 어딘가"),
        "zip_code": kw.pop("zip_code", "01663"),
        "mapping_status": "exact",
        "created_at": kw.pop("created_at", "2026-06-02T06:11:00+00:00"),
    }
    row.update(kw)
    return row


def prepared(orders, jobs, **kw):
    return prepare_orders(pd.DataFrame(orders), pd.DataFrame(jobs), **kw)


def test_kst_wall_clock_is_preserved():
    """+00:00 라벨이 붙어도 저장된 값은 KST 벽시계다. 9시간 밀리면 월 경계가 깨진다."""
    naive = to_kst_naive(pd.Series(["2026-08-17T08:20:53.233252+00:00"]))
    assert naive.iat[0] == pd.Timestamp("2026-08-17 08:20:53.233252")


def test_duplicate_order_number_in_different_jobs_counts_twice():
    """order_number 는 job 마다 리셋되므로 다른 날 같은 번호는 별개 주문이다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "김철수", "김철수", "지갑", created_at="2026-06-03T06:11:00+00:00"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert len(summary) == 1
    assert summary.loc[0, "주문횟수"] == 2
    assert summary.loc[0, "총수량"] == 2


def test_reextraction_of_same_file_does_not_inflate_volume():
    """같은 라이브를 두 번 추출한 job → 주문 1건, 수량도 1개로 유지되어야 한다."""
    jobs = [make_job("j1"), make_job("j2")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", volume=2),
        make_order("j2", "김철수", "김철수", "가방", volume=2),
    ]
    df = prepared(orders, jobs)
    deduped, removed = dedupe_reextractions(df)
    assert removed == 1
    summary = build_buyer_summary(deduped)
    assert summary.loc[0, "주문횟수"] == 1
    assert summary.loc[0, "총수량"] == 2


def test_same_day_different_products_are_kept():
    """중복 제거가 같은 날의 서로 다른 상품까지 지워서는 안 된다."""
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방"),
        make_order("j1", "김철수", "김철수", "지갑"),
    ]
    deduped, removed = dedupe_reextractions(prepared(orders, jobs))
    assert removed == 0
    summary = build_buyer_summary(deduped)
    assert summary.loc[0, "주문횟수"] == 1
    assert summary.loc[0, "총수량"] == 2
    assert summary.loc[0, "상품종류수"] == 2


def test_file_suffix_and_whitespace_are_merged():
    """파일 중복 다운로드로 붙는 ' (2)' 와 공백 표기 차이는 동일인으로 합친다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "이수정", "이수정", "가방", created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "이수정 (2)", "이수정", "지갑", created_at="2026-06-03T06:11:00+00:00"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert len(summary) == 1
    assert summary.loc[0, "주문횟수"] == 2
    assert summary.loc[0, "채팅명표기"] == "이수정 | 이수정 (2)"


def test_file_suffix_kept_when_disabled():
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "이수정", "이수정", "가방"),
        make_order("j1", "이수정 (2)", "이수정", "가방"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs, strip_file_suffix=False))
    assert len(summary) == 2


def test_missing_order_name_groups_as_unknown():
    jobs = [make_job("j1")]
    orders = [make_order("j1", "🤍", None, "가방", volume=3)]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert summary.loc[0, "주문자명"] == UNKNOWN_NAME
    assert summary.loc[0, "총수량"] == 3


def test_null_volume_is_counted_separately_not_silently():
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", volume=None),
        make_order("j1", "김철수", "김철수", "지갑", volume=2),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert summary.loc[0, "총수량"] == 2
    assert summary.loc[0, "수량미상건수"] == 1


def test_item_summary_is_sorted_by_volume():
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", option="블랙", volume=1),
        make_order("j1", "김철수", "김철수", "지갑", option=None, volume=5),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert summary.loc[0, "주문상품요약"] == "지갑 x5, 가방(블랙) x1"


def test_buyer_product_summary_sums_across_lives():
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", volume=1, created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "김철수", "김철수", "가방", volume=2, created_at="2026-06-03T06:11:00+00:00"),
    ]
    detail = build_buyer_product_summary(prepared(orders, jobs))
    assert len(detail) == 1
    assert detail.loc[0, "총수량"] == 3
    assert detail.loc[0, "주문횟수"] == 2


def test_stale_live_start_time_falls_back_to_created_date():
    """live_start_time 에 UI 기본값이 남은 job 은 추출 일자로 대체한다."""
    jobs = [make_job("j1", live="2026-05-29T00:30:00+00:00")]
    orders = [make_order("j1", "김철수", "김철수", "가방", created_at="2026-07-12T09:50:00+00:00")]
    df = prepared(orders, jobs, time_basis="live")
    assert df.loc[0, "라이브일자"] == pd.Timestamp("2026-07-12")
    assert df.loc[0, "라이브일자보정"]


def test_orphan_job_id_raises():
    """job 조회 범위가 주문 범위를 못 덮으면 조용히 누락되지 않고 실패해야 한다."""
    with pytest.raises(ValueError, match="extraction_jobs 에 없는"):
        prepared([make_order("없는job", "김철수", "김철수", "가방")], [make_job("j1")])


def test_empty_orders_raises():
    with pytest.raises(ValueError, match="0건"):
        prepare_orders(pd.DataFrame(), pd.DataFrame([make_job("j1")]))


def test_build_sheets_returns_all_sheets_and_stats():
    jobs = [make_job("j1"), make_job("j2")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", volume=2),
        make_order("j2", "김철수", "김철수", "가방", volume=2),
        make_order("j1", "박영희", None, "지갑", volume=1),
    ]
    sheets, stats = build_sheets(
        pd.DataFrame(orders), pd.DataFrame(jobs), pd.DataFrame(), meta=[("테스트", True)]
    )
    assert set(sheets) == {
        "구매자요약",
        "구매자별상품",
        "채팅명요약",
        "전화번호요약",
        "주문내역",
        "제외_미해결항목",
        "집계기준",
    }
    assert stats["재추출 중복 제거 row"] == 1
    assert stats["집계 대상 row"] == 2
    assert stats["총수량"] == 3
    assert stats["주문 건수(라이브일자 x 채팅명)"] == 2
    assert "우편번호" in sheets["주문내역"].columns
    assert ("테스트", "True") in list(sheets["집계기준"].itertuples(index=False, name=None))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  김 철수 ", "김 철수"),
        ("이수정 (2)", "이수정"),
        ("이수정(2)", "이수정"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_distinct_phone_count_flags_conflated_buyers():
    """식별력 없는 채팅명 + 주문자명 미상 조합은 서로 다른 사람이 섞인다. 전화번호로 드러나야 한다."""
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", ".", None, "가방", phone_number="010-1111-1111"),
        make_order("j1", ". (2)", None, "지갑", phone_number="010-2222-2222"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert len(summary) == 1
    assert summary.loc[0, "전화번호종류수"] == 2


def test_distinct_phone_count_ignores_format_differences():
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "김철수", "김철수", "가방", phone_number="010-1111-1111"),
        make_order("j1", "김철수", "김철수", "지갑", phone_number="01011111111"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert summary.loc[0, "전화번호종류수"] == 1


def test_meta_sheet_keeps_zero_as_text_not_false():
    """bool 과 int 가 섞이면 엑셀에서 0 이 FALSE 로 기록되던 문제를 막는다."""
    sheet = build_meta_sheet([("플래그", False), ("건수", 0)])
    assert list(sheet["값"]) == ["False", "0"]


def test_missing_order_name_is_backfilled_from_same_chat_name():
    """같은 채팅명에 이름이 하나만 붙어 있으면, 이름이 빈 주문도 같은 사람으로 합친다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "HEE", "유언희", "가방", created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "HEE", None, "지갑", created_at="2026-06-03T06:11:00+00:00"),
    ]
    df = prepared(orders, jobs)
    assert set(df["주문자명키"]) == {"유언희"}
    summary = build_buyer_summary(df)
    assert len(summary) == 1
    assert summary.loc[0, "주문횟수"] == 2


def test_backfill_skipped_when_chat_name_has_multiple_names():
    """동명이인·수령인 변경으로 이름이 2개 이상이면 무엇으로 채울지 알 수 없으므로 (미상) 유지."""
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "♡", "이화영", "가방", id="a"),
        make_order("j1", "♡", "김철수", "지갑", id="b"),
        make_order("j1", "♡", None, "신발", id="c"),
    ]
    df = prepared(orders, jobs)
    assert (df["주문자명키"] == UNKNOWN_NAME).sum() == 1


def test_phone_summary_merges_name_variants():
    """이름 표기가 달라도 같은 번호면 한 행으로 묶여, 이름 기준 집계를 교차 검증할 수 있다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "HEE🍀", "유언희", "가방", phone_number="010-8028-3799",
                   created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "HEE🍀_행복마미_유언희", "유언희", "지갑", phone_number="01080283799",
                   created_at="2026-06-03T06:11:00+00:00"),
    ]
    df = prepared(orders, jobs)
    assert len(build_buyer_summary(df)) == 2  # 채팅명 변형은 이름 기준으로 못 합친다
    phones = build_phone_summary(df)
    assert len(phones) == 1
    assert phones.loc[0, "전화번호"] == "01080283799"
    assert phones.loc[0, "주문횟수"] == 2
    assert phones.loc[0, "총수량"] == 2


def test_phone_summary_excludes_orders_without_phone():
    jobs = [make_job("j1")]
    orders = [make_order("j1", "초코", None, "가방", phone_number=None)]
    assert build_phone_summary(prepared(orders, jobs)).empty


def test_order_name_whitespace_is_ignored():
    """'나 소희' 와 '나소희' 는 같은 사람이다."""
    jobs = [make_job("j1")]
    orders = [
        make_order("j1", "나♡HEE", "나소희", "가방", id="a"),
        make_order("j1", "나♡HEE", "나 소희", "지갑", id="b"),
    ]
    assert len(build_buyer_summary(prepared(orders, jobs))) == 1


def test_chat_name_with_order_name_suffix_is_merged():
    """'KSJ' 와 'KSJ_김숙전' 은 같은 사람이다 — 잘라내는 토큰이 그 row 의 주문자명과 같을 때만 합친다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "KSJ", "김숙전", "가방", created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "KSJ_김숙전", "김숙전", "지갑", created_at="2026-06-03T06:11:00+00:00"),
    ]
    summary = build_buyer_summary(prepared(orders, jobs))
    assert len(summary) == 1
    assert summary.loc[0, "채팅명"] == "KSJ"
    assert summary.loc[0, "주문횟수"] == 2


def test_chat_name_suffix_kept_when_it_is_not_the_order_name():
    """'하효진_코코지니' 처럼 접미가 주문자명이 아니면 손대지 않는다."""
    jobs = [make_job("j1")]
    orders = [make_order("j1", "하효진_코코지니", "하효진", "가방")]
    df = prepared(orders, jobs)
    assert df.loc[0, "채팅명키"] == "하효진_코코지니"


def test_chat_name_suffix_not_stripped_to_empty():
    """채팅명 전체가 주문자명이면 잘라내지 않는다(빈 키 방지)."""
    jobs = [make_job("j1")]
    orders = [make_order("j1", "_김숙전", "김숙전", "가방")]
    df = prepared(orders, jobs)
    assert df.loc[0, "채팅명키"] == "_김숙전"


def test_chat_prefix_is_stripped_when_requested():
    """대화 파일명에 서비스 접두어가 두 번 들어가 채팅명이 쪼개진 경우를 합친다."""
    jobs = [make_job("j1"), make_job("j2", live="2026-06-03T06:00:00+00:00")]
    orders = [
        make_order("j1", "KSJ", "김숙전", "가방", created_at="2026-06-02T06:11:00+00:00"),
        make_order("j2", "O이지픽_KSJ", "김숙전", "지갑", created_at="2026-06-03T06:11:00+00:00"),
    ]
    assert len(build_buyer_summary(prepared(orders, jobs))) == 2
    merged = build_buyer_summary(prepared(orders, jobs, strip_chat_prefixes=("O이지픽_",)))
    assert len(merged) == 1
    assert merged.loc[0, "주문횟수"] == 2
