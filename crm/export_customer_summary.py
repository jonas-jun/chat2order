"""기간 내 구매자별 주문 집계를 엑셀로 추출한다 (보상 대상 선정용).

사용 예 (2026-06-01 ~ 2026-08-31, 판매자 계정 1개)

    export SUPABASE_URL="https://<project-ref>.supabase.co"
    export SUPABASE_KEY="<secret key>"
    python -m crm.export_customer_summary \\
        --start 2026-06-01 --end 2026-09-01 \\
        --user-id uiwe2156@gmail.com

집계 정의와 시각 컬럼 취급은 ``crm/customer_summary.py`` 의 모듈 docstring 에 있다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crm.customer_summary import SHEET_BUYER, build_sheets  # noqa: E402
from database import get_connection  # noqa: E402
from excel_utils import write_excel_with_text_zipcode  # noqa: E402
from settings import get_env  # noqa: E402


PAGE_SIZE = 1000
# 저장된 시각은 KST 벽시계에 +00:00 라벨이 붙은 값이므로, 경계도 같은 라벨로 만들어야
# KST 기준으로 잘린다. settings.kst_month_range 의 +09:00 경계와 다른 이유는
# customer_summary 모듈 docstring 참고.
BOUNDARY_SUFFIX = "T00:00:00+00:00"


def fetch_paginated(conn, table: str, *, filters=None, page_size: int = PAGE_SIZE) -> list[dict]:
    """PostgREST 응답 상한(기본 1000 row)을 넘겨 전체 row 를 가져온다."""
    rows: list[dict] = []
    start = 0
    while True:
        query = conn.table(table).select("*")
        for column, op, value in filters or []:
            query = getattr(query, op)(column, value)
        result = query.order("id").range(start, start + page_size - 1).execute()
        rows.extend(result.data)
        if len(result.data) < page_size:
            return rows
        start += page_size


def period_filters(start: date, end: date) -> list[tuple[str, str, str]]:
    return [
        ("created_at", "gte", f"{start.isoformat()}{BOUNDARY_SUFFIX}"),
        ("created_at", "lt", f"{end.isoformat()}{BOUNDARY_SUFFIX}"),
    ]


def resolve_user_ids(orders: pd.DataFrame, jobs: pd.DataFrame, args) -> list[str] | None:
    """대상 계정을 확정한다. 미지정 상태로 여러 계정이 섞여 있으면 중단한다.

    같은 채팅 파일을 다른 계정(개발·테스트 계정)으로 재추출한 이력이 실제로 있어,
    계정을 섞으면 주문 건수와 수량이 조용히 이중 계상된다.
    """
    owner = jobs.set_index("id")["user_id"]
    counts = orders["job_id"].map(owner).value_counts()
    if args.user_id:
        unknown = [u for u in args.user_id if u not in counts.index]
        if unknown:
            raise SystemExit(
                f"기간 내 주문이 없는 계정입니다: {', '.join(unknown)}\n"
                f"기간 내 계정별 주문 row: {counts.to_dict()}"
            )
        return list(args.user_id)
    if args.all_users or len(counts) <= 1:
        return None
    raise SystemExit(
        "기간 내에 여러 계정의 주문이 섞여 있습니다. 같은 채팅 파일을 다른 계정으로 "
        "재추출한 이력이 있으면 이중 계상되므로 대상 계정을 지정하세요.\n"
        f"  계정별 주문 row: {counts.to_dict()}\n"
        "  예) --user-id <계정>   전부 합치려면 --all-users"
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="집계 시작일 (KST, 포함). 예 2026-06-01")
    parser.add_argument("--end", required=True, help="집계 종료일 (KST, 제외). 예 2026-09-01")
    parser.add_argument("--user-id", nargs="+", help="대상 판매자 계정. 여러 개 지정 가능")
    parser.add_argument("--all-users", action="store_true", help="계정을 구분하지 않고 전부 합친다")
    parser.add_argument(
        "--time-basis",
        choices=("created", "live"),
        default="created",
        help="라이브 일자 기준. created=추출 시각(기본), live=live_start_time",
    )
    parser.add_argument(
        "--keep-file-suffix",
        action="store_true",
        help="채팅명의 ' (1)', ' (2)' 접미를 남긴다(기본은 제거해 동일인으로 합침)",
    )
    parser.add_argument("--strip-emoji", action="store_true", help="채팅명에서 이모지를 제거한다")
    parser.add_argument(
        "--strip-chat-prefix",
        nargs="+",
        default=[],
        metavar="PREFIX",
        help="채팅명 앞에 붙은 접두어를 제거한다. 대화 파일명에 서비스 접두어가 두 번 들어간 "
        "경우에 쓴다. 예) --strip-chat-prefix O이지픽_",
    )
    parser.add_argument("--top", type=int, default=0, help="구매자요약 시트를 상위 N명으로 제한 (0=전체)")
    parser.add_argument("--output", help="출력 xlsx 경로 (기본 crm/output/customer_summary_<기간>.xlsx)")
    args = parser.parse_args(argv)
    args.start = datetime.strptime(args.start, "%Y-%m-%d").date()
    args.end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if args.start >= args.end:
        parser.error("--start 는 --end 보다 앞이어야 합니다 (--end 는 제외 경계)")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)

    url, key = get_env("SUPABASE_URL"), get_env("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY 환경변수를 설정하세요.")
    conn = get_connection(url, key)

    print(f"기간: {args.start} ~ {args.end - timedelta(days=1)} (KST, --end 제외 경계)")
    jobs = pd.DataFrame(fetch_paginated(conn, "extraction_jobs"))
    orders = pd.DataFrame(fetch_paginated(conn, "extracted_orders", filters=period_filters(args.start, args.end)))
    print(f"조회: jobs {len(jobs)}건 / 주문 row {len(orders)}건")
    if orders.empty:
        raise SystemExit(
            "기간 내 주문 row 가 0건입니다. 기간을 확인하고, RLS 로 막히지 않는 키인지 "
            "(service_role / secret key) 확인하세요."
        )

    user_ids = resolve_user_ids(orders, jobs, args)
    unresolved = pd.DataFrame(fetch_paginated(conn, "unresolved_items", filters=period_filters(args.start, args.end)))
    if user_ids:
        owner = jobs.set_index("id")["user_id"]
        orders = orders[orders["job_id"].map(owner).isin(user_ids)]
        if not unresolved.empty:
            unresolved = unresolved[unresolved["job_id"].map(owner).isin(user_ids)]
        print(f"계정 {', '.join(user_ids)} 필터 후 주문 row {len(orders)}건")

    meta = [
        ("집계 기간(KST)", f"{args.start} ~ {args.end - timedelta(days=1)}"),
        ("대상 계정", ", ".join(user_ids) if user_ids else "전체"),
        ("라이브 일자 기준", args.time_basis),
        ("채팅명 ' (n)' 접미 제거", not args.keep_file_suffix),
        ("이모지 제거", args.strip_emoji),
        ("채팅명 접두어 제거", ", ".join(args.strip_chat_prefix) or "없음"),
        ("추출 시각", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    sheets, stats = build_sheets(
        orders,
        jobs,
        unresolved,
        time_basis=args.time_basis,
        strip_file_suffix=not args.keep_file_suffix,
        strip_emoji=args.strip_emoji,
        strip_chat_prefixes=tuple(args.strip_chat_prefix),
        meta=meta,
        top=args.top,
    )

    output = Path(args.output) if args.output else Path(__file__).resolve().parent / "output" / (
        f"customer_summary_{args.start:%Y%m%d}_{args.end:%Y%m%d}.xlsx"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    first = sheets.pop(SHEET_BUYER)
    output.write_bytes(
        write_excel_with_text_zipcode(first, SHEET_BUYER, extra_sheets=sheets)
    )

    for label, value in stats.items():
        print(f"  {label}: {value}")
    print(f"저장: {output}  ({output.stat().st_size / 1024:.0f} KB)")
    print("주의: 이름·전화번호·주소가 들어간 개인정보 파일입니다. 커밋·외부 공유 금지.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
