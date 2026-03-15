import io
import datetime
import yaml
import pandas as pd
import streamlit as st

from services import (
    parse_custom_jsonl,
    parse_csv,
    extract_orders_from_chat,
    lookup_zip_code,
    format_phone_number,
    extract_timestamp,
    extract_chat_name,
)
from database import get_connection, save_training_record

# --- UI 구성 ---
st.set_page_config(page_title="Chat2Order: Convert Chat to Order", layout="wide")

if "logged_in_user" not in st.session_state:
    st.session_state["logged_in_user"] = None

if not st.session_state["logged_in_user"]:
    st.markdown("## 🔐 로그인")
    with st.form("login_form"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submit_button = st.form_submit_button("로그인")

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
    if st.button("로그아웃"):
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

# 메인 화면: 파일 업로드
col1, col2 = st.columns(2)
with col1:
    st.subheader("1. 카탈로그 업로드")
    catalog_file = st.file_uploader(
        "catalog.jsonl 파일을 업로드하세요.", type=["jsonl"]
    )

with col2:
    st.subheader("2. 대화 내역 업로드")
    chat_files = st.file_uploader(
        "대화 파일들을 업로드하세요. (CSV 또는 JSONL)",
        type=["csv", "jsonl"],
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

st.subheader("3. 라이브쇼핑 시간 입력")
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

# 실행 버튼
if st.button("🚀 주문서 추출 실행", type="primary", use_container_width=True):
    if not api_key_input:
        st.warning("왼쪽 사이드바에 Gemini API Key를 입력해 주세요.")
    elif not catalog_file:
        st.warning("카탈로그 파일을 업로드해 주세요.")
    elif not chat_files:
        st.warning("대화 내역 파일을 1개 이상 업로드해 주세요.")
    else:
        with st.spinner(
            "Gemini가 대화를 분석하고 주문서를 작성 중입니다... (데이터량에 따라 10~30초 소요)"
        ):
            catalog_data = parse_custom_jsonl(catalog_file)
            all_extracted_orders = []

            for chat_file in chat_files:
                if chat_file.name.endswith(".csv"):
                    chat_data, ts = parse_csv(
                        chat_file,
                        filename_prefix=config["csv"]["filename_prefix"],
                        exclude_messages=config["csv"]["exclude_messages"],
                        time_after=time_after,
                        time_before=time_before,
                    )
                else:
                    chat_data = parse_custom_jsonl(
                        chat_file, time_after=time_after, time_before=time_before
                    )
                    ts = extract_timestamp(chat_file.name)

                try:
                    extracted_data = extract_orders_from_chat(
                        api_key_input,
                        catalog_data,
                        chat_data,
                        model=config["gemini"]["model"],
                        temperature=config["gemini"]["temperature"],
                        prompt_template=config["prompt"]["order_extraction"],
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
                    chat_name = extract_chat_name(
                        chat_file.name,
                        filename_prefix=(
                            config["csv"]["filename_prefix"]
                            if chat_file.name.endswith(".csv")
                            else ""
                        ),
                    )
                    for order in extracted_data:
                        order["time"] = ts
                        order["chat_name"] = chat_name
                        order["live_time"] = time_after
                    all_extracted_orders.extend(extracted_data)

        if all_extracted_orders:
            df = pd.DataFrame(all_extracted_orders)
            df["phone_number"] = df["phone_number"].apply(format_phone_number)

            if juso_api_key:
                with st.spinner("우편번호를 조회 중입니다..."):
                    df["zip_code"] = df["search_address"].apply(
                        lambda addr: lookup_zip_code(addr, juso_api_key)
                    )

            col_map = config["output_columns"]
            rename = {v: k for k, v in col_map.items() if v}
            df = df.rename(columns=rename)
            df = df.reindex(columns=list(col_map.keys()), fill_value="")

            st.success("🎉 주문 데이터 추출이 완료되었습니다!")
            st.dataframe(df, use_container_width=True)

            output = io.BytesIO()
            with pd.ExcelWriter(
                output, engine="openpyxl", datetime_format="YYYY-MM-DD HH:MM:SS"
            ) as writer:
                df.to_excel(
                    writer, index=False, sheet_name=config["output"]["sheet_name"]
                )

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
