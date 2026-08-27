"""구매자별 주문 집계 로직 (순수 함수).

Supabase 조회·엑셀 쓰기는 ``crm/export_customer_summary.py``가 담당하고, 이 모듈은
DataFrame in → DataFrame out 만 수행한다. 테스트는 ``tests/test_customer_summary.py``.

집계 정의
- **구매자**: ``(채팅명, 주문자명)`` 쌍. 요청 기준이며, 주문자명이 없으면 ``(미상)``으로 묶는다.
- **주문 1건**: ``(라이브 일자, 채팅명)``. 채팅 파일 1개가 주문 1건이므로(``ui/tab_order.py``)
  같은 날 같은 채팅명은 1건으로 센다.
- **라이브 일자**: 기본은 ``extracted_orders.created_at``(추출 시각)의 날짜.
  ``extraction_jobs.live_start_time`` 이 의미상 더 맞지만 UI 기본값이 그대로 남은 job 이
  실제로 있어(추출 월과 다른 월을 가리키는 job 21개) 기본값으로 쓰지 않는다.
  ``time_basis="live"`` 로 바꿀 수 있고, 그 경우 그런 job 은 추출 일자로 대체한다.

시각 컬럼 취급
``created_at``/``live_start_time``은 KST 벽시계 값이 ``+00:00`` 라벨로 저장돼 있다.
``database._rows_from``이 ``datetime.now()``(컨테이너 TZ=Asia/Seoul)를 naive 문자열로 넣고
컬럼이 ``timestamptz``라서 생긴 결과다. 예) job title ``20260817_082053``(KST 08:20)의
``created_at``이 ``2026-08-17T08:20:53+00:00``. 따라서 UTC 로 읽은 뒤 tz 를 떼면 KST 벽시계가
그대로 복원되며, 이 값으로 기간을 자르면 KST 기준과 일치한다.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd


UNKNOWN_NAME = "(미상)"

# 카카오톡 대화 파일을 같은 라이브에서 두 번 내려받으면 파일명에 " (1)", " (2)"가 붙고
# 그대로 채팅명이 된다. 기본적으로 제거해 동일인으로 합친다.
_FILE_SUFFIX = re.compile(r"\s*\(\d+\)$")
_WHITESPACE = re.compile(r"\s+")

RAW_SHEET_COLUMNS = (
    "라이브일자",
    "채팅명키",
    "주문자명키",
    "chat_name",
    "order_name",
    "product",
    "option",
    "volume",
    "phone_number",
    "address",
    "zip_code",
    "mapping_status",
    "user_id",
    "order_number",
    "job_id",
    "created_at",
)

RAW_SHEET_RENAME = {
    "chat_name": "채팅명원본",
    "order_name": "주문자명원본",
    "product": "상품명",
    "option": "옵션명",
    "volume": "수량",
    "phone_number": "전화번호",
    "address": "주소",
    "zip_code": "우편번호",
    "mapping_status": "매핑상태",
    "user_id": "계정",
    "order_number": "주문번호",
    "created_at": "추출시각",
}

SHEET_BUYER = "구매자요약"
SHEET_BUYER_PRODUCT = "구매자별상품"
SHEET_CHAT = "채팅명요약"
SHEET_PHONE = "전화번호요약"
SHEET_RAW = "주문내역"
SHEET_UNRESOLVED = "제외_미해결항목"
SHEET_META = "집계기준"


def normalize_name(
    value,
    *,
    strip_file_suffix: bool = True,
    strip_emoji: bool = False,
    drop_whitespace: bool = False,
    strip_prefixes: tuple[str, ...] = (),
) -> str | None:
    """이름 표기를 비교 가능한 형태로 정규화한다. 빈 값이면 None.

    ``drop_whitespace`` 는 주문자명에만 쓴다. 한국어 이름에서 공백은 의미가 없어
    ``"나 소희"`` 와 ``"나소희"`` 는 같은 사람이지만, 채팅명에서 공백을 지우면 서로 다른
    사람이 합쳐질 수 있다.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = unicodedata.normalize("NFC", str(value))
    text = _WHITESPACE.sub(" ", text).strip()
    if strip_file_suffix:
        text = _FILE_SUFFIX.sub("", text).strip()
    for prefix in strip_prefixes:
        if prefix and text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if drop_whitespace:
        text = _WHITESPACE.sub("", text)
    if strip_emoji:
        import emoji as emoji_lib

        text = emoji_lib.replace_emoji(text, replace="").strip()
    return text or None


def to_kst_naive(series: pd.Series) -> pd.Series:
    """``+00:00`` 라벨이 붙은 KST 벽시계 문자열을 naive datetime 으로 되돌린다."""
    return pd.to_datetime(series, format="ISO8601", utc=True).dt.tz_localize(None)


def prepare_orders(
    orders: pd.DataFrame,
    jobs: pd.DataFrame,
    *,
    time_basis: str = "created",
    strip_file_suffix: bool = True,
    strip_emoji: bool = False,
    strip_chat_prefixes: tuple[str, ...] = (),
) -> pd.DataFrame:
    """주문 row 에 job 정보를 붙이고 집계용 파생 컬럼을 만든다.

    ``time_basis``
        ``"created"``  라이브 일자를 추출 시각 기준으로 잡는다(기본).
        ``"live"``     ``live_start_time`` 기준. 추출 월과 다른 월을 가리키는 job 은
                       UI 기본값이 남은 것으로 보고 추출 일자로 대체한다.
    """
    if time_basis not in ("created", "live"):
        raise ValueError(f"time_basis 는 'created' 또는 'live' 여야 합니다: {time_basis!r}")
    if orders.empty:
        raise ValueError("주문 row 가 0건입니다. 기간·계정 조건 또는 API 키 권한을 확인하세요.")

    job_cols = jobs[["id", "user_id", "live_start_time"]].rename(columns={"id": "job_id"})
    df = orders.merge(job_cols, on="job_id", how="left", validate="many_to_one")
    orphans = int(df["user_id"].isna().sum())
    if orphans:
        raise ValueError(
            f"extraction_jobs 에 없는 job_id 를 가진 주문 row 가 {orphans}건 있습니다. "
            "job 조회 범위가 주문 조회 범위를 덮는지 확인하세요."
        )

    df["추출일시"] = to_kst_naive(df["created_at"])
    live = to_kst_naive(df["live_start_time"])
    created_date = df["추출일시"].dt.normalize()
    if time_basis == "live":
        # 추출 월과 다른 월을 가리키는 live_start_time 은 UI 기본값이 남은 값으로 본다.
        stale = live.dt.to_period("M") != df["추출일시"].dt.to_period("M")
        df["라이브일자"] = live.dt.normalize().where(~stale, created_date)
        df["라이브일자보정"] = stale
    else:
        df["라이브일자"] = created_date
        df["라이브일자보정"] = False

    df["채팅명키"] = [
        normalize_name(
            v,
            strip_file_suffix=strip_file_suffix,
            strip_emoji=strip_emoji,
            strip_prefixes=strip_chat_prefixes,
        )
        or UNKNOWN_NAME
        for v in df["chat_name"]
    ]
    df["주문자명키"] = [
        normalize_name(
            v, strip_file_suffix=False, strip_emoji=strip_emoji, drop_whitespace=True
        )
        or UNKNOWN_NAME
        for v in df["order_name"]
    ]
    df["채팅명키"], df["채팅명접미제거"] = _strip_name_suffix(df["채팅명키"], df["주문자명키"])
    df["주문자명키"], df["주문자명보정"] = _backfill_order_name(df)
    return df


def _strip_name_suffix(
    chat_keys: pd.Series, order_keys: pd.Series
) -> tuple[pd.Series, pd.Series]:
    """채팅명 끝의 ``_<주문자명>`` 을 제거한다.

    같은 사람의 대화 파일이 ``KSJ``, ``KSJ_김숙전``, ``O이지픽_KSJ_김숙전`` 처럼 여러 이름으로
    저장돼 있어 이름 기준 집계가 쪼개진다(전화번호 기준으로 보면 한 사람이다). 제거하는
    토큰이 그 row 자신의 주문자명과 같을 때만 잘라내므로 서로 다른 사람이 합쳐질 일은 없다.
    """
    stripped, changed = [], []
    for chat, order in zip(chat_keys, order_keys):
        head, _, tail = chat.rpartition("_")
        head = head.strip()
        merged = (
            order != UNKNOWN_NAME and head and _WHITESPACE.sub("", tail.strip()) == order
        )
        stripped.append(head if merged else chat)
        changed.append(bool(merged))
    index = chat_keys.index
    return pd.Series(stripped, index=index), pd.Series(changed, index=index)


def _backfill_order_name(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """주문자명이 빈 row 를, 같은 채팅명의 주문자명이 유일하게 확정될 때만 그 이름으로 채운다.

    LLM 이 대화에서 수령인 이름을 못 잡는 경우가 있어(전체의 약 15%) 같은 사람의 주문이
    ``채팅명/이름`` 과 ``채팅명/(미상)`` 두 행으로 쪼개진다. 실제로 ``HEE🍀`` 은 다른 라이브에서
    ``유언희`` 로 저장돼 있었다. 채팅명 하나에 이름이 2개 이상 붙어 있으면(동명이인·수령인 변경)
    무엇으로 채울지 알 수 없으므로 ``(미상)`` 그대로 둔다.
    """
    names = df["주문자명키"]
    filled_any = pd.Series(False, index=df.index)
    # 좁은 범위(같은 날 같은 채팅명)부터 채운다. 재추출 사이에 이름을 잡은 job 과 못 잡은
    # job 이 섞인 경우가 여기서 걸린다.
    for scope in (["라이브일자", "채팅명키"], ["채팅명키"]):
        unique_name = _unique_name_by(df.assign(주문자명키=names), scope)
        scope_key = _scope_key(df, scope)
        target = (names == UNKNOWN_NAME) & scope_key.isin(unique_name)
        names = names.where(~target, scope_key.map(unique_name))
        filled_any |= target
    return names, filled_any


def _scope_key(df: pd.DataFrame, scope: list[str]) -> pd.Series:
    if len(scope) == 1:
        return df[scope[0]]
    return pd.Series(list(zip(*(df[c] for c in scope))), index=df.index)


def _unique_name_by(df: pd.DataFrame, scope: list[str]) -> dict:
    """범위별로 주문자명이 유일하게 확정되는 곳만 ``{범위키: 이름}`` 으로 반환한다."""
    known = df.loc[df["주문자명키"] != UNKNOWN_NAME]
    if known.empty:
        return {}
    grouped = known.groupby(scope, sort=False)["주문자명키"].unique()
    return {key: values[0] for key, values in grouped.items() if len(values) == 1}


def dedupe_reextractions(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """``(라이브일자, 채팅명)`` 마다 job 하나의 주문 내역만 남긴다.

    같은 대화 파일을 여러 번 추출한 job 이 실제로 존재해(같은 날 10 row job 3개 등) 그대로
    두면 수량이 배로 잡힌다. 주문 건수는 ``(라이브일자, 채팅명)`` distinct 라 영향이 없지만
    수량 합계는 이 단계를 반드시 거쳐야 한다.

    **row 내용을 비교해 중복을 지우지 않는다.** 한 job 안에서 같은 상품이 여러 row 로 나오는
    것은 그 고객이 실제로 여러 개를 주문한 것이므로(예: ``새틴바지`` x1 이 3 row) 지우면
    수량이 깎인다. 반대로 재추출 사이에 ``order_name``·``phone_number`` 가 엇갈리면
    (한 번은 이름을 잡고 한 번은 못 잡음) 내용 비교로는 중복이 걸러지지 않는다.
    그래서 job 단위로 통째로 하나만 고른다.

    고르는 기준은 **row 가 가장 많은 job**(가장 완전한 추출), 동수면 나중에 추출한 job.

    한계: 같은 날 서로 다른 라이브가 2회 있고 같은 고객이 양쪽에 주문했다면 1건으로만
    센다. 이 데이터에서 job 이 2개 이상인 690 그룹 중 642 그룹은 주문 내역이 완전히
    동일해(= 순수 재추출) 그 영향은 작다.
    """
    ranked = (
        df.groupby(["라이브일자", "채팅명키", "job_id"], sort=False)
        .agg(row수=("job_id", "size"), 최종추출=("추출일시", "max"))
        .reset_index()
        .sort_values(
            ["라이브일자", "채팅명키", "row수", "최종추출", "job_id"],
            ascending=[True, True, False, False, True],
        )
    )
    keep = ranked.drop_duplicates(["라이브일자", "채팅명키"], keep="first")
    deduped = df.merge(
        keep[["라이브일자", "채팅명키", "job_id"]],
        on=["라이브일자", "채팅명키", "job_id"],
        how="inner",
    )
    return deduped, len(df) - len(deduped)


def _product_label(product, option) -> str:
    product = "(상품미상)" if product is None or pd.isna(product) else str(product)
    if option is None or pd.isna(option) or str(option).strip() == "":
        return product
    return f"{product}({option})"


def _summarize_items(group: pd.DataFrame) -> str:
    """``상품(옵션) x수량`` 을 수량 내림차순으로 이어 붙인다."""
    items = (
        group.assign(label=[_product_label(p, o) for p, o in zip(group["product"], group["option"])])
        .groupby("label", as_index=False)["volume"]
        .sum()
        .sort_values(["volume", "label"], ascending=[False, True])
    )
    return ", ".join(f"{row.label} x{int(row.volume)}" for row in items.itertuples())


def _representative_phone(series: pd.Series) -> str:
    values = series.dropna().astype(str)
    values = values[values.str.strip() != ""]
    if values.empty:
        return ""
    return values.mode().iat[0]


def _distinct_phones(series: pd.Series) -> int:
    """그룹 안의 서로 다른 전화번호 수.

    채팅명이 ``.``/``♡`` 처럼 식별력이 없고 주문자명까지 비어 있으면 서로 다른 사람이
    한 그룹으로 묶인다. 이 값이 2 이상인 행은 보상 대상으로 확정하기 전에 사람이 확인해야 한다.
    """
    values = series.dropna().astype(str).str.replace(r"\D", "", regex=True)
    return int(values[values != ""].nunique())


def _display_names(series: pd.Series) -> str:
    """정규화로 합쳐진 원본 표기들. 사람이 동일인 여부를 최종 판단할 근거."""
    seen = dict.fromkeys(v for v in series.dropna().astype(str) if v.strip())
    return " | ".join(seen)


def _aggregate(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    rows = []
    for key, group in df.groupby(keys, dropna=False, sort=False):
        key = key if isinstance(key, tuple) else (key,)
        rows.append(
            {
                **dict(zip(keys, key)),
                "주문횟수": group["라이브일자"].nunique(),
                "총수량": int(group["volume"].fillna(0).sum()),
                "상품종류수": group.groupby(["product", "option"], dropna=False).ngroups,
                "첫주문일": group["라이브일자"].min(),
                "마지막주문일": group["라이브일자"].max(),
                "대표전화번호": _representative_phone(group["phone_number"]),
                "전화번호종류수": _distinct_phones(group["phone_number"]),
                "수량미상건수": int(group["volume"].isna().sum()),
                "주문상품요약": _summarize_items(group),
                "채팅명표기": _display_names(group["chat_name"]),
                "주문자명표기": _display_names(group["order_name"]),
            }
        )
    summary = pd.DataFrame(rows)
    summary = summary.sort_values(
        ["주문횟수", "총수량", keys[0]], ascending=[False, False, True]
    ).reset_index(drop=True)
    summary.insert(0, "순위", summary.index + 1)
    for column in ("첫주문일", "마지막주문일"):
        summary[column] = summary[column].dt.strftime("%Y-%m-%d")
    return summary


def build_buyer_summary(df: pd.DataFrame) -> pd.DataFrame:
    """요청 기준: 채팅명 + 주문자명 단위 요약."""
    summary = _aggregate(df, ["채팅명키", "주문자명키"])
    return summary.rename(columns={"채팅명키": "채팅명", "주문자명키": "주문자명"})


def build_chat_summary(df: pd.DataFrame) -> pd.DataFrame:
    """주문자명이 비어 조각난 경우를 보정해 보기 위한 채팅명 단위 요약."""
    summary = _aggregate(df, ["채팅명키"]).drop(columns=["주문자명표기"])
    return summary.rename(columns={"채팅명키": "채팅명"})


def build_phone_summary(df: pd.DataFrame) -> pd.DataFrame:
    """전화번호 단위 요약. 이름 기준 집계의 교차 검증용.

    채팅명·주문자명은 동명이인이 합쳐지고(``김미경`` 그룹에 번호가 7개) 표기 변형으로
    쪼개지기도 한다(``HEE🍀`` / ``HEE🍀_행복마미_유언희``). 전화번호가 잡힌 주문만 모아
    같은 사람을 번호로 다시 묶어 보여준다. 번호가 없는 주문은 이 시트에 없다.
    """
    phone = df["phone_number"].astype("string").str.replace(r"\D", "", regex=True)
    with_phone = df.assign(전화번호키=phone)
    with_phone = with_phone[with_phone["전화번호키"].notna() & (with_phone["전화번호키"] != "")]
    if with_phone.empty:
        return pd.DataFrame(columns=["순위", "전화번호", "주문횟수", "총수량"])
    summary = _aggregate(with_phone, ["전화번호키"]).drop(
        columns=["대표전화번호", "전화번호종류수"]
    )
    return summary.rename(columns={"전화번호키": "전화번호"})


def build_buyer_product_summary(df: pd.DataFrame) -> pd.DataFrame:
    """구매자 × 상품/옵션 단위 수량."""
    grouped = (
        df.groupby(["채팅명키", "주문자명키", "product", "option"], dropna=False)
        .agg(총수량=("volume", lambda s: int(s.fillna(0).sum())), 주문횟수=("라이브일자", "nunique"))
        .reset_index()
        .rename(
            columns={
                "채팅명키": "채팅명",
                "주문자명키": "주문자명",
                "product": "상품명",
                "option": "옵션명",
            }
        )
    )
    return grouped.sort_values(
        ["채팅명", "주문자명", "총수량", "상품명"], ascending=[True, True, False, True]
    ).reset_index(drop=True)


def build_raw_sheet(df: pd.DataFrame) -> pd.DataFrame:
    """검증·재집계용 원본 row. 우편번호 컬럼명을 맞춰 엑셀 텍스트 서식을 받게 한다."""
    columns = [c for c in RAW_SHEET_COLUMNS if c in df.columns]
    raw = df[columns].copy()
    raw["라이브일자"] = raw["라이브일자"].dt.strftime("%Y-%m-%d")
    raw = raw.sort_values(["라이브일자", "채팅명키", "product"]).reset_index(drop=True)
    return raw.rename(columns=RAW_SHEET_RENAME)


def build_meta_sheet(rows: list[tuple[str, object]]) -> pd.DataFrame:
    """항목·값 2열 시트. bool 과 int 를 한 컬럼에 섞으면 엑셀에서 0 이 FALSE 로 기록되므로
    값은 모두 문자열로 고정한다."""
    return pd.DataFrame([(k, str(v)) for k, v in rows], columns=["항목", "값"])


def build_sheets(
    orders: pd.DataFrame,
    jobs: pd.DataFrame,
    unresolved: pd.DataFrame,
    *,
    time_basis: str = "created",
    strip_file_suffix: bool = True,
    strip_emoji: bool = False,
    strip_chat_prefixes: tuple[str, ...] = (),
    meta: list[tuple[str, object]] | None = None,
    top: int = 0,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """엑셀 시트 dict 와 진단 수치 dict 를 반환한다."""
    prepared = prepare_orders(
        orders,
        jobs,
        time_basis=time_basis,
        strip_file_suffix=strip_file_suffix,
        strip_emoji=strip_emoji,
        strip_chat_prefixes=strip_chat_prefixes,
    )
    deduped, removed = dedupe_reextractions(prepared)

    buyer = build_buyer_summary(deduped)
    stats = {
        "조회 주문 row": len(prepared),
        "재추출 중복 제거 row": removed,
        "집계 대상 row": len(deduped),
        "주문 건수(라이브일자 x 채팅명)": deduped.groupby(["라이브일자", "채팅명키"]).ngroups,
        "구매자 수(채팅명+주문자명)": len(buyer),
        "채팅명 수": deduped["채팅명키"].nunique(),
        "총수량": int(deduped["volume"].fillna(0).sum()),
        "수량 미상 row": int(deduped["volume"].isna().sum()),
        "주문자명 미상 row": int((deduped["주문자명키"] == UNKNOWN_NAME).sum()),
        "라이브일자 보정 row": int(prepared["라이브일자보정"].sum()),
        "주문자명 보정 row": int(prepared["주문자명보정"].sum()),
        "채팅명 '_주문자명' 접미 제거 row": int(prepared["채팅명접미제거"].sum()),
        "실측 기간": (
            f"{deduped['라이브일자'].min():%Y-%m-%d} ~ {deduped['라이브일자'].max():%Y-%m-%d}"
        ),
        "미해결 항목 수(집계 제외)": len(unresolved),
    }

    sheets = {
        SHEET_BUYER: buyer.head(top) if top else buyer,
        SHEET_BUYER_PRODUCT: build_buyer_product_summary(deduped),
        SHEET_CHAT: build_chat_summary(deduped),
        SHEET_PHONE: build_phone_summary(deduped),
        SHEET_RAW: build_raw_sheet(deduped),
        SHEET_UNRESOLVED: unresolved,
        SHEET_META: build_meta_sheet((meta or []) + list(stats.items())),
    }
    return sheets, stats
