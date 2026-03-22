import io
import json
import datetime
from pathlib import Path
import yaml
import pandas as pd
import streamlit as st

from services import (
    parse_catalog_json,
    generate_catalog_from_csv,
    parse_csv,
    extract_orders_from_chat,
    lookup_zip_code,
    format_phone_number,
    normalize_zip_code,
    batch_lookup_zip_codes,
    extract_chat_name,
)
from database import get_connection, save_training_record

# --- UI 구성 ---
st.set_page_config(page_title="Chat2Order: Convert Chat to Order", layout="wide")

with open("styles/main.css", encoding="utf-8") as css_file:
    st.markdown(f"<style>{css_file.read()}</style>", unsafe_allow_html=True)

if "logged_in_user" not in st.session_state:
    st.session_state["logged_in_user"] = None

if not st.session_state["logged_in_user"]:
    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_center:
        st.markdown(
            """
        <div style="text-align:center; padding:2rem 0;">
            <h1 style="color:#FF6B35;">Chat2Order</h1>
            <p style="color:#888;">로그인하여 시작하세요</p>
        </div>
        """,
            unsafe_allow_html=True,
        )
        with st.form("login_form"):
            email = st.text_input("이메일")
            password = st.text_input("비밀번호", type="password")
            submit_button = st.form_submit_button(
                "LogIn", use_container_width=True, type="primary"
            )

            if submit_button:
                tester_accounts = st.secrets.get("tester_accounts", {})
                if email in tester_accounts and str(tester_accounts[email]) == password:
                    st.session_state["logged_in_user"] = email
                    st.rerun()
                else:
                    st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
    st.stop()  # 로그인되지 않은 경우 아래 메인 앱 코드 실행 방지

# 로그인된 사용자 표시 및 로그아웃 버튼 (사이드바)
with st.sidebar:
    st.write(f"👤 **{st.session_state['logged_in_user']}**님 환영합니다.")
    if st.button("LogOut"):
        st.session_state["logged_in_user"] = None
        st.rerun()
    st.divider()


# --- 설정 파일 로드 ---
with open("config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

st.markdown(
    "## 📦 <span style='color:#FF6B35;font-weight:bold;'>C</span>hat<span style='color:#FF6B35;font-weight:bold;'>2O</span>rder",
    unsafe_allow_html=True,
)
st.markdown(
    "사장님은 소통에만 집중하세요. 대화 속 주문 정리는 C2O가 알아서 엑셀로 만들어 드립니다.",
    unsafe_allow_html=True,
)

juso_api_key = st.secrets.get("juso", {}).get("api_key", "")

# --- DB 연결 ---
supabase_url = st.secrets.get("supabase", {}).get("url", "")
supabase_key = st.secrets.get("supabase", {}).get("key", "")
db_conn = (
    get_connection(supabase_url, supabase_key)
    if supabase_url and supabase_key
    else None
)

# 사이드바: API 키 입력
with st.sidebar:
    st.header("설정")
    api_key_input = st.text_input("Gemini API Key를 입력하세요", type="password")
    st.markdown("[API Key 발급받기](https://aistudio.google.com/app/apikey)")

# --- 탭 구성 ---
tab_order, tab_catalog, tab_zipcode = st.tabs(
    ["📦 주문서 추출", "📋 카탈로그 생성", "📮 우편번호 추출"]
)

# ===== 탭 1: 주문서 추출 (기존 기능) =====
with tab_order:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            '<span class="step-badge">1</span> **카탈로그 업로드**',
            unsafe_allow_html=True,
        )
        catalog_file = st.file_uploader(
            "카탈로그를 업로드하세요.",
            type=["json"],
        )

    with col2:
        st.markdown(
            '<span class="step-badge">2</span> **대화 내역 업로드**',
            unsafe_allow_html=True,
        )
        chat_files = st.file_uploader(
            "카카오톡 대화 파일들을 업로드하세요.",
            type=["csv"],
            accept_multiple_files=True,
        )

        if chat_files:
            seen_names = set()
            unique_files = []
            for f in chat_files:
                if f.name not in seen_names:
                    seen_names.add(f.name)
                    unique_files.append(f)
            chat_files = unique_files
            with st.expander(f"📁 업로드된 파일 {len(chat_files)}개 보기"):
                for f in chat_files:
                    st.write(f"• {f.name}")

    st.markdown(
        '<span class="step-badge">3</span> **라이브쇼핑 시간 입력**',
        unsafe_allow_html=True,
    )
    start_col1, start_col2, end_col1, end_col2 = st.columns(4)
    with start_col1:
        start_date = st.date_input("시작 날짜")
    with start_col2:
        start_time = st.time_input("시작 시간")
    with end_col1:
        end_date = st.date_input("종료 날짜", value=start_date)
    with end_col2:
        end_time = st.time_input("종료 시간", value=datetime.time(23, 59))
    time_after = datetime.datetime.combine(start_date, start_time)
    time_before = datetime.datetime.combine(end_date, end_time)

    if st.button("🚀 주문서 추출 실행", type="primary", use_container_width=True):
        if not api_key_input:
            st.warning("왼쪽 사이드바에 Gemini API Key를 입력해 주세요.")
        elif not catalog_file:
            st.warning("카탈로그 파일을 업로드해 주세요.")
        elif not chat_files:
            st.warning("대화 내역 파일을 1개 이상 업로드해 주세요.")
        else:
            with st.status("주문서 추출 중입니다", expanded=True) as status:
                st.write("📋 카탈로그를 파싱 중")
                catalog_data = parse_catalog_json(catalog_file)
                all_extracted_orders = []

                today_str = datetime.date.today().strftime("%Y%m%d")
                seq = 1
                total_files = len(chat_files)
                progress_text = st.empty()
                progress_bar = st.progress(0)
                for i, chat_file in enumerate(chat_files):
                    progress_text.write(f"💬 채팅 내역 분석 중 ({i}/{total_files})")
                    chat_data, ts = parse_csv(
                        chat_file,
                        filename_prefix=config["csv"]["filename_prefix"],
                        exclude_messages=config["csv"]["exclude_messages"],
                        time_after=time_after,
                        time_before=time_before,
                    )

                    try:
                        extracted_data = extract_orders_from_chat(
                            api_key_input,
                            catalog_data,
                            chat_data,
                            model=config["gemini"]["model"],
                            temperature=config["gemini"]["temperature"],
                            prompt_template=st.secrets["prompt"]["order_extraction"],
                        )
                    except RuntimeError as e:
                        st.error(str(e))
                        extracted_data = None

                    if extracted_data:
                        if db_conn:
                            save_training_record(
                                conn=db_conn,
                                user_email=st.session_state["logged_in_user"],
                                chat_filename=chat_file.name,
                                model_name=config["gemini"]["model"],
                                catalog_data=catalog_data,
                                chat_data=chat_data,
                                response_json=extracted_data,
                            )
                        items = extracted_data.get("items", [])
                        if items:
                            chat_name = extract_chat_name(
                                chat_file.name,
                                filename_prefix=config["csv"]["filename_prefix"],
                            )
                            order_number = f"{today_str}{seq:03d}"
                            for item in items:
                                row = {
                                    **item,
                                    "order_name": extracted_data.get("order_name"),
                                    "phone_number": extracted_data.get("phone_number"),
                                    "address": extracted_data.get("address"),
                                    "search_address": extracted_data.get(
                                        "search_address"
                                    ),
                                    "time": ts,
                                    "chat_name": chat_name,
                                    "live_time": time_after,
                                    "order_number": order_number,
                                }
                                all_extracted_orders.append(row)
                            seq += 1

                    progress_bar.progress((i + 1) / total_files)
                    progress_text.write(f"💬 채팅 내역 분석 중 ({i + 1}/{total_files})")

                if juso_api_key:
                    st.write("📮 우편번호 조회 중")

                status.update(
                    label="🎉 주문 데이터 추출이 완료되었습니다!", state="complete"
                )

            if all_extracted_orders:
                df = pd.DataFrame(all_extracted_orders)
                df["phone_number"] = df["phone_number"].apply(format_phone_number)
                if "zip_code" in df.columns:
                    df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

                if juso_api_key:
                    df["zip_code"] = df["search_address"].apply(
                        lambda addr: lookup_zip_code(addr, juso_api_key)
                    )
                    df["zip_code"] = df["zip_code"].apply(normalize_zip_code)

                col_map = config["output_columns"]
                rename = {v: k for k, v in col_map.items() if v}
                df = df.rename(columns=rename)
                df = df.reindex(columns=list(col_map.keys()), fill_value="")

                st.dataframe(df, use_container_width=True)

                output = io.BytesIO()
                with pd.ExcelWriter(
                    output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS"
                ) as writer:
                    df.to_excel(
                        writer, index=False, sheet_name=config["output"]["sheet_name"]
                    )
                    worksheet = writer.sheets[config["output"]["sheet_name"]]
                    if "우편번호" in df.columns:
                        zip_col_idx = df.columns.get_loc("우편번호") + 1
                        for row in worksheet.iter_rows(
                            min_row=2,
                            max_row=worksheet.max_row,
                            min_col=zip_col_idx,
                            max_col=zip_col_idx,
                        ):
                            row[0].number_format = "@"

                st.download_button(
                    label="📥 엑셀 파일(.xlsx) 다운로드",
                    data=output.getvalue(),
                    file_name=config["output"]["file_name"],
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                )
            else:
                st.error(
                    "추출된 데이터가 없습니다. 원본 데이터나 API 상태를 확인해 주세요."
                )

# ===== 탭 2: 카탈로그 생성 =====
with tab_catalog:
    st.markdown(
        '<span class="step-badge">1</span> **재고 CSV 업로드**', unsafe_allow_html=True
    )
    stk_csv_file = st.file_uploader(
        "CSV 파일을 업로드하세요. (상품명·옵션내용 컬럼 필요)",
        type=["csv"],
        key="catalog_csv_uploader",
    )

    if stk_csv_file:
        try:
            catalog_dict = generate_catalog_from_csv(stk_csv_file)
        except ValueError as e:
            st.error(str(e))
            st.stop()

        st.markdown(
            '<span class="step-badge">2</span> **미리보기 및 확인**',
            unsafe_allow_html=True,
        )

        total_products = len(catalog_dict)
        total_options = sum(len(opts) for opts in catalog_dict.values())
        single_products = sum(
            1 for opts in catalog_dict.values() if opts == ["단일상품"]
        )

        stat_cols = st.columns(3)
        with stat_cols[0]:
            st.metric("총 상품 수", total_products)
        with stat_cols[1]:
            st.metric("총 옵션 수", total_options)
        with stat_cols[2]:
            st.metric("단일상품", single_products)

        preview_rows = []
        for product, options in catalog_dict.items():
            preview_rows.append(
                {
                    "상품명": product,
                    "옵션": ", ".join(options),
                    "옵션 수": len(options),
                }
            )
        preview_df = pd.DataFrame(preview_rows)
        preview_df.index = preview_df.index + 1
        preview_df.index.name = "#"
        st.dataframe(preview_df, use_container_width=True)

        st.markdown(
            '<span class="step-badge">3</span> **카탈로그 다운로드**',
            unsafe_allow_html=True,
        )

        catalog_json_str = json.dumps(catalog_dict, ensure_ascii=False, indent=2)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        st.download_button(
            label="📥 catalog.json 다운로드",
            data=catalog_json_str.encode("utf-8"),
            file_name=f"catalog_{timestamp}.json",
            mime="application/json",
            type="primary",
            use_container_width=True,
        )

# ===== 탭 3: 우편번호 추출 =====
with tab_zipcode:
    st.markdown(
        '<span class="step-badge">1</span> **엑셀 파일 업로드**',
        unsafe_allow_html=True,
    )
    zip_excel_file = st.file_uploader(
        "주소 컬럼이 포함된 엑셀 파일을 업로드하세요.",
        type=["xlsx", "xls"],
        key="zip_excel_uploader",
    )

    if zip_excel_file:
        zip_df = pd.read_excel(zip_excel_file)
        if "주소" not in zip_df.columns:
            st.error(
                f"'주소' 컬럼을 찾을 수 없습니다. 발견된 컬럼: {list(zip_df.columns)}"
            )
        else:
            st.markdown(
                '<span class="step-badge">2</span> **미리보기**',
                unsafe_allow_html=True,
            )
            has_zip_col = "우편번호" in zip_df.columns
            if has_zip_col:
                st.info(
                    "파일에 이미 '우편번호' 컬럼이 있습니다. 조회 결과로 덮어씁니다."
                )
            st.dataframe(zip_df.head(10), use_container_width=True)
            st.caption(f"총 {len(zip_df)}건")

            if st.button(
                "📮 우편번호 조회 실행",
                type="primary",
                use_container_width=True,
                key="zip_lookup_btn",
            ):
                if not api_key_input:
                    st.warning("왼쪽 사이드바에 Gemini API Key를 입력해 주세요.")
                elif not juso_api_key:
                    st.warning("도로명주소 API 키가 설정되지 않았습니다.")
                else:
                    with st.status("우편번호 조회 중입니다", expanded=True) as zstatus:
                        progress_text = st.empty()
                        progress_bar = st.progress(0)
                        total = len(zip_df)

                        def _progress(idx, total_count):
                            pct = min((idx + 1) / total_count, 1.0)
                            progress_bar.progress(pct)
                            progress_text.write(
                                f"📮 우편번호 조회 중... ({idx + 1}/{total_count})"
                            )

                        result_series = batch_lookup_zip_codes(
                            df=zip_df,
                            address_col="주소",
                            juso_api_key=juso_api_key,
                            api_key=api_key_input,
                            model=config["gemini"]["model"],
                            temperature=config["gemini"]["temperature"],
                            prompt_template=st.secrets.get("prompt", {}).get(
                                "address_to_search", ""
                            ),
                            progress_callback=_progress,
                        )
                        if has_zip_col:
                            zip_df["우편번호"] = result_series
                        else:
                            addr_pos = zip_df.columns.get_loc("주소")
                            zip_df.insert(addr_pos + 1, "우편번호", result_series)

                        found = (zip_df["우편번호"] != "").sum()
                        zstatus.update(
                            label=f"🎉 우편번호 조회 완료! ({found}/{total}건 성공)",
                            state="complete",
                        )

                    st.markdown(
                        '<span class="step-badge">3</span> **결과 확인 및 다운로드**',
                        unsafe_allow_html=True,
                    )

                    found = (zip_df["우편번호"] != "").sum()
                    missed = total - found
                    stat_cols = st.columns(3)
                    with stat_cols[0]:
                        st.metric("전체", total)
                    with stat_cols[1]:
                        st.metric("성공", found)
                    with stat_cols[2]:
                        st.metric("미조회", missed)

                    st.dataframe(zip_df, use_container_width=True)

                    zip_output = io.BytesIO()
                    with pd.ExcelWriter(zip_output, engine="openpyxl") as zwriter:
                        zip_df.to_excel(zwriter, index=False, sheet_name="Sheet1")
                        zws = zwriter.sheets["Sheet1"]
                        if "우편번호" in zip_df.columns:
                            zcol = zip_df.columns.get_loc("우편번호") + 1
                            for row in zws.iter_rows(
                                min_row=2,
                                max_row=zws.max_row,
                                min_col=zcol,
                                max_col=zcol,
                            ):
                                row[0].number_format = "@"

                    original_name = Path(zip_excel_file.name).stem
                    st.download_button(
                        label="📥 엑셀 파일(.xlsx) 다운로드",
                        data=zip_output.getvalue(),
                        file_name=f"{original_name}_우편번호.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary",
                        key="zip_download_btn",
                    )
